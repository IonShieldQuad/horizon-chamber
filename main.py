"""Horizon Chamber — FastAPI application entry point."""

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

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import deepseek_client
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

    # Start the sunrise scheduler background checker
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

    yield

    # Shutdown: cancel background tasks
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


@app.get("/api/today")
async def today():
    """Return the 3 most recent 'now' items."""
    items = await db.get_recent_now_items(limit=3)
    return {"items": [it["text"] for it in items]}


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
