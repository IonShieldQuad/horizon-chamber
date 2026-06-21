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
