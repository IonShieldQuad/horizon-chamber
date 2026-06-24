"""Tests for the new goal/task/board/analysis API endpoints."""

import os
import sys
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-123")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "horizon_goals_test.db")

from main import app
from db import init_db, get_db


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database before each test."""
    loop = __import__("asyncio").new_event_loop()
    loop.run_until_complete(init_db())
    yield
    async def _cleanup():
        db = await get_db()
        for table in ("goals", "tasks", "task_log", "chaos"):
            try:
                await db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        await db.commit()
        await db.close()
    loop.run_until_complete(_cleanup())
    loop.close()


# ── Goal CRUD ────────────────────────────────────────────────────────────


def test_create_goal(client):
    """POST /api/goals with valid body → 200, returns goal with id."""
    resp = client.post("/api/goals", json={
        "title": "Learn Python",
        "type": "long_term",
        "target_days": 90,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] > 0
    assert data["type"] == "long_term"
    assert data["title"] == "Learn Python"
    assert data["target_days"] == 90


def test_create_goal_defaults(client):
    """Creating a habit without frequency defaults to daily."""
    resp = client.post("/api/goals", json={
        "title": "Walk daily",
        "type": "habit",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "habit"
    assert data["frequency"] == "daily"


def test_create_goal_auto_type(client):
    """Without type, auto-detect via AI (mocked)."""
    from unittest.mock import AsyncMock, patch as p
    with p("main.deepseek_client.is_api_key_set", return_value=True), \
         p("main.deepseek_client.classify_as_goal", new=AsyncMock(return_value={
             "type": "habit", "frequency": "daily"
         })):
        resp = client.post("/api/goals", json={
            "title": "Read every day",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "habit"


def test_get_goals_empty(client):
    """GET /api/goals returns empty list when no goals exist."""
    resp = client.get("/api/goals")
    assert resp.status_code == 200
    assert resp.json() == {"goals": []}


def test_get_goals_with_data(client):
    """GET /api/goals returns created goals."""
    client.post("/api/goals", json={"title": "Goal A", "type": "long_term"})
    client.post("/api/goals", json={"title": "Goal B", "type": "habit"})

    resp = client.get("/api/goals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["goals"]) == 2


def test_get_single_goal(client):
    """GET /api/goals/{id} returns a single goal."""
    created = client.post("/api/goals", json={
        "title": "Single Goal", "type": "long_term"
    }).json()

    resp = client.get(f"/api/goals/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Single Goal"


def test_get_single_goal_not_found(client):
    """GET /api/goals/{id} with invalid id returns 404."""
    resp = client.get("/api/goals/99999")
    assert resp.status_code == 404


def test_update_goal(client):
    """PUT /api/goals/{id} updates fields."""
    created = client.post("/api/goals", json={
        "title": "Old Title", "type": "long_term"
    }).json()

    resp = client.put(f"/api/goals/{created['id']}", json={
        "title": "New Title",
        "progress_pct": 50.0,
    })
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"
    assert resp.json()["progress_pct"] == 50.0


def test_archive_goal(client):
    """DELETE /api/goals/{id} soft-deletes (archives) a goal."""
    created = client.post("/api/goals", json={
        "title": "To Archive", "type": "habit"
    }).json()

    resp = client.delete(f"/api/goals/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Should not appear in default list
    goals = client.get("/api/goals").json()["goals"]
    assert len(goals) == 0

    # Should appear with archived=true
    archived = client.get("/api/goals?archived=true").json()["goals"]
    assert len(archived) >= 1
    assert archived[0]["id"] == created["id"]


# ── Tasks ────────────────────────────────────────────────────────────────


def test_update_task_status(client):
    """PATCH /api/tasks/{id} updates status to done."""
    from db import insert_goal, insert_task, get_task
    loop = __import__("asyncio").new_event_loop()
    gid = loop.run_until_complete(insert_goal("long_term", "Test Goal"))
    tid = loop.run_until_complete(insert_task(gid, "Test Task", "2025-01-15"))
    loop.close()

    resp = client.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["completed_at"] is not None


def test_update_task_not_found(client):
    """PATCH /api/tasks/{id} with invalid id returns 404."""
    resp = client.patch("/api/tasks/99999", json={"status": "done"})
    assert resp.status_code == 404


# ── Board ────────────────────────────────────────────────────────────────


def test_board_endpoint(client):
    """GET /api/board returns board structure."""
    # Create a goal and task first
    from db import insert_goal, insert_task, update_task
    loop = __import__("asyncio").new_event_loop()
    gid = loop.run_until_complete(insert_goal("long_term", "Board Goal"))
    loop.run_until_complete(insert_task(gid, "Board Task", "2025-01-15"))
    loop.close()

    resp = client.get("/api/board?date=2025-01-15")
    assert resp.status_code == 200
    data = resp.json()
    assert "columns" in data
    assert "goals" in data
    assert "stats" in data
    assert data["date"] == "2025-01-15"


# ── Today list ───────────────────────────────────────────────────────────


def test_today_list(client):
    """GET /api/today returns tasks with date."""
    resp = client.get("/api/today")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert "date" in data


# ── Analysis ─────────────────────────────────────────────────────────────


def test_analysis_endpoint(client):
    """GET /api/analysis returns signals."""
    resp = client.get("/api/analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert "completion_rate" in data
    assert "nudges" in data
    assert "stats" in data


# ── Engine control ───────────────────────────────────────────────────────


def test_engine_generate(client):
    """POST /api/engine/generate triggers generation."""
    resp = client.post("/api/engine/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert "created" in data


def test_engine_status(client):
    """GET /api/engine/status returns status."""
    resp = client.get("/api/engine/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "running" in data
    assert "today_generated" in data


# ── Analysis refresh ─────────────────────────────────────────────────────


def test_analysis_refresh(client):
    """POST /api/analysis/refresh re-runs analysis."""
    resp = client.post("/api/analysis/refresh")
    assert resp.status_code == 200
