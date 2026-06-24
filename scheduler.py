"""In-memory sunrise scheduler for Horizon Chamber.

Stores the user's auto-sunrise schedule in a simple dict so it resets
on server restart — acceptable for v0.1 MVP.
"""

import asyncio
import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── In-memory store ──────────────────────────────────────────────────────

_schedule: dict = {
    "enabled": False,
    "time": "07:00",       # HH:MM in 24h format
    "sunrise_triggered": False,   # set True when time matches; poller consumes it
    "last_triggered_date": None,  # ISO date string to avoid re-triggering same day
}


# ── Public API (called by route handlers) ────────────────────────────────

def get_schedule() -> dict:
    """Return a copy of the current schedule state.

    Consumes the sunrise_triggered flag so the frontend sees it exactly once.
    """
    triggered = _schedule["sunrise_triggered"]
    if triggered:
        _schedule["sunrise_triggered"] = False
    return {
        "enabled": _schedule["enabled"],
        "time": _schedule["time"],
        "sunrise_triggered": triggered,
    }


def set_schedule(enabled: bool, time: str) -> None:
    """Update the schedule.  'time' must be a valid HH:MM 24h string."""
    # Validate format (HH:MM)
    parts = time.split(":")
    if len(parts) != 2:
        raise ValueError("time must be in HH:MM format (e.g. '07:00')")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError("time must contain numeric hours and minutes (e.g. '07:00')")
    if not (0 <= h <= 23) or not (0 <= m <= 59):
        raise ValueError("time must be a valid 24h time (HH:MM)")

    _schedule["enabled"] = enabled
    _schedule["time"] = f"{h:02d}:{m:02d}"
    _schedule["sunrise_triggered"] = False
    _schedule["last_triggered_date"] = None
    logger.info("Sunrise schedule updated: enabled=%s time=%s", enabled, time)


def consume_sunrise_trigger() -> bool:
    """Check and consume the sunrise-triggered flag.

    Returns True if the frontend should start sunrise, False otherwise.
    The flag is reset after reading so the sunrise only fires once.
    """
    if _schedule["sunrise_triggered"]:
        _schedule["sunrise_triggered"] = False
        return True
    return False


# ── Background checker task ──────────────────────────────────────────────

async def _check_schedule_loop(interval: float = 30.0) -> None:
    """Periodically check whether the scheduled time has arrived."""
    logger.info("Sunrise scheduler background task started (interval=%ss)", interval)
    try:
        while True:
            now = datetime.now()
            today_str = now.date().isoformat()

            if _schedule["enabled"]:
                target_h, target_m = _schedule["time"].split(":")
                target_minute = int(target_h) * 60 + int(target_m)
                current_minute = now.hour * 60 + now.minute

                # Trigger when the clock minute matches and we haven't fired today
                if (
                    current_minute == target_minute
                    and _schedule["last_triggered_date"] != today_str
                ):
                    _schedule["sunrise_triggered"] = True
                    _schedule["last_triggered_date"] = today_str
                    logger.info(
                        "Sunrise triggered at scheduled time %s", _schedule["time"]
                    )

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Sunrise scheduler background task cancelled")
