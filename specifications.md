# Horizon Chamber — Technical Specifications

## 1. Tech Stack
- **Backend:** Python 3.9+, FastAPI, Uvicorn.
- **Database:** SQLite + `aiosqlite` (async support).
- **Frontend:** Single `static/index.html`. All CSS (dark glassmorphism) and vanilla JS embedded. No CDN dependencies.
- **AI:** DeepSeek API via `httpx` (async). Key from environment variable `DEEPSEEK_API_KEY`.

## 2. API Endpoints

### Core
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Returns `{"status": "ok", "deepseek_key_set": bool}` |
| GET | `/api/time_color` | Returns `{"hex": "#4A90E2"}` based on current hour (5-8=Gold, 9-16=Blue, 17-20=Orange, 21-4=Purple) |
| POST | `/api/classify` | Body: `{"raw_text": "..."}`. Calls DeepSeek, stores result in SQLite, returns `{"now": [...], "later": [...], "trash": [...]}` |

### Sunrise Schedule
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sunrise/schedule` | Returns `{"enabled": bool, "time": "HH:MM", "sunrise_triggered": bool}` |
| PUT | `/api/sunrise/schedule` | Body: `{"enabled": bool, "time": "HH:MM"}`. Sets the auto-sunrise schedule (persisted to DB) |

### Goals & Tasks
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/goals` | List goals with optional type/archived filters |
| POST | `/api/goals` | Create a goal; AI auto-detects type (long_term/habit/maintenance) and frequency |
| GET | `/api/goals/{id}` | Single goal with progress analysis |
| PUT | `/api/goals/{id}` | Update goal fields (title, progress, pause, archive) |
| DELETE | `/api/goals/{id}` | Soft-delete (archive) a goal |
| GET | `/api/board` | Full kanban board state (`?date=YYYY-MM-DD`) |
| GET | `/api/board/stream` | SSE stream for real-time board updates |
| GET | `/api/today` | Today's task list in priority order |
| POST | `/api/tasks` | Create a new task for today |
| PATCH | `/api/tasks/{id}` | Update task (status, title, date) |
| POST | `/api/tasks/{id}/split` | AI-split a task into subtasks |
| POST | `/api/tasks/{id}/suggest` | AI-suggest next step for parent goal |

### Analysis & Engine
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/analysis` | Performance signals (nudges, progress) |
| GET | `/api/analysis/goal/{id}` | Per-goal analysis |
| POST | `/api/analysis/refresh` | Force re-run analysis |
| POST | `/api/engine/generate` | Manually trigger daily task generation |
| GET | `/api/engine/status` | Goal engine status |

### Activity Tracking (v0.2)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/activity/now` | Currently active app/window + idle seconds |
| GET | `/api/activity/summary` | Aggregated focus summary by app (`?period=today|yesterday|last_7_days`) |
| GET | `/api/activity/stream` | SSE stream of live activity changes |
| PUT | `/api/activity/categories` | Set an app's category |
| GET | `/api/activity/categories` | All categorized apps |
| GET | `/api/activity/history` | Recent activity log entries |

### Feed / n8n Integration (v0.5)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/feed/ingest` | [Auth] Receive n8n summarized feed data |
| GET | `/api/feed/items` | List feed items with filters |
| GET | `/api/feed/items/{id}` | Single feed item detail |
| PATCH | `/api/feed/items/{id}/dismiss` | Dismiss a feed item |
| PATCH | `/api/feed/items/{id}/undismiss` | Restore a dismissed item |
| POST | `/api/feed/trigger` | Manually trigger n8n fetch |
| GET | `/api/feed/stats` | Feed statistics and last run info |
| GET | `/api/feed/stream` | SSE stream for live feed events |

## 3. Database Schema (SQLite)

Current tables:

- **`chaos`** — AI-classified thought items (now/later/trash). Used as inflow source for the kanban board.
- **`activity_log`** — Raw OS activity samples (app_name, window_title, idle_seconds).
- **`time_blocks`** — Aggregated focus periods (start/end/duration per app).
- **`app_categories`** — User-defined app categorization (focus/distraction/neutral/idle/away).
- **`goals`** — Parent goals (long_term/habit/maintenance) with progress tracking.
- **`tasks`** — Individual work items tied to goals, with status (pending/doing/done/skipped).
- **`task_log`** — Audit trail for task status changes.
- **`sunrise_config`** — Persisted auto-sunrise schedule.
- **`feed_items`** — n8n-ingested content items with relevance scores.
- **`feed_runs`** — Feed fetch run history.

## 4. Frontend Architecture

- Full-viewport `<canvas>` rendering 200 drifting star particles (adaptive: reduces to 100 if frame > 50ms).
- Four draggable/collapsible "zone" panels (top-left: Quest/Feed, top-right: Stats, bottom-left: Kanban/Goals, bottom-right: Chat).
- VSCode-style tab bars within each zone for panel switching.
- Persistent state via `localStorage` (panel layout, main quest, auto-sunrise backup, card colors).
- SSE connections for real-time activity, board, and feed updates.

## 5. File Structure

```
/main.py               (FastAPI app, routes, lifespan)
/db.py                 (Async SQLite database layer)
/deepseek_client.py    (Async DeepSeek API client)
/monitor.py            (OS activity tracking: Win32, macOS, Linux stubs)
/scheduler.py          (Sunrise schedule checker + background loop)
/feed.py               (n8n feed aggregation scheduler)
/goal_engine.py        (Kanban goals system: generation, carry-over, analysis)
/desktop_app.py        (PyWebView desktop launcher)
/static/
  index.html           (All-in-one frontend: CSS + HTML + JS)
/tests/
  test_api.py, test_activity_api.py, test_goals_api.py, test_feed_api.py
  test_db.py, test_deepseek_client.py, test_scheduler.py, test_main.py
  test_monitor.py, test_goal_engine.py, test_feed.py
/.env.example          (Environment variable template)
```
