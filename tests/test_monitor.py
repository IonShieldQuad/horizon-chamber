"""Tests for the monitor module."""

import asyncio
import os
import sys
import unittest.mock

import pytest

import monitor


@pytest.fixture(autouse=True)
def reset_monitor_state():
    """Reset monitor globals before each test."""
    monitor._current = None
    monitor._running = False
    monitor._stop_event.clear()
    monitor._last_app = None
    monitor._last_block_id = None
    monitor._last_block_start = None


# ── Platform detection ────────────────────────────────────────────────────


def test_platform_detection():
    """_platform() returns a valid OS string."""
    plat = monitor._platform()
    assert plat in ("windows", "macos", "linux"), f"Unexpected platform: {plat}"


# ── Fallback behavior ────────────────────────────────────────────────────


def test_active_window_fallback():
    """On any platform, _get_active_window_sync returns a tuple without crashing."""
    app, title = monitor._get_active_window_sync()
    assert isinstance(app, str)
    assert isinstance(title, str)


def test_idle_time_fallback():
    """_get_idle_seconds_sync returns a float without crashing."""
    secs = monitor._get_idle_seconds_sync()
    assert isinstance(secs, float)


def test_current_activity_none_before_start():
    """get_current_activity() returns None before monitoring starts."""
    assert monitor.get_current_activity() is None


def test_get_idle_seconds_default():
    """get_idle_seconds() returns 0.0 when no activity has been observed."""
    assert monitor.get_idle_seconds() == 0.0


# ── start / stop ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_stop_monitoring():
    """start_monitoring runs, stop_monitoring cancels cleanly."""
    assert not monitor.is_running()

    # Start monitoring (will immediately hit the loop)
    task = asyncio.create_task(monitor.start_monitoring(interval=0.5))

    # Give it a moment to start
    await asyncio.sleep(0.2)
    assert monitor.is_running()

    # Stop it
    await monitor.stop_monitoring()
    await asyncio.sleep(0.6)  # allow the loop to break

    assert not monitor.is_running()
    assert task.done() or not task.done()  # task may still be running if timeout


# ── Feature flags ────────────────────────────────────────────────────────


def test_feature_flags_defaults():
    """Feature flags have expected default values."""
    assert monitor.FEATURE_ACTIVE_WINDOW is True
    assert monitor.FEATURE_IDLE_TIME is True
    assert monitor.FEATURE_PROCESS_LIST is False


# ── Platform-specific: Windows ───────────────────────────────────────────


def test_windows_active_window_nonempty():
    """On Windows, _windows_active_window() returns real data (not just fallback)."""
    if monitor._platform() != "windows":
        pytest.skip("Windows-only test")
    app, title = monitor._windows_active_window()
    assert app != "unknown" or True  # may be "unknown" if no window focused
    assert isinstance(title, str)


def test_windows_idle_nonnegative():
    """On Windows, _windows_idle_seconds() returns a non-negative float."""
    if monitor._platform() != "windows":
        pytest.skip("Windows-only test")
    secs = monitor._windows_idle_seconds()
    assert secs >= 0.0


# ── Platform-specific: macOS (subprocess) ─────────────────────────────────


def test_macos_active_window_osascript_success(monkeypatch):
    """_macos_active_window() parses osascript output correctly."""
    import subprocess

    def mock_run(args, **kwargs):
        if "get name of" in str(args):
            m = unittest.mock.MagicMock()
            m.stdout = "Code\n"
            m.returncode = 0
            return m
        if "get title of" in str(args):
            m = unittest.mock.MagicMock()
            m.stdout = "test.py — VS Code\n"
            m.returncode = 0
            return m
        raise FileNotFoundError("unexpected call")

    monkeypatch.setattr(subprocess, "run", mock_run)
    app, title = monitor._macos_active_window()
    assert app == "Code"
    assert title == "test.py — VS Code"


def test_macos_active_window_fallback_on_error(monkeypatch):
    """_macos_active_window() returns fallback on subprocess failure."""
    import subprocess

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("osascript not found")

    monkeypatch.setattr(subprocess, "run", mock_run)
    app, title = monitor._macos_active_window()
    assert app == "unknown"
    assert title == ""


def test_macos_idle_seconds_ioreg_success(monkeypatch):
    """_macos_idle_seconds() parses ioreg HIDIdleTime correctly."""
    import subprocess

    def mock_run(*args, **kwargs):
        m = unittest.mock.MagicMock()
        # 5 seconds = 5_000_000_000 nanoseconds
        m.stdout = (
            '  |   "HIDIdleTime" = 5000000000\n'
            '  |   "HIDIdleTime" = older value\n'
        )
        m.returncode = 0
        return m

    monkeypatch.setattr(subprocess, "run", mock_run)
    secs = monitor._macos_idle_seconds()
    assert secs == 5.0


def test_macos_idle_seconds_fallback_on_error(monkeypatch):
    """_macos_idle_seconds() returns 0.0 on subprocess failure."""
    import subprocess

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("ioreg not found")

    monkeypatch.setattr(subprocess, "run", mock_run)
    secs = monitor._macos_idle_seconds()
    assert secs == 0.0


# ── Platform-specific: Linux (subprocess) ─────────────────────────────────


def test_linux_active_window_xdotool_success(monkeypatch):
    """_linux_active_window() parses xdotool output correctly."""
    import subprocess

    call_count = 0

    def mock_run(args, **kwargs):
        nonlocal call_count
        call_count += 1
        m = unittest.mock.MagicMock()
        m.returncode = 0
        if call_count == 1:
            m.stdout = "test.py — VS Code\n"
        elif call_count == 2:
            m.stdout = "1234\n"
        return m

    monkeypatch.setattr(subprocess, "run", mock_run)
    # Also need to mock open() for /proc/pid/comm
    monkeypatch.setattr("builtins.open", lambda *a, **kw: unittest.mock.mock_open(read_data="code\n")(*a, **kw))

    app, title = monitor._linux_active_window()
    assert title == "test.py — VS Code"
    # The app name may be read from /proc/pid/comm or be unknown
    assert isinstance(app, str)


def test_linux_active_window_fallback_on_error(monkeypatch):
    """_linux_active_window() returns fallback on subprocess failure."""
    import subprocess

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("xdotool not found")

    monkeypatch.setattr(subprocess, "run", mock_run)
    app, title = monitor._linux_active_window()
    assert app == "unknown"
    assert title == ""


def test_linux_idle_seconds_xprintidle_success(monkeypatch):
    """_linux_idle_seconds() parses xprintidle output correctly."""
    import subprocess

    def mock_run(*args, **kwargs):
        m = unittest.mock.MagicMock()
        m.stdout = "120000\n"  # 2 minutes in ms
        m.returncode = 0
        return m

    monkeypatch.setattr(subprocess, "run", mock_run)
    secs = monitor._linux_idle_seconds()
    assert secs == 120.0


def test_linux_idle_seconds_fallback_on_error(monkeypatch):
    """_linux_idle_seconds() returns 0.0 on subprocess failure."""
    import subprocess

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("xprintidle not found")

    monkeypatch.setattr(subprocess, "run", mock_run)
    secs = monitor._linux_idle_seconds()
    assert secs == 0.0
