# Kalshi Trading System

Automated market scanning, edge detection, and paper/live trading for [Kalshi](https://kalshi.com) prediction markets.

**Gabriel, CFO — SMF Works** (financial logic, strategy, edge detection)  
**Liam, CDO — SMF Works** (architecture, implementation, operations)

## Architecture

```
kalshi/
├── auth.py          # RSA key-pair authentication (Kalshi API v2)
├── db.py            # Single-source database schema (SQLite)
├── scanner.py       # Market sync + price history recording
├── edges.py         # Edge detection — 5 signal types
├── sizing.py        # Position sizing — Kelly Criterion
├── portfolio.py     # Paper + live trade management
└── reporter.py      # Daily intelligence reports

scripts/
├── daily_run.py     # Full pipeline CLI
└── daily_run.sh     # Cron entry point
```

## Quick Start

1. **Set up credentials:**
   ```bash
   cp kalshi.env.example kalshi.env
   # Add your KALSHI_API_KEY_ID
   # Place your RSA private key as kalshi_private.key
   ```

2. **Install dependencies:**
   ```bash
   pip install requests python-dotenv cryptography
   ```

3. **Run:**
   ```bash
   python3 scripts/daily_run.py --quick           # Scan + detect + report
   python3 scripts/daily_run.py --quick --trade --dry-run  # Preview trades
   ```

## Signal Types

| Signal | Description | Status |
|--------|-------------|--------|
| Extreme Valuation | YES >95% or <5% — contrarian | ✅ Live |
| Price Inefficiency | YES + NO > 105¢ — arbitrage | ✅ Live |
| Momentum | Price velocity over N periods | 🔧 In Progress |
| Mean Reversion | Z-score vs historical range | 🔧 In Progress |
| Event Correlation | External event impact | 🔧 In Progress |

## Trading Rules

- Capital: $50.00
- Max per trade: $1.00 (2%)
- Max open positions: 5
- Dark reserve: $40.00 (80%)
- All trades require signal verification (see `KALSHI_TRADING_RULES.md`)

## Cron Setup

```bash
# Every 4 hours: sync price history
0 */4 * * * cd ~/workspace && python3 scripts/daily_run.py --quick --scan-only

# Daily at 10am: full scan + edge detection + report
0 10 * * * cd ~/workspace && ./scripts/daily_run.sh --quick
```
