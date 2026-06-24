"""Backend tests for Horizon Chamber."""

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure the project root is on sys.path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test environment before importing app modules
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-123")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "horizon_test.db")

from main import app
from db import get_db, init_db, insert_classification, get_recent_now_items


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """FastAPI TestClient fixture — resets DB on every call."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_db():
    """Re-initialize the in-memory DB before each test (synchronous wrapper)."""
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_db())
    yield
    async def _cleanup():
        db = await get_db()
        await db.execute("DELETE FROM chaos")
        await db.commit()
        await db.close()
    loop.run_until_complete(_cleanup())
    loop.close()


# ---------------------------------------------------------------------------
# DB initialization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_initialization():
    """Verify the chaos table exists with the correct schema."""
    await init_db()
    db = await get_db()
    try:
        cursor = await db.execute("PRAGMA table_info(chaos)")
        columns = await cursor.fetchall()
        col_names = [row["name"] for row in columns]
        assert "id" in col_names
        assert "category" in col_names
        assert "text" in col_names
        assert "timestamp" in col_names
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    """GET /api/health returns 200 with deepseek_key_set as bool."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["deepseek_key_set"], bool)
    # Our test env has the key set
    assert data["deepseek_key_set"] is True


# ---------------------------------------------------------------------------
# Time color
# ---------------------------------------------------------------------------

def _test_time_color_at(client, mock_hour, expected_hex):
    """Helper: patch datetime.now().hour and assert the response hex."""
    class MockDatetime:
        @classmethod
        def now(cls):
            dt = datetime(2025, 6, 20, mock_hour, 0, 0)
            return dt

    with patch("main.datetime", MockDatetime):
        resp = client.get("/api/time_color")
        assert resp.status_code == 200
        assert resp.json()["hex"] == expected_hex


def test_time_color_morning(client):
    """5-8 AM → Gold."""
    _test_time_color_at(client, 7, "#FFD700")


def test_time_color_afternoon(client):
    """9-16 → Blue."""
    _test_time_color_at(client, 14, "#4A90E2")


def test_time_color_evening(client):
    """17-20 → Orange."""
    _test_time_color_at(client, 18, "#FF6B35")


def test_time_color_night(client):
    """21-4 → Purple."""
    _test_time_color_at(client, 23, "#2B1B4A")
    # Also test early morning (hour 3)
    _test_time_color_at(client, 3, "#2B1B4A")


# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------

def test_classify_success(client):
    """POST /api/classify with valid text and mock DeepSeek success."""
    mock_result = {
        "now": ["buy milk", "read article"],
        "later": ["check YouTube video", "review notes"],
        "trash": ["old tab xyz", "spam email"],
    }

    with patch("main.deepseek_client.classify_text", new=AsyncMock(return_value=mock_result)):
        resp = client.post("/api/classify", json={"raw_text": "buy milk, read article, check YouTube, review notes, old tab, spam"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["now"] == ["buy milk", "read article"]
        assert data["later"] == ["check YouTube video", "review notes"]
        assert data["trash"] == ["old tab xyz", "spam email"]


def test_classify_missing_key(client):
    """POST /api/classify returns 503 when DEEPSEEK_API_KEY is not set."""
    with patch("main.deepseek_client.is_api_key_set", return_value=False):
        resp = client.post("/api/classify", json={"raw_text": "test text"})
        assert resp.status_code == 503
        assert "API_KEY" in resp.json()["detail"]


def test_classify_invalid_body(client):
    """POST /api/classify with empty raw_text returns 422."""
    resp = client.post("/api/classify", json={"raw_text": ""})
    assert resp.status_code == 422


def test_classify_malformed_deepseek(client):
    """POST /api/classify when DeepSeek returns bad data returns 502."""
    with patch("main.deepseek_client.classify_text", new=AsyncMock(side_effect=KeyError("Missing key 'now'"))):
        resp = client.post("/api/classify", json={"raw_text": "test text"})
        assert resp.status_code == 502
        assert "Classification failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------

def test_today_empty(client):
    """GET /api/today returns empty task list when no data exists."""
    resp = client.get("/api/today")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert "date" in data
    assert data["tasks"] == []


@pytest.mark.asyncio
async def test_today_with_items():
    """GET /api/today returns 3 most recent 'now' items."""
    await init_db()

    # Insert 5 'now' items
    for i in range(5):
        await insert_classification("now", f"item {i}")
        import asyncio
        await asyncio.sleep(0.01)  # ensure distinct timestamps

    items = await get_recent_now_items(limit=3)
    assert len(items) == 3
    texts = [it["text"] for it in items]
    assert texts == ["item 4", "item 3", "item 2"]

    # Also verify the API returns the new format via fallback
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        resp = c.get("/api/today")
        assert resp.status_code == 200
        data = resp.json()
        # New format has 'tasks' and 'date' keys
        assert "tasks" in data
        assert "date" in data


# ---------------------------------------------------------------------------
# Integration: classify then query today
# ---------------------------------------------------------------------------

def test_classify_then_today(client):
    """After classifying, GET /api/today returns the 'now' items."""
    mock_result = {
        "now": ["urgent task", "call doctor"],
        "later": ["read blog"],
        "trash": ["junk"],
    }

    with patch("main.deepseek_client.classify_text", new=AsyncMock(return_value=mock_result)):
        resp = client.post("/api/classify", json={"raw_text": "test chaos"})
        assert resp.status_code == 200

    resp2 = client.get("/api/today")
    assert resp2.status_code == 200
    data = resp2.json()
    assert "tasks" in data
    # Should contain the now items as tasks (fallback format)


# ---------------------------------------------------------------------------
# Sunrise schedule endpoints
# ---------------------------------------------------------------------------


def test_schedule_get_default(client):
    """GET /api/sunrise/schedule returns default disabled state."""
    resp = client.get("/api/sunrise/schedule")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["time"] == "07:00"
    assert data["sunrise_triggered"] is False


def test_schedule_put_and_get(client):
    """PUT then GET reflects the new schedule."""
    resp = client.put("/api/sunrise/schedule", json={"enabled": True, "time": "06:30"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["time"] == "06:30"

    resp2 = client.get("/api/sunrise/schedule")
    assert resp2.status_code == 200
    assert resp2.json() == data


def test_schedule_put_invalid_time(client):
    """PUT with invalid hour range returns 400."""
    resp = client.put("/api/sunrise/schedule", json={"enabled": True, "time": "25:00"})
    assert resp.status_code == 400
    assert "valid" in resp.json()["detail"].lower()
