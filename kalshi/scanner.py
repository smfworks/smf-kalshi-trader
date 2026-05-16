"""
Kalshi Market Scanner — Sync markets and record price history.
Single implementation. All other modules depend on this one.

Usage:
    from kalshi.scanner import Scanner
    s = Scanner()
    s.sync_markets()          # Full sync
    s.sync_markets(quick=True)  # Fast: 200 markets
"""

import sqlite3
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from kalshi.auth import KalshiAuth
from kalshi.db import get_db_path


def _to_cents(val) -> Optional[int]:
    """Convert Kalshi dollar-decimal price to cents. None → None."""
    if val is None:
        return None
    if isinstance(val, str):
        val = float(val)
    return int(round(val * 100))


def _fp_to_int(val) -> int:
    """Convert Kalshi fixed-point value to int. None → 0."""
    if val is None:
        return 0
    if isinstance(val, str):
        return int(float(val))
    return int(val)


def categorize(ticker: str, title: str) -> str:
    """Categorize market by ticker/title patterns."""
    t = ticker.upper()
    tl = title.lower()

    if any(x in t for x in ["FED", "CPI", "GDP", "NFP", "UNEMP", "JOBS", "INFLATION", "RATE"]):
        return "economic"
    if any(x in t for x in ["WEATHER", "TEMP", "RAIN", "SNOW", "HURRICANE", "TORNADO", "STORM"]):
        return "weather"
    if any(x in tl for x in ["temperature", "rainfall", "snowfall", "hurricane", "tornado", "weather"]) and "player" not in tl:
        return "weather"
    if any(x in tl for x in ["stock", "bitcoin", "crypto", "s&p", "nasdaq", "oil", "gold", "etf"]):
        return "financial"
    if any(x in t for x in ["NBA", "NFL", "MLB", "NHL", "ESPORTS", "GOLF", "TENNIS", "SOCCER", "UFC"]):
        return "sports"
    if "MULTIGAME" in t or "CROSSCATEGORY" in t:
        return "sports_multi"
    return "other"


class Scanner:
    """Market scanner: sync from Kalshi API → local SQLite."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()
        self.auth = KalshiAuth()

    def sync_markets(self, quick: bool = False, max_pages: int = 15) -> int:
        """Sync active markets from Kalshi to local database.

        Args:
            quick: If True, only fetch ~200 markets for speed.
            max_pages: Max pages to fetch (15 * 100 = 1500 markets default).

        Returns:
            Number of active markets stored.
        """
        print("📡 Syncing Kalshi markets...")

        all_markets: List[Dict] = []
        cursor = ""
        page = 0
        pages = 2 if quick else max_pages

        while page < pages:
            markets, cursor = self.auth.get_markets(limit=100, cursor=cursor)
            if not markets:
                break
            all_markets.extend(markets)
            page += 1
            print(f"  Page {page}: +{len(markets)} markets (total: {len(all_markets)})")
            if not cursor:
                break
            time.sleep(0.3)

        if not all_markets:
            print("❌ No markets fetched.")
            return 0

        # Store
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        now = datetime.now().isoformat()
        stored = 0

        for m in all_markets:
            ticker = m.get("ticker", "")
            title = m.get("title", "")
            status = m.get("status", "")

            if status != "active":
                continue

            cat = categorize(ticker, title)
            yes_ask = _to_cents(m.get("yes_ask_dollars"))
            yes_bid = _to_cents(m.get("yes_bid_dollars"))
            no_ask = _to_cents(m.get("no_ask_dollars"))
            no_bid = _to_cents(m.get("no_bid_dollars"))
            volume = _fp_to_int(m.get("volume_fp"))
            oi = _fp_to_int(m.get("open_interest_fp"))
            spread = (yes_ask - yes_bid) if (yes_ask is not None and yes_bid is not None) else None

            # Upsert market
            c.execute(
                """INSERT INTO markets (ticker, title, category, status, market_type,
                   event_ticker, settlement_date, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                   title=excluded.title, category=excluded.category,
                   status=excluded.status, updated_at=excluded.updated_at""",
                (ticker, title, cat, status, "binary",
                 m.get("event_ticker"), m.get("settlement_date"), now, now),
            )

            # Record price snapshot
            c.execute(
                """INSERT INTO price_history
                   (ticker, yes_ask, yes_bid, no_ask, no_bid, volume,
                    open_interest, bid_ask_spread, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticker, yes_ask, yes_bid, no_ask, no_bid, volume, oi, spread, now),
            )
            stored += 1

        conn.commit()
        conn.close()

        print(f"✓ Stored {stored} active markets with price snapshots")
        return stored
