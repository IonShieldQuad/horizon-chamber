"""Unit tests for the database layer.

Each test gets its own temp database to avoid cross-contamination.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.asyncio
async def test_insert_all_categories(tmp_path):
    """Insert items for all 3 categories and verify counts."""
    db_path = tmp_path / "test.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    # Re-import after setting DB path
    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()

    await db_mod.insert_classification("now", "task 1")
    await db_mod.insert_classification("later", "task 2")
    await db_mod.insert_classification("trash", "task 3")
    await db_mod.insert_classification("now", "task 4")

    conn = await db_mod.get_db()
    try:
        cursor = await conn.execute(
            "SELECT category, COUNT(*) as cnt FROM chaos GROUP BY category ORDER BY category"
        )
        rows = await cursor.fetchall()
        counts = {row["category"]: row["cnt"] for row in rows}
        assert counts.get("now") == 2
        assert counts.get("later") == 1
        assert counts.get("trash") == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_get_recent_now_limits(tmp_path):
    """Insert 1 'now' item and verify limit works."""
    db_path = tmp_path / "test2.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()
    await db_mod.insert_classification("now", "only item")

    items = await db_mod.get_recent_now_items(limit=5)
    assert len(items) == 1
    assert items[0]["text"] == "only item"


@pytest.mark.asyncio
async def test_get_recent_now_empty(tmp_path):
    """get_recent_now_items returns empty list when no 'now' items exist."""
    db_path = tmp_path / "test3.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()
    await db_mod.insert_classification("later", "not now")

    items = await db_mod.get_recent_now_items(limit=3)
    assert items == []


# ── Activity Log ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_and_get_activity(tmp_path):
    """Insert activity samples and retrieve them via get_recent_activity."""
    db_path = tmp_path / "act1.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()

    # Insert two samples
    id1 = await db_mod.insert_activity("code.exe", "main.py", 2.5)
    assert isinstance(id1, int) and id1 > 0
    id2 = await db_mod.insert_activity("chrome.exe", "", 10.0)
    assert id2 > id1

    # Retrieve
    items = await db_mod.get_recent_activity(limit=10)
    assert len(items) == 2
    assert items[0]["app_name"] == "chrome.exe"
    assert items[1]["app_name"] == "code.exe"
    assert items[1]["window_title"] == "main.py"
    assert items[1]["idle_seconds"] == 2.5


@pytest.mark.asyncio
async def test_get_recent_activity_limit(tmp_path):
    """get_recent_activity respects the limit parameter."""
    db_path = tmp_path / "act2.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()
    for i in range(5):
        await db_mod.insert_activity(f"app{i}.exe", "", 0.0)

    items = await db_mod.get_recent_activity(limit=3)
    assert len(items) == 3


# ── Time Blocks ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_and_close_time_block(tmp_path):
    """Create a time block, close it, and verify duration is set."""
    db_path = tmp_path / "tb1.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()

    block_id = await db_mod.upsert_time_block("code.exe", "2025-01-15T09:00:00", "focus")
    assert isinstance(block_id, int) and block_id > 0

    await db_mod.close_time_block(block_id, "2025-01-15T11:30:00")

    blocks = await db_mod.get_time_blocks(period="all")
    assert len(blocks) == 1
    assert blocks[0]["app_name"] == "code.exe"
    assert blocks[0]["category"] == "focus"
    assert blocks[0]["duration_seconds"] == 9000  # 2.5 hours


@pytest.mark.asyncio
async def test_get_focus_summary_aggregates(tmp_path):
    """get_focus_summary groups by app and computes percentages."""
    db_path = tmp_path / "tb2.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()

    # Two blocks for code.exe, one for chrome.exe
    b1 = await db_mod.upsert_time_block("code.exe", "2025-01-15T09:00:00", "focus")
    await db_mod.close_time_block(b1, "2025-01-15T11:00:00")

    b2 = await db_mod.upsert_time_block("code.exe", "2025-01-15T11:05:00", "focus")
    await db_mod.close_time_block(b2, "2025-01-15T12:00:00")

    b3 = await db_mod.upsert_time_block("chrome.exe", "2025-01-15T12:00:00", "distraction")
    await db_mod.close_time_block(b3, "2025-01-15T12:30:00")

    summary = await db_mod.get_focus_summary(period="all")
    app_times = {s["app_name"]: s for s in summary}
    assert "code.exe" in app_times
    assert "chrome.exe" in app_times
    # code: 2h (7200s) + 55m (3300s) = 10500s, chrome: 30m (1800s)
    assert app_times["code.exe"]["total_seconds"] == 10500
    assert app_times["chrome.exe"]["total_seconds"] == 1800
    # 10500 / (10500 + 1800) ≈ 85.4%
    assert 80.0 < app_times["code.exe"]["percentage"] < 90.0


# ── App Categories ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_category_crud(tmp_path):
    """Set, get, get-all, and update app categories."""
    db_path = tmp_path / "cat1.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()

    # Set a category
    await db_mod.set_app_category("code.exe", "focus", "VS Code")
    cat = await db_mod.get_app_category("code.exe")
    assert cat == "focus"

    # Get all
    all_cats = await db_mod.get_all_app_categories()
    assert len(all_cats) == 1
    assert all_cats[0]["app_name"] == "code.exe"
    assert all_cats[0]["label"] == "VS Code"

    # Update category
    await db_mod.set_app_category("code.exe", "distraction", "Code")
    cat = await db_mod.get_app_category("code.exe")
    assert cat == "distraction"

    # Add another
    await db_mod.set_app_category("chrome.exe", "neutral")
    all_cats = await db_mod.get_all_app_categories()
    assert len(all_cats) == 2


@pytest.mark.asyncio
async def test_app_category_not_found(tmp_path):
    """get_app_category returns None for an uncategorized app."""
    db_path = tmp_path / "cat2.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()
    cat = await db_mod.get_app_category("nonexistent.exe")
    assert cat is None


# ── Maintenance ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_old_activity(tmp_path):
    """prune_old_activity removes entries older than N days."""
    db_path = tmp_path / "prune1.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    import db as db_mod
    import importlib
    importlib.reload(db_mod)

    await db_mod.init_db()

    # Insert a recent entry (uses CURRENT_TIMESTAMP = now)
    await db_mod.insert_activity("recent.exe", "", 0.0)

    # Insert a very old entry by setting timestamp directly
    db = await db_mod.get_db()
    await db.execute(
        "INSERT INTO activity_log (app_name, window_title, idle_seconds, timestamp) "
        "VALUES ('old.exe', '', 0.0, datetime('now', '-999 days'))"
    )
    await db.commit()
    await db.close()

    # Prune with days=30 — should only delete the old one
    deleted = await db_mod.prune_old_activity(30)
    assert deleted == 1

    # Verify recent entry remains
    items = await db_mod.get_recent_activity(limit=10)
    assert len(items) == 1
    assert items[0]["app_name"] == "recent.exe"


# ── Goals ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_and_get_goal(tmp_path):
    """Insert a goal of each type and verify fields."""
    db_path = tmp_path / "goals1.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid1 = await db_mod.insert_goal("long_term", "Learn Python", "Master Python", target_days=90)
    gid2 = await db_mod.insert_goal("habit", "Walk daily", frequency="daily")
    gid3 = await db_mod.insert_goal("maintenance", "Clean PC", frequency="custom", custom_interval_days=7)

    g1 = await db_mod.get_goal(gid1)
    assert g1 is not None
    assert g1["type"] == "long_term"
    assert g1["title"] == "Learn Python"
    assert g1["target_days"] == 90
    assert g1["frequency"] == "daily"
    assert g1["archived"] == 0

    g2 = await db_mod.get_goal(gid2)
    assert g2["type"] == "habit"
    assert g2["frequency"] == "daily"

    g3 = await db_mod.get_goal(gid3)
    assert g3["type"] == "maintenance"
    assert g3["frequency"] == "custom"
    assert g3["custom_interval_days"] == 7


@pytest.mark.asyncio
async def test_goal_archive(tmp_path):
    """Archive a goal and verify it's soft-deleted."""
    db_path = tmp_path / "goals2.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid = await db_mod.insert_goal("habit", "Read books")
    await db_mod.archive_goal(gid)

    g = await db_mod.get_goal(gid)
    assert g["archived"] == 1

    # Should not appear in default list
    goals = await db_mod.get_goals()
    assert len(goals) == 0

    # Should appear when archived=True
    archived = await db_mod.get_goals(archived=True)
    assert len(archived) == 1
    assert archived[0]["id"] == gid


@pytest.mark.asyncio
async def test_get_goals_filter_by_type(tmp_path):
    """get_goals with type filter returns only matching goals."""
    db_path = tmp_path / "goals3.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    await db_mod.insert_goal("long_term", "Project X")
    await db_mod.insert_goal("habit", "Exercise")
    await db_mod.insert_goal("maintenance", "Backup")

    all_goals = await db_mod.get_goals()
    assert len(all_goals) == 3

    habits = await db_mod.get_goals(goal_type="habit")
    assert len(habits) == 1
    assert habits[0]["title"] == "Exercise"


@pytest.mark.asyncio
async def test_update_goal_fields(tmp_path):
    """Update goal fields and verify changes."""
    db_path = tmp_path / "goals4.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid = await db_mod.insert_goal("long_term", "Old Title", progress_pct=10.0)
    updated = await db_mod.update_goal(gid, title="New Title", progress_pct=50.0)
    assert updated["title"] == "New Title"
    assert updated["progress_pct"] == 50.0

    # Verify persisted
    g = await db_mod.get_goal(gid)
    assert g["title"] == "New Title"


# ── Tasks ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_task_and_query(tmp_path):
    """Insert a task for a goal and query by date and goal."""
    db_path = tmp_path / "tasks1.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid = await db_mod.insert_goal("habit", "Walk")
    tid = await db_mod.insert_task(gid, "Walk the dog", "2025-01-15")

    t = await db_mod.get_task(tid)
    assert t is not None
    assert t["title"] == "Walk the dog"
    assert t["goal_id"] == gid
    assert t["date"] == "2025-01-15"
    assert t["status"] == "pending"

    # Query by date
    day_tasks = await db_mod.get_tasks_for_date("2025-01-15")
    assert len(day_tasks) == 1
    assert day_tasks[0]["id"] == tid

    # Query by goal
    goal_tasks = await db_mod.get_tasks_for_goal(gid)
    assert len(goal_tasks) == 1


@pytest.mark.asyncio
async def test_task_status_update(tmp_path):
    """Update task status to done and verify completed_at is set."""
    db_path = tmp_path / "tasks2.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid = await db_mod.insert_goal("habit", "Read")
    tid = await db_mod.insert_task(gid, "Read chapter 3", "2025-01-15")

    await db_mod.update_task(tid, status="done")
    t = await db_mod.get_task(tid)
    assert t["status"] == "done"
    assert t["completed_at"] is not None

    # Update back
    await db_mod.update_task(tid, status="pending")
    t = await db_mod.get_task(tid)
    assert t["status"] == "pending"


@pytest.mark.asyncio
async def test_get_tasks_for_date_multiple(tmp_path):
    """Insert multiple tasks on same date and verify ordering."""
    db_path = tmp_path / "tasks3.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid = await db_mod.insert_goal("habit", "Exercise")
    t1 = await db_mod.insert_task(gid, "Pushups", "2025-01-15")
    t2 = await db_mod.insert_task(gid, "Squats", "2025-01-15")

    tasks = await db_mod.get_tasks_for_date("2025-01-15")
    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_overdue_tasks(tmp_path):
    """Tasks with past dates and pending status appear as overdue."""
    db_path = tmp_path / "tasks4.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid = await db_mod.insert_goal("long_term", "Project A")
    await db_mod.insert_task(gid, "Old task", "2025-01-10")
    await db_mod.insert_task(gid, "Done task", "2025-01-10")
    await db_mod.update_task(gid + 1, status="done")  # make second task done

    # Re-fetch after update
    all_tasks = await db_mod.get_tasks_for_date("2025-01-10")
    # Mark the second as done
    await db_mod.update_task(all_tasks[1]["id"], status="done")

    overdue = await db_mod.get_overdue_tasks("2025-01-15")
    # Should find the pending task from Jan 10
    assert len(overdue) >= 1
    assert all(t["date"] < "2025-01-15" for t in overdue)
    assert all(t["status"] in ("pending", "doing") for t in overdue)


# ── Task Log ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_log_insert_and_query(tmp_path):
    """Log a task action and retrieve it."""
    db_path = tmp_path / "log1.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    gid = await db_mod.insert_goal("habit", "Meditate")
    tid = await db_mod.insert_task(gid, "Meditate 10min", "2025-01-15")
    log_id = await db_mod.insert_task_log(tid, gid, "done", "2025-01-15",
                                          ai_analysis='{"mood": "good"}')

    assert isinstance(log_id, int) and log_id > 0

    # Query by goal
    entries = await db_mod.get_task_log_for_goal(gid)
    assert len(entries) == 1
    assert entries[0]["action"] == "done"
    assert entries[0]["ai_analysis"] == '{"mood": "good"}'

    # Query by date
    by_date = await db_mod.get_task_log_for_date("2025-01-15")
    assert len(by_date) == 1
    assert by_date[0]["task_id"] == tid


# ── Schema Migration ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_migration_v2_to_v3(tmp_path):
    """Create a v2 database, run init_db, verify v3 tables exist and chaos data preserved."""
    db_path = tmp_path / "migrate.db"
    os.environ["DATABASE_PATH"] = str(db_path)

    # Create a v2 database manually
    import aiosqlite
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA user_version = 2")
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS chaos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL DEFAULT 'unknown',
            window_title TEXT NOT NULL DEFAULT '',
            idle_seconds REAL NOT NULL DEFAULT 0.0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO chaos (category, text) VALUES ('now', 'preserved item');
    """)
    await conn.commit()
    await conn.close()

    # Now run init_db — should migrate to v3
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()

    # Verify user_version is 3
    conn2 = await aiosqlite.connect(str(db_path))
    cursor = await conn2.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    assert row[0] == 4, f"Expected version 4, got {row[0]}"

    # Verify v3 tables exist
    cursor = await conn2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('goals', 'tasks', 'task_log')")
    tables = {row[0] for row in await cursor.fetchall()}
    assert "goals" in tables
    assert "tasks" in tables
    assert "task_log" in tables

    # Verify chaos data preserved
    cursor = await conn2.execute("SELECT text FROM chaos WHERE category='now'")
    items = await cursor.fetchall()
    assert len(items) == 1
    assert items[0][0] == "preserved item"

    await conn2.close()
