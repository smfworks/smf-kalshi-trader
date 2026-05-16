# Kalshi API Authentication — Setup Guide
**Gabriel, CFO — SMF Works**
**Date:** 2026-05-12
**Status:** CRITICAL — Blocks all live trading

---

## The Problem

My authentication test was **completely wrong**. I used `Authorization: Bearer <token>` which is how most APIs work. Kalshi does **not** use Bearer tokens. It uses **RSA key-pair signing** on every single request.

**Why some endpoints worked:** `/markets` and `/exchange/status` are **public** — no auth needed. That's why I got 200 OK and thought auth was fine.

**Why trading endpoints failed:** `/portfolio/balance`, `/portfolio/orders`, and order creation **require** proper RSA-signed headers. My Bearer header was meaningless, so they returned 401.

---

## How Kalshi Authentication Actually Works

Every request needs THREE headers:

| Header | What It Is | Example |
|--------|-----------|---------|
| `KALSHI-ACCESS-KEY` | Your API Key ID (UUID) | `a952bcbe-ec3b-4b5b-b8f9-11dae589608c` |
| `KALSHI-ACCESS-TIMESTAMP` | Current time in milliseconds | `1703123456789` |
| `KALSHI-ACCESS-SIGNATURE` | RSA-PSS signature (base64) | `base64_encoded_signature` |

**The signature is created by signing:** `timestamp + HTTP_METHOD + path`

Example message to sign: `1703123456789GET/trade-api/v2/portfolio/balance`

Signed with your **private key** using RSA-PSS + SHA256. The private key is a `.key` file you download from Kalshi.

---

## Step-by-Step: What Michael Needs to Do

### Step 1: Generate a New API Key

1. Go to **https://kalshi.com** and log in
2. Navigate to **Account & Security → API Keys**
3. Click **Create Key**
4. You will see TWO things — save BOTH immediately:
   - **Private Key** — downloaded as a `.key` file (e.g., `kalshi-key-2.key`)
   - **API Key ID** — displayed on screen as a UUID string

**CRITICAL:** Kalshi does NOT store your private key. If you close the page without saving the `.key` file, it's gone forever. You'll need to delete the key and create a new one.

### Step 2: Store the Private Key Securely

Save the `.key` file to the workspace:

```bash
cp ~/Downloads/kalshi-key-*.key /home/mikesai1/.openclaw/agents/gabriel/workspace/kalshi_private.key
chmod 600 /home/mikesai1/.openclaw/agents/gabriel/workspace/kalshi_private.key
```

### Step 3: Add the Key ID to kalshi.env

Open `kalshi.env` and replace `your-api-key-id-here` with the actual UUID:

```
KALSHI_API_KEY_ID=a952bcbe-ec3b-4b5b-b8f9-11dae589608c
KALSHI_PRIVATE_KEY_PATH=kalshi_private.key
KALSHI_BASE_URL=https://external-api.kalshi.com/trade-api/v2
```

### Step 4: Test Authentication

Run the corrected test:

```bash
cd /home/mikesai1/.openclaw/agents/gabriel/workspace
python3 kalshi_auth_test.py
```

Expected output if everything works:
```
TEST 1: Public market data — ✓ PASS
TEST 2: Portfolio balance — ✓ PASS (Balance: $X.XX)
TEST 3: Portfolio positions — ✓ PASS
TEST 4: Exchange status — ✓ PASS
TEST 5: API tier limits — ✓ PASS
RESULTS: 5 passed, 0 failed
```

If Tests 2, 3, or 5 fail with 401, the key itself lacks trading permissions. See troubleshooting below.

---

## Troubleshooting

### 401 on trading endpoints but market data works

Your API Key ID is correct, but the KEY ITSELF doesn't have trading permissions. This is an account-level setting, not an API configuration issue.

**Solutions (in order):**

1. **Check API key permissions in Kalshi dashboard**
   - Account & Security → API Keys → look for "Trading: Enabled" or similar
   - Some keys are created as "read-only" by default

2. **Apply for Advanced API tier** (most likely needed)
   - Basic tier might have limited trading capabilities
   - Apply at: https://kalshi.typeform.com/advanced-api
   - This is a form that asks about your use case

3. **Contact Kalshi support**
   - support@kalshi.com or Discord #dev channel
   - Ask them to enable trading permissions on your API key
   - Mention you're a registered user building a trading application

### "Private key file not found"

The `.key` file isn't where `kalshi.env` says it is. Check the path and make sure the file was saved correctly.

### "Signature error" or similar

The timestamp might be wrong (needs to be milliseconds, not seconds). Or the path signing might be including query parameters (must strip `?` and everything after before signing). The corrected test script handles all of this.

---

## Important Technical Notes

### RSA Key Format

Kalshi generates keys in PEM format (starts with `-----BEGIN RSA PRIVATE KEY-----`). The Python `cryptography` library handles this natively.

### Timestamp Precision

Must be in **milliseconds** (not seconds, not microseconds). My script uses `int(datetime.now().timestamp() * 1000)` which is correct.

### Path Signing

The path signed must be the **full URL path from the API root**, without query parameters. For example:
- Request URL: `https://external-api.kalshi.com/trade-api/v2/portfolio/orders?limit=5`
- Path to sign: `/trade-api/v2/portfolio/orders`

My script uses `urlparse` to extract this correctly.

### Demo Environment

For testing without real money, use the demo environment:
- URL: `https://external-api.demo.kalshi.co/trade-api/v2`
- Web: `https://demo.kalshi.co`
- Requires a separate demo account with separate API keys

---

## Rate Limits (Critical for Bot Design)

Kalshi uses **token-based rate limiting** with separate Read and Write budgets:

| Tier | Read/sec | Write/sec | How to Qualify |
|------|----------|-----------|---------------|
| Basic | 200 | 100 | Complete account signup |
| Advanced | 300 | 300 | Apply at kalshi.typeform.com/advanced-api |
| Premier | 1,000 | 1,000 | Criteria TBD |
| Paragon | 2,000 | 2,000 | Criteria TBD |
| Prime | 4,000 | 4,000 | Criteria TBD |

**Write bucket holds 2 seconds of budget** (except Basic = 1 second). This means idle time builds up burst capacity — useful for reacting quickly to market moves.

**Default request cost:** 10 tokens per request. Batch requests cost per-item, not per-batch.

**429 response:** "too many requests" with no Retry-After header. Use exponential backoff.

---

## What Gabriel Needs After Auth Works

Once authentication is confirmed:
1. Update all scripts (`kalshi_scanner.py`, `kalshi_paper_portfolio.py`, `kalshi_paper_trader.py`) to use RSA signing
2. Transition from paper trades to live with $1-5 positions (Kelly Criterion sizing)
3. Monitor rate limits — current scanner is likely within Basic tier but order placement needs Write budget tracking

---

## Files Updated

| File | What Changed |
|------|-------------|
| `kalshi_auth_test.py` | Completely rewritten with correct RSA signing |
| `kalshi.env` | New format with API_KEY_ID + PRIVATE_KEY_PATH + BASE_URL |
| `KALSHI_AUTH_SETUP.md` | This document |

---

*Gabriel*
*CFO, SMF Works*
*2026-05-12*
