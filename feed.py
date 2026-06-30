"""Feed module — n8n summarization scheduler and data management.

Follows the scheduler.py pattern: runs as an asyncio background loop,
manages in-memory state for cooldowns, and persists data via db.py.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any

import httpx

import db

logger = logging.getLogger(__name__)

# ── Configuration (env-var overridable) ──────────────────────────────────

N8N_WEBHOOK_URL = os.getenv(
    "N8N_WEBHOOK_URL",
    "http://localhost:5678/webhook/3506aa26-ed0a-4bc9-ac7f-3f8ee9c5e918",
)
N8N_API_KEY = os.getenv("N8N_API_KEY", "")
FEED_API_KEY = os.getenv("FEED_API_KEY", "")
FEED_POLL_INTERVAL_HOURS = int(os.getenv("FEED_POLL_INTERVAL_HOURS", "6"))
FEED_AUTO_FETCH = (
    os.getenv("FEED_AUTO_FETCH", "true").lower() in ("true", "1", "yes")
)
FEED_SCHEDULER_INTERVAL = int(os.getenv("FEED_SCHEDULER_INTERVAL", "60"))

# ── In-memory state ──────────────────────────────────────────────────────

_fetch_in_progress: bool = False
_last_fetch_time: datetime | None = None
_last_manual_fetch: datetime | None = None
_run_id: int | None = None  # current feed run id during a fetch

# SSE event broadcasting — asyncio Queue for new-item events
_sse_queues: list[asyncio.Queue] = []


# ── SSE helpers ──────────────────────────────────────────────────────────


def register_sse_queue(q: asyncio.Queue) -> None:
    """Register an asyncio.Queue to receive feed events."""
    _sse_queues.append(q)


def unregister_sse_queue(q: asyncio.Queue) -> None:
    """Remove a previously registered SSE queue."""
    if q in _sse_queues:
        _sse_queues.remove(q)


async def _broadcast_event(event: str, data: Any) -> None:
    """Push an event to all registered SSE listeners."""
    dead: list[asyncio.Queue] = []
    for q in _sse_queues:
        try:
            q.put_nowait((event, data))
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        unregister_sse_queue(q)


# ── Public API ───────────────────────────────────────────────────────────


async def get_feed_items(
    dismissed: bool | None = False,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Return feed items from the database, newest first."""
    return await db.get_feed_items(
        dismissed=dismissed, category=category, limit=limit, offset=offset
    )


async def get_feed_item(item_id: int) -> dict | None:
    """Return a single feed item by id."""
    return await db.get_feed_item(item_id)


async def dismiss_item(item_id: int) -> bool:
    """Dismiss a feed item. Returns True if the item existed."""
    return await db.dismiss_feed_item(item_id, dismissed=True)


async def undismiss_item(item_id: int) -> bool:
    """Restore a previously dismissed feed item."""
    return await db.dismiss_feed_item(item_id, dismissed=False)


async def get_dismissed_count() -> int:
    """Return the number of dismissed items."""
    return await db.get_dismissed_count()


async def get_last_run_info() -> dict | None:
    """Return info about the most recent fetch run."""
    return await db.get_last_feed_run()


async def get_stats() -> dict:
    """Return aggregate feed statistics."""
    return await db.get_feed_stats()


def is_fetch_in_progress() -> bool:
    """Return True if a fetch is currently running."""
    return _fetch_in_progress


def can_manual_fetch() -> bool:
    """Return True if a manual fetch is allowed (cooldown check)."""
    if _fetch_in_progress:
        return False
    if _last_manual_fetch is None:
        return True
    elapsed = (datetime.now() - _last_manual_fetch).total_seconds()
    return elapsed >= 1800  # 30-minute cooldown


def get_next_scheduled_fetch() -> str | None:
    """Return the estimated next scheduled fetch time as ISO string, or None."""
    if _last_fetch_time is None:
        return None
    from datetime import timedelta

    next_time = _last_fetch_time + timedelta(hours=FEED_POLL_INTERVAL_HOURS)
    return next_time.isoformat()


# ── Ingest: called by POST /api/feed/ingest ──────────────────────────────


async def ingest_payload(payload: dict) -> dict:
    """Parse, validate, and persist an n8n payload.

    Returns a summary::
        {"ingested": N, "skipped_duplicates": M, "total_relevant": R}
    """
    processed = payload.get("processed_items", [])
    total_relevant = payload.get("total_relevant", 0)

    ingested = 0
    skipped = 0

    run_id = None
    try:
        last_run = await db.get_last_feed_run()
        if last_run and last_run["status"] == "running":
            run_id = last_run["id"]
    except Exception:
        pass

    for item in processed:
        original_id = str(item.get("original_id", ""))
        if not original_id:
            continue

        item_id = await db.insert_feed_item(
            original_id=original_id,
            one_liner=str(item.get("one_liner", "")),
            relevance_score=float(item.get("relevance_score", 0.0)),
            is_relevant=bool(item.get("is_relevant", False)),
            priority_level=int(item.get("priority_level", 50)),
            category=str(item.get("category", "")),
            sentiment_tone=str(item.get("sentiment_tone", "neutral")),
            source_url=str(item.get("source_url", "")),
            source_name=str(item.get("source_name", "")),
            feed_run_id=run_id,
        )
        if item_id is not None:
            ingested += 1
            # Broadcast new item to SSE listeners
            try:
                new_item = await db.get_feed_item(item_id)
                if new_item:
                    await _broadcast_event("feed_item", new_item)
            except Exception:
                pass
        else:
            skipped += 1

    # Update the feed run stats if we have a run_id
    if run_id and ingested > 0:
        try:
            from datetime import datetime

            now_str = datetime.utcnow().isoformat()
            items = await db.get_feed_items(dismissed=None, limit=10000)
            avg_rel = 0.0
            if items:
                avg_rel = sum(
                    it.get("relevance_score", 0.0) for it in items
                ) / len(items)
            await db.update_feed_run(
                run_id,
                completed_at=now_str,
                total_items=ingested + skipped,
                total_relevant=total_relevant,
                average_relevance=round(avg_rel, 2),
                status="completed",
            )
        except Exception as exc:
            logger.warning("Could not update feed run stats: %s", exc)

    return {
        "ingested": ingested,
        "skipped_duplicates": skipped,
        "total_relevant": total_relevant,
    }


# ── Trigger: called by POST /api/feed/trigger or scheduler ───────────────


async def trigger_fetch(triggered_by: str = "schedule") -> dict:
    """Trigger the n8n workflow via webhook.

    Returns:
        {"ok": True, "message": "..."} on success,
        {"ok": False, "message": "..."} on error or rate-limited.
    """
    global _fetch_in_progress, _last_fetch_time, _last_manual_fetch, _run_id

    if _fetch_in_progress:
        return {"ok": False, "message": "Fetch already in progress"}

    if triggered_by == "manual":
        if not can_manual_fetch():
            remaining = int(
                1800 - (datetime.now() - _last_manual_fetch).total_seconds()
            )
            return {
                "ok": False,
                "message": f"Manual fetch on cooldown. Try again in {remaining // 60}m {remaining % 60}s.",
            }
        _last_manual_fetch = datetime.now()

    _fetch_in_progress = True

    try:
        # Create a feed run record
        _run_id = await db.insert_feed_run(triggered_by=triggered_by)

        # Mark as running
        await db.update_feed_run(_run_id, status="running")

        # Call n8n webhook
        headers = {"Content-Type": "application/json"}
        if N8N_API_KEY:
            headers["Authorization"] = f"Bearer {N8N_API_KEY}"

        body = {"trigger_source": "horizon", "feed_type": "all"}

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                N8N_WEBHOOK_URL, json=body, headers=headers
            )

        if response.status_code >= 400:
            error_detail = f"n8n returned {response.status_code}: {response.text[:200]}"
            await db.update_feed_run(_run_id, status="failed")
            logger.error(error_detail)
            return {"ok": False, "message": error_detail}

        _last_fetch_time = datetime.now()
        logger.info(
            "n8n workflow triggered successfully (%s)", triggered_by
        )

        # Broadcast a trigger event (frontend can use this to know a fetch started)
        await _broadcast_event(
            "feed_fetch_started",
            {
                "triggered_by": triggered_by,
                "run_id": _run_id,
                "started_at": _last_fetch_time.isoformat(),
            },
        )

        return {"ok": True, "message": "n8n workflow triggered"}

    except httpx.RequestError as exc:
        error_msg = f"Could not reach n8n: {exc}"
        logger.error(error_msg)
        if _run_id:
            try:
                await db.update_feed_run(_run_id, status="failed")
            except Exception:
                pass
        return {"ok": False, "message": error_msg}
    except Exception as exc:
        error_msg = f"Failed to trigger n8n: {exc}"
        logger.exception(error_msg)
        if _run_id:
            try:
                await db.update_feed_run(_run_id, status="failed")
            except Exception:
                pass
        return {"ok": False, "message": error_msg}
    finally:
        _fetch_in_progress = False
        _run_id = None


# ── Background scheduler loop ────────────────────────────────────────────


async def _scheduler_loop(interval: float | None = None) -> None:
    """Periodically check whether it's time to fetch and auto-trigger.

    Also triggers on startup if FEED_AUTO_FETCH is true and the last
    fetch was more than FEED_POLL_INTERVAL_HOURS ago.
    """
    if interval is None:
        interval = float(FEED_SCHEDULER_INTERVAL)
    logger.info(
        "Feed scheduler background task started "
        "(interval=%ss, poll_every=%sh, auto_fetch=%s)",
        interval,
        FEED_POLL_INTERVAL_HOURS,
        FEED_AUTO_FETCH,
    )

    # Startup: check if we should auto-fetch
    if FEED_AUTO_FETCH:
        try:
            last_run = await db.get_last_feed_run()
            should_fetch = True
            if last_run and last_run["completed_at"]:
                from datetime import timedelta

                try:
                    last_time = datetime.fromisoformat(last_run["completed_at"])
                    elapsed = (datetime.now() - last_time).total_seconds()
                    if elapsed < FEED_POLL_INTERVAL_HOURS * 3600:
                        should_fetch = False
                except (ValueError, TypeError):
                    pass

            if should_fetch:
                logger.info(
                    "Feed auto-fetch on startup (last run older than %sh or absent)",
                    FEED_POLL_INTERVAL_HOURS,
                )
                result = await trigger_fetch(triggered_by="startup")
                if not result.get("ok"):
                    logger.warning("Startup auto-fetch: %s", result.get("message"))
            else:
                logger.info(
                    "Skipping startup auto-fetch (recent run exists)"
                )
        except Exception as exc:
            logger.warning("Startup auto-fetch check failed: %s", exc)

    # Main loop
    try:
        while True:
            await asyncio.sleep(interval)

            try:
                last_run = await db.get_last_feed_run()
                if last_run and last_run.get("completed_at"):
                    from datetime import timedelta

                    try:
                        last_time = datetime.fromisoformat(
                            last_run["completed_at"]
                        )
                        elapsed = (datetime.now() - last_time).total_seconds()
                        if elapsed >= FEED_POLL_INTERVAL_HOURS * 3600:
                            logger.info(
                                "Feed scheduler: time elapsed, triggering fetch"
                            )
                            result = await trigger_fetch(triggered_by="schedule")
                            if not result.get("ok"):
                                logger.warning(
                                    "Scheduled fetch: %s", result.get("message")
                                )
                    except (ValueError, TypeError):
                        pass
                elif last_run is None:
                    # No runs at all — trigger one
                    logger.info("Feed scheduler: no runs yet, triggering initial fetch")
                    result = await trigger_fetch(triggered_by="startup")
                    if not result.get("ok"):
                        logger.warning(
                            "Initial fetch: %s", result.get("message")
                        )
            except Exception as exc:
                logger.warning("Feed scheduler check failed: %s", exc)

    except asyncio.CancelledError:
        logger.info("Feed scheduler background task cancelled")
