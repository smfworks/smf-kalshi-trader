"""
Edge Detection — Trading signal generation.
OWNER: Gabriel (CFO) — financial logic and signal formulas.

This module contains the signal detection algorithms:
  1. EXTREME_VALUATION — extreme probability mispricing
  2. MOMENTUM           — price velocity over N periods
  3. MEAN_REVERSION     — z-score vs historical range
  4. EVENT_CORRELATION  — external event impact scoring
  5. ARBITRAGE          — cross-market price divergence

STUB: Basic extreme valuation + price inefficiency signals.
Gabriel will add momentum, mean reversion, event-driven, calibration.

Usage:
    from kalshi.edges import EdgeDetector
    ed = EdgeDetector()
    signals = ed.detect()
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

from kalshi.db import get_db_path


@dataclass
class EdgeSignal:
    """A detected trading opportunity."""
    ticker: str
    signal_type: str
    yes_implied_prob: float
    edge_score: float
    confidence: str
    suggestion: str
    detected_at: str


class EdgeDetector:
    """Detect mispricings using configured signal types."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()

    def _latest_prices(self) -> List[Dict]:
        """Get latest price snapshot for all active markets."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT m.ticker, m.title, m.category, p.yes_ask, p.yes_bid,
                   p.no_ask, p.no_bid, p.volume, p.open_interest
            FROM markets m
            JOIN (
                SELECT ticker, MAX(recorded_at) as max_date
                FROM price_history GROUP BY ticker
            ) latest ON m.ticker = latest.ticker
            JOIN price_history p
                ON m.ticker = p.ticker AND latest.max_date = p.recorded_at
            WHERE m.status = 'active'
              AND p.yes_ask IS NOT NULL
              AND p.no_ask IS NOT NULL
        """).fetchall()

        conn.close()
        return [dict(r) for r in rows]

    def _historical_prices(self, ticker: str, periods: int = 20) -> List[Dict]:
        """Get recent price history for a ticker (for momentum/mean reversion)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT yes_ask, yes_bid, volume, recorded_at FROM price_history
               WHERE ticker = ? ORDER BY recorded_at DESC LIMIT ?""",
            (ticker, periods),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ---------- Signal 1: Extreme Valuation ----------
    def _detect_extreme_valuation(self, markets: List[Dict]) -> List[EdgeSignal]:
        """Markets priced at extremes (>95% or <5%) — contrarian opportunities."""
        signals = []
        now = datetime.now().isoformat()

        for m in markets:
            yes_prob = m["yes_ask"] / 100.0

            if yes_prob > 0.95:
                signals.append(EdgeSignal(
                    ticker=m["ticker"],
                    signal_type="EXTREME_YES_OVERVALUED",
                    yes_implied_prob=yes_prob,
                    edge_score=yes_prob - 0.90,
                    confidence="high",
                    suggestion=f"NO at {m['no_ask']}¢ — market >95% confident, contrarian edge",
                    detected_at=now,
                ))
            elif yes_prob < 0.05:
                signals.append(EdgeSignal(
                    ticker=m["ticker"],
                    signal_type="EXTREME_YES_UNDERVALUED",
                    yes_implied_prob=yes_prob,
                    edge_score=0.10 - yes_prob,
                    confidence="high",
                    suggestion=f"YES at {m['yes_ask']}¢ — market <5% confident, potential value",
                    detected_at=now,
                ))

        return signals

    # ---------- Signal 2: Price Inefficiency / Arbitrage ----------
    def _detect_price_inefficiency(self, markets: List[Dict]) -> List[EdgeSignal]:
        """YES + NO > 105¢ indicates potential arbitrage or mispricing."""
        signals = []
        now = datetime.now().isoformat()

        for m in markets:
            total = m["yes_ask"] + m["no_ask"]
            if total > 105:
                signals.append(EdgeSignal(
                    ticker=m["ticker"],
                    signal_type="PRICE_INEFFICIENCY",
                    yes_implied_prob=m["yes_ask"] / 100.0,
                    edge_score=(total - 100) / 100.0,
                    confidence="medium",
                    suggestion=f"YES+NO={total}¢ > 100¢ — potential arbitrage or mispricing",
                    detected_at=now,
                ))

        return signals

    # ---------- Signal 3: Momentum (Gabriel's implementation) ----------
    def _detect_momentum(self, markets: List[Dict]) -> List[EdgeSignal]:
        """Detect price momentum: velocity over N periods with accelerating trend."""
        signals = []
        now = datetime.now().isoformat()

        for m in markets:
            history = self._historical_prices(m["ticker"], periods=8)
            if len(history) < 4:
                continue

            yes_prices = [h["yes_ask"] for h in history if h["yes_ask"] is not None]
            if len(yes_prices) < 4:
                continue

            # Calculate velocity (price change per period)
            recent = yes_prices[-4:]  # Last 4 periods
            velocities = [recent[i] - recent[i-1] for i in range(1, len(recent))]
            avg_velocity = sum(velocities) / len(velocities)

            # Minimum velocity threshold: 2 cents per period
            if abs(avg_velocity) < 2.0:
                continue

            # Determine direction
            side = "YES" if avg_velocity > 0 else "NO"
            opposing_price = m["no_ask"] if side == "YES" else m["yes_ask"]

            # Score: normalize velocity (max realistic: 10 cents per period)
            score = min(abs(avg_velocity) / 10.0, 1.0)

            # Confidence based on consistency
            if len(velocities) > 1:
                import statistics
                try:
                    stdev = statistics.stdev(velocities)
                    consistency = 1.0 - (stdev / (abs(avg_velocity) + 0.001))
                    confidence = max(0.3, min(consistency, 0.95))
                except:
                    confidence = 0.5
            else:
                confidence = 0.5

            # Map confidence to string
            conf_str = "high" if confidence > 0.7 else "medium" if confidence > 0.4 else "low"

            signals.append(EdgeSignal(
                ticker=m["ticker"],
                signal_type="MOMENTUM",
                yes_implied_prob=m["yes_ask"] / 100.0,
                edge_score=score,
                confidence=conf_str,
                suggestion=f"{side} momentum: {avg_velocity:.1f}¢/period over 4 periods — buy {side} at {opposing_price}¢",
                detected_at=now,
            ))

        return signals

    # ---------- Signal 4: Mean Reversion (Gabriel's implementation) ----------
    def _detect_mean_reversion(self, markets: List[Dict]) -> List[EdgeSignal]:
        """Detect prices deviating significantly from historical mean."""
        signals = []
        now = datetime.now().isoformat()

        for m in markets:
            history = self._historical_prices(m["ticker"], periods=20)
            if len(history) < 15:
                continue

            yes_prices = [h["yes_ask"] for h in history if h["yes_ask"] is not None]
            if len(yes_prices) < 15:
                continue

            import statistics
            try:
                mean_price = statistics.mean(yes_prices)
                stdev = statistics.stdev(yes_prices)
            except:
                continue

            if stdev == 0:
                continue

            current = yes_prices[0]  # Most recent (DESC order)
            z_score = (current - mean_price) / stdev

            # Threshold: |z| > 2.0
            if abs(z_score) < 2.0:
                continue

            # Direction: if price is high above mean, expect reversion DOWN (favor NO)
            # If price is far below mean, expect reversion UP (favor YES)
            if z_score > 0:
                side = "NO"
                score = min(z_score / 4.0, 1.0)
                opposing_price = m["no_ask"]
            else:
                side = "YES"
                score = min(abs(z_score) / 4.0, 1.0)
                opposing_price = m["yes_ask"]

            confidence = min(abs(z_score) / 3.0, 0.95)
            conf_str = "high" if confidence > 0.7 else "medium" if confidence > 0.4 else "low"

            signals.append(EdgeSignal(
                ticker=m["ticker"],
                signal_type="MEAN_REVERSION",
                yes_implied_prob=m["yes_ask"] / 100.0,
                edge_score=-score if z_score > 0 else score,
                confidence=conf_str,
                suggestion=f"Mean reversion: z={z_score:.2f} (mean={mean_price:.1f}¢, σ={stdev:.1f}¢) — buy {side} at {opposing_price}¢",
                detected_at=now,
            ))

        return signals

    # ---------- Signal 5: Event-Driven (STUB — Gabriel's math goes here) ----------
    def _detect_event_correlation(self, markets: List[Dict]) -> List[EdgeSignal]:
        """Correlate market prices with external events.

        STUB: Gabriel to implement:
        - Map economic/weather/financial markets to external data feeds
        - Compute correlation between event occurrence and price movement
        - Generate signals when correlation exceeds threshold
        """
        return []  # Gabriel: implement me

    # ---------- Orchestration ----------

    def detect(self, signal_types: Optional[List[str]] = None) -> List[EdgeSignal]:
        """Run all enabled signal detectors.

        Args:
            signal_types: Subset of signal types to run. None = all.
        """
        markets = self._latest_prices()
        if not markets:
            print("⚠️ No market data. Run scanner.sync_markets() first.")
            return []

        all_signals: List[EdgeSignal] = []

        detectors = {
            "EXTREME_VALUATION": self._detect_extreme_valuation,
            "PRICE_INEFFICIENCY": self._detect_price_inefficiency,
            "MOMENTUM": self._detect_momentum,
            "MEAN_REVERSION": self._detect_mean_reversion,
            "EVENT_CORRELATION": self._detect_event_correlation,
        }

        to_run = signal_types or list(detectors.keys())

        for sig_type in to_run:
            if sig_type in detectors:
                results = detectors[sig_type](markets)
                all_signals.extend(results)
                if results:
                    print(f"  {sig_type}: {len(results)} signals")

        # Store
        if all_signals:
            self._store_signals(all_signals)

        return all_signals

    def _store_signals(self, signals: List[EdgeSignal]):
        """Persist detected signals to database."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        for s in signals:
            c.execute(
                """INSERT INTO edge_signals
                   (ticker, signal_type, yes_implied_prob, edge_score, confidence,
                    suggestion, detected_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (s.ticker, s.signal_type, s.yes_implied_prob, s.edge_score,
                 s.confidence, s.suggestion, s.detected_at),
            )
        conn.commit()
        conn.close()
