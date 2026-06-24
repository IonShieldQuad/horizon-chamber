"""Async SQLite database layer for Horizon Chamber.

Schema version history:
  version 1 — v0.1 MVP: chaos table (now/later/trash classification)
  version 2 — v0.2: activity_log, time_blocks, app_categories tables
  version 3 — v0.3: goals, tasks, task_log tables for dynamic kanban goals system
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
            # Fresh database — SCHEMA_SQL already creates v4 tables
            await _set_schema_version(db, 4)
            await db.commit()
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
            return

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
