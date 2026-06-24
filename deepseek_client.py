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


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

_ai_cache: dict[str, tuple[float, object]] = {}  # key -> (timestamp, value)
CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str):
    """Get cached value if still valid."""
    entry = _ai_cache.get(key)
    if entry is None:
        return None
    ts, val = entry
    import time
    if time.monotonic() - ts > CACHE_TTL:
        del _ai_cache[key]
        return None
    return val


def _cache_set(key: str, value: object):
    """Set cached value."""
    import time
    _ai_cache[key] = (time.monotonic(), value)


async def _call_deepseek(system_prompt: str, user_message: str, cache_key: str = "") -> str:
    """Send a prompt to DeepSeek and return the raw response content.

    If cache_key is non-empty, results are cached with that key.
    Falls back gracefully if API key is not set.
    """
    if not is_api_key_set():
        raise ValueError("DEEPSEEK_API_KEY is not set or is empty")

    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached  # type: ignore

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(DEEPSEEK_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"].strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    if cache_key:
        _cache_set(cache_key, content)

    return content


def _parse_json_response(content: str) -> dict:
    """Parse JSON from a DeepSeek response, handling markdown fences."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON from DeepSeek response: %s", content[:200])
        return {}


def _fallback_classify_goal(title: str) -> dict:
    """Fallback when AI is unavailable: default to long_term/daily."""
    return {"type": "long_term", "frequency": "daily", "custom_interval_days": None}


def _fallback_split_task(task_title: str) -> list[str]:
    """Fallback when AI is unavailable."""
    return [task_title]


def _fallback_estimate_progress() -> dict:
    """Fallback when AI is unavailable."""
    return {"progress_pct": 0.0, "next_step": ""}


def _fallback_suggest_frequency() -> dict:
    """Fallback when AI is unavailable."""
    return {"frequency": "daily", "custom_interval_days": None}


def _fallback_reminder() -> dict:
    """Fallback when AI is unavailable."""
    return {"reminder": ""}


def _fallback_nudge() -> str:
    """Fallback when AI is unavailable."""
    return ""


# ═══════════════════════════════════════════════════════════════════════════
#  Goal-specific AI functions
# ═══════════════════════════════════════════════════════════════════════════

GOAL_CLASSIFY_SYSTEM_PROMPT = (
    "You are a goal classifier. Given a goal title, classify it as one of: "
    "'long_term' (has end state, needs step-by-step progress), "
    "'habit' (repeat daily, never ends), "
    "'maintenance' (periodic recurring, flexible interval). "
    "If habit or maintenance, suggest a frequency: 'daily', 'weekly', or 'custom' "
    "with a custom_interval_days number. "
    "Return ONLY valid JSON with keys 'type', 'frequency', 'custom_interval_days'."
)

SPLIT_TASK_SYSTEM_PROMPT = (
    "You are a task planner. Given a task title, split it into 2-5 concrete "
    "subtasks that can each be done in one sitting. "
    "Return ONLY valid JSON with key 'subtasks' as an array of strings."
)

PROGRESS_ESTIMATE_PROMPT = (
    "You are a progress estimator. Given a goal, its type, the number of tasks done, "
    "and the last task title, estimate progress 0-100 and suggest the next step. "
    "Return ONLY valid JSON with keys 'progress_pct' (float 0-100) and 'next_step' (string)."
)

FREQUENCY_SUGGEST_PROMPT = (
    "You are a frequency optimizer. Given a goal title, type, completion rate, "
    "and current frequency, suggest an optimal frequency. "
    "Return ONLY valid JSON with keys 'frequency' and 'custom_interval_days'."
)

REMINDER_GENERATE_PROMPT = (
    "You are a reminder generator. Given a goal title and days since last activity, "
    "generate a brief, specific reminder for what to do next. "
    "Return ONLY valid JSON with key 'reminder' as a string."
)

NUDGE_COPYWRITING_PROMPT = (
    "You are a gentle productivity coach. Given a list of user signals, "
    "generate one brief, non-judgmental suggestion sentence. "
    "Return ONLY valid JSON with key 'nudge' as a string."
)


async def classify_as_goal(title: str) -> dict:
    """Classify a goal title as long_term/habit/maintenance with suggested frequency.

    Falls back gracefully when API key is not configured.
    """
    if not is_api_key_set():
        return _fallback_classify_goal(title)

    cache_key = f"classify_goal:{title.strip().lower()}"
    try:
        content = await _call_deepseek(GOAL_CLASSIFY_SYSTEM_PROMPT, title, cache_key)
        result = _parse_json_response(content)
        if "type" not in result:
            return _fallback_classify_goal(title)
        return result
    except Exception:
        logger.exception("Goal classification failed, using fallback")
        return _fallback_classify_goal(title)


async def split_task(task_title: str) -> list[str]:
    """Split a task title into 2-5 concrete subtasks.

    Falls back gracefully when API key is not configured.
    """
    if not is_api_key_set():
        return _fallback_split_task(task_title)

    cache_key = f"split_task:{task_title.strip().lower()}"
    try:
        content = await _call_deepseek(SPLIT_TASK_SYSTEM_PROMPT, task_title, cache_key)
        result = _parse_json_response(content)
        subtasks = result.get("subtasks", [])
        if not subtasks or not isinstance(subtasks, list):
            return _fallback_split_task(task_title)
        return subtasks
    except Exception:
        logger.exception("Task split failed, using fallback")
        return _fallback_split_task(task_title)


async def estimate_progress(
    goal_title: str,
    tasks_done: int,
    last_task_title: str,
    goal_type: str,
) -> dict:
    """Estimate progress 0-100 and suggest next step.

    Falls back gracefully when API key is not configured.
    """
    if not is_api_key_set():
        return _fallback_estimate_progress()

    user_msg = (
        f"Goal: '{goal_title}'. Type: {goal_type}. Tasks done: {tasks_done}. "
        f"Last task: '{last_task_title}'. Estimate progress 0-100 and suggest next step."
    )
    cache_key = f"estimate_progress:{goal_title.strip().lower()}:{tasks_done}"
    try:
        content = await _call_deepseek(PROGRESS_ESTIMATE_PROMPT, user_msg, cache_key)
        result = _parse_json_response(content)
        if "progress_pct" not in result:
            return _fallback_estimate_progress()
        return result
    except Exception:
        logger.exception("Progress estimation failed, using fallback")
        return _fallback_estimate_progress()


async def suggest_frequency(
    goal_title: str,
    goal_type: str,
    completion_rate: float,
    current_frequency: str,
) -> dict:
    """Suggest an optimal frequency for a goal.

    Falls back gracefully when API key is not configured.
    """
    if not is_api_key_set():
        return _fallback_suggest_frequency()

    user_msg = (
        f"Goal: '{goal_title}' type {goal_type}. "
        f"Completion rate: {completion_rate}%. Current frequency: {current_frequency}. "
        f"Suggest optimal frequency."
    )
    cache_key = f"suggest_freq:{goal_title.strip().lower()}"
    try:
        content = await _call_deepseek(FREQUENCY_SUGGEST_PROMPT, user_msg, cache_key)
        result = _parse_json_response(content)
        if "frequency" not in result:
            return _fallback_suggest_frequency()
        return result
    except Exception:
        logger.exception("Frequency suggestion failed, using fallback")
        return _fallback_suggest_frequency()


async def generate_reminder(goal_title: str, days_idle: int) -> dict:
    """Generate a brief, specific reminder for an idle goal.

    Falls back gracefully when API key is not configured.
    """
    if not is_api_key_set():
        return _fallback_reminder()

    user_msg = f"Goal: '{goal_title}'. Days since last activity: {days_idle}. Generate a brief reminder."
    cache_key = f"reminder:{goal_title.strip().lower()}:{days_idle}"
    try:
        content = await _call_deepseek(REMINDER_GENERATE_PROMPT, user_msg, cache_key)
        result = _parse_json_response(content)
        if "reminder" not in result:
            return _fallback_reminder()
        return result
    except Exception:
        logger.exception("Reminder generation failed, using fallback")
        return _fallback_reminder()


async def generate_nudge_text(signals: list[dict]) -> str:
    """Generate a non-judgmental nudge sentence from signals.

    Not cached (signals are always fresh).
    Falls back gracefully when API key is not configured.
    """
    if not is_api_key_set():
        return _fallback_nudge()

    try:
        signals_json = json.dumps(signals)
        content = await _call_deepseek(NUDGE_COPYWRITING_PROMPT, signals_json)
        result = _parse_json_response(content)
        return result.get("nudge", "")
    except Exception:
        logger.exception("Nudge generation failed, using fallback")
        return _fallback_nudge()
