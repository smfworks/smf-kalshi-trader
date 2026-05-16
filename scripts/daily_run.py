#!/usr/bin/env python3
"""
Kalshi Daily Run — Scan, detect edges, report.
One script to rule them all.

Usage:
    python3 scripts/daily_run.py              # Full run: scan + detect + report
    python3 scripts/daily_run.py --quick      # Quick scan (200 markets)
    python3 scripts/daily_run.py --scan-only  # Only sync markets
    python3 scripts/daily_run.py --trade      # Scan + auto-trade (respects limits)
    python3 scripts/daily_run.py --trade --dry-run  # Scan + show what WOULD trade
"""

import sys
import argparse
from pathlib import Path

# Ensure workspace root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kalshi.db import init_db
from kalshi.scanner import Scanner
from kalshi.edges import EdgeDetector
from kalshi.sizing import PositionSizer
from kalshi.portfolio import Portfolio
from kalshi.reporter import Reporter

# Trading limits (from Gabriel's rules)
MAX_OPEN_POSITIONS = 5
MAX_PER_TRADE_CENTS = 100  # $1.00
MIN_EDGE_SCORE = 0.05
MAX_PRICE_CENTS = 10  # Only cheap contracts
MAX_DAILY_TRADES = 3
BANKROLL_CENTS = 5000  # $50.00


def main():
    parser = argparse.ArgumentParser(description="Kalshi Daily Trading Run")
    parser.add_argument("--quick", action="store_true", help="Quick scan (200 markets)")
    parser.add_argument("--scan-only", action="store_true", help="Only sync, no edge detection")
    parser.add_argument("--trade", action="store_true", help="Auto-trade based on signals")
    parser.add_argument("--dry-run", action="store_true", help="Show trades without placing them")
    parser.add_argument("--report-only", action="store_true", help="Only generate report from existing data")
    args = parser.parse_args()

    # Always init DB first
    init_db()

    if args.report_only:
        reporter = Reporter()
        report = reporter.generate()
        reporter.print(report)
        reporter.save(report)
        return

    # Step 1: Sync markets
    print("\n🔄 STEP 1: Market Sync")
    scanner = Scanner()
    count = scanner.sync_markets(quick=args.quick)
    if count == 0:
        print("❌ No markets synced. Aborting.")
        sys.exit(1)

    if args.scan_only:
        print("✓ Scan complete.")
        return

    # Step 2: Detect edges
    print("\n🔍 STEP 2: Edge Detection")
    detector = EdgeDetector()
    signals = detector.detect()

    if not signals:
        print("   No signals detected.")

    # Step 3: Optional auto-trading
    if args.trade:
        print("\n📈 STEP 3: Auto-Trading")

        portfolio = Portfolio()
        sizer = PositionSizer(bankroll_cents=BANKROLL_CENTS)

        # Check capacity
        open_count = portfolio.open_position_count
        if open_count >= MAX_OPEN_POSITIONS:
            print(f"⚠️ Max positions reached ({open_count}/{MAX_OPEN_POSITIONS}). Skipping trades.")
        else:
            # Sort signals by edge score
            scored_signals = sorted(signals, key=lambda s: s.edge_score, reverse=True)
            trades_placed = 0

            for sig in scored_signals:
                if trades_placed >= MAX_DAILY_TRADES:
                    break
                if portfolio.open_position_count + trades_placed >= MAX_OPEN_POSITIONS:
                    break

                # Filter: only cheap contracts, minimum edge, non-zero price
                price = int(sig.yes_implied_prob * 100)
                if price == 0 or price > MAX_PRICE_CENTS or sig.edge_score < MIN_EDGE_SCORE:
                    continue

                side = "yes" if sig.signal_type in ["EXTREME_YES_UNDERVALUED", "MOMENTUM_BULLISH"] else "no"

                # Size (using Kelly stub for now)
                contracts = sizer.kelly(
                    ticker=sig.ticker,
                    your_prob=sig.yes_implied_prob,
                    market_price_cents=price,
                    side=side,
                )

                result = portfolio.place_live_trade(
                    ticker=sig.ticker,
                    side=side,
                    price_cents=price,
                    contracts=contracts,
                    edge_score=sig.edge_score,
                    confidence=sig.confidence,
                    strategy=sig.signal_type,
                    notes=f"Auto: {sig.suggestion}",
                    dry_run=args.dry_run,
                )

                if result:
                    trades_placed += 1

            if trades_placed == 0:
                print("   No trades placed (no signals met criteria).")

    # Step 4: Generate report
    print("\n📄 STEP 4: Report")
    reporter = Reporter()
    report = reporter.generate()
    reporter.print(report)
    reporter.save(report)

    print("\n✓ Daily run complete.")


if __name__ == "__main__":
    main()
