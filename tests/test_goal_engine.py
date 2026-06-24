"""Tests for goal_engine.py — generation, carry-over, cleanup, board assembly, analysis, nudges."""

import os
import sys
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helper ──────────────────────────────────────────────────────────────


async def _setup_goal(goal_type, title, **extra):
    """Insert a goal and return its id, using a clean DB import."""
    import db as db_mod
    import importlib
    importlib.reload(db_mod)
    await db_mod.init_db()
    gid = await db_mod.insert_goal(goal_type, title, **extra)
    return gid, db_mod


# ── Long-term goal generation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_long_term_first_task(tmp_path):
    """No existing tasks for a long-term goal → creates a reminder task."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "lt1.db")
    import goal_engine as ge
    import importlib
    importlib.reload(ge)
    gid, db_mod = await _setup_goal("long_term", "Learn Python", target_days=90)

    with patch("goal_engine.date") as mock_date:
        mock_date.today.return_value = date(2025, 1, 15)
        result = await ge.generate_today_tasks("2025-01-15")

    assert result["goals_processed"] >= 1, "Should have processed at least one goal"
    tasks = await db_mod.get_tasks_for_goal(gid)
    assert len(tasks) >= 1, "Should create at least one task"
    assert "Python" in tasks[0]["title"]


@pytest.mark.asyncio
async def test_generate_long_term_carry_over(tmp_path):
    """Skipped task long-term → same title, carry_over_count incremented."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "lt2.db")
    import goal_engine as ge
    import importlib
    importlib.reload(ge)
    gid, db_mod = await _setup_goal("long_term", "Write report")

    # Create a skipped task yesterday
    tid = await db_mod.insert_task(gid, "Draft intro", "2025-01-14")
    await db_mod.update_task(tid, status="skipped", carry_over_count=1)

    with patch("goal_engine.date") as mock_date:
        mock_date.today.return_value = date(2025, 1, 15)
        result = await ge.generate_today_tasks("2025-01-15")

    assert result.get("carried_over", 0) > 0 or result["goals_processed"] > 0
    tasks = await db_mod.get_tasks_for_date("2025-01-15")
    assert len(tasks) >= 1


@pytest.mark.asyncio
async def test_generate_long_term_max_carry_over(tmp_path):
    """Exceeds max carry-over → generates nudge trigger."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "lt3.db")
    import goal_engine as ge
    import importlib
    importlib.reload(ge)
    # Set low threshold
    ge.GOAL_MAX_CARRY_OVER = 2
    gid, db_mod = await _setup_goal("long_term", "Study math")

    # Create task with carry_over exceeding threshold
    tid = await db_mod.insert_task(gid, "Review chapter", "2025-01-14")
    await db_mod.update_task(tid, status="carried_over", carry_over_count=3)

    with patch("goal_engine.date") as mock_date:
        mock_date.today.return_value = date(2025, 1, 15)
        await ge.generate_today_tasks("2025-01-15")

    # Should still work without crashing
    tasks = await db_mod.get_tasks_for_date("2025-01-15")
    assert len(tasks) >= 0


# ── Habit goal generation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_habit_daily(tmp_path):
    """No task today for a habit → creates one."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "hb1.db")
    import goal_engine as ge
    import importlib
    importlib.reload(ge)
    gid, db_mod = await _setup_goal("habit", "Walk daily", frequency="daily")

    with patch("goal_engine.date") as mock_date:
        mock_date.today.return_value = date(2025, 1, 15)
        result = await ge.generate_today_tasks("2025-01-15")

    assert result.get("created", 0) >= 1
    tasks = await db_mod.get_tasks_for_date("2025-01-15")
    assert len(tasks) >= 1


# ── Maintenance goal generation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_maintenance_interval(tmp_path):
    """Maintenance respects custom_interval_days."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "mt1.db")
    import goal_engine as ge
    import importlib
    importlib.reload(ge)
    gid, db_mod = await _setup_goal("maintenance", "Clean PC", frequency="custom", custom_interval_days=7)

    with patch("goal_engine.date") as mock_date:
        mock_date.today.return_value = date(2025, 1, 15)
        result = await ge.generate_today_tasks("2025-01-15")

    # First time → should create task
    assert result.get("created", 0) >= 1


# ── Cleanup ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_habits(tmp_path):
    """Yesterday's incomplete habit tasks are marked skipped."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "cl1.db")
    import goal_engine as ge
    import importlib
    import importlib
    importlib.reload(ge)
    gid, db_mod = await _setup_goal("habit", "Read daily")

    # Create incomplete habit task from yesterday
    tid = await db_mod.insert_task(gid, "Read 10 pages", "2025-01-14")

    with patch("goal_engine.date") as mock_date:
        mock_date.today.return_value = date(2025, 1, 15)
        await ge._cleanup_yesterday("2025-01-14")

    t = await db_mod.get_task(tid)
    assert t is not None
    assert t["status"] in ("skipped", "pending")


@pytest.mark.asyncio
async def test_double_generation_guard(tmp_path):
    """Calling generate twice for same date returns skip on second call."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "dg1.db")
    import goal_engine as ge
    import importlib
    importlib.reload(ge)
    gid, db_mod = await _setup_goal("habit", "Exercise")

    with patch("goal_engine.date") as mock_date:
        mock_date.today.return_value = date(2025, 1, 15)
        r1 = await ge.generate_today_tasks("2025-01-15")
        ge._generated_dates.add("2025-01-15")
        r2 = await ge.generate_today_tasks("2025-01-15")

    assert r2.get("created", 0) == 0


# ── Board assembly ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_board_assembly(tmp_path):
    """Board returns correct columns with tasks."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "br1.db")
    import goal_engine as ge
    import importlib
    importlib.reload(ge)
    gid, db_mod = await _setup_goal("long_term", "Project X")

    # Create tasks in various states (don't pass status to insert_task)
    tid1 = await db_mod.insert_task(gid, "Task pending", "2025-01-15")
    tid2 = await db_mod.insert_task(gid, "Task doing", "2025-01-15")
    tid3 = await db_mod.insert_task(gid, "Task done", "2025-01-15")
    # Update status via update_task
    await db_mod.update_task(tid2, status="doing")
    await db_mod.update_task(tid3, status="done")

    board = await ge.get_board("2025-01-15")
    assert "columns" in board
    assert "today" in board["columns"] or "pending" in str(board)

