"""Persistência em SQLite: histórico de análises, favoritos, posições, alertas e configurações."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from .b3 import FUTURES
from .config import DATA_DIR, DB_PATH, DEFAULT_FAVORITES, DEFAULT_SETTINGS, LOCAL_TZ

_init_lock = threading.Lock()
_initialized = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    price REAL,
    change_pct REAL,
    action TEXT,
    side TEXT,
    setup TEXT,
    score REAL,
    confidence REAL,
    trend TEXT,
    forecast TEXT,
    rsi REAL,
    macd REAL,
    headline TEXT,
    explanation TEXT,
    source TEXT DEFAULT 'scanner'
);
CREATE INDEX IF NOT EXISTS idx_analyses_symbol ON analyses(symbol, timeframe, id);

CREATE TABLE IF NOT EXISTS favorites (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    position INTEGER DEFAULT 0,
    added_at TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL,
    stop_price REAL,
    target_price REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_price REAL,
    pnl_pct REAL,
    status TEXT NOT NULL DEFAULT 'open',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT,
    timeframe TEXT,
    level TEXT,
    title TEXT,
    message TEXT,
    action TEXT,
    price REAL
);

CREATE TABLE IF NOT EXISTS signal_state (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    action TEXT,
    side TEXT,
    setup TEXT,
    price REAL,
    ts TEXT,
    PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    setup TEXT,
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    target1 REAL,
    target2 REAL NOT NULL,
    confidence REAL,
    reliability TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    result_r REAL,
    exit_price REAL,
    closed_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status, id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    init()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
            _migrate(conn)
            if conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0:
                for pos, symbol in enumerate(DEFAULT_FAVORITES):
                    conn.execute("INSERT INTO favorites(symbol, position, added_at) VALUES (?, ?, ?)",
                                 (symbol, pos, now_iso()))
            conn.commit()
        finally:
            conn.close()
        _initialized = True


SETTINGS_VERSION = 6


def _migrate(conn: sqlite3.Connection) -> None:
    """Atualiza padrões antigos que o usuário nunca alterou e acrescenta colunas novas."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'settings_version'").fetchone()
    version = int(row[0]) if row else 1
    if version < 2:
        conn.execute("UPDATE settings SET value = 'auto' WHERE key = 'atr_stop_mult' AND value = '1.5'")
        conn.execute("UPDATE settings SET value = '1h' WHERE key = 'scan_timeframe' AND value = '15m'")
    if version < 4:  # o usuário optou por não usar o MetaTrader 5
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'use_mt5'")
    if version < 5:  # o usuário pediu entradas no M15 guiadas pelas zonas do mensal/semanal/diário
        conn.execute("UPDATE settings SET value = '15m' WHERE key = 'scan_timeframe'")
    if version < 6:  # método do usuário no ouro: rompimento de zona + pullback + novo rompimento
        conn.execute("UPDATE settings SET value = 'pullback' WHERE key = 'strategy_mode' AND value = 'zonas'")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(positions)")}
    for column in ("result_r", "pnl_money"):
        if column not in columns:
            conn.execute(f"ALTER TABLE positions ADD COLUMN {column} REAL")
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('settings_version', ?)", (str(SETTINGS_VERSION),))


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


# ---------------------------------------------------------------- configurações

def get_settings() -> dict[str, str]:
    with connect() as conn:
        stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return {**DEFAULT_SETTINGS, **stored}


def update_settings(values: dict[str, str]) -> dict[str, str]:
    with connect() as conn:
        for key, value in values.items():
            if key in DEFAULT_SETTINGS:
                conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, str(value)))
    return get_settings()


# ---------------------------------------------------------------- favoritos

def list_favorites() -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute("SELECT symbol, name, position FROM favorites ORDER BY position, added_at"))


def add_favorite(symbol: str, name: str | None = None) -> None:
    with connect() as conn:
        next_pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM favorites").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO favorites(symbol, name, position, added_at) VALUES (?, ?, ?, ?)",
                     (symbol, name, next_pos, now_iso()))


def remove_favorite(symbol: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM favorites WHERE symbol = ?", (symbol,))


# ---------------------------------------------------------------- análises

def save_analysis(a: dict, source: str = "scanner") -> int:
    s = a["signal"]
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO analyses (ts, symbol, timeframe, price, change_pct, action, side, setup, score, confidence,
                                     trend, forecast, rsi, macd, headline, explanation, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), a["symbol"], a["timeframe"]["key"], a["price"], a["change_pct"], s["action"], s.get("side"),
             s.get("setup"), a["score"], s.get("confidence"), a["trend"]["direction"], a["forecast"]["headline"],
             a["indicators"]["rsi"]["value"], a["indicators"]["macd"]["value"], s["headline"], s["explanation"], source),
        )
        return cur.lastrowid


def history(symbol: str | None = None, timeframe: str | None = None, limit: int = 200,
            only_signals: bool = False) -> list[dict]:
    query, params = "SELECT * FROM analyses WHERE 1=1", []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    if timeframe:
        query += " AND timeframe = ?"
        params.append(timeframe)
    if only_signals:
        query += " AND action IN ('ENTRAR_AGORA', 'SAIR_AGORA', 'PREPARE_SE')"
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return _rows(conn.execute(query, params))


def last_analysis(symbol: str, timeframe: str) -> dict | None:
    rows = history(symbol, timeframe, limit=1)
    return rows[0] if rows else None


# ---------------------------------------------------------------- estado do sinal (detecção de mudança)

def get_signal_state(symbol: str, timeframe: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM signal_state WHERE symbol = ? AND timeframe = ?", (symbol, timeframe)).fetchone()
    return dict(row) if row else None


def set_signal_state(symbol: str, timeframe: str, action: str, side: str | None, setup: str | None, price: float) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO signal_state VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (symbol, timeframe, action, side, setup, price, now_iso()))


# ---------------------------------------------------------------- alertas

def add_alert(symbol: str, timeframe: str, level: str, title: str, message: str, action: str | None,
              price: float | None) -> dict:
    with connect() as conn:
        ts = now_iso()
        cur = conn.execute(
            "INSERT INTO alerts (ts, symbol, timeframe, level, title, message, action, price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, symbol, timeframe, level, title, message, action, price),
        )
        return {"id": cur.lastrowid, "ts": ts, "symbol": symbol, "timeframe": timeframe, "level": level,
                "title": title, "message": message, "action": action, "price": price}


def list_alerts(since_id: int = 0, limit: int = 50) -> list[dict]:
    with connect() as conn:
        return _rows(conn.execute("SELECT * FROM alerts WHERE id > ? ORDER BY id DESC LIMIT ?", (since_id, limit)))


# ---------------------------------------------------------------- sinais emitidos (acompanhamento real)

def record_signal(a: dict) -> int:
    s, p = a["signal"], a["plan"]
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO signals (ts, symbol, timeframe, side, setup, entry, stop, target1, target2, confidence, reliability)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), a["symbol"], a["timeframe"]["key"], s["side"], s.get("setup"), p["entry"], p["stop"],
             p["target1"], p["target2"], s.get("confidence"), a["context"]["reliability"]["level"]),
        )
        return cur.lastrowid


def list_signals(status: str | None = None, symbol: str | None = None, limit: int = 500) -> list[dict]:
    query, params = "SELECT * FROM signals WHERE 1=1", []
    if status:
        query += " AND status = ?"
        params.append(status)
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return _rows(conn.execute(query, params))


def close_signal(signal_id: int, status: str, result_r: float, exit_price: float, closed_ts: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE signals SET status=?, result_r=?, exit_price=?, closed_ts=? WHERE id=?",
                     (status, result_r, exit_price, closed_ts, signal_id))


# ---------------------------------------------------------------- posições

def open_position(data: dict) -> dict:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO positions (symbol, timeframe, side, entry_price, quantity, stop_price, target_price, opened_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["symbol"], data.get("timeframe"), data["side"], data["entry_price"], data.get("quantity"),
             data.get("stop_price"), data.get("target_price"), now_iso(), data.get("notes")),
        )
        position_id = cur.lastrowid
    return get_position(position_id)  # lido após o commit


def get_position(position_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
    return dict(row) if row else None


def open_position_for(symbol: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM positions WHERE symbol = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                           (symbol,)).fetchone()
    return dict(row) if row else None


def list_positions(status: str | None = None, limit: int = 200) -> list[dict]:
    query, params = "SELECT * FROM positions", []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return _rows(conn.execute(query, params))


def close_position(position_id: int, exit_price: float, max_stop_pct: float = 3.0) -> dict | None:
    pos = get_position(position_id)
    if not pos or pos["status"] != "open":
        return pos
    direction = 1 if pos["side"] == "COMPRA" else -1
    entry = pos["entry_price"]
    pnl = (exit_price / entry - 1) * 100 * direction
    stop = pos["stop_price"] or entry * (1 - direction * max_stop_pct / 100)
    risk = abs(entry - stop)
    result_r = (exit_price - entry) * direction / risk if risk else None
    point_value = FUTURES[pos["symbol"]].point_value if pos["symbol"] in FUTURES else 1.0
    pnl_money = (exit_price - entry) * direction * (pos["quantity"] or 0) * point_value if pos["quantity"] else None
    with connect() as conn:
        conn.execute("UPDATE positions SET status='closed', closed_at=?, exit_price=?, pnl_pct=?, result_r=?, pnl_money=? "
                     "WHERE id=?",
                     (now_iso(), exit_price, round(pnl, 3), None if result_r is None else round(result_r, 3),
                      None if pnl_money is None else round(pnl_money, 2), position_id))
    return get_position(position_id)


def positions_since(iso_start: str) -> list[dict]:
    """Operações abertas ou encerradas a partir de uma data/hora (ISO local)."""
    with connect() as conn:
        return _rows(conn.execute("SELECT * FROM positions WHERE opened_at >= ? OR closed_at >= ? ORDER BY id",
                                  (iso_start, iso_start)))


def delete_position(position_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
