"""Async SQLite database layer for Horizon Chamber.

Schema version history:
  version 1 — v0.1 MVP: chaos table (now/later/trash classification)
  version 2 — v0.2: activity_log, time_blocks, app_categories tables
"""

import os
from datetime import datetime
from typing import Optional

import aiosqlite

DATABASE_PATH = os.getenv("DATABASE_PATH", "./horizon.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chaos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK(category IN ('now', 'later', 'trash')),
    text TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Activity samples — raw poll observations
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL DEFAULT 'unknown',
    window_title TEXT NOT NULL DEFAULT '',
    idle_seconds REAL NOT NULL DEFAULT 0.0,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Time blocks — aggregated focus periods
CREATE TABLE IF NOT EXISTS time_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'focus'
        CHECK(category IN ('focus', 'distraction', 'idle', 'away')),
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    duration_seconds INTEGER
);

-- App categorization — user-defined (or LLM-suggested) labels
CREATE TABLE IF NOT EXISTS app_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'focus'
        CHECK(category IN ('focus', 'distraction', 'neutral', 'idle', 'away')),
    label TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


async def get_db() -> aiosqlite.Connection:
    """Return an async SQLite connection."""
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def _get_schema_version(db: aiosqlite.Connection) -> int:
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def _set_schema_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute(f"PRAGMA user_version = {version}")


async def init_db():
    """Create tables on startup if they don't exist. Run migrations."""
    db = await get_db()
    try:
        await db.executescript(SCHEMA_SQL)

        current_ver = await _get_schema_version(db)
        if current_ver < 2:
            await _set_schema_version(db, 2)

        await db.commit()
    finally:
        await db.close()


# ── Chaos (existing) ──────────────────────────────────────────────────────


async def insert_classification(category: str, text: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO chaos (category, text) VALUES (?, ?)",
            (category, text),
        )
        await db.commit()
    finally:
        await db.close()


async def get_recent_now_items(limit: int = 3) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, text, timestamp FROM chaos WHERE category = 'now' ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {"id": row["id"], "text": row["text"], "timestamp": row["timestamp"]}
            for row in rows
        ]
    finally:
        await db.close()


# ── Activity Log ──────────────────────────────────────────────────────────


async def insert_activity(app_name: str, window_title: str, idle_seconds: float) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO activity_log (app_name, window_title, idle_seconds) VALUES (?, ?, ?)",
            (app_name, window_title, idle_seconds),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_recent_activity(limit: int = 50) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, app_name, window_title, idle_seconds, timestamp "
            "FROM activity_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "app_name": row["app_name"],
                "window_title": row["window_title"],
                "idle_seconds": row["idle_seconds"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]
    finally:
        await db.close()


# ── Time Blocks ────────────────────────────────────────────────────────────


async def upsert_time_block(app_name: str, start_time: str, category: str = "focus") -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO time_blocks (app_name, category, start_time) VALUES (?, ?, ?)",
            (app_name, category, start_time),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def close_time_block(block_id: int, end_time: str) -> None:
    db = await get_db()
    try:
        await db.execute(
            "UPDATE time_blocks SET end_time = ?, "
            "duration_seconds = ROUND((julianday(?) - julianday(start_time)) * 86400) "
            "WHERE id = ?",
            (end_time, end_time, block_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_time_blocks(period: str = "today", limit: int = 100) -> list[dict]:
    if period == "today":
        where = "WHERE date(start_time) = date('now')"
    elif period == "yesterday":
        where = "WHERE date(start_time) = date('now', '-1 day')"
    elif period == "last_7_days":
        where = "WHERE start_time >= datetime('now', '-7 days')"
    else:
        where = ""

    db = await get_db()
    try:
        cursor = await db.execute(
            f"SELECT id, app_name, category, start_time, end_time, duration_seconds "
            f"FROM time_blocks {where} ORDER BY start_time DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "app_name": row["app_name"],
                "category": row["category"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "duration_seconds": row["duration_seconds"],
            }
            for row in rows
        ]
    finally:
        await db.close()


async def get_focus_summary(period: str = "today") -> list[dict]:
    """Aggregated: app_name -> total_seconds, percentage for the given period."""
    if period == "today":
        where_clause = "WHERE end_time IS NOT NULL AND date(start_time) = date('now')"
    elif period == "yesterday":
        where_clause = "WHERE end_time IS NOT NULL AND date(start_time) = date('now', '-1 day')"
    elif period == "last_7_days":
        where_clause = "WHERE end_time IS NOT NULL AND start_time >= datetime('now', '-7 days')"
    else:
        where_clause = "WHERE end_time IS NOT NULL"

    db = await get_db()
    try:
        cursor = await db.execute(
            f"SELECT app_name, COALESCE(SUM(duration_seconds), 0) AS total_seconds "
            f"FROM time_blocks {where_clause} "
            f"GROUP BY app_name ORDER BY total_seconds DESC"
        )
        rows = await cursor.fetchall()

        total = sum(row["total_seconds"] for row in rows)
        result = []
        for row in rows:
            pct = round(row["total_seconds"] / total * 100, 1) if total > 0 else 0.0
            result.append(
                {
                    "app_name": row["app_name"],
                    "total_seconds": row["total_seconds"],
                    "percentage": pct,
                }
            )
        return result
    finally:
        await db.close()


# ── App Categories ────────────────────────────────────────────────────────


async def set_app_category(app_name: str, category: str, label: Optional[str] = None) -> None:
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO app_categories (app_name, category, label) VALUES (?, ?, ?) "
            "ON CONFLICT(app_name) DO UPDATE SET category = excluded.category, label = excluded.label",
            (app_name, category, label),
        )
        await db.commit()
    finally:
        await db.close()


async def get_app_category(app_name: str) -> Optional[str]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT category FROM app_categories WHERE app_name = ?",
            (app_name,),
        )
        row = await cursor.fetchone()
        return row["category"] if row else None
    finally:
        await db.close()


async def get_all_app_categories() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, app_name, category, label, created_at "
            "FROM app_categories ORDER BY app_name"
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "app_name": row["app_name"],
                "category": row["category"],
                "label": row["label"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        await db.close()


# ── Maintenance ──────────────────────────────────────────────────────────


async def prune_old_activity(days: int = 30) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM activity_log WHERE timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )
        await db.commit()
        return cursor.rowcount
    finally:
        await db.close()
