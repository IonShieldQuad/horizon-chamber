"""Async DeepSeek API client for Horizon Chamber."""

import json
import os
import logging

import httpx

logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"  # or deepseek-reasoner
REQUEST_TIMEOUT = 30.0

SYSTEM_PROMPT = (
    "You are a strict classifier. Given a block of messy text (browser tabs, "
    "URLs, thoughts, notes), categorize each distinct item into exactly one of "
    "three categories. Return ONLY valid JSON with three keys: \"now\" (urgent "
    "action items, must do today), \"later\" (to read/review, not urgent), "
    "\"trash\" (irrelevant, spam, already done). Each key's value is a list of strings."
)


def is_api_key_set() -> bool:
    """Check whether a non-empty DeepSeek API key is configured."""
    return bool(DEEPSEEK_API_KEY and DEEPSEEK_API_KEY.strip())


async def classify_text(raw_text: str) -> dict:
    """Send raw_text to DeepSeek for classification.

    Returns the parsed JSON with keys 'now', 'later', 'trash'.

    Raises:
        ValueError: If the API key is missing.
        httpx.HTTPStatusError: If DeepSeek returns a non-2xx status.
        json.JSONDecodeError: If the response body is not valid JSON.
        KeyError: If the response JSON lacks required keys.
        httpx.TimeoutException: If the request times out.
    """
    if not is_api_key_set():
        raise ValueError("DEEPSEEK_API_KEY is not set or is empty")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0.1,  # low temperature for deterministic classification
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(DEEPSEEK_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    # Extract the assistant's message content
    content = data["choices"][0]["message"]["content"]

    # DeepSeek may wrap JSON in markdown code fences
    content = content.strip()
    if content.startswith("```"):
        # Strip ```json ... ``` fences
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    parsed = json.loads(content)

    # Validate required keys
    for key in ("now", "later", "trash"):
        if key not in parsed:
            raise KeyError(f"Missing required key '{key}' in DeepSeek response: {parsed}")

    return parsed
