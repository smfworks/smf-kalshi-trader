"""
Kalshi Portfolio — Paper and live position management.
Handles trade recording, position tracking, P&L computation.

Gabriel owns the trade decision logic. This module handles the recording.

Usage:
    from kalshi.portfolio import Portfolio
    p = Portfolio()
    p.open_paper_trade("TICKER", "yes", 3, notes="Mean reversion signal")
    p.close_paper_trade(1, 85)
    print(p.summary())
"""

import sqlite3
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from kalshi.auth import KalshiAuth
from kalshi.db import get_db_path


class Portfolio:
    """Unified paper and live position tracker."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()
        self.auth = KalshiAuth()

    # -- Paper trades (simulated) --

    def open_paper_trade(
        self,
        ticker: str,
        side: str,
        contracts: int,
        notes: str = "",
        signal_id: Optional[int] = None,
    ) -> Optional[int]:
        """Record a paper trade at current market price.

        Returns trade ID or None if price unavailable.
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Get latest price
        c.execute(
            """SELECT yes_ask, no_ask FROM price_history
               WHERE ticker = ? ORDER BY recorded_at DESC LIMIT 1""",
            (ticker,),
        )
        row = c.fetchone()
        if not row or (side == "yes" and row[0] is None) or (side == "no" and row[1] is None):
            conn.close()
            print(f"❌ No price data for {ticker}")
            return None

        entry_price = row[0] if side == "yes" else row[1]
        total_cost = entry_price * contracts
        now = datetime.now().isoformat()

        c.execute(
            """INSERT INTO paper_trades
               (ticker, side, entry_price, contracts, total_cost, trade_date, notes, signal_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, side, entry_price, contracts, total_cost, now, notes, signal_id),
        )
        trade_id = c.lastrowid
        conn.commit()
        conn.close()

        print(f"✓ Paper: {side.upper()} {contracts}× {ticker[:40]} @ {entry_price}¢ = ${total_cost/100:.2f}")
        return trade_id

    def close_paper_trade(self, trade_id: int, exit_price: int) -> Optional[int]:
        """Close a paper trade. Returns P&L in cents or None."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute(
            "SELECT side, entry_price, contracts FROM paper_trades WHERE id = ? AND status = 'open'",
            (trade_id,),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            print(f"❌ Trade {trade_id} not found or already closed")
            return None

        side, entry_price, contracts = row
        pnl = (exit_price - entry_price) * contracts
        now = datetime.now().isoformat()

        c.execute(
            """UPDATE paper_trades
               SET status='closed', exit_price=?, exit_date=?, pnl=?
               WHERE id=?""",
            (exit_price, now, pnl, trade_id),
        )
        conn.commit()
        conn.close()

        print(f"✓ Closed trade {trade_id}: P&L ${pnl/100:+.2f}")
        return pnl

    # -- Live trades (real Kalshi orders) --

    def place_live_trade(
        self,
        ticker: str,
        side: str,
        price_cents: int,
        contracts: int,
        edge_score: float = 0.0,
        confidence: str = "medium",
        strategy: str = "manual",
        notes: str = "",
        dry_run: bool = False,
    ) -> Optional[Dict]:
        """Place a real trade on Kalshi and record it locally.

        Returns order info dict or None on failure.
        """
        if dry_run:
            print(f"[DRY RUN] {side.upper()} {contracts}× {ticker} @ {price_cents}¢")
            return {"order_id": "dry-run", "status": "dry_run"}

        result = self.auth.place_order(
            ticker=ticker,
            side=side,
            action="buy",
            count=contracts,
            price_cents=price_cents,
            client_order_id=f"gabriel-{uuid.uuid4().hex[:8]}",
        )

        if result["status_code"] not in (200, 201):
            print(f"❌ Order failed: {result.get('response', '')}")
            return None

        order_id = result["order_id"]
        total_cost = price_cents * contracts
        now = datetime.now().isoformat()

        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            """INSERT INTO live_trades
               (order_id, ticker, side, action, entry_price_cents, contracts,
                total_cost_cents, opened_at, notes, edge_score, confidence, strategy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, ticker, side, "buy", price_cents, contracts,
             total_cost, now, notes, edge_score, confidence, strategy),
        )
        conn.commit()
        conn.close()

        print(f"✓ Live: {side.upper()} {contracts}× {ticker} @ {price_cents}¢ = ${total_cost/100:.2f}")
        return result

    def close_live_trade(self, order_id: str, exit_price_cents: int) -> Optional[int]:
        """Close a live trade in the database (assumes already sold on exchange)."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute(
            "SELECT entry_price_cents, contracts FROM live_trades WHERE order_id = ? AND status = 'open'",
            (order_id,),
        )
        row = c.fetchone()
        if not row:
            conn.close()
            return None

        entry_price, contracts = row
        pnl = (exit_price_cents - entry_price) * contracts
        now = datetime.now().isoformat()

        c.execute(
            """UPDATE live_trades
               SET status='closed', exit_price_cents=?, closed_at=?, pnl_cents=?
               WHERE order_id=?""",
            (exit_price_cents, now, pnl, order_id),
        )
        conn.commit()
        conn.close()
        return pnl

    # -- Queries --

    def get_open_paper_trades(self) -> List[Dict]:
        """List open paper positions."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY trade_date"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_open_live_trades(self) -> List[Dict]:
        """List open live positions."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM live_trades WHERE status = 'open' ORDER BY opened_at"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def summary(self) -> Dict:
        """Portfolio summary: open positions, deployed capital, P&L, win rate."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Paper
        c.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_cost), 0) FROM paper_trades WHERE status = 'open'"
        )
        paper_open, paper_cost = c.fetchone()

        c.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl), 0) FROM paper_trades WHERE status = 'closed'"
        )
        paper_closed, paper_pnl = c.fetchone()

        c.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE status = 'closed' AND pnl > 0"
        )
        paper_wins = c.fetchone()[0] or 0
        paper_win_rate = (paper_wins / paper_closed * 100) if paper_closed else 0

        # Live
        c.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_cost_cents), 0) FROM live_trades WHERE status = 'open'"
        )
        live_open, live_cost = c.fetchone()

        c.execute(
            "SELECT COUNT(*), COALESCE(SUM(pnl_cents), 0) FROM live_trades WHERE status = 'closed'"
        )
        live_closed, live_pnl = c.fetchone()

        c.execute(
            "SELECT COUNT(*) FROM live_trades WHERE status = 'closed' AND pnl_cents > 0"
        )
        live_wins = c.fetchone()[0] or 0
        live_win_rate = (live_wins / live_closed * 100) if live_closed else 0

        conn.close()

        return {
            "paper": {
                "open": paper_open or 0,
                "deployed": f"${(paper_cost or 0) / 100:.2f}",
                "closed": paper_closed or 0,
                "pnl": f"${(paper_pnl or 0) / 100:+.2f}",
                "win_rate": f"{paper_win_rate:.1f}%",
            },
            "live": {
                "open": live_open or 0,
                "deployed": f"${(live_cost or 0) / 100:.2f}",
                "closed": live_closed or 0,
                "pnl": f"${(live_pnl or 0) / 100:+.2f}",
                "win_rate": f"{live_win_rate:.1f}%",
            },
        }

    @property
    def open_position_count(self) -> int:
        s = self.summary()
        return s["live"]["open"] + s["paper"]["open"]
