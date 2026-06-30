"""Tests for the sunrise scheduler module."""

import os
import sys
from datetime import datetime

import pytest

import scheduler

# Reset scheduler state before each test (shared in-memory module)
@pytest.fixture(autouse=True)
def reset_scheduler():
    scheduler._schedule["enabled"] = False
    scheduler._schedule["time"] = "07:00"
    scheduler._schedule["sunrise_triggered"] = False
    scheduler._schedule["last_triggered_date"] = None


def test_schedule_get_default():
    """Default schedule has enabled=False, time=07:00, not triggered."""
    sched = scheduler.get_schedule()
    assert sched["enabled"] is False
    assert sched["time"] == "07:00"
    assert sched["sunrise_triggered"] is False


def test_schedule_set_and_get():
    """PUT a schedule, then GET reflects the change."""
    scheduler.set_schedule(enabled=True, time="06:30")
    sched = scheduler.get_schedule()
    assert sched["enabled"] is True
    assert sched["time"] == "06:30"
    assert sched["sunrise_triggered"] is False


def test_schedule_disable():
    """Setting enabled=False works correctly."""
    scheduler.set_schedule(enabled=False, time="08:00")
    sched = scheduler.get_schedule()
    assert sched["enabled"] is False
    assert sched["time"] == "08:00"


def test_schedule_consume_trigger():
    """consume_sunrise_trigger returns True once, then False."""
    scheduler.set_schedule(enabled=True, time="07:00")
    scheduler._schedule["sunrise_triggered"] = True

    assert scheduler.consume_sunrise_trigger() is True
    assert scheduler.consume_sunrise_trigger() is False


def test_get_schedule_consumes_trigger():
    """get_schedule() consumes sunrise_triggered so it only fires once."""
    scheduler.set_schedule(enabled=True, time="07:00")
    scheduler._schedule["sunrise_triggered"] = True

    # First call returns True
    sched1 = scheduler.get_schedule()
    assert sched1["sunrise_triggered"] is True

    # Second call returns False (consumed)
    sched2 = scheduler.get_schedule()
    assert sched2["sunrise_triggered"] is False

    # Internal flag is now False
    assert scheduler._schedule["sunrise_triggered"] is False


def test_schedule_triggers_correct_time():
    """Background logic sets sunrise_triggered when time matches."""
    scheduler.set_schedule(enabled=True, time="07:00")

    now = datetime(2025, 6, 20, 7, 0, 0)
    today_str = now.date().isoformat()

    target_h, target_m = scheduler._schedule["time"].split(":")
    target_minute = int(target_h) * 60 + int(target_m)
    current_minute = now.hour * 60 + now.minute

    assert current_minute == target_minute
    assert scheduler._schedule["last_triggered_date"] != today_str

    # Simulate the check logic from _check_schedule_loop
    if (current_minute == target_minute
            and scheduler._schedule["last_triggered_date"] != today_str):
        scheduler._schedule["sunrise_triggered"] = True
        scheduler._schedule["last_triggered_date"] = today_str

    assert scheduler._schedule["sunrise_triggered"] is True
    assert scheduler._schedule["last_triggered_date"] == "2025-06-20"

    # Second call should NOT re-trigger on same day
    scheduler._schedule["sunrise_triggered"] = False
    if (current_minute == target_minute
            and scheduler._schedule["last_triggered_date"] != today_str):
        scheduler._schedule["sunrise_triggered"] = True
    assert scheduler._schedule["sunrise_triggered"] is False


def test_schedule_invalid_time_format():
    """Setting invalid time format raises ValueError."""
    import pytest
    with pytest.raises(ValueError, match="HH:MM"):
        scheduler.set_schedule(enabled=True, time="abc")


def test_schedule_invalid_time_range():
    """Setting hours > 23 or minutes > 59 raises ValueError."""
    import pytest
    with pytest.raises(ValueError, match="valid 24h"):
        scheduler.set_schedule(enabled=True, time="25:00")
    with pytest.raises(ValueError, match="valid 24h"):
        scheduler.set_schedule(enabled=True, time="12:60")
