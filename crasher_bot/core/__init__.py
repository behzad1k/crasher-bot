"""Database layer for tracking sessions, multipliers, and bets."""

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_PATH = "./crasher_data.db"


class Database:
    """SQLite database for round and bet history."""

    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_tables()
        self.current_session_id: Optional[int] = None

    def _init_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_timestamp DATETIME NOT NULL,
                end_timestamp DATETIME,
                start_balance REAL,
                end_balance REAL,
                total_rounds INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS multipliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                multiplier REAL NOT NULL,
                bettor_count INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                session_id INTEGER REFERENCES sessions(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT,
                bet_amount REAL NOT NULL,
                outcome TEXT CHECK(outcome IN ('win', 'loss')),
                multiplier REAL,
                profit_loss REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Ensure session_id column exists on multipliers (migration)
        cur.execute("PRAGMA table_info(multipliers)")
        columns = [col[1] for col in cur.fetchall()]
        if "session_id" not in columns:
            cur.execute(
                "ALTER TABLE multipliers ADD COLUMN session_id INTEGER REFERENCES sessions(id)"
            )
        self.conn.commit()

    # ── Session management ──────────────────────────────────────────

    def create_session(self, start_balance: Optional[float] = None) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO sessions (start_timestamp, start_balance) VALUES (?, ?)",
            (datetime.now(), start_balance),
        )
        self.conn.commit()
        session_id = cur.lastrowid
        self.current_session_id = session_id
        return session_id

    def end_session(self, end_balance: Optional[float] = None):
        if self.current_session_id is None:
            return
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE sessions SET end_timestamp = ?, end_balance = ? WHERE id = ?",
            (datetime.now(), end_balance, self.current_session_id),
        )
        self.conn.commit()

    def get_last_session(self) -> Optional[Tuple[int, str, int]]:
        """Returns (session_id, last_timestamp, round_count) or None."""
        cur = self.conn.cursor()
        self._migrate_orphan_multipliers(cur)

        cur.execute("""
            SELECT s.id, MAX(m.timestamp), COUNT(m.id)
            FROM sessions s
            LEFT JOIN multipliers m ON s.id = m.session_id
            GROUP BY s.id ORDER BY s.id DESC LIMIT 1
        """)
        row = cur.fetchone()
        return row if row and row[1] else None

    def _migrate_orphan_multipliers(self, cur: sqlite3.Cursor):
        """Assign orphan multipliers (no session_id) to a new session."""
        cur.execute("SELECT COUNT(*) FROM sessions")
        if cur.fetchone()[0] > 0:
            return
        cur.execute(
            "SELECT COUNT(*), MAX(timestamp) FROM multipliers WHERE session_id IS NULL"
        )
        count, _ = cur.fetchone()
        if not count or count == 0:
            return
        logger.info("Migrating %d orphan multipliers...", count)
        cur.execute("""
            INSERT INTO sessions (start_timestamp, end_timestamp)
            VALUES (
                (SELECT MIN(timestamp) FROM multipliers WHERE session_id IS NULL),
                (SELECT MAX(timestamp) FROM multipliers WHERE session_id IS NULL)
            )
        """)
        new_id = cur.lastrowid
        cur.execute(
            "UPDATE multipliers SET session_id = ? WHERE session_id IS NULL",
            (new_id,),
        )
        self.conn.commit()

    def list_sessions(self) -> List[Tuple[int, str, str, int]]:
        """Return all sessions as (id, start_timestamp, end_timestamp, round_count)."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT s.id, s.start_timestamp, s.end_timestamp, COUNT(m.id)
            FROM sessions s
            LEFT JOIN multipliers m ON s.id = m.session_id
            GROUP BY s.id
            ORDER BY s.id DESC
        """)
        return cur.fetchall()

    def get_all_session_multipliers(self, session_id: int) -> List[float]:
        """Get ALL multipliers for a session (chronological order)."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT multiplier FROM multipliers WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        return [row[0] for row in cur.fetchall()]

    def get_session_multipliers(self, session_id: int, n: int) -> List[float]:
        """Get last N multipliers from a session (chronological order)."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT multiplier FROM multipliers WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, n),
        )
        return [row[0] for row in reversed(cur.fetchall())]

    def add_missing_rounds(
        self,
        session_id: int,
        multipliers: List[float],
        start_time: datetime,
        end_time: datetime,
    ):
        if not multipliers:
            return
        cur = self.conn.cursor()
        total_sec = (end_time - start_time).total_seconds()
        sec_per = total_sec / max(len(multipliers) - 1, 1)

        for i, mult in enumerate(multipliers):
            ts = end_time if i == len(multipliers) - 1 else start_time + timedelta(seconds=sec_per * (i + 1))
            try:
                cur.execute(
                    "INSERT INTO multipliers (multiplier, session_id, timestamp) VALUES (?, ?, ?)",
                    (mult, session_id, ts),
                )
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()

    # ── Round data ──────────────────────────────────────────────────

    def add_multiplier(self, multiplier: float, bettor_count: Optional[int] = None):
        if self.current_session_id is None:
            raise ValueError("No active session")
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO multipliers (multiplier, bettor_count, session_id) VALUES (?, ?, ?)",
            (multiplier, bettor_count, self.current_session_id),
        )
        self.conn.commit()

    def get_recent_multipliers(self, count: int) -> List[float]:
        if self.current_session_id is None:
            return []
        cur = self.conn.cursor()
        cur.execute(
            "SELECT multiplier FROM multipliers WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (self.current_session_id, count),
        )
        return [row[0] for row in reversed(cur.fetchall())]

    # ── Bets ────────────────────────────────────────────────────────

    def add_bet(
        self,
        strategy_name: str,
        bet_amount: float,
        outcome: str,
        multiplier: float,
        profit_loss: float,
    ):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO bets (strategy_name, bet_amount, outcome, multiplier, profit_loss) VALUES (?, ?, ?, ?, ?)",
            (strategy_name, bet_amount, outcome, multiplier, profit_loss),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
