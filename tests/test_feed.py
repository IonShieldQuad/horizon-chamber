"""Tests for the feed module (scheduling, ingest, trigger)."""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

# Set test environment
os.environ["DATABASE_PATH"] = os.path.join(os.path.dirname(__file__), "..", "horizon_test.db")
os.environ["FEED_API_KEY"] = "test-feed-key"
os.environ["N8N_WEBHOOK_URL"] = "http://localhost:9999/test-webhook"
os.environ["FEED_POLL_INTERVAL_HOURS"] = "6"
os.environ["FEED_AUTO_FETCH"] = "false"

import feed as feed_module
import db


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def reset_db_and_state():
    """Re-init DB and reset feed module state before each test."""
    await db.init_db()
    # Clean feed tables
    conn = await db.get_db()
    try:
        await conn.execute("DELETE FROM feed_items")
        await conn.execute("DELETE FROM feed_runs")
        await conn.commit()
    finally:
        await conn.close()
    # Reset feed module state
    feed_module._fetch_in_progress = False
    feed_module._last_fetch_time = None
    feed_module._last_manual_fetch = None
    feed_module._run_id = None
    feed_module._sse_queues.clear()
    yield


# ── SSE queue tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_and_broadcast_sse():
    """Registering a queue and broadcasting sends events to it."""
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    feed_module.register_sse_queue(q)
    await feed_module._broadcast_event("test_event", {"key": "value"})
    event, data = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event == "test_event"
    assert data == {"key": "value"}
    feed_module.unregister_sse_queue(q)


@pytest.mark.asyncio
async def test_unregister_sse_queue():
    """Unregistering removes the queue from broadcast list."""
    q: asyncio.Queue = asyncio.Queue()
    feed_module.register_sse_queue(q)
    feed_module.unregister_sse_queue(q)
    assert q not in feed_module._sse_queues


# ── Ingest tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_empty_payload():
    """Ingest with no items returns zeros."""
    result = await feed_module.ingest_payload({"processed_items": [], "total_relevant": 0})
    assert result == {"ingested": 0, "skipped_duplicates": 0, "total_relevant": 0}


@pytest.mark.asyncio
async def test_ingest_new_items():
    """Ingesting valid items stores them and returns the count."""
    payload = {
        "processed_items": [
            {
                "original_id": "yt-123",
                "one_liner": "A great video about AI safety",
                "relevance_score": 8.5,
                "is_relevant": True,
                "priority_level": 80,
                "category": "ai",
                "sentiment_tone": "positive",
                "source_url": "https://youtube.com/watch?v=123",
                "source_name": "YouTube",
            },
            {
                "original_id": "lw-456",
                "one_liner": "New LessWrong post on alignment",
                "relevance_score": 6.0,
                "is_relevant": True,
                "priority_level": 60,
                "category": "longform",
                "sentiment_tone": "neutral",
                "source_name": "LessWrong",
            },
        ],
        "total_relevant": 2,
        "summary_stats": {"average_relevance": 7.25, "highest_priority_item_id": "yt-123"},
    }
    result = await feed_module.ingest_payload(payload)
    assert result["ingested"] == 2
    assert result["skipped_duplicates"] == 0
    assert result["total_relevant"] == 2

    # Verify stored in DB
    items = await feed_module.get_feed_items()
    assert len(items) == 2
    assert items[0]["original_id"] == "lw-456"  # newest first
    assert items[1]["original_id"] == "yt-123"


@pytest.mark.asyncio
async def test_ingest_deduplicates():
    """Ingesting the same original_id twice skips the duplicate."""
    item = {
        "original_id": "dup-999",
        "one_liner": "Duplicated item",
        "relevance_score": 5.0,
    }
    payload = {"processed_items": [item], "total_relevant": 1}

    result1 = await feed_module.ingest_payload(payload)
    assert result1["ingested"] == 1

    result2 = await feed_module.ingest_payload(payload)
    assert result2["ingested"] == 0
    assert result2["skipped_duplicates"] == 1

    items = await feed_module.get_feed_items()
    assert len(items) == 1


@pytest.mark.asyncio
async def test_ingest_missing_original_id():
    """Items without original_id are silently skipped."""
    payload = {
        "processed_items": [
            {"original_id": "", "one_liner": "No ID"},
            {"one_liner": "Also no ID"},
        ],
        "total_relevant": 0,
    }
    result = await feed_module.ingest_payload(payload)
    assert result["ingested"] == 0
    assert result["skipped_duplicates"] == 0


@pytest.mark.asyncio
async def test_ingest_with_running_run():
    """Ingest links items to the open feed run."""
    # Create a running run
    run_id = await db.insert_feed_run(triggered_by="schedule")
    await db.update_feed_run(run_id, status="running")

    payload = {
        "processed_items": [
            {"original_id": "run-test-1", "one_liner": "Linked to run"}
        ],
        "total_relevant": 1,
    }
    result = await feed_module.ingest_payload(payload)
    assert result["ingested"] == 1

    # Verify run was completed and stats updated
    last_run = await db.get_last_feed_run()
    assert last_run["status"] == "completed"
    assert last_run["total_items"] == 1


# ── Dismiss / Undismiss tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dismiss_and_undismiss():
    """Dismissing hides an item; undismissing restores it."""
    payload = {
        "processed_items": [
            {"original_id": "dismiss-test", "one_liner": "Will be dismissed"}
        ],
        "total_relevant": 1,
    }
    await feed_module.ingest_payload(payload)

    items = await feed_module.get_feed_items(dismissed=False)
    assert len(items) == 1
    item_id = items[0]["id"]

    # Dismiss
    ok = await feed_module.dismiss_item(item_id)
    assert ok is True

    active = await feed_module.get_feed_items(dismissed=False)
    assert len(active) == 0

    dismissed = await feed_module.get_feed_items(dismissed=True)
    assert len(dismissed) == 1

    # Undismiss
    ok = await feed_module.undismiss_item(item_id)
    assert ok is True
    active = await feed_module.get_feed_items(dismissed=False)
    assert len(active) == 1


@pytest.mark.asyncio
async def test_dismiss_nonexistent():
    """Dismissing a non-existent item returns False."""
    ok = await feed_module.dismiss_item(99999)
    assert ok is False


# ── Stats tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_stats_empty():
    """Stats on an empty feed return zeros."""
    stats = await feed_module.get_stats()
    assert stats["active_items"] == 0
    assert stats["dismissed_items"] == 0
    assert stats["average_relevance"] == 0.0
    assert stats["categories"] == {}
    assert stats["last_run"] is None


@pytest.mark.asyncio
async def test_get_stats_with_data():
    """Stats reflect ingested items."""
    payload = {
        "processed_items": [
            {"original_id": "s1", "one_liner": "Item 1", "relevance_score": 8.0, "category": "ai"},
            {"original_id": "s2", "one_liner": "Item 2", "relevance_score": 6.0, "category": "ai"},
            {"original_id": "s3", "one_liner": "Item 3", "relevance_score": 4.0, "category": "video"},
        ],
        "total_relevant": 3,
    }
    await feed_module.ingest_payload(payload)

    stats = await feed_module.get_stats()
    assert stats["active_items"] == 3
    assert stats["average_relevance"] == pytest.approx(6.0, rel=0.1)
    assert stats["categories"] == {"ai": 2, "video": 1}


# ── Trigger / cooldown tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_fetch_cooldown():
    """Manual trigger respects 30-minute cooldown."""
    feed_module._last_manual_fetch = datetime.now()
    result = await feed_module.trigger_fetch(triggered_by="manual")
    assert result["ok"] is False
    assert "cooldown" in result["message"].lower()


@pytest.mark.asyncio
async def test_trigger_fetch_in_progress():
    """Trigger returns error if fetch is already in progress."""
    feed_module._fetch_in_progress = True
    result = await feed_module.trigger_fetch(triggered_by="schedule")
    assert result["ok"] is False
    assert "already in progress" in result["message"].lower()


@pytest.mark.asyncio
async def test_trigger_fetch_network_error():
    """Network error returns a helpful message and marks run as failed."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = Exception("Connection refused")
        result = await feed_module.trigger_fetch(triggered_by="schedule")
        assert result["ok"] is False
        assert "Connection refused" in result["message"]

    # Verify a run was created but marked failed
    last_run = await db.get_last_feed_run()
    assert last_run is not None
    assert last_run["status"] == "failed"


@pytest.mark.asyncio
async def test_can_manual_fetch():
    """can_manual_fetch returns correct values based on cooldown."""
    feed_module._last_manual_fetch = None
    assert feed_module.can_manual_fetch() is True

    feed_module._last_manual_fetch = datetime.now()
    assert feed_module.can_manual_fetch() is False

    feed_module._last_manual_fetch = datetime.now() - timedelta(minutes=31)
    assert feed_module.can_manual_fetch() is True

    feed_module._fetch_in_progress = True
    assert feed_module.can_manual_fetch() is False


# ── Feed items query tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_feed_items_category_filter():
    """get_feed_items filters by category."""
    payload = {
        "processed_items": [
            {"original_id": "c1", "one_liner": "Cat A", "category": "ai"},
            {"original_id": "c2", "one_liner": "Cat B", "category": "video"},
            {"original_id": "c3", "one_liner": "Cat C", "category": "ai"},
        ],
        "total_relevant": 3,
    }
    await feed_module.ingest_payload(payload)

    ai_items = await feed_module.get_feed_items(category="ai")
    assert len(ai_items) == 2

    video_items = await feed_module.get_feed_items(category="video")
    assert len(video_items) == 1

    no_items = await feed_module.get_feed_items(category="nonexistent")
    assert len(no_items) == 0
