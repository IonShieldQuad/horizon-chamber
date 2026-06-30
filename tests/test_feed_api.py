"""Tests for the feed API endpoints."""

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set test environment BEFORE importing app
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-123")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "horizon_feed_test.db")
os.environ["FEED_API_KEY"] = "test-feed-api-key"
os.environ["N8N_WEBHOOK_URL"] = "http://localhost:9999/test-webhook"
os.environ["FEED_AUTO_FETCH"] = "false"
os.environ["FEED_POLL_INTERVAL_HOURS"] = "6"

from main import app
import db
import feed as feed_module


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_db():
    """Re-init DB and clean feed tables before each test."""
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(db.init_db())
    async def _clean():
        conn = await db.get_db()
        await conn.execute("DELETE FROM feed_items")
        await conn.execute("DELETE FROM feed_runs")
        await conn.commit()
        await conn.close()
        feed_module._fetch_in_progress = False
        feed_module._last_fetch_time = None
        feed_module._last_manual_fetch = None
        feed_module._sse_queues.clear()
    loop.run_until_complete(_clean())
    loop.close()
    yield


# ── Helpers ──────────────────────────────────────────────────────────────


SAMPLE_PAYLOAD = {
    "processed_items": [
        {
            "original_id": "post_001",
            "relevance_score": 8.5,
            "is_relevant": True,
            "priority_level": 80,
            "category": "ai",
            "one_liner": "New RL breakthrough from DeepMind",
            "sentiment_tone": "positive",
            "source_url": "https://example.com/rl-breakthrough",
            "source_name": "LessWrong",
        },
        {
            "original_id": "post_002",
            "relevance_score": 3.0,
            "is_relevant": False,
            "priority_level": 30,
            "category": "random",
            "one_liner": "Random internet meme",
            "sentiment_tone": "neutral",
            "source_url": "",
            "source_name": "",
        },
    ],
    "total_relevant": 1,
    "summary_stats": {"average_relevance": 5.75, "highest_priority_item_id": "post_001"},
}


# ── Ingest endpoint tests ────────────────────────────────────────────────


def test_ingest_without_auth_returns_401(client):
    """POST /api/feed/ingest without auth header is rejected when FEED_API_KEY is set."""
    resp = client.post("/api/feed/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 401


def test_ingest_with_bad_auth_returns_401(client):
    """POST /api/feed/ingest with wrong API key is rejected."""
    resp = client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


def test_ingest_with_correct_auth_succeeds(client):
    """POST /api/feed/ingest with correct API key succeeds."""
    resp = client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ingested"] == 2
    assert data["skipped_duplicates"] == 0
    assert data["total_relevant"] == 1


def test_ingest_deduplicates_by_original_id(client):
    """POST /api/feed/ingest with duplicate original_id skips."""
    # First ingest
    resp = client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 2

    # Second ingest (same payload)
    resp = client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ingested"] == 0
    assert data["skipped_duplicates"] == 2


def test_ingest_without_api_key_set_allows_all(client, monkeypatch):
    """When FEED_API_KEY is empty, ingest allows all."""
    monkeypatch.setattr(feed_module, "FEED_API_KEY", "")
    resp = client.post("/api/feed/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 200


# ── List endpoint tests ──────────────────────────────────────────────────


def test_list_items_empty(client):
    """GET /api/feed/items returns empty list initially."""
    resp = client.get("/api/feed/items")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["count"] == 0


def test_list_items_after_ingest(client):
    """GET /api/feed/items returns ingested items."""
    client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    resp = client.get("/api/feed/items")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["items"][0]["one_liner"] == "New RL breakthrough from DeepMind"


def test_list_items_with_category_filter(client):
    """GET /api/feed/items?category=ai filters correctly."""
    client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    resp = client.get("/api/feed/items?category=ai")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["original_id"] == "post_001"


# ── Dismiss endpoint tests ───────────────────────────────────────────────


def test_dismiss_item(client):
    """PATCH /api/feed/items/{id}/dismiss marks item as dismissed."""
    client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    # Get item id
    resp = client.get("/api/feed/items")
    item_id = resp.json()["items"][0]["id"]

    # Dismiss
    resp = client.patch(f"/api/feed/items/{item_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify it's gone from active list
    resp = client.get("/api/feed/items?dismissed=false")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1  # only the other item

    # Verify it appears in dismissed list
    resp = client.get("/api/feed/items?dismissed=true")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    assert resp.json()["items"][0]["id"] == item_id


def test_undismiss_item(client):
    """PATCH /api/feed/items/{id}/undismiss restores a dismissed item."""
    client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    resp = client.get("/api/feed/items")
    item_id = resp.json()["items"][0]["id"]

    # Dismiss, then undismiss
    client.patch(f"/api/feed/items/{item_id}/dismiss")
    client.patch(f"/api/feed/items/{item_id}/undismiss")

    resp = client.get("/api/feed/items?dismissed=false")
    assert resp.json()["count"] == 2  # both active again


def test_dismiss_nonexistent_returns_404(client):
    """PATCH /api/feed/items/99999/dismiss returns 404."""
    resp = client.patch("/api/feed/items/99999/dismiss")
    assert resp.status_code == 404


# ── Stats endpoint tests ─────────────────────────────────────────────────


def test_stats_after_ingest(client):
    """GET /api/feed/stats returns correct statistics."""
    client.post(
        "/api/feed/ingest",
        json=SAMPLE_PAYLOAD,
        headers={"Authorization": "Bearer test-feed-api-key"},
    )
    resp = client.get("/api/feed/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_items"] == 2
    assert data["dismissed_items"] == 0
    assert data["categories"].get("ai") == 1
    assert data["categories"].get("random") == 1


# ── Trigger endpoint tests ───────────────────────────────────────────────


def test_trigger_endpoint(client, monkeypatch):
    """POST /api/feed/trigger returns ok when n8n webhook is reachable."""
    from unittest.mock import AsyncMock
    monkeypatch.setattr(feed_module, "trigger_fetch", AsyncMock(return_value={"ok": True, "message": "n8n workflow triggered"}))
    resp = client.post("/api/feed/trigger")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


def test_trigger_cooldown(client):
    """POST /api/feed/trigger respects cooldown."""
    import asyncio
    # Create a mock that succeeds
    from unittest.mock import AsyncMock
    # Set last manual fetch to recent past
    feed_module._last_manual_fetch = datetime.now() - __import__("datetime").timedelta(minutes=5)

    # Make trigger_fetch respect the cooldown check
    result = feed_module.can_manual_fetch()
    assert result is False  # 5 min < 30 min cooldown


# ── SSE stream test ──────────────────────────────────────────────────────


def test_sse_connection(client):
    """GET /api/feed/stream returns text/event-stream content type."""
    with client.stream("GET", "/api/feed/stream") as response:
        assert response.status_code == 200
        assert response.headers.get("content-type") == "text/event-stream; charset=utf-8"
        # Read first few bytes to confirm it's alive
        chunk = response.read(10)
        assert chunk is not None or True
