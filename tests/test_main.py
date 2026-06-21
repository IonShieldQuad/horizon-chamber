"""Tests for edge cases in main.py."""

import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-123")
os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "horizon_test_main.db")

from main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_serve_index(client):
    """GET / returns 200 with HTML content."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<canvas" in resp.text or "Horizon Chamber" in resp.text


def test_classify_timeout(client):
    """POST /api/classify returns 504 when DeepSeek times out."""
    with patch("main.deepseek_client.classify_text", new=AsyncMock(side_effect=TimeoutError())):
        resp = client.post("/api/classify", json={"raw_text": "test"})
        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()


def test_classify_value_error(client):
    """POST /api/classify returns 503 when DeepSeek raises ValueError."""
    with patch("main.deepseek_client.classify_text", new=AsyncMock(side_effect=ValueError("bad key"))):
        resp = client.post("/api/classify", json={"raw_text": "test"})
        assert resp.status_code == 503


def test_static_favicon_served(client):
    """GET /static/favicon.png returns the icon file."""
    resp = client.get("/static/favicon.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] in ("image/png", "image/png; charset=utf-8")
    assert len(resp.content) > 1000


def test_static_ico_served(client):
    """GET /static/horizon_icon.ico returns the icon file."""
    resp = client.get("/static/horizon_icon.ico")
    assert resp.status_code == 200
    assert len(resp.content) > 1000


def test_static_missing_returns_404(client):
    """GET /static/nonexistent.file returns 404."""
    resp = client.get("/static/nonexistent.file")
    assert resp.status_code == 404
