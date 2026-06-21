# Horizon Chamber — Activity Tracking & OS Integration (v0.2)

**Status:** Planning — Upgrade from SDD draft to executable implementation plan.

## 1. Summary

Turn the Activity Tracking & OS Integration specification (from `requirements_activity_tracking.md`) into a concrete, shippable feature increment. The majority of the backend (monitor module, DB tables, API endpoints, SSE helper, desktop bridge) has already been implemented. This plan focuses on **verifying completeness, closing remaining gaps, hardening edge cases, and ensuring all acceptance criteria pass**.

**Total estimated effort:** 2–3 hours (mostly verification, polish, and macOS/Linux stubs).

---

## 2. Current Baseline (Already Implemented)

The following subsystems are already in place and need only verification, not reimplementation:

| Subsystem | Files | What's Done |
|-----------|-------|-------------|
| **Monitor module** | `monitor.py` (317 lines) | Platform detection, Windows active-window + idle-time via ctypes, macOS/Linux stubs, async polling loop with configurable interval, in-memory cache, feature flags, DB persistence, time-block management, daily pruning |
| **Database layer** | `db.py` (323 lines) | `activity_log`, `time_blocks`, `app_categories` tables with full CRUD: `insert_activity`, `upsert_time_block`, `close_time_block`, `get_focus_summary`, `set_app_category`, `get_app_category`, `get_all_app_categories`, `prune_old_activity`, `init_db` with schema version migration (v1→v2) |
| **SSE helper** | `sse.py` (86 lines) | `sse_event()` formatter, `sse_response()` with keep-alive pings |
| **API routes** | `main.py` (280 lines) | `GET /api/activity/now`, `GET /api/activity/summary`, `GET /api/activity/stream` (SSE), `PUT /api/activity/categories`, `GET /api/activity/categories`, `GET /api/activity/history`. Monitor starts in `lifespan` when `AUTO_START_MONITOR=true` |
| **Desktop bridge** | `desktop_app.py` (221 lines) | `HorizonApi` class with `get_active_window()`, `get_idle_time()`, `open_path()`, `set_wallpaper()`. Sets `AUTO_START_MONITOR=true` by default in desktop mode. Server starts in background thread, graceful shutdown on window close |
| **Frontend** | `static/index.html` (1376 lines) | Activity indicator pill, focus panel with bar chart, SSE client with polling fallback, PyWebView bridge detection (`hasJsApi`), app category toggles, `startActivityTracking()` called on DOMContentLoaded |
| **Monitor tests** | `tests/test_monitor.py` (135 lines) | Platform detection, fallback behavior, start/stop cycle, feature flag defaults, Windows-specific tests, macOS/Linux stub tests |
| **Activity API tests** | `tests/test_activity_api.py` (139 lines) | Activity now endpoint, summary (empty + with data), category CRUD, invalid category rejection, history pagination |
| **Configuration** | `.env` | `AUTO_START_MONITOR=false`, `ACTIVITY_POLL_INTERVAL=3`, `ACTIVITY_IDLE_THRESHOLD=60`, `ACTIVITY_PRUNE_DAYS=30` |
| **Documentation** | `readme.md` (106 lines) | Activity tracking section with API table, feature descriptions, privacy note, env var table |

---

## 3. Remaining Gaps (Ordered by Priority)

### Gap G-1: Activity DB Tests Missing
`tests/test_db.py` only tests `chaos` table operations (v0.1). No tests exist for `activity_log`, `time_blocks`, or `app_categories` functions.

### Gap G-2: macOS & Linux Active-Window / Idle-Time Are Stubs
`monitor.py` has `_macos_active_window()`, `_macos_idle_seconds()`, `_linux_active_window()`, `_linux_idle_seconds()` — all return fallback values (`"unknown"`, `0.0`). Not implemented.

### Gap G-3: `FEATURE_PROCESS_LIST` Flag Unused in Collection Logic
The flag exists as a module constant but no process-list collection code branches on it. Either implement the process-list check or document it as always-off.

### Gap G-4: Schema Version Migration Code Truncated / May Be Incomplete
`db.py` contains `init_db()` with `_get_schema_version` / `_set_schema_version` but the migration logic between versions 1→2 needs verification — the file was truncated during inspection.

### Gap G-5: Frontend Focus Panel Bar Chart Needs Visual Verification
The `updateFocusPanel()` function exists but the rendering approach (Canvas vs div-based bars) and the bar chart styling have not been visually confirmed. Category toggle on app bars must also be verified.

### Gap G-6: No Integration Test for Full SSE Stream Flow
The existing SSE endpoint test only covers the route returning correct headers; no test verifies that the event generator yields actual `activity` events when the monitor updates.

### Gap G-7: Pruning Check Shares Interval With Poll Loop
The daily pruning check in `start_monitoring()` runs on every poll iteration (every ~3s) with an approximate day-number guard. This is wasteful — a cleaner approach would check once at startup or via a separate scheduled task.

---

## 4. Pre-mortem

Imagine this shipped and failed. Work backwards.

### Tigers (Real problems — act on them)

| Tiger | Classification | Mitigation | Owner | Decision Date |
|-------|---------------|------------|-------|---------------|
| macOS/Linux users see only "unknown" — perceived as broken | Launch-Blocking | Implement subprocess-based fallbacks (osascript for macOS, xdotool/xprintidle for Linux) before shipping v0.2. Document degraded mode clearly. | Backend | Before v0.2 tag |
| Activity DB grows unbounded if pruning fails silently | Launch-Blocking | Add pruning to `lifespan` shutdown + log any pruning errors at WARNING level; add a `PRAGMA`-based size cap as second line of defense | Backend | Before v0.2 tag |
| SSE stream holds stale DB connection or leaks file handles | Launch-Blocking | Verify `sse.py` uses no DB connections during streaming (only reads from in-memory `get_current_activity()`). Confirm `StreamingResponse` is garbage-collected on disconnect. | Backend | Before v0.2 tag |
| Frontend polls `/api/activity/summary` every 30s even when panel is hidden — wasteful | Fast-Follow | Only poll focus summary when the focus panel is visible. Use `IntersectionObserver` or a visibility check. | Frontend | +7 days |
| Desktop bridge `HorizonApi` methods called on JS thread may block UI | Fast-Follow | Ensure all bridge calls that hit the OS (`get_active_window`, `get_idle_time`) complete in < 50ms. If they exceed, wrap in `asyncio.to_thread`. | Backend | +14 days |

### Paper Tigers (Others worry, we don't — document)

- *"Activity tracking is a privacy concern"* — Already addressed: OFF by default in browser mode, ON in desktop mode, all data stays local in SQLite. Documented in `readme.md`.
- *"SSE might not work behind reverse proxies"* — Already set `X-Accel-Buffering: no` header. nginx/gunicorn users must configure their proxies; out of scope for the app itself.

### Elephants (Unspoken, investigate before committing)

- *Will the focus panel bar chart work at small window sizes?* — Need to verify responsive breakpoints in the frontend.
- *Does PyWebView's JavaScript bridge handle rapid polling (every 3s) gracefully?* — Need to verify with a real Windows desktop session.

---

## 5. Implementation Steps

### Phase 0: Verification & Audit (30 min)
*High-value, low-effort — done first so we know what's truly broken.*

- [ ] **V-1:** Read `db.py` migration logic between lines 70–110 to confirm version 1→2 migration works. If missing or broken, fix it. (covers: G-4)
- [ ] **V-2:** Run `pytest tests/ -v` and capture full output. All existing tests must pass before any changes. (covers: Acceptance Criteria #6)
- [ ] **V-3:** Start the server (`uvicorn main:app --reload`) and manually test:
  - `GET /api/health` returns 200
  - `GET /api/activity/now` returns valid structure (even with unknown/fallback)
  - `PUT /api/activity/categories` with valid/invalid category
  - `GET /api/activity/summary?period=today` returns block structure
  - SSE endpoint at `/api/activity/stream` returns `text/event-stream` headers
- [ ] **V-4:** Visually inspect the frontend by loading `http://localhost:8000`:
  - Activity indicator visible in top bar area
  - Focus panel visible (right sidebar or bottom section)
  - Bar chart renders with dummy data after 2 seconds
  - SSE connection starts (check browser dev tools Network tab)
  - Category toggle works (click on an app bar)
  - (covers: G-5)

### Phase 1: Fix Gaps Found in Audit (45 min)
*Fix the problems identified in Phase 0.*

- [ ] **DB migration fix:** If schema version tracking is incomplete, add explicit version compare and ALTER TABLE / CREATE TABLE logic inside `init_db()`. (covers: G-4)
- [ ] **Activity DB tests:** Add tests to `tests/test_db.py` for:
  - `insert_activity` and `get_recent_activity`
  - `upsert_time_block` / `close_time_block` / `get_focus_summary`
  - `set_app_category` / `get_app_category` / `get_all_app_categories`
  - `prune_old_activity`
  - Each test gets its own temp database via `tmp_path` fixture. (covers: G-1)
- [ ] **Process-list flag documentation:** Add a comment in `monitor.py` near `FEATURE_PROCESS_LIST = False` noting that the flag is reserved for v0.3 and currently no code branches on it. Remove the flag check from collection if it's dead code. (covers: G-3)
- [ ] **Pruning efficiency:** Move the daily pruning check to a separate `asyncio.create_task` that runs once at monitor startup (after the initial pruning) and then sleeps for 24h, rather than checking on every poll cycle. (covers: G-7)

### Phase 2: macOS & Linux Implementations (45 min)
*Highest user-impact gap — fix for cross-platform credibility.*

- [ ] **macOS active window:** Replace `_macos_active_window()` stub with:
  - Subprocess call to `osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true'`
  - Subprocess call to `osascript -e 'tell application "System Events" to get title of first window of (first application process whose frontmost is true)'`
  - Wrap in try/except; log warnings on failure; return `("unknown", "")` on error.
  - (covers: Phase 2 in SDD — macOS stubs)
- [ ] **macOS idle time:** Replace `_macos_idle_seconds()` stub with:
  - Subprocess call to `ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print $NF/1000000000; exit}'`
  - Wrap in try/except; return `0.0` on error.
  - (covers: Phase 2 in SDD — macOS stubs)
- [ ] **Linux active window:** Replace `_linux_active_window()` stub with:
  - Subprocess call to `xdotool getactivewindow getwindowname`
  - Subprocess call to `xdotool getactivewindow getwindowpid` → read `/proc/<pid>/comm` for app name
  - Wrap in try/except; return `("unknown", "")` on error.
  - (covers: Phase 2 in SDD — Linux stubs)
- [ ] **Linux idle time:** Replace `_linux_idle_seconds()` stub with:
  - Subprocess call to `xprintidle` (result is in milliseconds, divide by 1000)
  - Wrap in try/except; return `0.0` on error.
  - (covers: Phase 2 in SDD — Linux stubs)
- [ ] **Update macOS/Linux tests:** Update `test_monitor.py` to mock subprocess calls for macOS/Linux and verify correct parsing. (covers: G-2)

### Phase 3: SSE Integration Test (15 min)
- [ ] In `tests/test_activity_api.py`, add an SSE-specific test:
  - Start monitor in test, toggle `_current` state, then call `/api/activity/stream` and verify the event generator yields the expected `activity` event.
  - Use `httpx` with streaming or FastAPI `TestClient` streaming support.
  - (covers: G-6)

### Phase 4: Frontend Polish & Verification (30 min)
- [ ] **Responsive focus panel:** Verify the focus panel bar chart works at widths down to 900px (the desktop min_size). If bars overflow, add horizontal scrolling or truncation.
- [ ] **Category toggle interaction:** Verify clicking an app bar in the focus panel sends `PUT /api/activity/categories` correctly. Fix any broken event handlers.
- [ ] **Reduced-motion respect:** Ensure the activity indicator does not animate (blinking/pulsing) when `prefers-reduced-motion: reduce` is set. Add `@media (prefers-reduced-motion: reduce)` overrides.
- [ ] **SSE reconnection:** Verify that if the SSE connection drops, the frontend falls back to polling and attempts to reconnect every 30s. The `EventSource` built-in reconnection may handle this; verify no infinite reconnect loops.
- [ ] (covers: Phase 4 in SDD)

### Phase 5: Final Verification (15 min)
- [ ] **Full test suite:** Run `pytest tests/ -v` — all tests pass, including new ones. (covers: Acceptance Criteria #6, #7)
- [ ] **Browser mode verification:** Open `http://localhost:8000` in a regular browser tab (no PyWebView). Activity indicator shows "unknown" with no errors. Focus panel shows "No activity data yet." No console errors. (covers: Acceptance Criteria #5)
- [ ] **Desktop mode verification (Windows only):** Run `python desktop_app.py`. Activity indicator updates within 5 seconds of switching apps. SSE stream delivers live updates. (covers: Acceptance Criteria #1, #4)
- [ ] **Idle detection:** Set `ACTIVITY_IDLE_THRESHOLD=5` in `.env` and restart. Walk away from computer for >5 seconds. Activity indicator shows idle_seconds > 5. (covers: Acceptance Criteria #2)
- [ ] **DB pruning:** Verify that after setting `ACTIVITY_PRUNE_DAYS=0` and inserting old data, the next monitor start prunes it. Check via `SELECT COUNT(*) FROM activity_log`.
- [ ] **No startup regression:** Measure server startup time with and without `AUTO_START_MONITOR=true`. Should be approximately equal. (covers: Acceptance Criteria #8)

---

## 6. Explicitly Deferred to v0.3/v0.4

Per the SDD Non-Goals section, the following remain deferred:

| Feature | Target |
|---------|--------|
| Global hotkey registration | v0.4 |
| System tray icon | v0.4 |
| File system watcher/organizer | v0.4 |
| Monitor brightness control | v0.4 |
| Full process tree monitoring (`FEATURE_PROCESS_LIST`) | v0.3 |
| Cloud sync of activity data | v0.4 |
| Browser extension for tab tracking | Separate project |
| Native C/Rust OS hooks | v0.4 |
| Real-time screenshot capture | Deferred indefinitely |
| React/framework migration | Deferred |

---

## 7. Acceptance Criteria

1. **Monitor runs reliably:** Start desktop app, switch apps; `/api/activity/now` returns active window within 5 seconds.
2. **Idle detection works:** Walk away for 2 minutes; `/api/activity/now` shows `idle_seconds >= 120`.
3. **Summary aggregates correctly:** After a few hours of use, `/api/activity/summary?period=today` shows a reasonable app breakdown.
4. **SSE streams live:** Activity indicator updates in real time as you switch apps. No manual refresh needed.
5. **Browser mode degrades gracefully:** In regular browser tab, activity shows `"unknown"` with no errors, no crashes.
6. **All existing tests pass:** `pytest tests/ -v` exits 0.
7. **All new tests pass:** Phase-specific test suites pass before moving to next phase.
8. **No increased startup time:** `desktop_app.py` starts in the same time as before (monitor starts async, doesn't block window open).
9. **DB doesn't grow unbounded:** After 30 days (configurable via `ACTIVITY_PRUNE_DAYS`), old `activity_log` entries are pruned.

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| macOS `osascript` subprocess blocks event loop | Low (macOS only) | Medium | Wrap in `asyncio.to_thread()` — already done for all OS calls |
| Linux `xdotool` not installed on user machine | Medium (Linux only) | Low | `try/except` returns fallback; log a one-time warning suggesting `apt install xdotool` |
| SSE connection leaks on page navigation | Low | Medium | EventSource closes on page unload by default. Verify `close()` in `window.onbeforeunload`. |
| Frontend focus panel empty on fresh install | High | Low | Show "No activity data yet. Start using your computer!" placeholder message. |
