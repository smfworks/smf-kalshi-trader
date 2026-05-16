"""
Position Sizing — Kelly Criterion and risk management.
OWNER: Gabriel (CFO) — financial math and bankroll strategy.

STUB: Gabriel to implement full Kelly Criterion math.
The module is structured and ready for his formulas.

Usage:
    from kalshi.sizing import PositionSizer
    sizer = PositionSizer(bankroll_cents=5000)
    contracts = sizer.kelly(ticker, your_prob=0.35, market_price_cents=5)
"""

import sqlite3
from typing import Optional

from kalshi.db import get_db_path


class PositionSizer:
    """Calculate optimal position sizes based on bankroll and edge magnitude."""

    def __init__(self, bankroll_cents: int = 5000, kelly_fraction: float = 0.5):
        """
        Args:
            bankroll_cents: Total trading bankroll in cents ($50.00 = 5000)
            kelly_fraction: Fraction of full Kelly to use (0.5 = half Kelly, standard)
        """
        self.bankroll = bankroll_cents
        self.kelly_fraction = kelly_fraction

    # ---------- Kelly Criterion (Gabriel's implementation) ----------
    def kelly(
        self,
        ticker: str,
        your_prob: float,
        market_price_cents: int,
        side: str = "yes",
    ) -> Optional[int]:
        """Full Kelly Criterion position sizing with fractional Kelly.

        f* = (b * p - q) / b
        where:
            b = net odds = (100 - market_price) / market_price
            p = your estimated probability of winning
            q = 1 - p

        Then: fraction = f* * kelly_fraction (default 0.5 = half Kelly)
        Position size = bankroll * fraction

        Args:
            your_prob: Our estimated probability (0.0 to 1.0)
            market_price_cents: Market price in cents (0-100)
            side: 'yes' or 'no'

        Returns:
            Number of contracts to buy, or None if no trade
        """
        if not (0 < your_prob < 1):
            return None
        if not (0 < market_price_cents <= 100):
            return None
        if self.bankroll <= 0:
            return None

        # Calculate edge
        market_prob = market_price_cents / 100.0
        edge = your_prob - market_prob

        # Minimum edge filter (5%)
        min_edge = 0.05
        if abs(edge) < min_edge:
            return None

        # Calculate odds
        if market_price_cents <= 0:
            return None
        b = (100 - market_price_cents) / market_price_cents  # decimal odds
        p = your_prob
        q = 1 - p

        # Full Kelly fraction
        kelly_full = (b * p - q) / b

        # Negative Kelly = no trade
        if kelly_full <= 0:
            return None

        # Fractional Kelly
        kelly_fractional = kelly_full * self.kelly_fraction

        # Position size in cents
        position_size_cents = int(self.bankroll * kelly_fractional)

        # Max loss cap (2% of bankroll per trade)
        max_loss_cents = int(self.bankroll * 0.02)
        if position_size_cents > max_loss_cents:
            position_size_cents = max_loss_cents

        # Convert to contracts
        if market_price_cents <= 0:
            return None
        contracts = position_size_cents // market_price_cents

        # Minimum 1 contract
        if contracts < 1:
            return None

        return contracts

    def flat_risk(self, risk_per_trade_cents: int = 100) -> int:
        """Simple fixed-risk sizing. Ignores edge magnitude.
        Used as fallback when Kelly parameters unknown.

        Returns max contracts for the given risk budget.
        """
        return risk_per_trade_cents

    def kelly_pct(self, b: float, p: float) -> float:
        """Raw Kelly percentage: f* = (b*p - q)/b

        STUB: This is the textbook formula. Gabriel to validate/adjust.
        """
        q = 1.0 - p
        f_star = (b * p - q) / b
        if f_star <= 0:
            return 0.0
        return f_star * self.kelly_fraction

    def validate_limits(self, proposed_contracts: int, price_cents: int) -> tuple[bool, str]:
        """Check whether a proposed trade respects position limits.

        Returns (allowed, reason).
        """
        cost = proposed_contracts * price_cents

        if cost > self.bankroll * 0.02:
            return False, f"Trade cost ${cost/100:.2f} exceeds 2% max (${self.bankroll * 0.02 / 100:.2f})"

        if cost > self.bankroll * 0.20:
            return False, f"Total deployed would exceed 20% reserve requirement"

        if proposed_contracts < 1:
            return False, "Must be at least 1 contract"

        return True, "ok"
