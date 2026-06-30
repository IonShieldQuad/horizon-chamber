"""Tests for activity tracking API endpoints."""

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-123")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "horizon_test_activity_api.db")

from main import app
from db import init_db, get_db


@pytest.fixture(autouse=True)
def reset_db():
    """Re-initialize DB before each test."""
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    yield
    async def _cleanup():
        db = await get_db()
        await db.execute("DELETE FROM activity_log")
        await db.execute("DELETE FROM time_blocks")
        await db.execute("DELETE FROM app_categories")
        await db.commit()
        await db.close()
    loop.run_until_complete(_cleanup())
    loop.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _insert_test_data():
    """Insert a sample activity entry and time block for testing."""
    import asyncio
    import db
    loop = asyncio.new_event_loop()
    loop.run_until_complete(db.insert_activity("code.exe", "main.py", 2.5))
    bid = loop.run_until_complete(db.upsert_time_block("code.exe", "2025-01-15T09:00:00", "focus"))
    loop.run_until_complete(db.close_time_block(bid, "2025-01-15T11:30:00"))
    loop.close()


# ── GET /api/activity/now ──────────────────────────────────────────────────


def test_activity_now_endpoint(client):
    """GET /api/activity/now returns 200 with valid structure (fallback)."""
    resp = client.get("/api/activity/now")
    assert resp.status_code == 200
    data = resp.json()
    assert "app_name" in data
    assert "window_title" in data
    assert "idle_seconds" in data
    assert "timestamp" in data


# ── GET /api/activity/summary ─────────────────────────────────────────────


def test_activity_summary_empty(client):
    """GET /api/activity/summary returns empty blocks when no data."""
    resp = client.get("/api/activity/summary?period=today")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "today"
    assert "blocks" in data


def test_activity_summary_with_data(client):
    """GET /api/activity/summary returns aggregated blocks after data is inserted."""
    _insert_test_data()
    resp = client.get("/api/activity/summary?period=all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period"] == "all"
    assert len(data["blocks"]) >= 1
    assert data["blocks"][0]["app_name"] == "code.exe"


# ── PUT/GET /api/activity/categories ─────────────────────────────────────


def test_put_get_categories(client):
    """PUT then GET returns saved category."""
    resp = client.put(
        "/api/activity/categories",
        json={"app_name": "code.exe", "category": "focus", "label": "VS Code"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    resp = client.get("/api/activity/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["categories"]) == 1
    assert data["categories"][0]["app_name"] == "code.exe"
    assert data["categories"][0]["category"] == "focus"
    assert data["categories"][0]["label"] == "VS Code"


def test_categories_invalid_category(client):
    """PUT with invalid category returns 422."""
    resp = client.put(
        "/api/activity/categories",
        json={"app_name": "code.exe", "category": "invalid_cat", "label": None},
    )
    assert resp.status_code == 422
    assert "Invalid category" in resp.json()["detail"]


# ── GET /api/activity/history ─────────────────────────────────────────────


def test_activity_history_pagination(client):
    """GET /api/activity/history with limit param returns at most N entries."""
    import asyncio
    import db
    loop = asyncio.new_event_loop()
    # Insert 3 entries
    for i in range(3):
        loop.run_until_complete(db.insert_activity(f"app{i}.exe", f"window{i}", 0.0))
    loop.close()

    resp = client.get("/api/activity/history?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 2


# ── GET /api/activity/stream (SSE) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_activity_stream_events():
    """GET /api/activity/stream yields SSE event when monitor has data."""
    import json
    import httpx
    import monitor as _monitor

    # Prime the monitor cache with a known activity value
    _monitor._current = {
        "app_name": "test_app.exe",
        "window_title": "test window",
        "idle_seconds": 1.5,
        "timestamp": "2025-01-15T14:30:00",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        async with client.stream("GET", "/api/activity/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"

            lines = []
            async for line in resp.aiter_lines():
                lines.append(line)
                if len(lines) >= 3:  # event line + data line + blank line
                    break

            full = "\n".join(lines)
            assert "event: activity" in full
            assert "data: " in full
            data_json = full.split("data: ")[1].split("\n")[0].strip()
            payload = json.loads(data_json)
            assert payload["app_name"] == "test_app.exe"
            assert payload["window_title"] == "test window"
            assert payload["idle_seconds"] == 1.5


@pytest.mark.asyncio
async def test_activity_stream_headers():
    """GET /api/activity/stream returns correct SSE headers."""
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        async with client.stream("GET", "/api/activity/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"
            assert "no-cache" in resp.headers.get("cache-control", "")
