"""
Trend-following breakout strategy (in the style of the Turtle traders).

Entry  : close breaks above the highest high of the last ENTRY_LOOKBACK days (long)
         or below the lowest low (short), in the direction of the 200-day trend.
Exit   : close breaks the opposite EXIT_LOOKBACK-day channel, or the
         trailing stop (STOP_ATR_MULT x ATR) is hit.
"""
import pandas as pd
import config as C


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df needs columns: open, high, low, close (daily bars)."""
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(C.ATR_PERIOD).mean()
    df["entry_high"] = df["high"].rolling(C.ENTRY_LOOKBACK).max().shift(1)
    df["entry_low"] = df["low"].rolling(C.ENTRY_LOOKBACK).min().shift(1)
    df["exit_high"] = df["high"].rolling(C.EXIT_LOOKBACK).max().shift(1)
    df["exit_low"] = df["low"].rolling(C.EXIT_LOOKBACK).min().shift(1)
    df["trend_ma"] = df["close"].rolling(C.TREND_FILTER).mean()
    return df


def entry_signal(row, allow_short: bool) -> int:
    """+1 = go long, -1 = go short, 0 = nothing. Uses a fully-formed bar."""
    if pd.isna(row["trend_ma"]) or pd.isna(row["atr"]) or row["atr"] <= 0:
        return 0
    if row["close"] > row["entry_high"] and row["close"] > row["trend_ma"]:
        return 1
    if allow_short and row["close"] < row["entry_low"] and row["close"] < row["trend_ma"]:
        return -1
    return 0


def initial_stop(direction: int, price: float, atr: float) -> float:
    return price - direction * C.STOP_ATR_MULT * atr


def update_trailing_stop(direction: int, stop: float, close: float, atr: float) -> float:
    """Stops only ever move in the trade's favour."""
    new = close - direction * C.STOP_ATR_MULT * atr
    return max(stop, new) if direction == 1 else min(stop, new)


def channel_exit(direction: int, row) -> bool:
    if direction == 1:
        return row["close"] < row["exit_low"]
    return row["close"] > row["exit_high"]
