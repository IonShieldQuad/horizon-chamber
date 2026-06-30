"""Tests for the DeepSeek API client."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-123")
# Re-import after setting env var
import importlib
import deepseek_client
importlib.reload(deepseek_client)


def test_is_api_key_set_true():
    """When DEEPSEEK_API_KEY is set, is_api_key_set returns True."""
    assert deepseek_client.is_api_key_set() is True


def test_is_api_key_set_false(monkeypatch):
    """When DEEPSEEK_API_KEY is empty/unset, is_api_key_set returns False."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    importlib.reload(deepseek_client)
    assert deepseek_client.is_api_key_set() is False
    # Restore for other tests
    os.environ["DEEPSEEK_API_KEY"] = "test-key-123"
    importlib.reload(deepseek_client)


@pytest.mark.asyncio
async def test_classify_text_missing_key():
    """classify_text raises ValueError when key is missing."""
    with patch("deepseek_client.is_api_key_set", return_value=False):
        with pytest.raises(ValueError, match="API_KEY"):
            await deepseek_client.classify_text("test")


def _make_mock_client(response_json: dict):
    """Build a mock AsyncClient whose post() returns the given JSON.

    The mock response uses regular MagicMock (not AsyncMock) because
    json(), raise_for_status() etc. are called synchronously inside the
    async function.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = response_json
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


@pytest.mark.asyncio
async def test_classify_text_strips_code_fences():
    """classify_text strips ```json ... ``` fences from response."""
    mock_response_data = {
        "choices": [{
            "message": {
                "content": '```json\n{"now": ["a"], "later": [], "trash": []}\n```'
            }
        }]
    }
    mock_client = _make_mock_client(mock_response_data)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await deepseek_client.classify_text("test")
        assert result == {"now": ["a"], "later": [], "trash": []}


@pytest.mark.asyncio
async def test_classify_text_missing_keys():
    """classify_text raises KeyError when response lacks required keys."""
    mock_response_data = {
        "choices": [{"message": {"content": '{"now": ["a"]}'}}]
    }
    mock_client = _make_mock_client(mock_response_data)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(KeyError, match="later"):
            await deepseek_client.classify_text("test")
