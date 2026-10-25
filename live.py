"""
Live / paper trading on Interactive Brokers.

Run it on a schedule (e.g. every hour). Each run it:
  1. pulls daily bars for every market,
  2. exits positions whose channel exit or trailing stop has triggered,
  3. updates trailing stops (saved in state.json),
  4. opens new breakout trades, sized by RISK_PER_TRADE.

Requirements: TWS or IB Gateway running and logged in (paper account first!),
API enabled in TWS settings, and `pip install ib_async pandas`.
Keep DRY_RUN = True in config.py until you trust what it prints.
"""
import json
import logging
import os
import sys
from datetime import datetime

import pandas as pd
from ib_async import IB, Contract, MarketOrder, LimitOrder, StopOrder, util

import config as C
import strategy as S
import risk as R

STATE_FILE = "state.json"
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(f"logs/bot_{datetime.now():%Y%m}.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot")


# ---------------- state (trailing stops, peak equity) ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"stops": {}, "peak_equity": 0.0, "halted_until": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------- IB helpers ----------------
def make_contract(m):
    return Contract(**m["ib"])


def daily_bars(ib, contract, what):
    bars = ib.reqHistoricalData(
        contract, endDateTime="", durationStr="2 Y", barSizeSetting="1 day",
        whatToShow=what, useRTH=True, formatDate=1,
    )
    if not bars:
        return None
    df = util.df(bars).set_index("date")[["open", "high", "low", "close"]]
    return S.add_indicators(df)


def current_positions(ib):
    out = {}
    for p in ib.positions():
        c = p.contract
        for name, m in C.MARKETS.items():
            if c.secType == m["ib"]["secType"] and c.symbol == m["ib"]["symbol"] \
                    and c.currency == m["ib"]["currency"]:
                out[name] = p.position
    return out


def net_liquidation(ib):
    for v in ib.accountSummary():
        if v.tag == "NetLiquidation" and v.currency == "USD":
            return float(v.value)
    raise RuntimeError("Could not read account equity")


def send(ib, contract, m, action, qty, ref_price):
    """Market order, except crypto which IB handles best as an aggressive IOC limit."""
    if qty <= 0:
        return
    if m["ib"]["secType"] == "CRYPTO":
        px = round(ref_price * (1.01 if action == "BUY" else 0.99), 2)
        order = LimitOrder(action, qty, px, tif="IOC")
    else:
        order = MarketOrder(action, qty)
    log.info(f"ORDER {action} {qty} {m['ib']['symbol']} (ref {ref_price:.5g})"
             + ("  [DRY RUN - not sent]" if C.DRY_RUN else ""))
    if not C.DRY_RUN:
        trade = ib.placeOrder(contract, order)
        ib.sleep(2)
        log.info(f"  status: {trade.orderStatus.status}")


def place_protective_stop(ib, contract, m, direction, qty, stop):
    """Broker-side stop so the position is protected even if the bot is offline.
    Crypto on IB does not take stop orders, so the bot checks those stops itself."""
    if m["ib"]["secType"] == "CRYPTO" or C.DRY_RUN:
        return
    for o in ib.openOrders():
        if o.orderType == "STP" and getattr(o, "orderRef", "") == m["ib"]["symbol"]:
            ib.cancelOrder(o)
    action = "SELL" if direction == 1 else "BUY"
    order = StopOrder(action, abs(qty), round(stop, 5), tif="GTC")
    order.orderRef = m["ib"]["symbol"]
    ib.placeOrder(contract, order)


# ---------------- main loop ----------------
def run_once():
    ib = IB()
    ib.connect(C.IB_HOST, C.IB_PORT, clientId=C.IB_CLIENT_ID)
    state = load_state()
    try:
        equity = net_liquidation(ib)
        peak, halted_until = R.update_halt(equity, state.get("peak_equity", 0.0),
                                           state.get("halted_until"), datetime.now())
        state["peak_equity"] = peak
        state["halted_until"] = halted_until.isoformat() if halted_until is not None else None
        if halted_until is not None:
            log.warning(f"Drawdown pause: no new trades until {halted_until.date()}")
        held = current_positions(ib)
        log.info(f"Equity ${equity:,.0f}  peak ${state['peak_equity']:,.0f}  open positions: {held}")

        frames, contracts = {}, {}
        for name, m in C.MARKETS.items():
            try:
                c = make_contract(m)
                ib.qualifyContracts(c)
                df = daily_bars(ib, c, m["what"])
                if df is None or len(df) < C.TREND_FILTER + 1:
                    log.warning(f"{name}: not enough data, skipping")
                    continue
                frames[name], contracts[name] = df, c
            except Exception as e:
                log.error(f"{name}: data error {e}")

        # --- manage open positions ---
        open_risk = 0.0
        for name, qty in list(held.items()):
            if name not in frames or qty == 0:
                continue
            m, df, c = C.MARKETS[name], frames[name], contracts[name]
            row, price = df.iloc[-1], df["close"].iloc[-1]
            d = 1 if qty > 0 else -1
            stop = state["stops"].get(name, S.initial_stop(d, price, row["atr"]))
            stop_hit = price <= stop if d == 1 else price >= stop
            if stop_hit or S.channel_exit(d, row):
                log.info(f"{name}: EXIT ({'stop' if stop_hit else 'channel'})")
                send(ib, c, m, "SELL" if d == 1 else "BUY", abs(qty), price)
                state["stops"].pop(name, None)
                held.pop(name)
                continue
            stop = S.update_trailing_stop(d, stop, price, row["atr"])
            state["stops"][name] = stop
            place_protective_stop(ib, c, m, d, qty, stop)
            open_risk += R.open_risk_usd(m, qty, price, stop)
            log.info(f"{name}: holding {qty}, stop {stop:.5g}")

        # --- look for new entries ---
        for name, df in frames.items():
            if name in held:
                continue
            m, row = C.MARKETS[name], df.iloc[-1]
            sig = S.entry_signal(row, m["short"])
            if not sig:
                continue
            if not R.can_open(equity, state["halted_until"] is not None, len(held), open_risk):
                log.info(f"{name}: signal {sig:+d} skipped (risk limits)")
                continue
            price = row["close"]
            stop = S.initial_stop(sig, price, row["atr"])
            qty = R.position_size(m, equity, price, stop)
            if qty <= 0:
                log.info(f"{name}: signal {sig:+d} but size rounds to 0")
                continue
            log.info(f"{name}: ENTRY {'LONG' if sig == 1 else 'SHORT'} qty {qty} stop {stop:.5g}")
            send(ib, contracts[name], m, "BUY" if sig == 1 else "SELL", qty, price)
            state["stops"][name] = stop
            place_protective_stop(ib, contracts[name], m, sig, qty, stop)
            held[name] = qty * sig
            open_risk += R.open_risk_usd(m, qty, price, stop)
    finally:
        save_state(state)
        ib.disconnect()


if __name__ == "__main__":
    run_once()
