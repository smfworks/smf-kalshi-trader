"""Edge Detection — Four Signal Types for Prediction Markets.

Implements:
1. Momentum: Price velocity over N periods
2. Mean Reversion: Z-score vs historical range
3. Event-Driven: Correlation with external events (Fed, weather, etc.)
4. Arbitrage: Cross-market price divergence

Each signal returns a standardized score (-1.0 to +1.0) and a confidence level.
Composite scoring weights signals by historical accuracy.

Author: Gabriel
"""

from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import statistics
import math


@dataclass
class Signal:
    """Standardized signal output."""
    signal_type: str
    market_id: str
    side: str  # "YES" or "NO"
    score: float  # -1.0 to +1.0, magnitude = strength
    confidence: float  # 0.0 to 1.0
    raw_value: float  # Original metric value
    description: str
    timestamp: datetime


class MomentumDetector:
    """
    Detect momentum signals: price moving with velocity.
    
    Logic: If price has been trending in one direction over N periods
    with accelerating velocity, expect continuation (momentum) or
    exhaustion (contrarian signal, depending on calibration).
    """
    
    def __init__(self, lookback_periods: int = 5, min_velocity: float = 0.02):
        self.lookback = lookback_periods
        self.min_velocity = min_velocity  # Min price change per period
    
    def detect(self, price_history: List[float], timestamps: List[datetime]) -> Optional[Signal]:
        """
        Args:
            price_history: List of YES prices in cents (0-100), oldest first
            timestamps: Corresponding timestamps
        
        Returns:
            Signal if momentum detected, None otherwise
        """
        if len(price_history) < self.lookback + 1:
            return None
        
        recent = price_history[-self.lookback:]
        
        # Calculate velocity (price change per period)
        velocities = [recent[i] - recent[i-1] for i in range(1, len(recent))]
        avg_velocity = sum(velocities) / len(velocities)
        
        # Check if velocity exceeds threshold
        if abs(avg_velocity) < self.min_velocity * 100:  # Convert to cents
            return None
        
        # Determine direction
        side = "YES" if avg_velocity > 0 else "NO"
        
        # Score: normalize velocity (0 to 1.0 scale)
        # Max realistic velocity: ~10 cents per period
        score = min(abs(avg_velocity) / 10.0, 1.0)
        if side == "NO":
            score = -score
        
        # Confidence based on consistency of velocity
        if len(velocities) > 1:
            velocity_consistency = 1.0 - (statistics.stdev(velocities) / (abs(avg_velocity) + 0.001))
            confidence = max(0.3, min(velocity_consistency, 0.95))
        else:
            confidence = 0.5
        
        return Signal(
            signal_type="MOMENTUM",
            market_id="",  # Set by caller
            side=side,
            score=score,
            confidence=confidence,
            raw_value=avg_velocity,
            description=f"{'Upward' if side == 'YES' else 'Downward'} momentum: {avg_velocity:.1f}¢/period over {self.lookback} periods",
            timestamp=timestamps[-1] if timestamps else datetime.now()
        )


class MeanReversionDetector:
    """
    Detect mean reversion: price has deviated from historical average.
    
    Logic: If price is >2 standard deviations from 20-period mean,
    expect reversion to mean. Z-score based.
    """
    
    def __init__(self, lookback_periods: int = 20, z_threshold: float = 2.0):
        self.lookback = lookback_periods
        self.z_threshold = z_threshold
    
    def detect(self, price_history: List[float], timestamps: List[datetime]) -> Optional[Signal]:
        if len(price_history) < self.lookback:
            return None
        
        historical = price_history[-self.lookback:]
        current = price_history[-1]
        
        mean = statistics.mean(historical)
        stdev = statistics.stdev(historical) if len(historical) > 1 else 1.0
        
        if stdev == 0:
            return None
        
        z_score = (current - mean) / stdev
        
        if abs(z_score) < self.z_threshold:
            return None
        
        # Direction: if price is high above mean, expect reversion DOWN (favor NO)
        # If price is far below mean, expect reversion UP (favor YES)
        if z_score > 0:
            side = "NO"  # Price too high, bet on reversion down
            score = -min(z_score / 4.0, 1.0)  # Cap at 1.0
        else:
            side = "YES"  # Price too low, bet on reversion up
            score = min(abs(z_score) / 4.0, 1.0)
        
        confidence = min(abs(z_score) / 3.0, 0.95)
        
        return Signal(
            signal_type="MEAN_REVERSION",
            market_id="",
            side=side,
            score=score,
            confidence=confidence,
            raw_value=z_score,
            description=f"Z-score: {z_score:.2f} (mean={mean:.1f}¢, σ={stdev:.1f}¢)",
            timestamp=timestamps[-1] if timestamps else datetime.now()
        )


class EventDrivenDetector:
    """
    Detect event-driven signals: price moves correlated with external events.
    
    Placeholder — requires external event feed (Fed announcements, weather, etc.)
    Framework ready for integration.
    """
    
    def __init__(self):
        self.event_calendar = {}  # {event_type: [event_dates]}
    
    def register_event(self, event_type: str, event_date: datetime, expected_impact: str):
        """Register a known event that may impact markets."""
        if event_type not in self.event_calendar:
            self.event_calendar[event_type] = []
        self.event_calendar[event_type].append({
            "date": event_date,
            "impact": expected_impact,
            "processed": False
        })
    
    def detect(self, market_id: str, price_history: List[float], 
               timestamps: List[datetime]) -> Optional[Signal]:
        """
        Check if recent price movement correlates with registered events.
        """
        # Placeholder: Look for price jumps within 24h of registered events
        if not self.event_calendar:
            return None
        
        now = timestamps[-1] if timestamps else datetime.now()
        recent_window = now - timedelta(hours=24)
        
        for event_type, events in self.event_calendar.items():
            for event in events:
                if not event["processed"] and recent_window <= event["date"] <= now:
                    # Mark as processed
                    event["processed"] = True
                    
                    # Analyze price change since event
                    if len(price_history) >= 2:
                        price_change = price_history[-1] - price_history[-2]
                        
                        return Signal(
                            signal_type="EVENT_DRIVEN",
                            market_id=market_id,
                            side="YES" if price_change > 0 else "NO",
                            score=min(abs(price_change) / 10.0, 1.0),
                            confidence=0.6,  # Events are noisy
                            raw_value=price_change,
                            description=f"Post-event movement after {event_type}: {price_change:.1f}¢",
                            timestamp=now
                        )
        
        return None


class ArbitrageDetector:
    """
    Detect arbitrage: price divergence between related markets.
    
    Examples:
    - YES + NO > 105¢ (risk-free arb on same market)
    - Correlated markets pricing same event differently
    """
    
    def __init__(self, arb_threshold: float = 5.0):
        self.threshold = arb_threshold  # Min cents of divergence
    
    def detect_same_market(self, yes_price: float, no_price: float, 
                          market_id: str, timestamp: datetime) -> Optional[Signal]:
        """
        Check if YES + NO > 100¢ + threshold (risk-free arb).
        """
        total = yes_price + no_price
        
        if total < 100.0 + self.threshold:
            return None
        
        # Sell the overpriced side
        if yes_price > no_price:
            side = "NO"  # YES is overpriced, buy NO
            overpriced = "YES"
        else:
            side = "YES"
            overpriced = "NO"
        
        profit = total - 100.0
        score = min(profit / 10.0, 1.0)
        
        return Signal(
            signal_type="ARBITRAGE",
            market_id=market_id,
            side=side,
            score=score,
            confidence=0.9,  # Arb is mathematically certain
            raw_value=profit,
            description=f"Same-market arb: {overpriced} overpriced by {profit:.1f}¢",
            timestamp=timestamp
        )
    
    def detect_cross_market(self, market_a: Dict, market_b: Dict) -> Optional[Signal]:
        """
        Detect arbitrage between correlated markets.
        Requires market definitions with correlation mapping.
        
        Placeholder — needs correlation database.
        """
        # TODO: Implement when correlation mapping exists
        return None


class CompositeScorer:
    """
    Combine multiple signals into a single trade decision.
    
    Weights signals by historical accuracy (from calibration module).
    """
    
    def __init__(self, signal_weights: Optional[Dict[str, float]] = None):
        self.weights = signal_weights or {
            "MOMENTUM": 0.20,
            "MEAN_REVERSION": 0.30,
            "EVENT_DRIVEN": 0.20,
            "ARBITRAGE": 0.30
        }
    
    def score(self, signals: List[Signal]) -> Tuple[float, float, str]:
        """
        Args:
            signals: List of detected signals
        
        Returns:
            (composite_score, confidence, reasoning)
            composite_score: -1.0 to +1.0 (negative = favor NO, positive = favor YES)
            confidence: 0.0 to 1.0
        """
        if not signals:
            return 0.0, 0.0, "no_signals"
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for signal in signals:
            weight = self.weights.get(signal.signal_type, 0.1)
            weighted_sum += signal.score * signal.confidence * weight
            total_weight += weight * signal.confidence
        
        if total_weight == 0:
            return 0.0, 0.0, "zero_weight"
        
        composite = weighted_sum / total_weight
        
        # Confidence: average of individual confidences, scaled by agreement
        avg_confidence = sum(s.confidence for s in signals) / len(signals)
        
        # Check for signal agreement (all pointing same direction)
        signs = [1 if s.score > 0 else -1 for s in signals if s.score != 0]
        if signs:
            agreement = abs(sum(signs)) / len(signs)  # 1.0 = perfect agreement
            confidence = avg_confidence * (0.5 + 0.5 * agreement)
        else:
            confidence = avg_confidence * 0.5
        
        reasoning = f"{len(signals)} signals: " + ", ".join(
            f"{s.signal_type}({s.side}, score={s.score:.2f})" for s in signals
        )
        
        return composite, confidence, reasoning


# --- Example / Test ---
if __name__ == "__main__":
    # Test momentum
    prices = [50, 52, 55, 58, 62, 65]  # Upward trend
    timestamps = [datetime.now() - timedelta(hours=i) for i in range(len(prices))]
    
    mom = MomentumDetector(lookback_periods=3)
    signal = mom.detect(prices, timestamps)
    if signal:
        print(f"Momentum: {signal.side}, score={signal.score:.2f}, conf={signal.confidence:.2f}")
    
    # Test mean reversion
    prices2 = [50, 51, 49, 50, 52, 48, 47, 80]  # Spike at end
    mr = MeanReversionDetector(lookback_periods=7)
    signal2 = mr.detect(prices2, timestamps)
    if signal2:
        print(f"Mean Reversion: {signal2.side}, score={signal2.score:.2f}, conf={signal2.confidence:.2f}")
    
    # Test arbitrage
    arb = ArbitrageDetector()
    signal3 = arb.detect_same_market(55, 52, "TEST-001", datetime.now())
    if signal3:
        print(f"Arbitrage: {signal3.side}, score={signal3.score:.2f}, profit={signal3.raw_value:.1f}¢")
    
    # Test composite
    signals = [signal, signal2, signal3] if all([signal, signal2, signal3]) else []
    if signals:
        scorer = CompositeScorer()
        comp, conf, reason = scorer.score(signals)
        print(f"Composite: score={comp:.2f}, conf={conf:.2f}")
