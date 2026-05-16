# Kalshi Trading Rules — Gabriel, CFO
**Established:** 2026-05-14 21:44 ET
**By:** Michael (Owner)
**Capital:** $50.00
**Status:** ACTIVE

---

## Rule 1: No Trade Without Signal Verification

Before any live order is placed, it MUST be cross-referenced against the signal system output.

**Required check:**
```
python3 kalshi_paper_portfolio.py --quick
```

The trade must correspond to one of the following signal types:
- **EXTREME_YES_OVERVALUED** — Market pricing YES >95% (contrarian NO bet)
- **EXTREME_YES_UNDERVALUED** — Market pricing YES <5% (value YES bet)
- **PRICE_INEFFICIENCY** — YES + NO prices sum to >105¢ (arbitrage)
- **WIDE_SPREAD** — Bid-ask spread >5¢ (liquidity play)

**If no signal exists for the market, the trade is DISQUALIFIED unless Rule 2 is satisfied.**

---

## Rule 2: Discretionary Trades Require Written Reasoning

If trading outside the signal system, a written justification must exist before order placement.

**Required fields:**
1. **Market:** What event are we trading?
2. **Edge:** What information or analysis gives us an advantage?
3. **Probability:** What do we believe the true probability is?
4. **Market Price:** What is the market pricing it at?
5. **Expected Value:** (True Prob × Payout) — Cost > 0?
6. **External Data:** What outside information supports this?
7. **Risk:** What could go wrong? How much do we lose?

**If any field is blank, the trade is DISQUALIFIED.**

---

## Rule 3: No Multi-Leg Parlays Without Explicit Risk Assessment

Multi-leg parlays (more than 2 conditions) require additional scrutiny.

**Required analysis:**
1. Correlation assessment — are the legs independent or correlated?
2. Compounding probability — P(A) × P(B) × P(C)... = true probability
3. Market pricing vs. calculated probability comparison
4. Maximum loss scenario — all legs fail

**If probability calculation shows negative expected value, the trade is DISQUALIFIED.**

---

## Rule 4: Position Sizing Limits

**Maximum risk per trade:** $1.00 (2% of $50)
**Maximum open positions:** 10 contracts total
**Maximum deployed capital:** $10.00 (20% of $50)
**Dark reserve:** $40.00 minimum (80% of $50)

**If a trade exceeds these limits, it is DISQUALIFIED regardless of edge.**

---

## Rule 5: Documentation Requirements

Every trade must be recorded in the database with:
1. Order ID from Kalshi API
2. Signal type that triggered it (if applicable)
3. Written reasoning (if discretionary)
4. Timestamp
5. Fill price and fill time (when filled)
6. P&L tracking (when resolved)

**If a trade is not in the database, it does not exist for reporting purposes.**

---

## Rule 6: Verification Protocol

Before placing ANY order, run this checklist:

```
[ ] Signal check: What does the scanner say about this market?
[ ] Price check: What is current bid/ask/spread?
[ ] Volume check: Is there actual trading activity?
[ ] Reasoning check: Is the justification documented?
[ ] Sizing check: Does this fit within $1.00 risk limit?
[ ] Reserve check: Will $40.00 remain after this trade?
[ ] Risk check: What is maximum loss and probability of loss?
[ ] External check: Is there relevant data from outside Kalshi?
```

**If any box is unchecked, the trade is DISQUALIFIED.**

---

## Violation Consequences

Any trade placed without satisfying Rules 1-6:
1. Is treated as unauthorized
2. Will be closed immediately if possible
3. Agent must explain the violation to Michael
4. Trading privileges may be suspended

---

## Current Position Status

| # | Market | Entry | Size | Cost | Current Price | P&L | Signal | Reasoning |
|---|--------|-------|------|------|---------------|-----|--------|-----------|
| 1 | UFC Cross | $0.32 | 1 | $0.32 | ~$0.02 | -$0.30 | NO SIGNAL | Poor — executed under pressure |
| 2-6 | UFC Cross | $0.01 | 5 | $0.05 | ~$0.02 | +$0.05 | NO SIGNAL | Poor — executed under pressure |

**Total deployed:** $0.37 / $50.00 (0.74%)
**Balance:** $49.63
**Status:** These positions are GRANDFATHERED but future trades must follow Rules 1-6.

---

*Established by Michael. Violation = unauthorized trade.*
*Gabriel, CFO — SMF Works*
