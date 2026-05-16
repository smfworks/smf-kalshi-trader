"""
Kalshi Database Schema — Single source of truth for all tables.
Every module imports from here. No more scattered CREATE TABLE statements.

Usage:
    from kalshi.db import get_db_path, init_db
    init_db()
    conn = sqlite3.connect(get_db_path())
"""

import sqlite3
import os
from pathlib import Path

_DB_FILENAME = "kalshi_paper.db"


def get_db_path() -> str:
    """Absolute path to the SQLite database."""
    workspace = Path(__file__).resolve().parent.parent
    return str(workspace / _DB_FILENAME)


def get_reports_dir() -> str:
    """Absolute path to the reports directory."""
    workspace = Path(__file__).resolve().parent.parent
    reports = workspace / "reports"
    os.makedirs(reports, exist_ok=True)
    return str(reports)


def init_db(db_path: str | None = None) -> str:
    """Initialize all database tables. Idempotent — safe to call every run.

    Returns the db path used.
    """
    if db_path is None:
        db_path = get_db_path()

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # -- Markets: current state of all tracked markets --
    c.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            ticker TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            status TEXT,
            market_type TEXT DEFAULT 'binary',
            event_ticker TEXT,
            settlement_date TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # -- Price history: time-series of price snapshots --
    c.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            yes_ask INTEGER,
            yes_bid INTEGER,
            no_ask INTEGER,
            no_bid INTEGER,
            volume INTEGER,
            open_interest INTEGER,
            bid_ask_spread INTEGER,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (ticker) REFERENCES markets(ticker)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_ticker_time
        ON price_history(ticker, recorded_at)
    """)

    # -- Edge signals: detected trading opportunities --
    c.execute("""
        CREATE TABLE IF NOT EXISTS edge_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            yes_implied_prob REAL,
            edge_score REAL,
            confidence TEXT,
            suggestion TEXT,
            detected_at TEXT NOT NULL,
            acted_on INTEGER DEFAULT 0,
            FOREIGN KEY (ticker) REFERENCES markets(ticker)
        )
    """)

    # -- Signal calibration: tracking accuracy over time --
    c.execute("""
        CREATE TABLE IF NOT EXISTS signal_calibration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            total_signals INTEGER DEFAULT 0,
            correct_signals INTEGER DEFAULT 0,
            avg_edge_score REAL DEFAULT 0.0,
            calibration_error REAL DEFAULT 0.0,
            last_updated TEXT
        )
    """)

    # -- Paper trades: simulated positions --
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price INTEGER NOT NULL,
            contracts INTEGER NOT NULL,
            total_cost INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            exit_price INTEGER,
            exit_date TEXT,
            pnl INTEGER,
            notes TEXT,
            signal_id INTEGER,
            FOREIGN KEY (ticker) REFERENCES markets(ticker)
        )
    """)

    # -- Live trades: real Kalshi orders --
    c.execute("""
        CREATE TABLE IF NOT EXISTS live_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            ticker TEXT NOT NULL,
            side TEXT,
            action TEXT,
            entry_price_cents INTEGER,
            contracts INTEGER,
            total_cost_cents INTEGER,
            status TEXT DEFAULT 'open',
            opened_at TEXT,
            closed_at TEXT,
            exit_price_cents INTEGER,
            pnl_cents INTEGER,
            notes TEXT,
            edge_score REAL,
            confidence TEXT,
            strategy TEXT,
            FOREIGN KEY (ticker) REFERENCES markets(ticker)
        )
    """)

    # -- Daily performance snapshots --
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_performance (
            date TEXT PRIMARY KEY,
            total_markets INTEGER,
            signals_generated INTEGER,
            trades_opened INTEGER,
            trades_closed INTEGER,
            daily_pnl_cents INTEGER DEFAULT 0,
            cumulative_pnl_cents INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0
        )
    """)

    conn.commit()
    conn.close()
    return db_path


def get_row_count(table: str, db_path: str | None = None) -> int:
    """Convenience: count rows in a table."""
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*) FROM {table}")
    count = c.fetchone()[0]
    conn.close()
    return count
