"""SQLite database for trade history, P&L, orders, and config."""
import sqlite3
import json
from datetime import datetime, date
from pathlib import Path
from contextlib import contextmanager
from . import config

_db_path = str(config.DB_PATH)


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            entry_time TEXT,
            exit_time TEXT,
            reason TEXT,
            source TEXT DEFAULT 'auto',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL,
            order_type TEXT DEFAULT 'MARKET',
            status TEXT DEFAULT 'pending',
            fill_price REAL,
            signal_price REAL,
            slippage REAL,
            ibkr_order_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            filled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_pnl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            bot TEXT NOT NULL,
            trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            gross_pnl REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            max_dd REAL DEFAULT 0,
            UNIQUE(date, bot)
        );

        CREATE TABLE IF NOT EXISTS alert_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            telegram_token TEXT,
            telegram_chat_id TEXT,
            email_smtp TEXT,
            email_user TEXT,
            email_pass TEXT,
            email_to TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS risk_config (
            bot TEXT PRIMARY KEY,
            max_positions INTEGER,
            capital_per_trade REAL,
            max_daily_loss REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Initialize defaults
        INSERT OR IGNORE INTO alert_config (id) VALUES (1);
        """)

        # Initialize risk config defaults
        for bot_id, defaults in config.DEFAULT_RISK.items():
            conn.execute(
                "INSERT OR IGNORE INTO risk_config (bot, max_positions, capital_per_trade, max_daily_loss) VALUES (?, ?, ?, ?)",
                (bot_id, defaults["max_positions"], defaults["capital_per_trade"], defaults["max_daily_loss"])
            )


@contextmanager
def get_conn():
    """Get a database connection with WAL mode for concurrent access."""
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Trades ──

def insert_trade(bot, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct,
                 entry_time, exit_time, reason, source="auto"):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trades (bot, symbol, side, qty, entry_price, exit_price,
               pnl, pnl_pct, entry_time, exit_time, reason, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bot, symbol, side, qty, entry_price, exit_price, pnl, pnl_pct,
             entry_time, exit_time, reason, source)
        )


def get_trades(bot=None, from_date=None, to_date=None, limit=50, offset=0):
    with get_conn() as conn:
        q = "SELECT * FROM trades WHERE 1=1"
        params = []
        if bot:
            q += " AND bot = ?"
            params.append(bot)
        if from_date:
            q += " AND entry_time >= ?"
            params.append(from_date)
        if to_date:
            q += " AND entry_time <= ?"
            params.append(to_date)
        q += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(q, params).fetchall()
        total = conn.execute(
            q.replace("SELECT *", "SELECT COUNT(*)").split("ORDER BY")[0], params[:-2]
        ).fetchone()[0]
        return [dict(r) for r in rows], total


# ── Orders ──

def insert_order(bot, symbol, side, qty, order_type="MARKET", signal_price=None):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO orders (bot, symbol, side, qty, order_type, signal_price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bot, symbol, side, qty, order_type, signal_price)
        )
        return cur.lastrowid


def update_order(order_id, status, fill_price=None, slippage=None, ibkr_order_id=None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE orders SET status=?, fill_price=?, slippage=?, ibkr_order_id=?,
               filled_at=datetime('now') WHERE id=?""",
            (status, fill_price, slippage, ibkr_order_id, order_id)
        )


def get_orders(bot=None, status=None, limit=50):
    with get_conn() as conn:
        q = "SELECT * FROM orders WHERE 1=1"
        params = []
        if bot:
            q += " AND bot = ?"
            params.append(bot)
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(q, params).fetchall()]


# ── P&L ──

def get_pnl_summary(bot=None):
    with get_conn() as conn:
        q = """SELECT bot,
               COUNT(*) as total_trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
               ROUND(SUM(pnl), 2) as total_pnl,
               ROUND(AVG(CASE WHEN pnl > 0 THEN pnl END), 2) as avg_win,
               ROUND(AVG(CASE WHEN pnl <= 0 THEN pnl END), 2) as avg_loss,
               ROUND(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) /
                     NULLIF(ABS(SUM(CASE WHEN pnl <= 0 THEN pnl ELSE 0 END)), 0), 2) as profit_factor
               FROM trades"""
        params = []
        if bot:
            q += " WHERE bot = ?"
            params.append(bot)
        q += " GROUP BY bot"
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_pnl_chart(bot=None, days=30):
    with get_conn() as conn:
        q = """SELECT DATE(exit_time) as date, bot,
               SUM(pnl) as daily_pnl,
               COUNT(*) as trades
               FROM trades WHERE exit_time IS NOT NULL"""
        params = []
        if bot:
            q += " AND bot = ?"
            params.append(bot)
        q += f" AND exit_time >= datetime('now', '-{days} days')"
        q += " GROUP BY DATE(exit_time), bot ORDER BY date"
        return [dict(r) for r in conn.execute(q, params).fetchall()]


# ── Risk Config ──

def get_risk_config(bot):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM risk_config WHERE bot = ?", (bot,)).fetchone()
        return dict(row) if row else config.DEFAULT_RISK.get(bot, {})


def set_risk_config(bot, max_positions=None, capital_per_trade=None, max_daily_loss=None):
    with get_conn() as conn:
        existing = get_risk_config(bot)
        conn.execute(
            """INSERT OR REPLACE INTO risk_config (bot, max_positions, capital_per_trade, max_daily_loss, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (bot,
             max_positions if max_positions is not None else existing.get("max_positions"),
             capital_per_trade if capital_per_trade is not None else existing.get("capital_per_trade"),
             max_daily_loss if max_daily_loss is not None else existing.get("max_daily_loss"))
        )
    # Write config file for bot to read
    cfg = get_risk_config(bot)
    cfg_file = Path(config.BOTS[bot]["config_file"])
    cfg_file.write_text(json.dumps({
        "max_positions": cfg["max_positions"],
        "capital_per_trade": cfg["capital_per_trade"],
        "max_daily_loss": cfg["max_daily_loss"],
    }))


# ── Alert Config ──

def get_alert_config():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM alert_config WHERE id = 1").fetchone()
        return dict(row) if row else {}


def set_alert_config(**kwargs):
    with get_conn() as conn:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE alert_config SET {sets}, updated_at = datetime('now') WHERE id = 1",
                     list(kwargs.values()))
