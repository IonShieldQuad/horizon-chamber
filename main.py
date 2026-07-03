"""Horizon Chamber — FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE other local imports so module-level os.getenv() calls
# in db.py and deepseek_client.py pick up the correct values.
load_dotenv()

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import deepseek_client
import feed as feed_module
import goal_engine
import monitor
import scheduler
import sse

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    logger.info("Database initialized at %s", db.DATABASE_PATH)
    if not deepseek_client.is_api_key_set():
        logger.warning("DEEPSEEK_API_KEY is not set — /api/classify will return 503")
    else:
        logger.info("DeepSeek API key is configured")

    # Load persisted sunrise schedule and start the background checker
    await scheduler.load_from_db()
    scheduler_task = asyncio.create_task(scheduler._check_schedule_loop())
    logger.info("Sunrise scheduler background task started")

    # Start the activity monitor if AUTO_START_MONITOR is true
    monitor_task = None
    if os.getenv("AUTO_START_MONITOR", "").lower() in ("true", "1", "yes"):
        monitor_task = asyncio.create_task(monitor.start_monitoring())
        logger.info("Activity monitor started (AUTO_START_MONITOR=true)")
    else:
        logger.info(
            "Activity monitor not started — set AUTO_START_MONITOR=true to enable"
        )

    # Start the goal engine background loop
    engine_task = asyncio.create_task(goal_engine.start_engine())
    logger.info("Goal engine background task started")

    # Start the feed scheduler background loop
    feed_task = asyncio.create_task(feed_module._scheduler_loop())
    logger.info("Feed scheduler background task started")

    yield

    # Shutdown: cancel background tasks
    engine_task.cancel()
    try:
        await engine_task
    except asyncio.CancelledError:
        pass

    if monitor_task is not None:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass

    # Cancel feed scheduler
    feed_task.cancel()
    try:
        await feed_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Horizon Chamber", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(exist_ok=True)

# Mount the static directory so /static/* files are served (favicon, icons, etc.)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Serve the index.html at the root
@app.get("/", include_in_schema=False)
async def serve_index():
    index_path = static_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Horizon Chamber</h1><p>Static frontend not found.</p>", status_code=200)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ClassifyRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, description="Raw text to classify")


class ScheduleRequest(BaseModel):
    enabled: bool = Field(..., description="Whether auto-sunrise is enabled")
    time: str = Field(..., min_length=4, max_length=5, description="Time in HH:MM 24h format")


class SetAppCategoryRequest(BaseModel):
    app_name: str = Field(..., min_length=1, description="Application name")
    category: str = Field(
        ..., description="Category: focus, distraction, neutral, idle, away"
    )
    label: str | None = Field(None, description="Optional user-friendly label")


# ── Goal & Task Schemas ──────────────────────────────────────────────────


class CreateGoalRequest(BaseModel):
    title: str = Field(..., min_length=1, description="Goal title")
    type: str | None = Field(None, description="Goal type: long_term, habit, maintenance")
    frequency: str | None = Field(None, description="Frequency: daily, weekly, custom")
    custom_interval_days: int | None = Field(None, ge=1, description="Days for custom frequency")
    target_days: int | None = Field(None, ge=1, description="Expected days to completion")
    description: str = Field("", description="Goal description")


class UpdateGoalRequest(BaseModel):
    type: str | None = Field(None, description="Goal type")
    title: str | None = Field(None, min_length=1, description="Goal title")
    description: str | None = None
    frequency: str | None = None
    custom_interval_days: int | None = None
    target_days: int | None = None
    progress_pct: float | None = Field(None, ge=0.0, le=100.0)
    paused: bool | None = None
    archived: bool | None = None
    sort_order: int | None = None


class UpdateTaskRequest(BaseModel):
    status: str | None = Field(
        None, description="Status: pending, doing, done, skipped"
    )
    title: str | None = Field(None, min_length=1, description="Task title")
    description: str | None = None
    sort_order: int | None = None
    date: str | None = Field(None, description="ISO date string, e.g. 2025-01-15")


class CreateTaskRequest(BaseModel):
    goal_id: int | None = Field(None, description="Parent goal id (auto-assigned to Inbox if omitted)")
    title: str = Field(..., min_length=1, description="Task title")


# ── Feed Schemas ─────────────────────────────────────────────────────────


class FeedProcessedItem(BaseModel):
    original_id: str = Field(..., description="Unique ID from the source")
    relevance_score: float = Field(0.0, ge=0.0, le=100.0)
    is_relevant: bool = False
    priority_level: int = Field(3, ge=1, le=5)
    category: str = ""
    one_liner: str = Field(..., min_length=1)
    sentiment_tone: str = "neutral"
    source_url: str = ""
    source_name: str = ""


class FeedIngestPayload(BaseModel):
    processed_items: list[FeedProcessedItem]
    total_relevant: int = 0
    summary_stats: dict = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    """Health check — reports whether DeepSeek API key is configured."""
    return {
        "status": "ok",
        "deepseek_key_set": deepseek_client.is_api_key_set(),
    }


def _get_time_color() -> str:
    """Return a hex color based on the current server hour."""
    hour = datetime.now().hour
    if 5 <= hour <= 8:
        return "#FFD700"      # Gold — sunrise
    elif 9 <= hour <= 16:
        return "#4A90E2"      # Blue — daylight
    elif 17 <= hour <= 20:
        return "#FF6B35"      # Orange — sunset
    else:
        return "#2B1B4A"      # Purple — night


@app.get("/api/time_color")
async def time_color():
    """Return the current time-based color."""
    return {"hex": _get_time_color()}


@app.post("/api/classify")
async def classify(req: ClassifyRequest):
    """Classify raw text via DeepSeek and persist results."""
    if not deepseek_client.is_api_key_set():
        raise HTTPException(
            status_code=503,
            detail="DEEPSEEK_API_KEY is not configured on the server",
        )

    try:
        result = await deepseek_client.classify_text(req.raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TimeoutError:
        raise HTTPException(
            status_code=504, detail="DeepSeek API request timed out"
        )
    except Exception as exc:
        logger.exception("Classification failed")
        raise HTTPException(
            status_code=502,
            detail=f"Classification failed: {exc}",
        )

    # Persist each classified item
    for category in ("now", "later", "trash"):
        for item in result.get(category, []):
            await db.insert_classification(category, item)

    return result


# ---------------------------------------------------------------------------
# Sunrise schedule endpoints
# ---------------------------------------------------------------------------


@app.get("/api/sunrise/schedule")
async def sunrise_schedule_get():
    """Return the current auto-sunrise schedule configuration."""
    return scheduler.get_schedule()


@app.put("/api/sunrise/schedule")
async def sunrise_schedule_put(req: ScheduleRequest):
    """Set the auto-sunrise schedule."""
    try:
        scheduler.set_schedule(enabled=req.enabled, time=req.time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return scheduler.get_schedule()


# ---------------------------------------------------------------------------
# Goal CRUD endpoints
# ---------------------------------------------------------------------------


@app.post("/api/goals")
async def create_goal(req: CreateGoalRequest):
    """Create a new goal. Auto-detect type via AI if not provided."""
    goal_type = req.type
    frequency = req.frequency
    custom_interval_days = req.custom_interval_days

    if goal_type is None and deepseek_client.is_api_key_set():
        try:
            ai_result = await deepseek_client.classify_as_goal(req.title)
            goal_type = ai_result.get("type", "long_term")
            if frequency is None:
                frequency = ai_result.get("frequency", "daily")
                custom_interval_days = ai_result.get("custom_interval_days")
        except Exception:
            goal_type = "long_term"
            frequency = req.frequency or "daily"
    elif goal_type is None:
        goal_type = "long_term"
        frequency = req.frequency or "daily"

    if goal_type not in ("long_term", "habit", "maintenance"):
        raise HTTPException(status_code=422, detail=f"Invalid goal type: {goal_type}")

    if frequency is None:
        frequency = "daily"

    gid = await db.insert_goal(
        goal_type=goal_type,
        title=req.title,
        description=req.description,
        frequency=frequency,
        custom_interval_days=custom_interval_days,
        target_days=req.target_days,
    )
    goal = await db.get_goal(gid)
    return goal


@app.get("/api/goals")
async def list_goals(
    type: str | None = None,
    archived: bool = False,
):
    """List goals with optional type and archived filters."""
    goals = await db.get_goals(goal_type=type, archived=archived, include_paused=True)
    return {"goals": goals}


@app.get("/api/goals/{goal_id}")
async def get_goal_by_id(goal_id: int):
    """Return a single goal with progress data."""
    goal = await db.get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    # Enrich with progress analysis
    try:
        analysis = await goal_engine.get_goal_analysis(goal_id)
        goal["analysis"] = analysis
    except Exception:
        goal["analysis"] = {}
    return goal


@app.put("/api/goals/{goal_id}")
async def update_goal(goal_id: int, req: UpdateGoalRequest):
    """Update one or more fields on a goal."""
    existing = await db.get_goal(goal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    kwargs = req.model_dump(exclude_unset=True)
    updated = await db.update_goal(goal_id, **kwargs)
    return updated


@app.delete("/api/goals/{goal_id}")
async def delete_goal(goal_id: int):
    """Soft-delete (archive) a goal."""
    existing = await db.get_goal(goal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    await db.archive_goal(goal_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Board & Task endpoints
# ---------------------------------------------------------------------------


@app.get("/api/board")
async def get_board(date: str | None = None):
    """Return the full kanban board state for today or a specific date."""
    board = await goal_engine.get_board(date)
    return board


@app.get("/api/today")
async def today_list():
    """Return today's tasks in priority order (simplified view)."""
    try:
        tasks = await goal_engine.get_today_list()
        from datetime import date as dt_date
        return {"tasks": tasks, "date": dt_date.today().isoformat()}
    except Exception:
        # Fallback: return legacy format
        items = await db.get_recent_now_items(limit=3)
        return {"tasks": [{"id": it["id"], "title": it["text"], "status": "pending"} for it in items], "date": ""}


@app.post("/api/tasks")
async def create_task(req: CreateTaskRequest):
    """Create a new task for today."""
    from datetime import date as dt_date
    # Resolve goal_id — auto-assign to Inbox if omitted
    goal_id = req.goal_id
    if goal_id is None:
        goal_id = await db.get_or_create_inbox_goal()
    else:
        goal = await db.get_goal(goal_id)
        if goal is None:
            raise HTTPException(status_code=404, detail="Goal not found")
    tid = await db.insert_task(
        goal_id=goal_id,
        title=req.title,
        date_str=dt_date.today().isoformat(),
    )
    task = await db.get_task(tid)
    return task


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, req: UpdateTaskRequest):
    """Update a task's status or other fields."""
    existing = await db.get_task(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Task not found")

    kwargs = req.model_dump(exclude_unset=True)
    await db.update_task(task_id, **kwargs)

    # Log the action if status changed
    if req.status:
        from datetime import date as dt_date
        await db.insert_task_log(
            task_id=task_id,
            goal_id=existing["goal_id"],
            action=req.status,
            date_str=dt_date.today().isoformat(),
        )

    updated = await db.get_task(task_id)
    return updated


@app.post("/api/tasks/{task_id}/split")
async def split_task(task_id: int):
    """AI-split a task into subtasks."""
    existing = await db.get_task(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if not deepseek_client.is_api_key_set():
        raise HTTPException(status_code=503, detail="AI key not configured")

    try:
        subtask_titles = await deepseek_client.split_task(existing["title"])
    except Exception as exc:
        logger.exception("Task split failed")
        raise HTTPException(status_code=502, detail=f"Split failed: {exc}")

    created = []
    for title in subtask_titles:
        tid = await db.insert_task(
            goal_id=existing["goal_id"],
            title=title,
            date_str=existing["date"],
            parent_task_id=task_id,
            ai_suggested=1,
        )
        created.append(await db.get_task(tid))

    # Log the split action
    from datetime import date as dt_date
    await db.insert_task_log(
        task_id=task_id,
        goal_id=existing["goal_id"],
        action="split",
        date_str=dt_date.today().isoformat(),
    )

    return {"subtasks": created}


@app.post("/api/tasks/{task_id}/suggest")
async def suggest_task(task_id: int):
    """AI-suggest a next step / frequency for the parent goal of a task."""
    existing = await db.get_task(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if not deepseek_client.is_api_key_set():
        raise HTTPException(status_code=503, detail="AI key not configured")

    goal = await db.get_goal(existing["goal_id"])
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")

    try:
        result = await deepseek_client.estimate_progress(
            goal_title=goal["title"],
            tasks_done=0,
            last_task_title=existing["title"],
            goal_type=goal["type"],
        )
    except Exception as exc:
        logger.exception("Suggestion failed")
        raise HTTPException(status_code=502, detail=f"Suggestion failed: {exc}")

    return result


# ---------------------------------------------------------------------------
# Analysis & engine control endpoints
# ---------------------------------------------------------------------------


@app.get("/api/analysis")
async def get_analysis():
    """Return performance signals."""
    signals = await goal_engine.compute_analysis()
    return signals


@app.get("/api/analysis/goal/{goal_id}")
async def get_goal_analysis_route(goal_id: int):
    """Return per-goal analysis."""
    analysis = await goal_engine.get_goal_analysis(goal_id)
    return analysis


@app.post("/api/analysis/refresh")
async def refresh_analysis():
    """Force re-run analysis."""
    # Analysis is computed fresh each time, so this is a no-op semantically
    signals = await goal_engine.compute_analysis()
    return signals


@app.post("/api/engine/generate")
async def engine_generate():
    """Manually trigger daily task generation."""
    from datetime import date as dt_date
    result = await goal_engine.generate_today_tasks(dt_date.today().isoformat())
    return result


@app.get("/api/engine/status")
async def engine_status():
    """Return goal engine status."""
    return goal_engine.get_engine_status()


# ---------------------------------------------------------------------------
# Board SSE stream
# ---------------------------------------------------------------------------


@app.get("/api/board/stream")
async def board_stream():
    """SSE stream delivering kanban board updates in real time."""

    async def event_source():
        last_version = 0
        while True:
            try:
                board = await goal_engine.get_board()
                # Compute a simple version hash to detect changes
                version = hash(str(board.get("stats", {})))
                if version != last_version:
                    last_version = version
                    yield ("board", board)
            except Exception:
                pass
            await asyncio.sleep(5)

    return sse.sse_response(event_source(), ping_interval=15.0)


# ---------------------------------------------------------------------------
# Activity tracking endpoints (v0.2)
# ---------------------------------------------------------------------------


@app.get("/api/activity/now")
async def activity_now():
    """Return the most recently observed activity."""
    activity = monitor.get_current_activity()
    if activity is None:
        return {
            "app_name": "unknown",
            "window_title": "",
            "idle_seconds": 0.0,
            "timestamp": datetime.utcnow().isoformat(),
        }
    return activity


@app.get("/api/activity/summary")
async def activity_summary(period: str = "today"):
    """Return aggregated focus summary for the given period."""
    result = await db.get_focus_summary(period)
    return {
        "period": period,
        "blocks": result,
    }


@app.get("/api/activity/stream")
async def activity_stream():
    """SSE stream delivering activity updates in real time."""

    async def event_source():
        last_data = None
        while True:
            current = monitor.get_current_activity()
            if current and current != last_data:
                last_data = dict(current)  # copy to avoid mutation
                yield ("activity", current)
            await asyncio.sleep(1)

    return sse.sse_response(event_source(), ping_interval=15.0)


@app.put("/api/activity/categories")
async def activity_categories_put(req: SetAppCategoryRequest):
    """Set an app's category and optional label."""
    valid = {"focus", "distraction", "neutral", "idle", "away"}
    if req.category not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category '{req.category}'. Must be one of: {', '.join(sorted(valid))}",
        )
    await db.set_app_category(req.app_name, req.category, req.label)
    return {"ok": True}


@app.get("/api/activity/categories")
async def activity_categories_get():
    """Return all categorized apps."""
    categories = await db.get_all_app_categories()
    return {"categories": categories}


@app.get("/api/activity/history")
async def activity_history(limit: int = 50):
    """Return recent activity log entries."""
    entries = await db.get_recent_activity(limit=limit)
    return {"entries": entries}


# ---------------------------------------------------------------------------
# Feed routes
# ---------------------------------------------------------------------------


def _verify_feed_api_key(authorization: str | None = None) -> bool:
    """Check the incoming Bearer token against FEED_API_KEY.

    If FEED_API_KEY is empty/unset, no auth is required (dev mode).
    """
    key = feed_module.FEED_API_KEY
    if not key:
        return True  # no key configured = allow all (dev mode)
    if not authorization:
        return False
    parts = authorization.split()
    return len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] == key


@app.post("/api/feed/ingest")
async def feed_ingest(
    payload: FeedIngestPayload,
    authorization: str | None = Header(None),
):
    """Receive feed data from n8n (authenticated with FEED_API_KEY)."""
    if not _verify_feed_api_key(authorization):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    try:
        result = await feed_module.ingest_payload(payload.model_dump())
        return result
    except Exception as exc:
        logger.exception("Feed ingest failed")
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}")


@app.get("/api/feed/items")
async def feed_list(
    dismissed: bool | None = False,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List feed items, newest first."""
    items = await feed_module.get_feed_items(
        dismissed=dismissed, category=category, limit=limit, offset=offset
    )
    return {"items": items, "count": len(items), "total_dismissed": await feed_module.get_dismissed_count()}


@app.get("/api/feed/items/{item_id}")
async def feed_get_item(item_id: int):
    """Return a single feed item by id."""
    item = await feed_module.get_feed_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Feed item not found")
    return item


@app.patch("/api/feed/items/{item_id}/dismiss")
async def feed_dismiss(item_id: int):
    """Dismiss a feed item."""
    ok = await feed_module.dismiss_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Feed item not found")
    return {"ok": True}


@app.patch("/api/feed/items/{item_id}/undismiss")
async def feed_undismiss(item_id: int):
    """Restore a dismissed feed item."""
    ok = await feed_module.undismiss_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Feed item not found")
    return {"ok": True}


@app.post("/api/feed/trigger")
async def feed_trigger():
    """Manually trigger an n8n fetch."""
    result = await feed_module.trigger_fetch(triggered_by="manual")
    if not result.get("ok"):
        raise HTTPException(status_code=429 if "cooldown" in result.get("message", "") else 502, detail=result["message"])
    return result


@app.get("/api/feed/stats")
async def feed_stats():
    """Return feed statistics and last run info."""
    stats = await feed_module.get_stats()
    return stats


@app.get("/api/feed/stream")
async def feed_stream():
    """SSE stream delivering feed events (new items, fetch status)."""

    async def event_source():
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        feed_module.register_sse_queue(q)
        try:
            while True:
                try:
                    event_name, data = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield (event_name, data)
                except asyncio.TimeoutError:
                    continue
        finally:
            feed_module.unregister_sse_queue(q)

    return sse.sse_response(event_source(), ping_interval=15.0)
