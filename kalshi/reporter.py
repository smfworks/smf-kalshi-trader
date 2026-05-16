"""
Kalshi Reporter — Generate daily intelligence reports.

Usage:
    from kalshi.reporter import Reporter
    r = Reporter()
    report = r.generate()
    path = r.save(report)
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from kalshi.db import get_db_path, get_reports_dir


class Reporter:
    """Generate structured reports from the trading database."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()
        self.reports_dir = get_reports_dir()

    def generate(self) -> str:
        """Generate a full daily intelligence report."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Market stats
        c.execute("SELECT COUNT(*) FROM markets WHERE status = 'active'")
        total = c.fetchone()[0]

        c.execute("SELECT category, COUNT(*) FROM markets WHERE status = 'active' GROUP BY category ORDER BY COUNT(*) DESC")
        cats = c.fetchall()

        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("SELECT COUNT(*) FROM price_history WHERE recorded_at LIKE ?", (f"{today}%",))
        snapshots = c.fetchone()[0]

        # Signals
        c.execute("SELECT COUNT(*) FROM edge_signals WHERE detected_at LIKE ?", (f"{today}%",))
        signals_today = c.fetchone()[0]

        c.execute("SELECT signal_type, COUNT(*) FROM edge_signals WHERE detected_at LIKE ? GROUP BY signal_type", (f"{today}%",))
        sig_types = c.fetchall()

        c.execute("""
            SELECT ticker, signal_type, ROUND(yes_implied_prob*100, 1) as prob_pct,
                   ROUND(edge_score, 3), confidence, suggestion
            FROM edge_signals
            WHERE detected_at LIKE ? AND confidence = 'high'
            ORDER BY edge_score DESC LIMIT 10
        """, (f"{today}%",))
        top_signals = c.fetchall()

        # Paper portfolio
        c.execute("SELECT COUNT(*), COALESCE(SUM(total_cost), 0) FROM paper_trades WHERE status = 'open'")
        paper_open, paper_cost = c.fetchone()
        c.execute("SELECT COUNT(*), COALESCE(SUM(pnl), 0) FROM paper_trades WHERE status = 'closed'")
        paper_closed, paper_pnl = c.fetchone()
        c.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'closed' AND pnl > 0")
        paper_wins = c.fetchone()[0] or 0
        paper_wr = (paper_wins / paper_closed * 100) if paper_closed else 0

        # Live portfolio
        c.execute("SELECT COUNT(*), COALESCE(SUM(total_cost_cents), 0) FROM live_trades WHERE status = 'open'")
        live_open, live_cost = c.fetchone()
        c.execute("SELECT COUNT(*), COALESCE(SUM(pnl_cents), 0) FROM live_trades WHERE status = 'closed'")
        live_closed, live_pnl = c.fetchone()
        c.execute("SELECT COUNT(*) FROM live_trades WHERE status = 'closed' AND pnl_cents > 0")
        live_wins = c.fetchone()[0] or 0
        live_wr = (live_wins / live_closed * 100) if live_closed else 0

        conn.close()

        # Build report
        lines = [
            "=" * 70,
            "KALSHI TRADING SYSTEM — DAILY INTELLIGENCE REPORT",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M ET')}",
            f"Gabriel, CFO — SMF Works",
            "=" * 70,
            "",
            "📊 MARKET OVERVIEW",
            f"   Active markets tracked: {total:,}",
            f"   Price snapshots today:  {snapshots:,}",
            f"   Signals generated:      {signals_today}",
            "",
            "📈 MARKETS BY CATEGORY",
        ]

        for cat, count in sorted(cats, key=lambda x: x[1], reverse=True):
            lines.append(f"   {cat:20s}: {count:5d} markets")

        lines += [
            "",
            "💰 PAPER PORTFOLIO",
            f"   Open positions:    {paper_open or 0}",
            f"   Capital deployed:  ${(paper_cost or 0) / 100:.2f}",
            f"   Closed trades:     {paper_closed or 0}",
            f"   Realized P&L:      ${(paper_pnl or 0) / 100:+.2f}",
            f"   Win rate:          {paper_wr:.1f}%",
            "",
            "💵 LIVE PORTFOLIO",
            f"   Open positions:    {live_open or 0}",
            f"   Capital deployed:  ${(live_cost or 0) / 100:.2f}",
            f"   Closed trades:     {live_closed or 0}",
            f"   Realized P&L:      ${(live_pnl or 0) / 100:+.2f}",
            f"   Win rate:          {live_wr:.1f}%",
        ]

        if sig_types:
            lines += ["", "🎯 SIGNALS BY TYPE"]
            for st, count in sig_types:
                lines.append(f"   {st:30s}: {count:3d}")

        if top_signals:
            lines += ["", "🔥 TOP HIGH-CONFIDENCE SIGNALS"]
            for t, st, prob, edge, conf, suggestion in top_signals:
                lines.append(f"   [{t[:45]:45s}]")
                lines.append(f"   {st:25s} | {prob}% | edge={edge} | {conf}")
                lines.append(f"   → {suggestion}")
                lines.append("")

        lines += ["", "=" * 70]
        return "\n".join(lines)

    def save(self, report: str, prefix: str = "kalshi_report") -> str:
        """Save report to file. Returns path."""
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        path = Path(self.reports_dir) / f"{prefix}_{ts}.txt"
        path.write_text(report)
        print(f"✓ Report saved: {path}")
        return str(path)

    def print(self, report: str):
        """Print report to stdout."""
        print(report)
