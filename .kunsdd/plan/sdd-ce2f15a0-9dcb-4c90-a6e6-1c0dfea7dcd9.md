# Critical Bug Fixes — Activity Tracking v0.2

## Summary

A code review of the completed Activity Tracking & OS Integration (v0.2) implementation identified three concrete bugs that will cause runtime failures. This plan fixes all three.

**Total effort:** ~15 minutes.

---

## B-1: Missing `import os` in `desktop_app.py` [P0 — Launch-Blocking]

### Problem
`desktop_app.py` uses `os.environ` (line ~149: setting `AUTO_START_MONITOR`) and `os.startfile()` (line ~177: in `HorizonApi.open_path()`) but never imports `os`. The only imports are `argparse`, `logging`, `socket`, `sys`, `threading`, `time`, and `pathlib.Path`. This will cause a `NameError: name 'os' is not defined` on startup.

### Fix
Add `import os` to the top-level imports in `desktop_app.py`.

### Test
The existing test suite doesn't cover `desktop_app.py` directly (it's a launcher script). Manually run `python desktop_app.py --port 9999 --debug` (with `AUTO_START_MONITOR=...` already set or unset) and verify it starts without a NameError.

---

## B-2: Sunrise Trigger Flag Never Consumed via API [P1 — Launch-Blocking]

### Problem
The `scheduler.py` module has a `consume_sunrise_trigger()` function that correctly checks and resets the `sunrise_triggered` flag. However, no API endpoint ever calls this function. The frontend polls `GET /api/sunrise/schedule` every 15 seconds, which calls `scheduler.get_schedule()` — which returns the raw `sunrise_triggered` value without consuming it. The frontend's `checkSunriseTrigger()` handler calls `startSunriseSequence()` every time `sunrise_triggered` is true. Result: an infinite loop of sunrise restarts every ~75 seconds.

### Fix
In `main.py`, modify `sunrise_schedule_get()` to call `scheduler.consume_sunrise_trigger()` and return the consumed value instead of the raw flag. The response shape stays the same (`{enabled, time, sunrise_triggered}`) but the flag will be False after the first read.

```python
# Before
@app.get("/api/sunrise/schedule")
async def sunrise_schedule_get():
    return scheduler.get_schedule()

# After
@app.get("/api/sunrise/schedule")
async def sunrise_schedule_get():
    schedule = scheduler.get_schedule()
    # Consume the trigger flag so the sunrise only fires once
    schedule["sunrise_triggered"] = scheduler.consume_sunrise_trigger()
    return schedule
```

### Test
In `tests/test_scheduler.py`, the existing `test_schedule_consume_trigger` already verifies `consume_sunrise_trigger()` returns True once and False thereafter. Add an API-level test in `tests/test_api.py`:

- `test_sunrise_schedule_consumes_trigger`: Call the endpoint, verify `sunrise_triggered` changes from True to False.

---

## B-3: Data Race on `_last_app` Between Check and Locked Update [P1 — Fast-Follow]

### Problem
In `monitor.py`'s `start_monitoring()` loop, the condition `app_name != _get_last_app_safe()` (line ~306) reads `_last_app` outside the lock, while `_last_app` is assigned inside the `async with _lock:` block (line ~313). The check is not atomic with the assignment, so two nearly-concurrent poll cycles could both see the same `_last_app` value and both skip creating a new time block — or both create one.

While the monitor loop is fundamentally sequential (each iteration awaits the OS calls, DB writes, and sleep), the read-outside-lock of a shared global could still cause issues if `get_current_activity()` or `get_focus_summary()` is called from another coroutine (SSE endpoint, API endpoint) while the monitor loop is mid-iteration.

### Fix
Move the `_last_app` read inside the lock. Restructure the time-block management block so both the read and write of `_last_app` are inside the same `async with _lock:` critical section.

In `monitor.py`, adjust the time-block management inside `start_monitoring()`:

```python
# Instead of:
global _current, _last_app, _last_block_id, _last_block_start
async with _lock:
    _current = { ... }

# Persist to DB
try:
    import db
    await db.insert_activity(app_name, window_title, idle_seconds)

    if app_name != _get_last_app_safe() and idle_seconds < IDLE_THRESHOLD:
        ...
        _last_app = app_name
    elif idle_seconds >= IDLE_THRESHOLD:
        ...
        _last_app = None

# Do:
global _current, _last_app, _last_block_id, _last_block_start
async with _lock:
    _current = { ... }

    # Persist to DB
    try:
        import db
        await db.insert_activity(app_name, window_title, idle_seconds)

        if app_name != _last_app and idle_seconds < IDLE_THRESHOLD:
            ...
            _last_app = app_name
        elif idle_seconds >= IDLE_THRESHOLD:
            ...
            _last_app = None
```

Key changes:
1. Read `_last_app` directly (not via the unsafe `_get_last_app_safe()` helper) since we're inside the lock
2. Move the entire time-block management inside the lock
3. The DB operations (`insert_activity`, `close_time_block`, `upsert_time_block`) are async but are awaited inside the lock — this is acceptable because the lock is short-held (these are fast SQLite writes).

Also, the `_get_last_app_safe()` function can be removed or renamed to document it's only for external read access.

### Test
The existing `test_start_stop_monitoring` should still pass. No new test needed — this is a correctness fix without behavioral change for the test suite.

---

## Pre-mortem

### Tigers

| Tiger | Classification | Mitigation | Owner | Decision Date |
|-------|---------------|------------|-------|---------------|
| B-1: `desktop_app.py` crashes on launch (NameError) | Launch-Blocking | Add `import os` before shipping. | Backend | Before next release tag |
| B-2: Sunrise infinite loop confuses users every ~75s | Launch-Blocking | Consume trigger flag in the API route. | Backend | Before next release tag |
| B-3: Data race causes dropped/duplicate time blocks | Fast-Follow | Move `_last_app` read inside lock. | Backend | +7 days |

### Paper Tigers
- *"Monitor might hold the lock too long during DB writes"* — SQLite writes on a local file typically complete in <1ms. The lock is only held for one iteration's DB operations at a time. Acceptable.

### Elephants
- *"Does `_get_last_app_safe()` still have any valid callers after the fix?"* — Check and clean up.

---

## Implementation Steps

### Step 1: Add `import os` to `desktop_app.py` (2 min)
- [ ] Add `import os` alongside the existing `import argparse` / `import logging` etc. (covers: B-1)
- [ ] Verify: `python desktop_app.py --port 9999 --debug` starts without NameError (covers: B-1)

### Step 2: Fix sunrise trigger consumption in `main.py` (5 min)
- [ ] Change `sunrise_schedule_get()` to call `scheduler.consume_sunrise_trigger()` and include the consumed value in the response (covers: B-2)
- [ ] Add test `test_sunrise_schedule_consumes_trigger` in `tests/test_api.py` (covers: B-2)
- [ ] Run full test suite: verify all existing tests still pass + new test passes (covers: B-2)

### Step 3: Fix `_last_app` data race in `monitor.py` (8 min)
- [ ] Move time-block management code inside the `async with _lock:` block so both the read and write of `_last_app` are atomic (covers: B-3)
- [ ] Replace `_get_last_app_safe()` calls inside the lock with direct `_last_app` access (covers: B-3)
- [ ] Run full test suite: verify 66+ tests still pass (covers: B-3)

---

## Acceptance Criteria

1. `python desktop_app.py` starts without NameError (covers: B-1)
2. After sunrise triggers, `GET /api/sunrise/schedule` returns `sunrise_triggered: false` on subsequent calls (covers: B-2)
3. Frontend does not restart sunrise sequence on every 15-second poll (covers: B-2)
4. Monitor time-block management uses atomic read-then-write for `_last_app` (covers: B-3)
5. All existing tests pass (66/66) (covers: all)
