"""
Settings for the multi-market trend-following bot.
Change values here, not in the other files.
"""

# ---------------- Risk ----------------
RISK_PER_TRADE = 0.02      # fraction of account lost if a stop is hit (0.01 = 1%)
MAX_TOTAL_RISK = 0.08      # max combined open risk across all positions (8%)
MAX_POSITIONS = 8          # max number of open positions at once
MAX_DRAWDOWN_HALT = 0.20   # pause new trades if equity falls 20% from its peak...
HALT_DAYS = 60             # ...for this many days, then start again from the new level

# ---------------- Strategy ----------------
ENTRY_LOOKBACK = 55        # enter on a breakout of the last 55-day high/low
EXIT_LOOKBACK = 20         # exit on a break of the last 20-day low/high
ATR_PERIOD = 20            # volatility measure (Average True Range)
STOP_ATR_MULT = 3.0        # trailing stop distance = 3 x ATR
TREND_FILTER = 200         # only go long above the 200-day average, short below

# ---------------- Backtest ----------------
INITIAL_CAPITAL = 10_000
COMMISSION_PCT = 0.0005    # 0.05% per side
SLIPPAGE_PCT = 0.0005      # 0.05% per side
BACKTEST_START = "2015-01-01"
DATA_FOLDERS = ["data", "~/Downloads"]   # where backtest looks for the CSV files

# ---------------- Interactive Brokers ----------------
IB_HOST = "127.0.0.1"
IB_PORT = 7497             # 7497 = TWS paper, 7496 = TWS live, 4002 = Gateway paper
IB_CLIENT_ID = 17
DRY_RUN = True             # True = only print orders, never send them

# ---------------- Markets ----------------
# stooq     : Stooq symbol; backtest reads the CSV downloaded from
#             stooq.com/q/d/?s=<symbol> (saved as <symbol>_d.csv)
# ib        : Interactive Brokers contract details
# what      : IB historical data type
# short     : allow short positions
# step      : smallest order size
# usd_base  : True for pairs like USDJPY where profit is in the quote currency,
#             so it must be converted back to USD
MARKETS = {
    # US stocks / ETFs
    "SPY": dict(stooq="spy.us", ib=dict(secType="STK", symbol="SPY", exchange="SMART", currency="USD"),
                what="TRADES", short=False, step=1),
    "QQQ": dict(stooq="qqq.us", ib=dict(secType="STK", symbol="QQQ", exchange="SMART", currency="USD"),
                what="TRADES", short=False, step=1),
    "IWM": dict(stooq="iwm.us", ib=dict(secType="STK", symbol="IWM", exchange="SMART", currency="USD"),
                what="TRADES", short=False, step=1),

    # Crypto (IB routes US crypto through Paxos; no shorting)
    "BTC": dict(stooq="btcusd", ib=dict(secType="CRYPTO", symbol="BTC", exchange="PAXOS", currency="USD"),
                what="AGGTRADES", short=False, step=0.0001),
    "ETH": dict(stooq="ethusd", ib=dict(secType="CRYPTO", symbol="ETH", exchange="PAXOS", currency="USD"),
                what="AGGTRADES", short=False, step=0.001),

    # Metals (spot gold/silver)
    "GOLD": dict(stooq="xauusd", ib=dict(secType="CMDTY", symbol="XAUUSD", exchange="SMART", currency="USD"),
                 what="MIDPOINT", short=True, step=1),
    "SILVER": dict(stooq="xagusd", ib=dict(secType="CMDTY", symbol="XAGUSD", exchange="SMART", currency="USD"),
                   what="MIDPOINT", short=True, step=1),

    # Forex
    "EURUSD": dict(stooq="eurusd", ib=dict(secType="CASH", symbol="EUR", exchange="IDEALPRO", currency="USD"),
                   what="MIDPOINT", short=True, step=1000),
    "GBPUSD": dict(stooq="gbpusd", ib=dict(secType="CASH", symbol="GBP", exchange="IDEALPRO", currency="USD"),
                   what="MIDPOINT", short=True, step=1000),
    "USDJPY": dict(stooq="usdjpy", ib=dict(secType="CASH", symbol="USD", exchange="IDEALPRO", currency="JPY"),
                   what="MIDPOINT", short=True, step=1000, usd_base=True),
}
