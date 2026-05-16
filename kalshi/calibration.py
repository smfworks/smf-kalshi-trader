"""Calibration Framework — Track Historical Accuracy and Refine Edge Scores.

The raw edge score (our_probability - market_probability) is not enough.
We need to calibrate: when we say "8% edge," how often are we actually right?

Implements:
- Brier score tracking (probability calibration)
- Signal-specific accuracy by type
- Bankroll tracking and reporting
- Performance attribution

Author: Gabriel
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import math
import json


@dataclass
class TradeOutcome:
    """Record of a resolved trade for calibration."""
    trade_id: str
    market_id: str
    side: str  # "YES" or "NO"
    entry_price: float  # cents
    predicted_probability: float  # Our estimate
    market_probability_at_entry: float  # Market price as prob
    edge_at_entry: float
    signal_types: List[str]  # Which signals triggered
    position_size: float  # cents
    outcome: bool  # True = won, False = lost
    resolved_price: float  # Final settlement (100 or 0)
    pnl: float  # Profit/loss in cents
    timestamp: datetime


class BrierTracker:
    """
    Track Brier scores for probability calibration.
    
    Brier score = (predicted_prob - actual_outcome)^2
    Lower is better. 0 = perfect, 0.25 = random, 0 = wrong certainty.
    """
    
    def __init__(self):
        self.scores: List[Tuple[float, bool]] = []  # (predicted_prob, outcome)
    
    def record(self, predicted_probability: float, outcome: bool):
        """Record a prediction and its outcome."""
        self.scores.append((predicted_probability, outcome))
    
    def brier_score(self) -> float:
        """Calculate mean Brier score across all predictions."""
        if not self.scores:
            return 0.0
        
        total = 0.0
        for pred, outcome in self.scores:
            actual = 1.0 if outcome else 0.0
            total += (pred - actual) ** 2
        
        return total / len(self.scores)
    
    def calibration_by_bin(self, n_bins: int = 5) -> Dict[float, float]:
        """
        Check calibration by probability bins.
        
        For each bin (e.g., 50-60%, 60-70%), what % of predictions actually resolved Yes?
        If we're calibrated, a 70% prediction should resolve Yes ~70% of the time.
        
        Returns:
            {bin_midpoint: actual_yes_rate}
        """
        bins: Dict[float, List[bool]] = {}
        
        for pred, outcome in self.scores:
            bin_idx = min(int(pred * n_bins), n_bins - 1)
            midpoint = (bin_idx + 0.5) / n_bins
            
            if midpoint not in bins:
                bins[midpoint] = []
            bins[midpoint].append(outcome)
        
        return {
            midpoint: sum(outcomes) / len(outcomes)
            for midpoint, outcomes in bins.items()
            if outcomes
        }


class SignalAccuracyTracker:
    """
    Track accuracy by signal type to weight composite scores.
    
    If MOMENTUM signals are right 65% of the time but MEAN_REVERSION
    is right 80%, we should weight MEAN_REVERSION higher.
    """
    
    def __init__(self):
        self.records: Dict[str, List[Tuple[float, bool]]] = {}  # signal_type: [(edge, won)]
    
    def record(self, signal_type: str, edge: float, won: bool):
        if signal_type not in self.records:
            self.records[signal_type] = []
        self.records[signal_type].append((edge, won))
    
    def accuracy(self, signal_type: str) -> float:
        """Return win rate for a signal type."""
        if signal_type not in self.records or not self.records[signal_type]:
            return 0.5  # Default: no data = coin flip
        
        wins = sum(1 for _, won in self.records[signal_type] if won)
        return wins / len(self.records[signal_type])
    
    def edge_accuracy(self, signal_type: str, min_edge: float = 0.0) -> float:
        """Accuracy for trades above a minimum edge threshold."""
        if signal_type not in self.records:
            return 0.5
        
        filtered = [won for edge, won in self.records[signal_type] if abs(edge) >= min_edge]
        if not filtered:
            return 0.5
        
        return sum(filtered) / len(filtered)
    
    def optimal_weights(self) -> Dict[str, float]:
        """
        Calculate optimal signal weights based on historical accuracy.
        
        Returns weights that sum to 1.0, proportional to accuracy.
        """
        accuracies = {
            signal: self.accuracy(signal)
            for signal in self.records.keys()
        }
        
        # Add default signals if not yet tracked
        for signal in ["MOMENTUM", "MEAN_REVERSION", "EVENT_DRIVEN", "ARBITRAGE"]:
            if signal not in accuracies:
                accuracies[signal] = 0.5
        
        total = sum(accuracies.values())
        if total == 0:
            return {s: 0.25 for s in accuracies.keys()}
        
        return {signal: acc / total for signal, acc in accuracies.items()}
    
    def report(self) -> str:
        """Generate accuracy report."""
        lines = ["=== Signal Accuracy Report ==="]
        
        for signal_type in sorted(self.records.keys()):
            trades = self.records[signal_type]
            wins = sum(1 for _, won in trades if won)
            acc = wins / len(trades) if trades else 0.0
            avg_edge = sum(e for e, _ in trades) / len(trades) if trades else 0.0
            
            lines.append(f"{signal_type}: {acc:.1%} win rate ({wins}/{len(trades)} trades, avg edge {avg_edge:.1%})")
        
        lines.append(f"\nOptimal weights: {self.optimal_weights()}")
        return "\n".join(lines)


class BankrollTracker:
    """
    Track bankroll over time with full attribution.
    """
    
    def __init__(self, initial_bankroll: float = 10000.0):
        self.initial = initial_bankroll
        self.current = initial_bankroll
        self.history: List[Tuple[datetime, float, str]] = []  # (timestamp, bankroll, reason)
        self.trades: List[TradeOutcome] = []
    
    def record_trade(self, outcome: TradeOutcome):
        """Record a completed trade and update bankroll."""
        self.current += outcome.pnl
        self.trades.append(outcome)
        self.history.append((outcome.timestamp, self.current, f"Trade {outcome.trade_id}: {outcome.pnl:+.0f}¢"))
    
    def record_fee(self, amount: float, timestamp: datetime, reason: str = "fee"):
        """Record a fee or other non-trade adjustment."""
        self.current -= amount
        self.history.append((timestamp, self.current, f"{reason}: -{amount:.0f}¢"))
    
    def total_pnl(self) -> float:
        """Total profit/loss in cents."""
        return self.current - self.initial
    
    def roi(self) -> float:
        """Return on investment."""
        if self.initial == 0:
            return 0.0
        return (self.current - self.initial) / self.initial
    
    def sharpe_proxy(self) -> float:
        """
        Simple Sharpe-like ratio: mean daily return / std dev.
        Uses daily snapshots from history.
        """
        if len(self.history) < 2:
            return 0.0
        
        daily_returns = []
        for i in range(1, len(self.history)):
            prev = self.history[i-1][1]
            curr = self.history[i][1]
            if prev > 0:
                daily_returns.append((curr - prev) / prev)
        
        if not daily_returns:
            return 0.0
        
        mean_return = sum(daily_returns) / len(daily_returns)
        
        # Population std dev
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
        std_dev = variance ** 0.5
        
        if std_dev == 0:
            return float('inf') if mean_return > 0 else 0.0
        
        return mean_return / std_dev
    
    def report(self) -> str:
        """Generate bankroll report."""
        pnl = self.total_pnl()
        roi = self.roi()
        sharpe = self.sharpe_proxy()
        
        lines = [
            "=== Bankroll Report ===",
            f"Initial: {self.initial:.0f}¢ (${self.initial/100:.2f})",
            f"Current: {self.current:.0f}¢ (${self.current/100:.2f})",
            f"PnL: {pnl:+.0f}¢ (${pnl/100:+.2f})",
            f"ROI: {roi:+.2%}",
            f"Sharpe proxy: {sharpe:.2f}",
            f"Total trades: {len(self.trades)}",
            f"Win rate: {self._win_rate():.1%}" if self.trades else "Win rate: N/A",
            ""
        ]
        
        # Recent history (last 10)
        for ts, bal, reason in self.history[-10:]:
            lines.append(f"{ts.strftime('%Y-%m-%d %H:%M')} | {bal:.0f}¢ | {reason}")
        
        return "\n".join(lines)
    
    def _win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.outcome)
        return wins / len(self.trades)


# --- Example / Test ---
if __name__ == "__main__":
    # Test Brier tracking
    brier = BrierTracker()
    brier.record(0.70, True)
    brier.record(0.30, False)
    brier.record(0.80, True)
    brier.record(0.60, False)
    
    print(f"Brier score: {brier.brier_score():.4f}")
    print(f"Calibration: {brier.calibration_by_bin()}")
    
    # Test signal accuracy
    tracker = SignalAccuracyTracker()
    tracker.record("MOMENTUM", 0.08, True)
    tracker.record("MOMENTUM", 0.05, False)
    tracker.record("MOMENTUM", 0.12, True)
    tracker.record("MEAN_REVERSION", 0.10, True)
    tracker.record("MEAN_REVERSION", 0.15, True)
    
    print(f"\n{tracker.report()}")
    
    # Test bankroll
    bankroll = BankrollTracker(initial_bankroll=10000)
    
    from datetime import timedelta
    now = datetime.now()
    
    trade1 = TradeOutcome(
        trade_id="T001", market_id="M001", side="YES",
        entry_price=55, predicted_probability=0.70,
        market_probability_at_entry=0.55, edge_at_entry=0.15,
        signal_types=["MOMENTUM", "ARBITRAGE"],
        position_size=200, outcome=True, resolved_price=100,
        pnl=82, timestamp=now
    )
    
    trade2 = TradeOutcome(
        trade_id="T002", market_id="M002", side="NO",
        entry_price=60, predicted_probability=0.60,
        market_probability_at_entry=0.60, edge_at_entry=0.00,
        signal_types=["MEAN_REVERSION"],
        position_size=100, outcome=False, resolved_price=100,
        pnl=-100, timestamp=now + timedelta(hours=1)
    )
    
    bankroll.record_trade(trade1)
    bankroll.record_trade(trade2)
    
    print(f"\n{bankroll.report()}")
