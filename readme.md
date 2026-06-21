# 🌌 Horizon Chamber

**Your ambient co-pilot for taming digital chaos and reclaiming your mornings.**

Horizon Chamber is a single-page web app that turns your browser into a living, breathing environment. It combines a time-aware visual nebula, a gentle sunrise alarm (manual or scheduled), and an AI-powered "chaos tamer" that organizes your scattered thoughts into actionable tasks.

## 🚀 Quick Start

### Option A — Browser (development)

```bash
uvicorn main:app --reload
# Open http://localhost:8000
```

### Option B — Desktop window (recommended)

```bash
pip install -r requirements.txt
python desktop_app.py
# A native window opens — no browser tabs needed.
```

### Option C — Standalone executable (portable)

```bash
pip install -r requirements.txt
python build.py --onefile
# dist\HorizonChamber.exe  — single .exe, no Python required
```

## 🧩 Core Features

-   **Dynamic Nebula:** Colors shift from sunrise gold to deep night purple based on your system clock.
-   **Sunrise Nudge (Manual):** Click "Start Sunrise" for a 60-second audiovisual wake-up sequence (canvas fades to gold + 440→880Hz sine tone via Web Audio API).
-   **Sunrise Nudge (Auto-Schedule):** Click the ⚙️ gear icon next to the Sunrise button to set a daily auto-sunrise time. The server checks every 30s and triggers sunrise at the configured time. A localStorage fallback ensures sunrise still fires if the server is unreachable.
    -   **Note:** The schedule is stored in-memory and resets when the server restarts. The browser's localStorage backup re-applies the last known setting on page load.
-   **Chaos Tamer:** Paste messy text/browser tabs; AI categorizes them into "Now", "Later", and "Trash" — each shown as a draggable card.
-   **Activity Tracking (v0.2):** The desktop app tracks which window you have focused and how long you have been idle. A small indicator in the top bar shows your current app. The right-side "Today's Focus" panel shows a live bar chart of top apps by usage time. Activity data stays local in SQLite.
    -   **Windows:** Full support via native Win32 API.
    -   **macOS/Linux:** Degraded mode — returns "unknown" (stubs ready for native implementation).
    -   **Privacy:** Tracking is OFF by default in browser mode, ON in desktop mode. Set `AUTO_START_MONITOR=false` to disable.
-   **Main Quest:** A single, editable daily intention stored in localStorage.

## 📡 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (reports if DeepSeek key is configured) |
| GET | `/api/time_color` | Current hex color based on server hour |
| POST | `/api/classify` | Classify text via DeepSeek, persist to DB |
| GET | `/api/today` | 3 most recent "now" items |
| GET | `/api/sunrise/schedule` | Current auto-sunrise schedule |
| PUT | `/api/sunrise/schedule` | Set auto-sunrise schedule `{"enabled": bool, "time": "HH:MM"}` |
| GET | `/api/activity/now` | Currently active app/window + idle seconds |
| GET | `/api/activity/summary` | Aggregated focus summary by app (`?period=today|yesterday|last_7_days`) |
| GET | `/api/activity/stream` | SSE stream of live activity changes |
| PUT | `/api/activity/categories` | Set an app's category `{"app_name","category","label"}` |
| GET | `/api/activity/categories` | All categorized apps |
| GET | `/api/activity/history` | Recent activity log entries (`?limit=50`)

## 🧪 Running Tests

```bash
pytest tests/ -v
```

The test suite covers: API endpoints, DB operations, DeepSeek client logic, scheduler, and edge cases (timeouts, missing keys, invalid input).

## 🖥️ Desktop App

The app can run as a native desktop window instead of a browser tab:

| Command | What it does |
|---|---|
| `python desktop_app.py` | Starts the FastAPI server in the background and opens a native window via **PyWebView** (Edge WebView2 on Windows, WebKit on macOS). No browser tabs, no address bar. |
| `python desktop_app.py --port 9000` | Use a custom port. |
| `python desktop_app.py --debug` | Keep the console visible with verbose logs. |

**How it works:** `desktop_app.py` spawns uvicorn in a daemon thread, waits for `/api/health` to respond, then opens a `pywebview` window pointed at `http://127.0.0.1:<port>`. When you close the window, the server shuts down gracefully.

**Single-file executable:** Run `python build.py --onefile` to produce a standalone `.exe` in `dist/` that bundles Python, the server, and the frontend into a single portable file. No Python installation needed on the target machine (though the [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) must be present — it comes pre-installed on Windows 11 and most recent Windows 10 systems).

## 🛠️ Tech Stack

-   Python 3.9+ / FastAPI (Backend)
-   Vanilla JS + Canvas (Frontend, no CDN dependencies)
-   SQLite + aiosqlite (Database)
-   DeepSeek API (AI Classification via httpx)
-   PyWebView (Native desktop wrapper)
-   PyInstaller (Standalone executable bundler)

## 🔧 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (required for Chaos Tamer) |
| `DATABASE_PATH` | `./horizon.db` | Path to SQLite database file |
| `AUTO_START_MONITOR` | `false` | Start activity monitor on server boot (`true` in desktop mode) |
| `ACTIVITY_POLL_INTERVAL` | `3` | Seconds between activity checks |
| `ACTIVITY_IDLE_THRESHOLD` | `60` | Seconds idle before marking "away" |
| `ACTIVITY_PRUNE_DAYS` | `30` | Auto-delete activity logs older than N days |
| `FEATURE_ACTIVE_WINDOW` | `true` | Enable active window tracking |
| `FEATURE_IDLE_TIME` | `true` | Enable idle time tracking |
| `FEATURE_PROCESS_LIST` | `false` | Enable process tree tracking (heavier, off by default) |
