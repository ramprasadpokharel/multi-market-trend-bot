"""
Position sizing. The risk percentage controls HOW MUCH you buy, not WHERE you exit.

    units = (equity x RISK_PER_TRADE) / (stop distance per unit, in USD)

So a wider stop means a smaller position, and every trade loses about the same
amount of the account if the stop is hit.
"""
import math
import config as C


def usd_per_unit(market: dict, price_move: float, price: float) -> float:
    """Convert a price move into USD per unit (handles USDJPY-style pairs)."""
    if market.get("usd_base"):
        return price_move / price
    return price_move


def position_size(market: dict, equity: float, entry: float, stop: float) -> float:
    risk_usd = equity * C.RISK_PER_TRADE
    per_unit = usd_per_unit(market, abs(entry - stop), entry)
    if per_unit <= 0:
        return 0.0
    units = risk_usd / per_unit
    # never put more than the whole account into one position (no hidden leverage)
    notional_per_unit = 1.0 if market.get("usd_base") else entry
    units = min(units, equity / notional_per_unit)
    step = market["step"]
    return math.floor(units / step) * step


def open_risk_usd(market: dict, units: float, price: float, stop: float) -> float:
    """How much would be lost right now if this position's stop were hit."""
    return max(0.0, usd_per_unit(market, abs(price - stop), price) * abs(units))


def update_halt(equity, peak, halted_until, today):
    """Drawdown circuit breaker. Returns (peak, halted_until).
    A 20% fall pauses new trades for HALT_DAYS; afterwards the peak resets to
    current equity so the bot can trade again (a permanent stop would freeze it forever)."""
    import pandas as pd
    today = pd.Timestamp(today)
    if halted_until is not None:
        if today >= pd.Timestamp(halted_until):
            return equity, None
        return peak, halted_until
    peak = max(peak, equity)
    if peak > 0 and equity < peak * (1 - C.MAX_DRAWDOWN_HALT):
        return peak, today + pd.Timedelta(days=C.HALT_DAYS)
    return peak, None


def can_open(equity: float, halted: bool, n_open: int, total_open_risk: float) -> bool:
    if halted:
        return False
    if n_open >= C.MAX_POSITIONS:
        return False
    if total_open_risk + equity * C.RISK_PER_TRADE > equity * C.MAX_TOTAL_RISK:
        return False
    return True
