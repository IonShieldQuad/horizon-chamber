"""Goal engine for Horizon Chamber v0.3 — dynamic kanban goals system.

Handles daily task generation, carry-over management, board assembly,
performance analysis, and nudges. Follows the scheduler.py pattern of
running as an asyncio background loop.
"""

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration (env-var overridable) ──────────────────────────────────

GOAL_REMINDER_GAP_DAYS = int(os.getenv("GOAL_REMINDER_GAP_DAYS", "3"))
GOAL_MAX_CARRY_OVER = int(os.getenv("GOAL_MAX_CARRY_OVER", "3"))
GOAL_CLEANUP_TIME = os.getenv("GOAL_CLEANUP_TIME", "04:00")
GOAL_GENERATE_ON_OPEN = os.getenv("GOAL_GENERATE_ON_OPEN", "true").lower() in ("true", "1")
NUDGES_ENABLED = os.getenv("NUDGES_ENABLED", "true").lower() in ("true", "1")

# ── In-memory state ──────────────────────────────────────────────────────

_generated_dates: set[str] = set()  # ISO date strings already generated today
_engine_running = False
_last_run: Optional[str] = None  # ISO datetime of last generation run
_last_cleanup: Optional[str] = None  # ISO datetime of last cleanup run

# Lazy import of db module (resolved at call time to allow test overrides)
_db_module = None


def _db():
    global _db_module
    if _db_module is None:
        import db as _db_module
    return _db_module


# ═══════════════════════════════════════════════════════════════════════════
#  Type-specific generation
# ═══════════════════════════════════════════════════════════════════════════


async def _generate_for_long_term(goal: dict, today_str: str) -> dict:
    """Generate a task for a long_term goal.

    Rules:
      - If no task in last N days → reminder
      - If last task was done → next occurrence
      - If last task was skipped/carried_over → copy, increment carry
      - If carry_over_count > threshold → flag
    """
    db = _db()
    last_task = await db.get_latest_task_for_goal(goal["id"])

    if last_task is None:
        # No tasks yet — create reminder
        title = f"{goal['title']} — start?"
        tid = await db.insert_task(goal["id"], title, today_str, ai_suggested=1)
        await db.insert_task_log(tid, goal["id"], "auto_generated", today_str)
        return {"created": 1, "carried_over": 0, "flagged": False}

    if last_task["status"] == "done":
        # Previous task done — create next step
        title = goal["title"]
        tid = await db.insert_task(goal["id"], title, today_str, ai_suggested=1)
        await db.insert_task_log(tid, goal["id"], "auto_generated", today_str)
        return {"created": 1, "carried_over": 0, "flagged": False}

    if last_task["status"] in ("skipped", "carried_over", "pending"):
        # Carry over with incremented count
        carry_count = last_task["carry_over_count"] + 1
        over_threshold = carry_count > GOAL_MAX_CARRY_OVER
        tid = await db.insert_task(
            goal["id"], last_task["title"], today_str,
            ai_suggested=1,
        )
        await db.update_task(tid, carry_over_count=carry_count)
        await db.insert_task_log(
            tid, goal["id"], "carried_over", today_str,
            ai_analysis=f'{{"carry_count": {carry_count}, "over_threshold": {str(over_threshold).lower()}}}',
        )
        return {"created": 1, "carried_over": 1, "flagged": over_threshold}

    return {"created": 0, "carried_over": 0, "flagged": False}


async def _generate_for_habit(goal: dict, today_str: str) -> dict:
    """Generate a task for a habit goal.

    If no task exists for today → create one.
    Previous day's incomplete tasks are handled by cleanup.
    """
    db = _db()
    today_tasks = await db.get_tasks_for_date(today_str)
    today_goal_tasks = [t for t in today_tasks if t["goal_id"] == goal["id"]]

    if today_goal_tasks:
        return {"created": 0, "carried_over": 0, "flagged": False}

    tid = await db.insert_task(
        goal["id"], goal["title"], today_str, ai_suggested=1,
    )
    await db.insert_task_log(tid, goal["id"], "auto_generated", today_str)
    return {"created": 1, "carried_over": 0, "flagged": False}


async def _generate_for_maintenance(goal: dict, today_str: str) -> dict:
    """Generate a task for a maintenance goal.

    Check interval (custom_interval_days or default). If today >= last + interval → create.
    """
    db = _db()
    last_task = await db.get_latest_task_for_goal(goal["id"])

    interval_days = goal["custom_interval_days"] or 1
    if goal["frequency"] == "weekly":
        interval_days = 7

    if last_task and last_task["date"]:
        # Parse last date
        try:
            last_date = datetime.strptime(last_task["date"], "%Y-%m-%d").date()
            today = datetime.strptime(today_str, "%Y-%m-%d").date()
            days_since = (today - last_date).days
            if days_since < interval_days:
                return {"created": 0, "carried_over": 0, "flagged": False}
        except ValueError:
            pass  # fall through to create

    tid = await db.insert_task(
        goal["id"], goal["title"], today_str, ai_suggested=1,
    )
    await db.insert_task_log(tid, goal["id"], "auto_generated", today_str)
    return {"created": 1, "carried_over": 0, "flagged": False}


# ═══════════════════════════════════════════════════════════════════════════
#  Cleanup
# ═══════════════════════════════════════════════════════════════════════════


async def _cleanup_yesterday(yesterday_str: str) -> dict:
    """Mark yesterday's incomplete habit and maintenance tasks as skipped.

    Long-term tasks are NOT cleaned — they carry over.
    Returns stats dict.
    """
    db = _db()
    yesterday_tasks = await db.get_tasks_for_date(yesterday_str)
    cleaned = 0
    skipped_ids = []

    for task in yesterday_tasks:
        if task["status"] in ("pending", "doing"):
            goal = await db.get_goal(task["goal_id"])
            if goal and goal["type"] in ("habit", "maintenance"):
                await db.update_task(task["id"], status="skipped")
                await db.insert_task_log(
                    task["id"], task["goal_id"], "skipped", yesterday_str,
                    ai_analysis='{"reason": "end_of_day_cleanup"}',
                )
                cleaned += 1
                skipped_ids.append(task["id"])

    return {"cleaned": cleaned, "skipped_ids": skipped_ids}


# ═══════════════════════════════════════════════════════════════════════════
#  Main generation entry point
# ═══════════════════════════════════════════════════════════════════════════


async def generate_today_tasks(today_str: Optional[str] = None) -> dict:
    """Generate tasks for today across all active goals.

    Args:
        today_str: ISO date string. Defaults to today.

    Returns:
        dict with keys: created, carried_over, flagged, goals_processed
    """
    if today_str is None:
        today_str = date.today().isoformat()

    if today_str in _generated_dates:
        logger.info("Tasks already generated for %s — skipping", today_str)
        return {"created": 0, "carried_over": 0, "flagged": 0, "goals_processed": 0}

    db = _db()
    goals = await db.get_goals()

    total_created = 0
    total_carried = 0
    total_flagged = 0
    goals_processed = 0

    for goal in goals:
        gen_func = None
        if goal["type"] == "long_term":
            gen_func = _generate_for_long_term
        elif goal["type"] == "habit":
            gen_func = _generate_for_habit
        elif goal["type"] == "maintenance":
            gen_func = _generate_for_maintenance

        if gen_func:
            try:
                result = await gen_func(goal, today_str)
                total_created += result["created"]
                total_carried += result["carried_over"]
                if result["flagged"]:
                    total_flagged += 1
                goals_processed += 1
            except Exception as exc:
                logger.error("Failed to generate for goal %d (%s): %s",
                             goal["id"], goal["title"], exc)

    _generated_dates.add(today_str)
    global _last_run
    _last_run = datetime.utcnow().isoformat()

    logger.info("Generated %d tasks (%d carried, %d flagged) for %s across %d goals",
                total_created, total_carried, total_flagged, today_str, goals_processed)

    return {
        "created": total_created,
        "carried_over": total_carried,
        "flagged": total_flagged,
        "goals_processed": goals_processed,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Board assembly
# ═══════════════════════════════════════════════════════════════════════════


async def get_board(date_str: Optional[str] = None) -> dict:
    """Assemble the full kanban board state for a given date.

    Args:
        date_str: ISO date string. Defaults to today.

    Returns:
        dict with 'columns', 'goals', 'stats' keys.
    """
    if date_str is None:
        date_str = date.today().isoformat()

    db = _db()

    # Lazy generation if enabled
    if GOAL_GENERATE_ON_OPEN and date_str not in _generated_dates:
        try:
            await generate_today_tasks(date_str)
        except Exception as exc:
            logger.warning("Lazy generation failed: %s", exc)

    # Query tasks for the date
    today_tasks = await db.get_tasks_for_date(date_str)
    all_goals = await db.get_goals()
    overdue_tasks = await db.get_overdue_tasks(date_str)

    # Filter out overdue tasks that are already in today_tasks
    today_task_ids = {t["id"] for t in today_tasks}
    overdue_unique = [t for t in overdue_tasks if t["id"] not in today_task_ids]

    # Assemble columns
    today_column = [t for t in today_tasks if t["status"] in ("pending", "doing")]
    doing_column = [t for t in today_tasks if t["status"] == "doing"]
    done_column = [t for t in today_tasks if t["status"] == "done"]
    overdue_column = overdue_unique

    # Compute stats
    total_tasks = len(today_tasks)
    done_count = len(done_column)
    pending_count = sum(1 for t in today_tasks if t["status"] == "pending")
    doing_count = len(doing_column)
    skipped_count = sum(1 for t in today_tasks if t["status"] == "skipped")

    # Annotate tasks with goal info
    goal_map = {g["id"]: g for g in all_goals}

    def annotate_task(t):
        goal = goal_map.get(t["goal_id"])
        return {
            **t,
            "goal_type": goal["type"] if goal else None,
            "goal_title": goal["title"] if goal else None,
        }

    # Build columns with annotations
    board_columns = {
        "today": [annotate_task(t) for t in today_column],
        "doing": [annotate_task(t) for t in doing_column],
        "done": [annotate_task(t) for t in done_column],
        "overdue": [annotate_task(t) for t in overdue_column],
    }

    stats = {
        "total_tasks": total_tasks,
        "done": done_count,
        "pending": pending_count,
        "doing": doing_count,
        "skipped": skipped_count,
        "active_goals": len([g for g in all_goals if not g["archived"]]),
        "completion_rate": round(done_count / total_tasks * 100, 1) if total_tasks > 0 else 0.0,
    }

    return {
        "date": date_str,
        "columns": board_columns,
        "goals": all_goals,
        "stats": stats,
    }


async def get_today_list() -> list[dict]:
    """Return today's tasks in priority order (simplified view)."""
    db = _db()
    today_str = date.today().isoformat()

    # Trigger lazy generation if needed
    if GOAL_GENERATE_ON_OPEN and today_str not in _generated_dates:
        try:
            await generate_today_tasks(today_str)
        except Exception:
            pass

    tasks = await db.get_tasks_for_date(today_str)

    # Annotate with goal info
    result = []
    for t in tasks:
        goal = await db.get_goal(t["goal_id"])
        result.append({
            "id": t["id"],
            "goal_id": t["goal_id"],
            "title": t["title"],
            "status": t["status"],
            "date": t["date"],
            "goal_type": goal["type"] if goal else None,
            "goal_title": goal["title"] if goal else None,
            "carry_over_count": t["carry_over_count"],
        })

    return result


# ═══════════════════════════════════════════════════════════════════════════
#  Analysis engine
# ═══════════════════════════════════════════════════════════════════════════


async def compute_analysis(date_str: Optional[str] = None) -> dict:
    """Compute performance signals for a given date.

    Returns structured dict with all detected signals.
    """
    if date_str is None:
        date_str = date.today().isoformat()

    db = _db()

    # Tasks for the day
    day_tasks = await db.get_tasks_for_date(date_str)
    total = len(day_tasks)
    done = sum(1 for t in day_tasks if t["status"] == "done")
    skipped = sum(1 for t in day_tasks if t["status"] == "skipped")
    pending = sum(1 for t in day_tasks if t["status"] == "pending")

    # Completion rate
    denominator = done + skipped
    completion_rate = round(done / denominator * 100, 1) if denominator > 0 else 0.0

    # Carry-over depth (max across all active tasks)
    active_tasks = [t for t in day_tasks if t["status"] in ("pending", "doing")]
    carry_over_depth = max((t["carry_over_count"] for t in active_tasks), default=0)

    # Carry-over breadth
    carried_count = sum(1 for t in day_tasks if t["status"] == "carried_over")
    total_pending = done + pending + skipped + carried_count
    carry_over_breadth = round(carried_count / total_pending * 100, 1) if total_pending > 0 else 0.0

    # Per-goal stagnation
    goals = await db.get_goals()
    stagnation = {}
    today = datetime.strptime(date_str, "%Y-%m-%d").date()
    for goal in goals:
        if goal["type"] == "long_term":
            tasks_for_goal = await db.get_tasks_for_goal(goal["id"])
            latest_done = None
            for t in tasks_for_goal:
                if t["status"] == "done":
                    try:
                        latest_done = datetime.strptime(t["date"], "%Y-%m-%d").date()
                    except ValueError:
                        pass
                    break
            if latest_done:
                stagnation_days = (today - latest_done).days
                if stagnation_days > 0:
                    stagnation[goal["id"]] = {
                        "goal_title": goal["title"],
                        "days_since_last_done": stagnation_days,
                    }

    # Overdue accumulation
    overdue_tasks = await db.get_overdue_tasks(date_str)
    overdue_count = len(overdue_tasks)

    # Nudges
    nudges = await _generate_nudges({
        "completion_rate": completion_rate,
        "carry_over_depth": carry_over_depth,
        "carry_over_breadth": carry_over_breadth,
        "stagnation": stagnation,
        "overdue_count": overdue_count,
        "total_tasks": total,
        "done": done,
        "pending": pending,
        "skipped": skipped,
    })

    return {
        "date": date_str,
        "completion_rate": completion_rate,
        "carry_over_depth": carry_over_depth,
        "carry_over_breadth": carry_over_breadth,
        "overdue_count": overdue_count,
        "stagnation": stagnation,
        "nudges": nudges,
        "stats": {
            "total": total,
            "done": done,
            "pending": pending,
            "skipped": skipped,
        },
    }


async def get_goal_analysis(goal_id: int) -> dict:
    """Per-goal analysis: progress, days active, tasks done/skipped, trend."""
    db = _db()
    goal = await db.get_goal(goal_id)
    if goal is None:
        return {}

    tasks = await db.get_tasks_for_goal(goal_id)
    done_count = sum(1 for t in tasks if t["status"] == "done")
    skipped_count = sum(1 for t in tasks if t["status"] == "skipped")
    total_count = len(tasks)

    # Days active
    if tasks:
        try:
            first_date = datetime.strptime(tasks[-1]["date"], "%Y-%m-%d").date()
            today = date.today()
            days_active = (today - first_date).days + 1
        except (ValueError, IndexError):
            days_active = 0
    else:
        days_active = 0

    return {
        "goal_id": goal_id,
        "goal_title": goal["title"],
        "goal_type": goal["type"],
        "progress_pct": goal["progress_pct"],
        "tasks_total": total_count,
        "tasks_done": done_count,
        "tasks_skipped": skipped_count,
        "days_active": days_active,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Nudge system
# ═══════════════════════════════════════════════════════════════════════════


async def _generate_nudges(analysis: dict) -> list[dict]:
    """Generate passive nudges based on analysis signals.

    Returns list of nudge dicts with level, text, and optional action.
    """
    if not NUDGES_ENABLED:
        return []

    nudges = []

    # Info: completion rate >85% (coast) or <50% (drowning)
    rate = analysis.get("completion_rate", 0.0)
    if rate > 85.0 and analysis.get("total_tasks", 0) > 0:
        nudges.append({
            "level": "info",
            "text": f"You're at {rate}% this week. Room for a new goal?",
            "action": "add_goal",
            "dismissable": True,
        })
    elif rate < 50.0 and analysis.get("total_tasks", 0) > 0:
        nudges.append({
            "level": "info",
            "text": f"Only {rate}% done today. Try tackling one small task.",
            "action": None,
            "dismissable": True,
        })

    # Suggestion: carry_over_depth > 3 on a single goal
    depth = analysis.get("carry_over_depth", 0)
    if depth > GOAL_MAX_CARRY_OVER:
        # Find the goal with highest carry-over
        nudges.append({
            "level": "suggestion",
            "text": f"A task has been carried over {depth} times. Consider splitting it into smaller steps.",
            "action": "split_task",
            "dismissable": True,
        })

    # Alert: carry_over_breadth > 50%
    breadth = analysis.get("carry_over_breadth", 0)
    if breadth > 50.0:
        nudges.append({
            "level": "alert",
            "text": f"{breadth}% of tasks are carried over. Maybe pause a goal?",
            "action": "pause_goal",
            "dismissable": True,
        })

    # Stagnation alert
    stagnation = analysis.get("stagnation", {})
    for goal_id, info in stagnation.items():
        days = info.get("days_since_last_done", 0)
        if days > 7:
            nudges.append({
                "level": "suggestion",
                "text": f"\"{info['goal_title']}\" hasn't seen progress in {days} days. Need a reminder?",
                "action": "remind",
                "goal_id": goal_id,
                "dismissable": True,
            })

    # Overdue accumulation
    overdue_count = analysis.get("overdue_count", 0)
    if overdue_count >= 3:
        nudges.append({
            "level": "alert",
            "text": f"{overdue_count} tasks are overdue. Want to clear them?",
            "action": "show_overdue",
            "dismissable": True,
        })

    return nudges[:3]  # max 3 nudges per analysis


# ═══════════════════════════════════════════════════════════════════════════
#  Background engine loop
# ═══════════════════════════════════════════════════════════════════════════


async def start_engine() -> None:
    """Start the background generation loop. Runs until cancelled.

    - Runs cleanup at GOAL_CLEANUP_TIME daily
    - Periodically checks if today needs generation (lazy trigger)
    """
    global _engine_running
    if _engine_running:
        logger.warning("Goal engine is already running")
        return

    _engine_running = True
    logger.info("Goal engine started (cleanup at %s)", GOAL_CLEANUP_TIME)

    last_cleanup_date = ""

    try:
        while True:
            now = datetime.now()
            today_str = now.date().isoformat()

            # Check if cleanup should run
            current_time = f"{now.hour:02d}:{now.minute:02d}"
            if current_time == GOAL_CLEANUP_TIME and last_cleanup_date != today_str:
                yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                try:
                    result = await _cleanup_yesterday(yesterday)
                    logger.info("Cleanup complete: %d tasks skipped", result["cleaned"])
                    global _last_cleanup
                    _last_cleanup = datetime.utcnow().isoformat()
                    last_cleanup_date = today_str
                except Exception as exc:
                    logger.error("Cleanup failed: %s", exc)

            await asyncio.sleep(60)  # Check every 60 seconds

    except asyncio.CancelledError:
        logger.info("Goal engine cancelled")
    finally:
        _engine_running = False


def is_engine_running() -> bool:
    """Return whether the engine background task is active."""
    return _engine_running


def get_engine_status() -> dict:
    """Return engine status dict."""
    today_str = date.today().isoformat()
    return {
        "running": _engine_running,
        "last_run": _last_run,
        "last_cleanup": _last_cleanup,
        "today_generated": today_str in _generated_dates,
    }


def reset_generated_today() -> None:
    """Clear the generated-today set (for testing or manual reset)."""
    _generated_dates.clear()
