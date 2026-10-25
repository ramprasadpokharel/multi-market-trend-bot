"""
Portfolio backtest across all markets in config.MARKETS.

    python3 backtest.py              # reads the Stooq CSV files from data/ or ~/Downloads
    python3 backtest.py --synthetic  # random data, just to check the code runs

Signals are computed on a day's close and filled at the NEXT day's open,
so the test never uses information it wouldn't have had.
"""
import argparse
import os
import numpy as np
import pandas as pd

import config as C
import strategy as S
import risk as R


# ---------------- data ----------------
def find_csv(name, m, folders):
    """Look for the Stooq file (e.g. spy_us_d.csv) or <NAME>.csv in each folder."""
    stooq_file = m["stooq"].replace(".", "_") + "_d.csv"
    for folder in folders:
        folder = os.path.expanduser(folder)
        for fname in (stooq_file, f"{name}.csv"):
            path = os.path.join(folder, fname)
            if os.path.exists(path):
                return path
    return None


def load_csv(folders, start):
    """Reads daily price CSVs (columns Date, Open, High, Low, Close; any case).
    Uses only pandas: no extra download tools."""
    out = {}
    for name, m in C.MARKETS.items():
        path = find_csv(name, m, folders)
        if not path:
            print(f"  {name}: file not found ({m['stooq'].replace('.', '_')}_d.csv), skipping")
            continue
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        if "date" not in df.columns or not {"open", "high", "low", "close"} <= set(df.columns):
            print(f"  {name}: {path} is not a price file, skipping")
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()[["open", "high", "low", "close"]].dropna()
        df = df[df.index >= pd.Timestamp(start) - pd.Timedelta(days=400)]  # extra year to warm up indicators
        print(f"  {name}: {len(df)} days from {path}")
        out[name] = df
    return out


def load_synthetic(seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", "2025-12-31")
    out = {}
    for i, name in enumerate(C.MARKETS):
        drift = rng.normal(0.0002, 0.0003)
        vol = rng.uniform(0.006, 0.03)
        start = {"USDJPY": 120.0, "EURUSD": 1.1, "GBPUSD": 1.3}.get(name, 100.0)
        close = start * np.exp(np.cumsum(rng.normal(drift, vol, len(idx))))
        opn = close * (1 + rng.normal(0, vol / 4, len(idx)))
        high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, len(idx))))
        low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, len(idx))))
        out[name] = pd.DataFrame({"open": opn, "high": high, "low": low, "close": close}, index=idx)
    return out


# ---------------- simulation ----------------
def run(data):
    data = {k: S.add_indicators(v) for k, v in data.items()}
    dates = sorted(set().union(*[d.index for d in data.values()]))
    cost = C.COMMISSION_PCT + C.SLIPPAGE_PCT

    cash = C.INITIAL_CAPITAL
    peak = cash
    halted_until = None
    halts = 0
    positions = {}   # name -> dict(dir, units, entry, stop, entry_date)
    pending = {}     # name -> action for next open: ("enter", dir) or ("exit", reason)
    last_close = {}
    trades, equity_curve = [], []

    def pnl_usd(m, pos, price):
        return R.usd_per_unit(m, (price - pos["entry"]) * pos["dir"], price) * pos["units"]

    def close_pos(name, price, date, reason):
        nonlocal cash
        m, pos = C.MARKETS[name], positions.pop(name)
        fill = price * (1 - pos["dir"] * cost)
        gross = pnl_usd(m, pos, fill)
        fee = R.usd_per_unit(m, pos["entry"] * cost, pos["entry"]) * pos["units"] if m.get("usd_base") \
            else pos["entry"] * cost * pos["units"]
        cash += gross - fee
        trades.append(dict(market=name, dir=pos["dir"], entry_date=pos["entry_date"], exit_date=date,
                           entry=pos["entry"], exit=fill, units=pos["units"], pnl=gross - fee, reason=reason))

    def equity_now():
        return cash + sum(pnl_usd(C.MARKETS[n], p, last_close.get(n, p["entry"])) for n, p in positions.items())

    for date in dates:
        # 1) execute yesterday's decisions at today's open, then check stops intraday
        for name, df in data.items():
            if date not in df.index:
                continue
            bar = df.loc[date]
            act = pending.pop(name, None)
            if act and act[0] == "exit" and name in positions:
                close_pos(name, bar["open"], date, act[1])
            elif act and act[0] == "enter" and name not in positions:
                d, atr = act[1], act[2]
                entry = bar["open"] * (1 + d * cost)
                stop = S.initial_stop(d, entry, atr)
                eq = equity_now()
                units = R.position_size(C.MARKETS[name], eq, entry, stop)
                if units > 0:
                    positions[name] = dict(dir=d, units=units, entry=entry, stop=stop, entry_date=date)
            if name in positions:
                p = positions[name]
                hit = bar["low"] <= p["stop"] if p["dir"] == 1 else bar["high"] >= p["stop"]
                if hit:
                    gap = bar["open"] < p["stop"] if p["dir"] == 1 else bar["open"] > p["stop"]
                    close_pos(name, bar["open"] if gap else p["stop"], date, "stop")
            last_close[name] = bar["close"]

        # 2) at the close, decide what to do tomorrow
        eq = equity_now()
        was_halted = halted_until is not None
        peak, halted_until = R.update_halt(eq, peak, halted_until, date)
        if halted_until is not None and not was_halted:
            halts += 1
        open_risk = sum(R.open_risk_usd(C.MARKETS[n], p["units"], last_close[n], p["stop"])
                        for n, p in positions.items())
        for name, df in data.items():
            if date not in df.index:
                continue
            row = df.loc[date]
            if name in positions:
                p = positions[name]
                if S.channel_exit(p["dir"], row):
                    pending[name] = ("exit", "channel")
                elif not pd.isna(row["atr"]):
                    p["stop"] = S.update_trailing_stop(p["dir"], p["stop"], row["close"], row["atr"])
            else:
                sig = S.entry_signal(row, C.MARKETS[name]["short"])
                n_open = len(positions) + sum(1 for a in pending.values() if a[0] == "enter")
                if sig and R.can_open(eq, halted_until is not None, n_open, open_risk):
                    pending[name] = ("enter", sig, row["atr"])
                    open_risk += eq * C.RISK_PER_TRADE
        equity_curve.append((date, eq))

    for name in list(positions):
        close_pos(name, last_close[name], dates[-1], "end of test")

    eq = pd.Series(dict(equity_curve)).sort_index()
    print(f"  Drawdown pauses triggered: {halts}")
    return eq, pd.DataFrame(trades)


# ---------------- report ----------------
def report(eq, trades):
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    rets = eq.pct_change().dropna()
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    print("\n========== BACKTEST RESULTS ==========")
    print(f"Period          : {eq.index[0].date()} to {eq.index[-1].date()} ({years:.1f} years)")
    print(f"Start / End     : ${eq.iloc[0]:,.0f} -> ${eq.iloc[-1]:,.0f}")
    print(f"Total return    : {total:.1%}")
    print(f"Yearly (CAGR)   : {cagr:.1%}")
    print(f"Max drawdown    : {dd:.1%}")
    print(f"Sharpe ratio    : {sharpe:.2f}")
    if len(trades):
        wins = trades[trades.pnl > 0]
        losses = trades[trades.pnl <= 0]
        pf = wins.pnl.sum() / abs(losses.pnl.sum()) if len(losses) and losses.pnl.sum() != 0 else float("inf")
        print(f"Trades          : {len(trades)}")
        print(f"Win rate        : {len(wins) / len(trades):.1%}")
        print(f"Avg win / loss  : ${wins.pnl.mean() if len(wins) else 0:,.0f} / ${losses.pnl.mean() if len(losses) else 0:,.0f}")
        print(f"Profit factor   : {pf:.2f}")
        print("\nP&L by market:")
        print(trades.groupby("market").pnl.agg(["count", "sum"]).round(0).to_string())
    print("======================================")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="extra folder to look in for the CSV files")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    if args.synthetic:
        data = load_synthetic()
    else:
        folders = ([args.csv] if args.csv else []) + C.DATA_FOLDERS
        print("Loading price files...")
        data = load_csv(folders, C.BACKTEST_START)
    if not data:
        raise SystemExit("No price files found. Download them from stooq.com (see README).")

    eq, trades = run(data)
    report(eq, trades)
    os.makedirs("results", exist_ok=True)
    eq.to_csv("results/equity.csv", header=["equity"])
    trades.to_csv("results/trades.csv", index=False)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        ax[0].plot(eq.index, eq.values)
        ax[0].set_title("Equity curve")
        ax[1].fill_between(eq.index, (eq / eq.cummax() - 1).values, 0, color="red", alpha=0.4)
        ax[1].set_title("Drawdown")
        plt.tight_layout()
        plt.savefig("results/equity.png", dpi=120)
        print("Saved results/equity.png, equity.csv, trades.csv")
    except ImportError:
        pass
