# Horizon Chamber — Activity Tracking & OS Integration (v0.2–v0.3)

## Context

This requirement file describes the refactor and new capabilities needed to make Horizon Chamber **aware of what the user does on their computer** — active window tracking, idle detection, focus time summaries, and real-time activity display in the UI. It is a prerequisite for the longer-term vision (v0.4 "The System" — global hotkeys, file system integration, OS-level hooks).

The existing MVP is a FastAPI + vanilla JS + SQLite app with a PyWebView desktop wrapper. See `specifications.md` for the current baseline.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│                     Desktop App                       │
│  ┌─────────────┐   HTTP/SSE   ┌────────────────────┐ │
│  │  HTML/JS    │ ◄──────────► │  FastAPI server     │ │
│  │  (WebView)  │              │  ┌──────────────┐  │ │
│  └─────────────┘              │  │  monitor.py  │  │ │
│                                │  │ (new module) │  │ │
│                                │  └──────┬───────┘  │ │
│                                │         │          │ │
│                                │  ┌──────▼───────┐  │ │
│                                │  │   SQLite DB  │  │ │
│                                │  │  (new tables)│  │ │
│                                │  └──────────────┘  │ │
│                                └────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

**Key constraint:** The activity tracker must work in both browser development mode AND the desktop app. When running in the browser, active-window tracking will be limited (browsers can't access the OS); the server should degrade gracefully. When running in the desktop app via PyWebView, the JS bridge provides direct OS access.

---

## 2. New Module: `monitor.py`

A new Python module responsible for collecting activity data from the OS. It runs as an asyncio background task (same pattern as `scheduler.py`'s `_check_schedule_loop`).

### 2.1 Core Collector

```python
# monitor.py — public API surface

async def start_monitoring(interval: float = 3.0) -> None:
    """Start the background activity collector. Runs until cancelled."""

async def stop_monitoring() -> None:
    """Signal the collector to stop."""

def get_current_activity() -> dict | None:
    """Return the most recently observed activity, or None if no data yet.
    Returns {"app_name": str, "window_title": str, "timestamp": ISO str}"""

def get_idle_seconds() -> float:
    """Return seconds since last user input (keyboard/mouse)."""

def get_focus_summary(period: str = "today") -> list[dict]:
    """Return time-block summary for the given period.
    period: "today", "yesterday", "last_7_days"
    Returns list of {app_name, total_seconds, percentage}"""
```

### 2.2 Implementation Strategy (Cross-Platform)

The collector must detect the host OS and use the appropriate API:

| Capability | Windows | macOS | Linux | Fallback |
|------------|---------|-------|-------|----------|
| Active window title | `ctypes` + `user32.dll` (`GetForegroundWindow`, `GetWindowText`) | `Quartz` + `CGWindowListCopyWindowInfo` (or subprocess `osascript`) | `subprocess` + `xdotool getactivewindow getwindowname` | Return `"unknown"` app, `""` title |
| Idle time | `ctypes` + `user32.dll` (`GetLastInputInfo`) | `Quartz` + `CGEventSourceSecondsSinceLastEventType` (or `IOKit` via subprocess) | `subprocess` + `xprintidle` | Return `0.0` (assume active) |
| Process list | `psutil` (optional dependency) | `psutil` | `psutil` | Skip rich process data |

**Detection helper:**
```python
def _platform() -> str:
    import sys
    if sys.platform == "win32": return "windows"
    if sys.platform == "darwin": return "macos"
    return "linux"
```

**All platform-specific code must live inside `if _platform() == "windows":` etc. blocks**, with a single fallback path that returns safe defaults. The module must **never crash** — a failed OS call should log a warning and return the fallback value.

### 2.3 Background Collection Loop

Runs forever (or until cancelled), polling every N seconds (default: 3s). On each tick:

1. Get the active window title via the platform-specific method.
2. Get the idle time.
3. Compare to previous observation:
   - If the app changed AND idle < threshold (default: 60s), record a time block for the previous app.
   - If idle > threshold, mark the block as "away" without closing it.
4. Write blocks and activity samples to SQLite.
5. Store the current activity in an in-memory cache (for `get_current_activity()`).

**Interval should be configurable:** read from `ACTIVITY_POLL_INTERVAL` env var, default `3`.

### 2.4 Performance Requirements

- The collection loop must never block the asyncio event loop. All OS calls should be run via `asyncio.to_thread()` or `loop.run_in_executor()`.
- `ctypes` calls are synchronous — they MUST run off the event loop.
- `psutil` calls (if used) can also block — same treatment.

### 2.5 Feature Flags

Each collector capability should be gated behind a feature flag (env var or constant), so individual features can be disabled:

```python
FEATURE_ACTIVE_WINDOW = True   # track which app/window is in focus
FEATURE_IDLE_TIME = True       # track idle/away periods
FEATURE_PROCESS_LIST = False   # track full process tree (heavier, off by default)
```

---

## 3. Database Changes

### 3.1 New Tables

Add these to `db.py`'s `SCHEMA_SQL` (or a separate migration):

```sql
-- Activity samples — raw poll observations
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL DEFAULT 'unknown',
    window_title TEXT NOT NULL DEFAULT '',
    idle_seconds REAL NOT NULL DEFAULT 0.0,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Time blocks — aggregated focus periods
CREATE TABLE IF NOT EXISTS time_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'focus'
        CHECK(category IN ('focus', 'distraction', 'idle', 'away')),
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    duration_seconds INTEGER
);

-- App categorization — user-defined (or LLM-suggested) labels
CREATE TABLE IF NOT EXISTS app_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'focus'
        CHECK(category IN ('focus', 'distraction', 'neutral', 'idle', 'away')),
    label TEXT,  -- optional user-friendly name
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 DB Layer Functions

Add to `db.py` (or a new `db_activity.py` if preferred for separation):

```python
# Activity log
async def insert_activity(app_name: str, window_title: str, idle_seconds: float) -> int
    """Insert a raw activity sample. Returns the new row id."""

async def get_recent_activity(limit: int = 50) -> list[dict]
    """Return the most recent activity samples."""

# Time blocks
async def upsert_time_block(app_name: str, start_time: str, ...) -> int
    """Create or update a time block. Returns block id."""

async def close_time_block(block_id: int, end_time: str) -> None
    """Set end_time and duration_seconds for a block."""

async def get_time_blocks(period: str = "today", limit: int = 100) -> list[dict]
    """Return time blocks for today, yesterday, last_7_days, or all."""

async def get_focus_summary(period: str = "today") -> list[dict]
    """Aggregated: app_name → total_seconds, percentage for the given period."""

# App categorization
async def set_app_category(app_name: str, category: str, label: str = None) -> None
    """Insert or update an app's category."""

async def get_app_category(app_name: str) -> str | None
    """Return the category for an app, or None if not set."""

async def get_all_app_categories() -> list[dict]
    """Return all categorized apps."""

# Maintenance
async def prune_old_activity(days: int = 30) -> int
    """Delete activity_log entries older than `days`. Returns deleted count."""
```

### 3.3 Migration Strategy

- On startup (`init_db`), execute `CREATE TABLE IF NOT EXISTS` for the new tables — same pattern as the existing `chaos` table.
- No breaking changes to the `chaos` table.
- Run `PRAGMA user_version` to track schema version. Set to `2` after migration.

---

## 4. New API Endpoints

All new endpoints follow the existing pattern in `main.py` (Pydantic models, async handlers, consistent error responses).

### 4.1 Activity — Current State

```
GET /api/activity/now
  → 200 {"app_name": "code.exe", "window_title": "horizon/main.py — Visual Studio Code", "idle_seconds": 2.3, "timestamp": "2025-01-15T14:30:00"}
  → 200 {"app_name": "unknown", "window_title": "", "idle_seconds": 0.0, "timestamp": "..."}
       (when no data yet or browser mode)
```

### 4.2 Activity — Summary

```
GET /api/activity/summary?period=today
  → 200 {
       "period": "today",
       "total_seconds": 28800,
       "blocks": [
         {"app_name": "code.exe", "category": "focus", "total_seconds": 14400, "percentage": 50.0},
         {"app_name": "chrome.exe", "category": "neutral", "total_seconds": 7200, "percentage": 25.0},
         ...
       ]
     }

Query params:
  period = "today" | "yesterday" | "last_7_days"  (default: "today")
```

### 4.3 Activity — Stream (SSE)

```
GET /api/activity/stream
  → SSE stream (text/event-stream)
  → Events:
      event: activity
      data: {"app_name": "code.exe", "window_title": "...", "idle_seconds": 2.3, "timestamp": "..."}

  → Sent every time the monitor observes a change (debounced at 1s).
  → If activity tracking is unavailable, send a single "unavailable" event and close.
```

Implementation note: use FastAPI's `StreamingResponse` with `media_type="text/event-stream"`, same pattern as:

```python
from starlette.responses import StreamingResponse
import asyncio, json

@app.get("/api/activity/stream")
async def activity_stream():
    async def event_generator():
        last_data = None
        while True:
            current = monitor.get_current_activity()
            if current and current != last_data:
                last_data = current
                yield f"event: activity\ndata: {json.dumps(current)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### 4.4 App Categories

```
PUT /api/activity/categories
  Body: {"app_name": "chrome.exe", "category": "distraction", "label": "Google Chrome"}
  → 200 {"ok": true}

GET /api/activity/categories
  → 200 {"categories": [{"app_name": "code.exe", "category": "focus", "label": "VS Code"}, ...]}
```

### 4.5 Activity History

```
GET /api/activity/history?limit=50
  → 200 {"entries": [{"id": 1, "app_name": "...", "window_title": "...", "idle_seconds": 2.3, "timestamp": "..."}, ...]}
```

---

## 5. SSE Infrastructure (Reusable)

Add a lightweight SSE helper module `sse.py` that the activity, sunrise, and any future streaming endpoints can share:

```python
# sse.py — reusable SSE event helpers

import asyncio, json
from typing import AsyncIterator
from starlette.responses import StreamingResponse

def sse_response(generator: AsyncIterator, *, ping_interval: float = 15.0) -> StreamingResponse:
    """Wrap an async generator in an SSE StreamingResponse with keep-alive pings."""
    ...

def sse_event(event: str, data: dict | str) -> str:
    """Format a single SSE event string."""
    ...
```

This avoids duplicating SSE boilerplate across routes.

---

## 6. Frontend Changes

### 6.1 New UI Elements

Add the following to `static/index.html` (keep vanilla JS, no frameworks):

1. **Activity indicator** — small pill in the top bar (next to "Horizon Chamber" title):
   - Shows current app icon/name: `🖥️ VS Code` or `🌐 Chrome`
   - Shows idle indicator: `💤 Away (5m)` when idle > 60s
   - Pulses subtly when activity changes

2. **Focus panel** — replaces or augments the current `#today-panel` on the right side:
   - A simple horizontal bar chart showing today's top apps by time
   - Each bar is color-coded by category (focus = gold, distraction = red, neutral = gray)
   - Click a bar to toggle that app's category

3. **Feed/focus timeline** (optional, v0.3) — a scrollable timeline of today's activity blocks, like "9:00 AM–11:30 AM: VS Code (focus)" — but this can be deferred.

### 6.2 SSE Client

Add a reusable SSE connection manager in the frontend JS:

```javascript
// sseManager — connects to /api/activity/stream and dispatches events
const activitySSE = new EventSource('/api/activity/stream');
activitySSE.addEventListener('activity', (e) => {
    const data = JSON.parse(e.data);
    updateActivityIndicator(data);
    updateFocusPanel(data);
});
activitySSE.addEventListener('error', () => {
    // SSE disconnected — fall back to polling every 30s
    startActivityPolling();
});
```

**Fallback behavior:** If SSE fails (browser doesn't support it, or server doesn't have monitor data), the frontend should silently degrade — poll `/api/activity/now` every 30s instead.

### 6.3 Styling

- Use existing glassmorphism style (`.glass` class) for any new panels.
- Respect existing `prefers-reduced-motion` and responsive breakpoints.
- Activity indicator should be subtle — opacity 0.5–0.7, small font, no animation by default (optional subtle pulse can be toggled).

---

## 7. Desktop App Changes (`desktop_app.py`)

### 7.1 PyWebView JS Bridge (Optional Enhancement)

Expose system commands to the frontend via PyWebView's `js_api`:

```python
class HorizonApi:
    """Python API exposed to JavaScript via PyWebView bridge."""

    def get_active_window(self) -> dict:
        """Return the current foreground window info."""
        return monitor.get_current_activity() or {}

    def get_idle_time(self) -> float:
        """Return idle seconds."""
        return monitor.get_idle_seconds()

    def open_path(self, path: str) -> None:
        """Open a file or folder in the OS default handler."""
        import os, subprocess, sys
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])

    def set_wallpaper(self, image_path: str) -> None:
        """Change the desktop wallpaper (Windows only for now)."""
        import ctypes
        ctypes.windll.user32.SystemParametersInfoW(20, 0, image_path, 0)
```

Register it in `webview.create_window(..., js_api=HorizonApi())`.

**In the frontend**, check for the bridge:

```javascript
const hasJsApi = typeof pywebview !== 'undefined' && pywebview.api;
if (hasJsApi) {
    // Use direct bridge for OS calls
    const activity = await pywebview.api.get_active_window();
} else {
    // Fall back to HTTP/SSE
    const res = await fetch('/api/activity/now');
    const activity = await res.json();
}
```

### 7.2 Startup Integration

In `desktop_app.py`'s `main()`, after starting the server, also start the monitor:

```python
# Start the activity monitor
import monitor
monitor_task = asyncio.create_task(monitor.start_monitoring())
```

The monitor should start automatically when the desktop app runs. In browser mode (uvicorn), the user starts it via an API call or env var (`AUTO_START_MONITOR=true`).

---

## 8. Environment & Configuration

New env vars (in `.env`):

```bash
# Activity tracking
AUTO_START_MONITOR=true        # Start monitor on server boot (default: false for browser, true for desktop)
ACTIVITY_POLL_INTERVAL=3       # Seconds between activity checks (default: 3)
ACTIVITY_IDLE_THRESHOLD=60     # Seconds of idle before marking "away" (default: 60)
ACTIVITY_PRUNE_DAYS=30         # Auto-delete activity_log entries older than N days (default: 30)
ACTIVITY_DB_PATH=./horizon.db  # Same DB as chaos table (reuse existing DATABASE_PATH)
```

---

## 9. Tests

Create `tests/test_monitor.py`:

| Test | What it verifies |
|------|-----------------|
| `test_platform_detection` | `_platform()` returns valid string for the host OS |
| `test_active_window_fallback` | On unsupported platform, returns `{"app_name": "unknown", ...}` without crashing |
| `test_idle_time_fallback` | `get_idle_seconds()` returns `0.0` on unsupported platform |
| `test_current_activity_none_before_start` | `get_current_activity()` returns `None` before monitoring starts |
| `test_start_stop_monitoring` | `start_monitoring()` runs, `stop_monitoring()` cancels cleanly |
| `test_activity_log_insert` | Insert and retrieve a sample via DB layer |
| `test_time_block_upsert_close` | Create a block, then close it, verify duration |
| `test_focus_summary_today` | Insert blocks, query summary, verify aggregation |
| `test_prune_old_activity` | Insert old + new entries, prune, verify old ones gone |
| `test_app_category_crud` | Set, get, update app categories |
| `test_monitor_does_not_block_event_loop` | Ensure OS calls run in executor, not on event loop |

Create `tests/test_activity_api.py`:

| Test | What it verifies |
|------|-----------------|
| `test_activity_now_endpoint` | 200 with valid structure |
| `test_activity_summary_endpoint` | 200 with period param, valid aggregation |
| `test_activity_stream_sse` | SSE headers correct, events arrive |
| `test_activity_stream_unavailable` | When monitor is off, stream closes gracefully |
| `test_put_get_categories` | PUT then GET returns saved category |
| `test_categories_invalid_category` | 422 on bad category name |
| `test_activity_history_pagination` | Limit param works |

---

## 10. File Structure After Refactor

```
/main.py                    ← Add activity + SSE routes, start monitor in lifespan
/scheduler.py               ← Unchanged
/db.py                      ← Add new tables + activity DB functions
/monitor.py                 ← NEW: Activity collector
/sse.py                     ← NEW: SSE helper utilities
/deepseek_client.py         ← Unchanged
/desktop_app.py             ← Add monitor startup, PyWebView JS bridge
/build.py                   ← Unchanged
/static/
  index.html                ← Add activity indicator, SSE client, focus panel
/tests/
  test_monitor.py           ← NEW
  test_activity_api.py      ← NEW
  test_db.py                ← Add activity DB tests
  test_api.py               ← Unchanged (or add activity route tests here)
  test_deepseek_client.py   ← Unchanged
  test_main.py              ← Unchanged
  test_scheduler.py         ← Unchanged
/.env                       ← Add new env vars
```

---

## 11. Implementation Phases

Execute in order. Each phase must pass all existing + new tests before proceeding.

### Phase 1: Database Foundation (1–2 hours)

- [ ] Add `activity_log`, `time_blocks`, `app_categories` tables to `db.py`
- [ ] Add all DB layer functions listed in §3.2
- [ ] Add `PRAGMA user_version` migration tracking
- [ ] Write and pass `test_db.py` additions

### Phase 2: Monitor Module (2–4 hours)

- [ ] Create `monitor.py` with platform detection
- [ ] Implement Windows active window + idle time (ctypes)
- [ ] Implement macOS + Linux stubs/fallbacks
- [ ] Implement `asyncio.to_thread()` wrapping for all OS calls
- [ ] Implement background collection loop with configurable interval
- [ ] In-memory cache for `get_current_activity()`
- [ ] Feature flags for individual capabilities
- [ ] Write and pass `tests/test_monitor.py`

### Phase 3: API Endpoints (1–2 hours)

- [ ] Add `GET /api/activity/now`
- [ ] Add `GET /api/activity/summary`
- [ ] Add `GET /api/activity/stream` (SSE)
- [ ] Add `PUT /api/activity/categories`
- [ ] Add `GET /api/activity/categories`
- [ ] Add `GET /api/activity/history`
- [ ] Create `sse.py` helper module
- [ ] Start monitor in `lifespan` if `AUTO_START_MONITOR=true`
- [ ] Write and pass `tests/test_activity_api.py`

### Phase 4: Frontend (2–3 hours)

- [ ] Add activity indicator pill to top bar
- [ ] Add focus panel with bar chart (Canvas or div-based)
- [ ] Add SSE client with polling fallback
- [ ] Add category toggle on app bars
- [ ] Style everything with existing glassmorphism theme
- [ ] Respect reduced-motion and responsive breakpoints
- [ ] Manual testing: browser mode + desktop mode

### Phase 5: Desktop Integration (1 hour)

- [ ] Add `HorizonApi` class with PyWebView JS bridge
- [ ] Add `monitor.start_monitoring()` to desktop startup
- [ ] Add JS-side bridge detection + graceful fallback
- [ ] Manual testing: verify OS data flows through bridge

### Phase 6: Polish (1 hour)

- [ ] Add all new env vars to `.env` with defaults
- [ ] Update `readme.md` with activity tracking section
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Verify no regressions in existing features (health, classify, sunrise, today)

---

## 12. Non-Goals (Explicitly Out of Scope)

-   ❌ Global hotkey registration — v0.4
-   ❌ System tray icon — v0.4
-   ❌ File system watcher/organizer — v0.4
-   ❌ Monitor brightness control — v0.4
-   ❌ Full process tree monitoring (wait for `FEATURE_PROCESS_LIST` flag)
-   ❌ Cloud sync of activity data — stays local
-   ❌ Browser extension for tab tracking — separate project
-   ❌ Native C/Rust OS hooks — Python-only for now
-   ❌ Real-time screenshot capture — privacy risk, deferred
-   ❌ Any React/framework migration — keep vanilla JS

---

## 13. Acceptance Criteria

1. **Monitor runs reliably:** Start the desktop app, switch between apps, and `/api/activity/now` returns the active window within 5 seconds.
2. **Idle detection works:** Walk away from the computer for 2 minutes, `/api/activity/now` shows `idle_seconds >= 120`.
3. **Summary aggregates correctly:** Use the computer for a few hours, then `/api/activity/summary?period=today` shows a reasonable breakdown by app.
4. **SSE streams live:** Open the frontend, the activity indicator updates in real time as you switch apps. No manual refresh.
5. **Browser mode degrades gracefully:** Open in a regular browser tab — activity shows `"unknown"` with no errors, no crashes.
6. **All existing tests still pass:** `pytest tests/ -v` exits 0.
7. **All new tests pass:** Phase-specific test suites pass before moving to next phase.
8. **No increased startup time:** `desktop_app.py` starts in the same time as before (monitor starts async, doesn't block window open).
9. **DB doesn't grow unbounded:** After 30 days, old activity_log entries are pruned. Default retention is configurable.
