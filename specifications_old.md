# Horizon Chamber - Project Specifications (Archived)

> **⚠️ Historical document — this describes the original v0.1 MVP concept.**
> The "Chaos Tamer" feature has since evolved into the kanban board's **Capture & Sort** system.
> See [`specifications.md`](specifications.md) and [`readme.md`](readme.md) for current documentation.

## 1. Project Overview
**Name:** Horizon Chamber (v0.1 MVP)
**Goal:** Build a single-page web application that merges an immersive, time-aware visual environment with an AI-powered "chaos tamer" to help the user organize their thoughts and gently manage their wake/sleep cycle.

## 2. Core Features (MVP Scope)

### 2.1. The Ambient Visual Environment
- **Full-screen Canvas Nebula:** Render a starfield with 150-200 drifting particles.
- **Time-Aware Colors:** The background gradient must change automatically based on the user's system time:
  - 5:00 AM – 8:00 AM: Golden Sunrise (#FFD700 blend)
  - 9:00 AM – 5:00 PM: Crisp Daylight Blues (#4A90E2)
  - 6:00 PM – 8:00 PM: Warm Sunset Orange (#FF6B35)
  - 9:00 PM – 4:00 AM: Deep Night Purple (#2B1B4A)
- **Smooth Transitions:** Color changes must interpolate (lerp) over 5 seconds.

### 2.2. The "Sunrise" Wake-Up Nudge
- A single prominent button labeled "Start Sunrise".
- **Visual Effect:** Over 60 seconds, smoothly transition the Canvas background color to bright gold (#FFD700).
- **Audio Effect:** Using the Web Audio API, generate a rising sine wave tone from 440Hz to 880Hz over the same 60 seconds, ending with a soft chime.

### 2.3. The "Chaos Tamer" (AI Organizer)
- A text input area where the user can paste messy text (e.g., copied browser tabs, YouTube URLs, random thoughts).
- An API route (`/api/classify`) that sends this text to the DeepSeek API with a system prompt to categorize it strictly into 3 JSON keys: `"now"` (urgent action), `"later"` (read/review), and `"trash"` (irrelevant).
- Results are saved to an SQLite database with a timestamp.
- Display the results as 3 draggable cards (using native HTML5 Drag & Drop) in a horizontal row.

### 2.4. The "Main Quest"
- A simple, editable text field at the top of the page.
- Stores exactly ONE string in the browser's `localStorage`.
- Purpose: To give the user a single, clear focus for the day without overwhelming them.

## 3. Technical Stack (Mandatory)
- **Backend:** Python 3.9+, FastAPI (for async support and auto-generated API docs).
- **Database:** SQLite with `aiosqlite` (async driver).
- **Frontend:** Single `index.html` with all CSS (Glassmorphism, dark theme) and Vanilla JavaScript embedded. No CDN links, no React/Vue.
- **AI:** DeepSeek API (environment variable `DEEPSEEK_API_KEY`).

## 4. API Endpoints Required
- `GET /api/time_color` → Returns the current hex color based on system time.
- `POST /api/classify` → Accepts raw text, calls DeepSeek, stores result in SQLite, returns the categorized JSON.
- `GET /api/today` → Returns today's 3 most recent "now" items from the database.
- `GET /api/sunrise` → (Optional) Triggers the audio/visual sequence if needed via server-side check.

## 5. Non-Goals (Out of Scope for v0.1)
- No Discord/YouTube RSS scraping.
- No OS-level automation (file system, global shortcuts).
- No user login/authentication.
- No budgeting or long-term planning dashboards.