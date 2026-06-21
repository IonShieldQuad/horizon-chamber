# Horizon Chamber — Technical Specifications (v0.1 MVP)

## 1. Tech Stack (Strict)
- **Backend:** Python 3.9+, FastAPI, Uvicorn.
- **Database:** SQLite + `aiosqlite` (async support).
- **Frontend:** Single `static/index.html`. All CSS (dark glassmorphism) and vanilla JS embedded. No CDN links.
- **AI:** DeepSeek API via `httpx` (async). Key from environment variable `DEEPSEEK_API_KEY`.

## 2. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Returns `{"status": "ok", "deepseek_key_set": bool}` |
| GET | `/api/time_color` | Returns `{"hex": "#4A90E2"}` based on current hour (5-8=Gold, 9-16=Blue, 17-20=Orange, 21-4=Purple) |
| POST | `/api/classify` | Body: `{"raw_text": "..."}`. Calls DeepSeek, stores result in SQLite, returns `{"now": [...], "later": [...], "trash": [...]}` |
| GET | `/api/today` | Returns the 3 most recent "now" items from SQLite |
| GET | `/api/sunrise/schedule` | Returns `{"enabled": bool, "time": "HH:MM", "sunrise_triggered": bool}` |
| PUT | `/api/sunrise/schedule` | Body: `{"enabled": bool, "time": "HH:MM"}`. Sets the auto-sunrise schedule (in-memory, resets on restart) |

## 3. Database Schema (SQLite)

Table: `chaos`
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `category` TEXT CHECK(category IN ('now', 'later', 'trash'))
- `text` TEXT NOT NULL
- `timestamp` DATETIME DEFAULT CURRENT_TIMESTAMP

## 4. Frontend Requirements

- Full-viewport `<canvas>` rendering 200 drifting star particles (adaptive: reduces to 100 if frame > 50ms).
- On load, fetch `/api/time_color` and interpolate canvas background to that color over 5 seconds.
- Top: Editable "Main Quest" text field (saved to `localStorage`).
- Middle: The Canvas Nebula.
- Bottom: A `<textarea>` + "Tame the Chaos" button. On click, POST to `/api/classify` and render 3 draggable cards horizontally (Now / Later / Trash).
- Bottom-right: "Start Sunrise" button. On click, animate canvas to gold and play 440Hz→880Hz Web Audio tone over 60 seconds. Click the ⚙️ gear icon to open auto-sunrise settings (toggle + time picker). Settings are stored on the server (in-memory) and backed up in `localStorage`.

## 5. File Structure

```
/main.py           (FastAPI app, routes, lifespan, DB init)
/scheduler.py      (In-memory sunrise scheduler + background checker task)
/db.py             (Async SQLite database layer)
/deepseek_client.py (Async DeepSeek API client)
/static/
  index.html       (All-in-one frontend: CSS + HTML + JS)
/tests/
  test_api.py      (Endpoint tests: health, time_color, classify, today, schedule)
  test_db.py       (Unit tests for database operations)
  test_deepseek_client.py (Unit tests for AI client: key check, fences, missing keys)
  test_main.py     (Edge-case tests: timeout, missing file, value error)
  test_scheduler.py (Unit tests for scheduler: defaults, CRUD, trigger logic)
/.env              (DEEPSEEK_API_KEY=sk-…, DATABASE_PATH=./horizon.db)
```
