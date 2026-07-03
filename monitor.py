"""Activity monitor for Horizon Chamber.

Collects active window info and idle time from the OS via a background
asyncio task.  Platform-specific code lives behind if/elif blocks so the
module never crashes — every OS call is guarded by try/except and returns
safe fallback values on failure.

Public API:
    start_monitoring(interval=3.0)
    stop_monitoring()
    get_current_activity() -> dict | None
    get_idle_seconds() -> float
    get_focus_summary(period="today") -> list[dict]
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Feature flags (env-var overridable) ──────────────────────────────────

FEATURE_ACTIVE_WINDOW = os.getenv("FEATURE_ACTIVE_WINDOW", "true").lower() == "true"
FEATURE_IDLE_TIME = os.getenv("FEATURE_IDLE_TIME", "true").lower() == "true"
FEATURE_PROCESS_LIST = os.getenv("FEATURE_PROCESS_LIST", "false").lower() == "true"
# NOTE: FEATURE_PROCESS_LIST is reserved for v0.3. No code currently
# branches on this flag — the full process-tree collector is not yet built.
# When implemented, it should gate a psutil-based process enumeration that
# runs off the event loop via run_in_executor.  (v0.3 tracking)

# ── Config (env-var overridable) ─────────────────────────────────────────

POLL_INTERVAL = float(os.getenv("ACTIVITY_POLL_INTERVAL", "3"))
IDLE_THRESHOLD = float(os.getenv("ACTIVITY_IDLE_THRESHOLD", "60"))
PRUNE_DAYS = int(os.getenv("ACTIVITY_PRUNE_DAYS", "30"))

# ── Platform detection ───────────────────────────────────────────────────


def _platform() -> str:
    """Return the current OS platform: 'windows', 'macos', or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# ── Windows helpers (ctypes) ──────────────────────────────────────────────


def _windows_active_window() -> tuple[str, str]:
    """Return (app_name, window_title) for the active foreground window."""
    import ctypes
    import ctypes.wintypes

    try:
        user32 = ctypes.windll.user32

        # Get foreground window handle
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ("unknown", "")

        # Get window text (title)
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buffer = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buffer, length)
        title = buffer.value or ""

        # Get window class name as a simple app_name proxy
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, 256)
        app_name = class_buffer.value or "unknown"

        return (app_name, title)
    except Exception as exc:
        logger.warning("Windows active-window detection failed: %s", exc)
        return ("unknown", "")


def _windows_idle_seconds() -> float:
    """Return seconds since last user input (keyboard/mouse)."""
    import ctypes
    import ctypes.wintypes

    try:
        user32 = ctypes.windll.user32

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.wintypes.UINT),
                        ("dwTime", ctypes.wintypes.DWORD)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0

        kernel32 = ctypes.windll.kernel32
        tick_now = ctypes.wintypes.DWORD(kernel32.GetTickCount())
        return (tick_now.value - lii.dwTime) / 1000.0
    except Exception as exc:
        logger.warning("Windows idle detection failed: %s", exc)
        return 0.0


# ── macOS helpers (subprocess) ───────────────────────────────────────────


def _macos_active_window() -> tuple[str, str]:
    """Return (app_name, window_title) for the active foreground window
    on macOS via osascript subprocess calls."""
    import subprocess
    try:
        app_result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=5, check=True,
        )
        app_name = app_result.stdout.strip()
        title_result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get title of first window of (first application process whose frontmost is true)'],
            capture_output=True, text=True, timeout=5,
        )
        title = title_result.stdout.strip() if title_result.returncode == 0 else ""
        return (app_name or "unknown", title)
    except Exception as exc:
        logger.warning("macOS active-window detection failed: %s", exc)
        return ("unknown", "")


def _macos_idle_seconds() -> float:
    """Return seconds since last user input on macOS via ioreg."""
    import subprocess
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        for line in result.stdout.splitlines():
            if "HIDIdleTime" in line:
                value_str = line.split("=")[-1].strip()
                idle_ns = int(value_str)
                return idle_ns / 1_000_000_000.0
        return 0.0
    except Exception as exc:
        logger.warning("macOS idle detection failed: %s", exc)
        return 0.0


# ── Linux helpers (subprocess) ───────────────────────────────────────────


def _linux_active_window() -> tuple[str, str]:
    """Return (app_name, window_title) for the active window on Linux
    via xdotool subprocess calls."""
    import subprocess
    try:
        title_result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        title = title_result.stdout.strip()
        app_name = "unknown"
        try:
            pid_result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowpid"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            pid = pid_result.stdout.strip()
            if pid:
                import os as _os
                comm_path = f"/proc/{pid}/comm"
                if _os.path.exists(comm_path):
                    with open(comm_path, "r") as f:
                        app_name = f.read().strip()
        except Exception:
            pass
        return (app_name, title)
    except Exception as exc:
        logger.warning("Linux active-window detection failed: %s", exc)
        return ("unknown", "")


def _linux_idle_seconds() -> float:
    """Return seconds since last user input on Linux via xprintidle."""
    import subprocess
    try:
        result = subprocess.run(
            ["xprintidle"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        idle_ms = int(result.stdout.strip())
        return idle_ms / 1000.0
    except Exception as exc:
        logger.warning("Linux idle detection failed: %s", exc)
        return 0.0


# ── Platform dispatch ────────────────────────────────────────────────────


def _get_active_window_sync() -> tuple[str, str]:
    """Synchronous call to get the active window."""
    plat = _platform()
    if plat == "windows" and FEATURE_ACTIVE_WINDOW:
        return _windows_active_window()
    if plat == "macos" and FEATURE_ACTIVE_WINDOW:
        return _macos_active_window()
    if plat == "linux" and FEATURE_ACTIVE_WINDOW:
        return _linux_active_window()
    return ("unknown", "")


def _get_idle_seconds_sync() -> float:
    """Synchronous call to get idle seconds."""
    plat = _platform()
    if plat == "windows" and FEATURE_IDLE_TIME:
        return _windows_idle_seconds()
    if plat == "macos" and FEATURE_IDLE_TIME:
        return _macos_idle_seconds()
    if plat == "linux" and FEATURE_IDLE_TIME:
        return _linux_idle_seconds()
    return 0.0


# ── State ────────────────────────────────────────────────────────────────

_current: Optional[dict] = None
_lock = asyncio.Lock()
_stop_event = asyncio.Event()
_running = False

# Track the last-seen app for time-block creation
_last_app: Optional[str] = None
_last_block_id: Optional[int] = None
_last_block_start: Optional[str] = None


# ── Public API ────────────────────────────────────────────────────────────


async def start_monitoring(interval: float = 3.0) -> None:
    """Start the background activity collector. Runs until cancelled."""
    global _running
    async with _lock:
        if _running:
            logger.warning("Monitor is already running")
            return
        _running = True
        _stop_event.clear()

    logger.info(
        "Activity monitor started (interval=%ss, platform=%s)",
        interval, _platform(),
    )

    # Run pruning on start
    try:
        import db
        pruned = await db.prune_old_activity(PRUNE_DAYS)
        if pruned:
            logger.info("Pruned %d old activity log entries", pruned)
    except Exception as exc:
        logger.warning("Failed to prune old activity: %s", exc)

    # Launch separate daily pruning task (runs ~every 24h)
    _prune_task = asyncio.create_task(_daily_prune_loop())
    logger.info("Daily pruning background task started (retention=%d days)", PRUNE_DAYS)

    _loop = asyncio.get_event_loop()

    try:
        while not _stop_event.is_set():
            # Run OS calls off the event loop
            app_name, window_title = await _loop.run_in_executor(
                None, _get_active_window_sync
            )
            idle_seconds = await _loop.run_in_executor(
                None, _get_idle_seconds_sync
            )

            now_iso = datetime.now(timezone.utc).isoformat()

            # Update in-memory cache
            global _current, _last_app, _last_block_id, _last_block_start
            async with _lock:
                _current = {
                    "app_name": app_name,
                    "window_title": window_title,
                    "idle_seconds": idle_seconds,
                    "timestamp": now_iso,
                }

            # Persist to DB (non-blocking, already async)
            try:
                import db
                await db.insert_activity(app_name, window_title, idle_seconds)

                # Manage time blocks
                if app_name != _get_last_app_safe() and idle_seconds < IDLE_THRESHOLD:
                    # App changed → close previous block, start new one
                    if _last_block_id is not None:
                        await db.close_time_block(_last_block_id, now_iso)
                    _last_block_id = await db.upsert_time_block(
                        app_name, now_iso, "focus"
                    )
                    _last_app = app_name
                    _last_block_start = now_iso

                elif idle_seconds >= IDLE_THRESHOLD:
                    # Idle → close block if one is open
                    if _last_block_id is not None:
                        await db.close_time_block(_last_block_id, now_iso)
                        _last_block_id = None
                        _last_app = None
                        _last_block_start = None

            except Exception as exc:
                logger.warning("Failed to persist activity: %s", exc)

            # Wait for the next poll cycle
            try:
                await asyncio.wait_for(
                    _stop_event.wait(), timeout=interval
                )
                # If _stop_event was set, break out
                break
            except asyncio.TimeoutError:
                continue

    except asyncio.CancelledError:
        logger.info("Activity monitor cancelled")
    finally:
        async with _lock:
            _running = False
        _prune_task.cancel()
        try:
            await _prune_task
        except asyncio.CancelledError:
            pass
        logger.info("Activity monitor stopped")


async def stop_monitoring() -> None:
    """Signal the collector to stop."""
    _stop_event.set()
    logger.info("Stop signal sent to activity monitor")


def get_current_activity() -> Optional[dict]:
    """Return the most recently observed activity, or None if no data yet."""
    return _current


def get_idle_seconds() -> float:
    """Return seconds since last user input."""
    if _current is not None:
        return _current.get("idle_seconds", 0.0)
    return 0.0


async def get_focus_summary(period: str = "today") -> list[dict]:
    """Return time-block summary for the given period via the DB layer."""
    import db
    return await db.get_focus_summary(period)


# ── Internal helpers ─────────────────────────────────────────────────────


async def _daily_prune_loop() -> None:
    """Background task that prunes old activity log entries once per day.
    Runs until cancelled.  The initial prune already ran in start_monitoring(),
    so we sleep for 24 h before the next check."""
    try:
        while True:
            await asyncio.sleep(86400)  # 24 hours
            try:
                import db
                pruned = await db.prune_old_activity(PRUNE_DAYS)
                if pruned:
                    logger.info("Daily pruning removed %d entries", pruned)
            except Exception as exc:
                logger.warning("Daily pruning failed: %s", exc)
    except asyncio.CancelledError:
        pass


def _get_last_app_safe() -> Optional[str]:
    """Thread-safe access to _last_app (called outside lock in monitor loop)."""
    return _last_app


def is_running() -> bool:
    """Return whether the monitor background task is active."""
    return _running
