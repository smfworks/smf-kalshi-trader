"""Kelly Criterion Position Sizing for Prediction Markets.

Implements fractional Kelly for binary outcome contracts (yes/no).
Accounts for bankroll, edge magnitude, and maximum loss constraints.

Formula: f* = (bp - q) / b
Where:
    b = odds (decimal) — for prediction markets, roughly 1/price
    p = probability of win (our estimated probability)
    q = 1 - p
    f* = fraction of bankroll to bet

We use fractional Kelly (typically 1/4 to 1/2 Kelly) to reduce volatility.

Author: Gabriel
"""

from typing import Optional, Tuple
import math


def kelly_criterion(
    our_probability: float,
    market_probability: float,
    bankroll: float,
    fraction: float = 0.25,
    max_loss_pct: float = 0.02,
    min_edge: float = 0.05
) -> Tuple[float, float, str]:
    """
    Calculate position size using fractional Kelly Criterion.

    Args:
        our_probability: Our estimated probability of YES (0.0 to 1.0)
        market_probability: Market's implied probability = market price (0.0 to 1.0)
        bankroll: Total available bankroll in cents
        fraction: Fractional Kelly (0.25 = quarter Kelly, default conservative)
        max_loss_pct: Maximum % of bankroll to risk on single trade (default 2%)
        min_edge: Minimum edge required to trade (default 5%)

    Returns:
        (position_size_cents, edge, reason)
        position_size_cents: 0 if no trade
        edge: our_probability - market_probability
        reason: "edge_too_low", "kelly_zero", "max_loss_cap", or "ok"
    """
    # Validate inputs
    if not (0 < our_probability < 1):
        return 0, 0.0, "invalid_probability"
    if not (0 < market_probability < 1):
        return 0, 0.0, "invalid_market_price"
    if bankroll <= 0:
        return 0, 0.0, "no_bankroll"

    # Calculate edge
    edge = our_probability - market_probability

    # Minimum edge filter
    if abs(edge) < min_edge:
        return 0, edge, "edge_too_low"

    # For prediction markets, "odds" b = (1 - market_price) / market_price
    # If market says 60¢, odds of YES are 0.4/0.6 = 0.667
    if market_probability <= 0.01:
        return 0, edge, "invalid_market_price"

    b = (1 - market_probability) / market_probability  # decimal odds
    p = our_probability
    q = 1 - p

    # Full Kelly fraction
    kelly_full = (b * p - q) / b

    # Edge cases: negative Kelly (no bet)
    if kelly_full <= 0:
        return 0, edge, "kelly_zero"

    # Fractional Kelly
    kelly_fractional = kelly_full * fraction

    # Position size in cents
    position_size = int(bankroll * kelly_fractional)

    # Maximum loss cap (don't risk more than max_loss_pct of bankroll)
    max_loss_amount = int(bankroll * max_loss_pct)
    if position_size > max_loss_amount:
        position_size = max_loss_amount
        reason = "max_loss_cap"
    else:
        reason = "ok"

    # Minimum trade size (Kalshi requires >= 1 cent)
    if position_size < 1:
        return 0, edge, "below_minimum"

    return position_size, edge, reason


def calculate_edge_from_price(
    our_yes_probability: float,
    market_yes_price: float
) -> float:
    """
    Simple edge calculation for YES contracts.
    
    Args:
        our_yes_probability: Our estimated P(YES)
        market_yes_price: Market price in cents (0-100)
    
    Returns:
        Edge as decimal (e.g., 0.08 = 8% edge)
    """
    market_probability = market_yes_price / 100.0
    return our_yes_probability - market_probability


def should_trade(
    edge: float,
    min_edge: float = 0.05,
    min_bankroll: float = 1000.0
) -> Tuple[bool, str]:
    """
    Simple trade/no-trade decision gate.
    
    Returns:
        (trade, reason)
    """
    if abs(edge) < min_edge:
        return False, f"edge {edge:.2%} below minimum {min_edge:.2%}"
    return True, "edge_sufficient"


def bankroll_after_trade(
    current_bankroll: float,
    position_size: float,
    won: bool,
    market_yes_price: float
) -> float:
    """
    Calculate bankroll after a resolved trade.
    
    For YES contracts:
    - If won: gain (100 - price) per contract
    - If lost: lose price per contract
    
    Args:
        current_bankroll: Starting bankroll
        position_size: Size of trade in cents
        won: True if YES resolved Yes
        market_yes_price: Price paid per contract in cents
    
    Returns:
        New bankroll
    """
    if won:
        profit = position_size * ((100 - market_yes_price) / market_yes_price)
        return current_bankroll + profit
    else:
        return current_bankroll - position_size


# --- Example / Test ---
if __name__ == "__main__":
    # Test case: We think YES is 70%, market says 55%
    our_prob = 0.70
    market_price = 0.55  # 55 cents
    bankroll = 10000.0  # $100 = 10000 cents

    size, edge, reason = kelly_criterion(our_prob, market_price, bankroll)
    print(f"Edge: {edge:.2%}")
    print(f"Kelly position: {size} cents (${size/100:.2f})")
    print(f"Reason: {reason}")
    print(f"Risk %: {size/bankroll:.2%}")

    # Test case: No edge
    size2, edge2, reason2 = kelly_criterion(0.52, 0.50, bankroll)
    print(f"\nNo edge case: size={size2}, reason={reason2}")
