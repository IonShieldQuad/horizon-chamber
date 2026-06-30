"""Async SQLite database layer for Horizon Chamber.

Schema version history:
  version 1 — v0.1 MVP: chaos table (now/later/trash classification)
  version 2 — v0.2: activity_log, time_blocks, app_categories tables
  version 3 — v0.3: goals, tasks, task_log tables for dynamic kanban goals system
  version 4 — widened task_log action CHECK
  version 5 — feed_items, feed_runs tables for n8n feed aggregation
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

-- Goals — the parent entity for the kanban board
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('long_term', 'habit', 'maintenance')),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    frequency TEXT DEFAULT 'daily' CHECK(frequency IN ('daily', 'weekly', 'custom')),
    custom_interval_days INTEGER,
    target_days INTEGER,
    progress_pct REAL DEFAULT 0.0,
    paused INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tasks — individual work items generated from goals
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'doing', 'done', 'skipped', 'carried_over')),
    carry_over_count INTEGER DEFAULT 0,
    parent_task_id INTEGER,
    ai_suggested INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(date);
CREATE INDEX IF NOT EXISTS idx_tasks_goal_id ON tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

-- Task completions log — append-only, for analysis
CREATE TABLE IF NOT EXISTS task_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    action TEXT NOT NULL CHECK(action IN ('pending', 'doing', 'done', 'skipped', 'carried_over', 'split', 'auto_generated')),
    date TEXT NOT NULL,
    ai_analysis TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Sunrise schedule persistence (enabled, time, last_triggered_date)
CREATE TABLE IF NOT EXISTS sunrise_config (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    time TEXT NOT NULL DEFAULT '07:00',
    last_triggered_date TEXT
);

-- Feed items — n8n summarization results
CREATE TABLE IF NOT EXISTS feed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id TEXT NOT NULL UNIQUE,
    relevance_score REAL DEFAULT 0.0,
    is_relevant INTEGER DEFAULT 0,
    priority_level INTEGER DEFAULT 50,
    category TEXT DEFAULT '',
    one_liner TEXT NOT NULL,
    sentiment_tone TEXT DEFAULT 'neutral',
    source_url TEXT DEFAULT '',
    source_name TEXT DEFAULT '',
    dismissed INTEGER DEFAULT 0,
    feed_run_id INTEGER REFERENCES feed_runs(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feed_items_dismissed ON feed_items(dismissed);
CREATE INDEX IF NOT EXISTS idx_feed_items_created ON feed_items(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_items_original_id ON feed_items(original_id);

-- Feed runs — records of each n8n fetch
CREATE TABLE IF NOT EXISTS feed_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_by TEXT DEFAULT 'schedule',
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    total_items INTEGER DEFAULT 0,
    total_relevant INTEGER DEFAULT 0,
    average_relevance REAL DEFAULT 0.0,
    status TEXT DEFAULT 'pending'
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

        if current_ver == 0:
            # Fresh database — SCHEMA_SQL already creates v5 tables
            await _set_schema_version(db, 5)
            await db.commit()
            import logging
            logging.getLogger(__name__).info("Fresh database initialized (schema v5)")
            return

        if current_ver == 2:
            # Migration v2 → v3: add goals, tasks, task_log tables
            await _set_schema_version(db, 3)
            await db.commit()
            import logging
            logging.getLogger(__name__).info("Schema migrated from v2 to v3")
            # Fall through to v3→v4

        if current_ver <= 3:
            # Migration v3 → v4: widen task_log action CHECK to include pending/doing
            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_log_v4 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id),
                    goal_id INTEGER NOT NULL REFERENCES goals(id),
                    action TEXT NOT NULL CHECK(action IN ('pending', 'doing', 'done', 'skipped', 'carried_over', 'split', 'auto_generated')),
                    date TEXT NOT NULL,
                    ai_analysis TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("INSERT OR IGNORE INTO task_log_v4 SELECT * FROM task_log")
            await db.execute("DROP TABLE IF EXISTS task_log")
            await db.execute("ALTER TABLE task_log_v4 RENAME TO task_log")
            await _set_schema_version(db, 4)
            await db.commit()
            import logging
            logging.getLogger(__name__).info("Schema migrated to v4 (task_log CHECK widened)")
            # Fall through to v4→v5

        if current_ver <= 4:
            # Migration v4 → v5: feed tables already exist via SCHEMA_SQL, just bump version
            await _set_schema_version(db, 5)
            await db.commit()
            import logging
            logging.getLogger(__name__).info("Schema migrated to v5 (feed tables added)")

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


# ── Goals ────────────────────────────────────────────────────────────────


async def insert_goal(
    goal_type: str,
    title: str,
    description: str = "",
    frequency: str = "daily",
    custom_interval_days: Optional[int] = None,
    target_days: Optional[int] = None,
    progress_pct: float = 0.0,
) -> int:
    """Create a new goal. Returns the new goal id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO goals (type, title, description, frequency,
               custom_interval_days, target_days, progress_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (goal_type, title, description, frequency,
             custom_interval_days, target_days, progress_pct),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_goal(goal_id: int) -> Optional[dict]:
    """Return a single goal by id, or None if not found."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_goals(
    goal_type: Optional[str] = None,
    archived: bool = False,
    include_paused: bool = False,
) -> list[dict]:
    """List goals with optional type filter.

    By default returns non-archived goals. Set archived=True to get only
    archived goals, or set include_paused=True to include paused goals.
    """
    db = await get_db()
    try:
        conditions = ["archived = ?"]
        params: list = [1 if archived else 0]
        if goal_type:
            conditions.append("type = ?")
            params.append(goal_type)
        if not include_paused:
            conditions.append("paused = 0")

        cursor = await db.execute(
            f"SELECT * FROM goals WHERE {' AND '.join(conditions)} ORDER BY sort_order, created_at DESC",
            params,
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def update_goal(goal_id: int, **kwargs) -> dict:
    """Update one or more fields on a goal.

    Accepted keyword arguments: type, title, description, frequency,
    custom_interval_days, target_days, progress_pct, paused, archived,
    sort_order.

    Returns the updated goal dict.
    """
    allowed = {
        "type", "title", "description", "frequency", "custom_interval_days",
        "target_days", "progress_pct", "paused", "archived", "sort_order",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields provided for update")

    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [goal_id]

    db = await get_db()
    try:
        await db.execute(
            f"UPDATE goals SET {set_clause} WHERE id = ?", values
        )
        await db.commit()
    finally:
        await db.close()

    result = await get_goal(goal_id)
    if result is None:
        raise ValueError(f"Goal {goal_id} not found after update")
    return result


async def archive_goal(goal_id: int) -> None:
    """Soft-delete a goal by setting archived=1."""
    await update_goal(goal_id, archived=1)


# ── Tasks ────────────────────────────────────────────────────────────────


async def insert_task(
    goal_id: int,
    title: str,
    date_str: str,
    description: str = "",
    ai_suggested: int = 0,
    parent_task_id: Optional[int] = None,
) -> int:
    """Create a new task. Returns the new task id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO tasks (goal_id, title, description, date,
               ai_suggested, parent_task_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (goal_id, title, description, date_str, ai_suggested, parent_task_id),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_task(task_id: int) -> Optional[dict]:
    """Return a single task by id, or None if not found."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_tasks_for_date(date_str: str, status: Optional[str] = None) -> list[dict]:
    """Return all tasks for a given date, ordered by sort_order then created_at."""
    db = await get_db()
    try:
        if status:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE date = ? AND status = ? ORDER BY sort_order, created_at",
                (date_str, status),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE date = ? ORDER BY sort_order, created_at",
                (date_str,),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_tasks_for_goal(goal_id: int) -> list[dict]:
    """Return all tasks for a goal, newest first."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM tasks WHERE goal_id = ? ORDER BY date DESC, sort_order",
            (goal_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def update_task(task_id: int, **kwargs) -> None:
    """Update one or more fields on a task.

    Accepted keyword arguments: title, description, status, date,
    carry_over_count, sort_order.

    Sets completed_at automatically when status='done'.
    Sets updated_at automatically.
    """
    allowed = {"title", "description", "status", "date",
               "carry_over_count", "sort_order"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        raise ValueError("No valid fields provided for update")

    updates["updated_at"] = datetime.utcnow().isoformat()
    if updates.get("status") == "done":
        updates["completed_at"] = datetime.utcnow().isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]

    db = await get_db()
    try:
        await db.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?", values
        )
        await db.commit()
    finally:
        await db.close()


async def get_overdue_tasks(date_str: str) -> list[dict]:
    """Return tasks with date before given date and status pending or doing."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM tasks WHERE date < ? AND status IN ('pending', 'doing') "
            "ORDER BY date ASC, sort_order",
            (date_str,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_latest_task_for_goal(goal_id: int) -> Optional[dict]:
    """Return the most recently created task for a goal, or None."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM tasks WHERE goal_id = ? ORDER BY created_at DESC LIMIT 1",
            (goal_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_incomplete_tasks_before_date(goal_id: int, date_str: str) -> list[dict]:
    """Return pending/doing tasks for a goal before a given date."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM tasks WHERE goal_id = ? AND date < ? "
            "AND status IN ('pending', 'doing') ORDER BY date DESC",
            (goal_id, date_str),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ── Task Log ─────────────────────────────────────────────────────────────


async def insert_task_log(
    task_id: int,
    goal_id: int,
    action: str,
    date_str: str,
    ai_analysis: Optional[str] = None,
) -> int:
    """Log an action on a task. Returns the new log id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO task_log (task_id, goal_id, action, date, ai_analysis) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, goal_id, action, date_str, ai_analysis),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_task_log_for_goal(goal_id: int, limit: int = 50) -> list[dict]:
    """Return task log entries for a goal, newest first."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM task_log WHERE goal_id = ? ORDER BY created_at DESC LIMIT ?",
            (goal_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_task_log_for_date(date_str: str) -> list[dict]:
    """Return task log entries for a specific date."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM task_log WHERE date = ? ORDER BY created_at DESC",
            (date_str,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


# ── Sunrise config persistence ────────────────────────────────────────────


async def get_sunrise_config() -> dict:
    """Load the sunrise schedule from DB. Returns defaults if no row exists."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT enabled, time, last_triggered_date FROM sunrise_config WHERE id = 1")
        row = await cursor.fetchone()
        if row:
            return {
                "enabled": bool(row["enabled"]),
                "time": row["time"],
                "last_triggered_date": row["last_triggered_date"],
            }
        return {"enabled": False, "time": "07:00", "last_triggered_date": None}
    finally:
        await db.close()


async def save_sunrise_config(enabled: bool, time: str, last_triggered_date: str | None = None) -> None:
    """Upsert the sunrise schedule into DB."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO sunrise_config (id, enabled, time, last_triggered_date) VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, time=excluded.time, last_triggered_date=excluded.last_triggered_date",
            (1 if enabled else 0, time, last_triggered_date),
        )
        await db.commit()
    finally:
        await db.close()


# ── Feed Items ────────────────────────────────────────────────────────────


async def insert_feed_item(
    original_id: str,
    one_liner: str,
    relevance_score: float = 0.0,
    is_relevant: bool = False,
    priority_level: int = 50,
    category: str = "",
    sentiment_tone: str = "neutral",
    source_url: str = "",
    source_name: str = "",
    feed_run_id: int | None = None,
) -> int | None:
    """Insert a feed item. Returns the new id, or None if original_id already exists."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT OR IGNORE INTO feed_items
               (original_id, one_liner, relevance_score, is_relevant,
                priority_level, category, sentiment_tone, source_url,
                source_name, feed_run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (original_id, one_liner, relevance_score, 1 if is_relevant else 0,
             priority_level, category, sentiment_tone, source_url,
             source_name, feed_run_id),
        )
        await db.commit()
        return cursor.lastrowid if cursor.lastrowid else None
    finally:
        await db.close()


async def get_feed_items(
    dismissed: bool | None = False,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Return feed items, newest first.

    Args:
        dismissed: False=active only, True=dismissed only, None=all.
        category: optional category filter.
    """
    db = await get_db()
    try:
        conditions: list[str] = []
        params: list = []

        if dismissed is not None:
            conditions.append("dismissed = ?")
            params.append(1 if dismissed else 0)
        if category:
            conditions.append("category = ?")
            params.append(category)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        cursor = await db.execute(
            f"SELECT * FROM feed_items {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_feed_item(item_id: int) -> dict | None:
    """Return a single feed item by id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM feed_items WHERE id = ?", (item_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def dismiss_feed_item(item_id: int, dismissed: bool = True) -> bool:
    """Mark a feed item as dismissed (or undismissed). Returns True if changed."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE feed_items SET dismissed = ? WHERE id = ?",
            (1 if dismissed else 0, item_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_dismissed_count() -> int:
    """Return the number of dismissed feed items."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM feed_items WHERE dismissed = 1"
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0
    finally:
        await db.close()


async def has_feed_item(original_id: str) -> bool:
    """Check if a feed item with this original_id already exists."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM feed_items WHERE original_id = ?", (original_id,)
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await db.close()


# ── Feed Runs ─────────────────────────────────────────────────────────────


async def insert_feed_run(triggered_by: str = "schedule") -> int:
    """Create a new feed run record. Returns the run id."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO feed_runs (triggered_by) VALUES (?)",
            (triggered_by,),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_feed_run(
    run_id: int,
    *,
    completed_at: str | None = None,
    total_items: int | None = None,
    total_relevant: int | None = None,
    average_relevance: float | None = None,
    status: str | None = None,
) -> None:
    """Update fields on a feed run record."""
    updates: dict[str, object] = {}
    if completed_at is not None:
        updates["completed_at"] = completed_at
    if total_items is not None:
        updates["total_items"] = total_items
    if total_relevant is not None:
        updates["total_relevant"] = total_relevant
    if average_relevance is not None:
        updates["average_relevance"] = average_relevance
    if status is not None:
        updates["status"] = status
    if not updates:
        return

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [run_id]

    db = await get_db()
    try:
        await db.execute(
            f"UPDATE feed_runs SET {set_clause} WHERE id = ?", values
        )
        await db.commit()
    finally:
        await db.close()


async def get_last_feed_run() -> dict | None:
    """Return the most recent feed run, or None."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM feed_runs ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_feed_stats() -> dict:
    """Return aggregate feed statistics."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM feed_items WHERE dismissed = 0"
        )
        active_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM feed_items WHERE dismissed = 1"
        )
        dismissed_count = (await cursor.fetchone())["cnt"]

        cursor = await db.execute(
            "SELECT COALESCE(AVG(relevance_score), 0.0) AS avg_rel FROM feed_items WHERE dismissed = 0"
        )
        avg_relevance = (await cursor.fetchone())["avg_rel"]

        cursor = await db.execute(
            "SELECT category, COUNT(*) AS cnt FROM feed_items WHERE dismissed = 0 GROUP BY category ORDER BY cnt DESC"
        )
        categories = {row["category"]: row["cnt"] for row in await cursor.fetchall()}

        last_run = await get_last_feed_run()

        return {
            "active_items": active_count,
            "dismissed_items": dismissed_count,
            "average_relevance": round(avg_relevance, 2),
            "categories": categories,
            "last_run": last_run,
        }
    finally:
        await db.close()
