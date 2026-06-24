# Horizon Chamber — Dynamic Kanban Goals System (v0.3)

## Context

This requirement describes the design and implementation of a **dynamic kanban goal board** that replaces the simple chaos-classification cards with a full goal-tracking system. It builds on the existing FastAPI + SQLite + vanilla JS architecture and integrates with the activity monitor (v0.2) for adaptive performance analysis.

**Design north star:** The board must feel as low-friction as a physical kanban board with sticky notes. Every interaction should take 1–2 actions maximum. No modals, no required fields, no confirmation dialogs for common operations.

---

## 1. Overall Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (vanilla JS)                     │
│  ┌──────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │ Chaos    │  │  Kanban Board    │  │  Today Priority List │  │
│  │ Inbox    │  │  (4 columns)     │  │  (simplified view)   │  │
│  │ (small)  │  └────────┬─────────┘  └──────────┬───────────┘  │
│  └────┬─────┘           │                       │              │
│       │                 │                       │              │
│       └────────────┬────┘───────────────────────┘              │
│                    │ HTTP/SSE                                  │
└────────────────────┼───────────────────────────────────────────┘
                     │
┌────────────────────┼───────────────────────────────────────────┐
│              FastAPI Server (Python)                           │
│  ┌────────────────▼──────────────────────────────────────┐    │
│  │              Goal Engine (new module)                  │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │    │
│  │  │ Daily Gen    │  │ Carry-over   │  │ Analysis   │  │    │
│  │  │ Engine       │  │ Manager      │  │ Engine     │  │    │
│  │  └──────────────┘  └──────────────┘  └────────────┘  │    │
│  └───────────────────────────────────────────────────────┘    │
│                          │                                    │
│  ┌───────────────────────▼──────────────────────────────┐    │
│  │                   SQLite DB                           │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────┐ │    │
│  │  │ goals    │  │ tasks    │  │ chaos    │  │ ...  │ │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────┘ │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

**Key integration points with existing system:**
- `monitor.py` feeds activity data → the analysis engine uses it for completion patterns and overload detection
- `deepseek_client.py` provides AI split/suggest/classify capabilities
- `scheduler.py` pattern is reused for the daily generation engine
- Chaos inbox (existing `chaos` table + `/api/classify`) becomes the **inflow point** for new goals

---

## 2. Goal Types (Data Model)

### 2.1 Conceptual Model

Based on the user's clarifications:

| Type | Behavior | AI Role |
|---|---|---|
| **long_term** | Has an end state. Generates a "reminder + AI suggestion" if no task was done recently. Each task has an AI-powered "split" button. | Split parent goal into sub-goals/tasks. Estimate progress. Suggest frequency. |
| **habit** | Never ends. Auto-generates task daily (or at user-defined frequency). Skipped tasks disappear at end-of-day cleanup. | Suggest frequency based on user's stated goal and completion data. |
| **maintenance** | Like habit but with flexible interval (e.g., every 2 days, weekly). Skipped tasks do NOT carry over — they just fade. | Infer interval from task content, completion data, and user clarification. |

### 2.2 Database Schema (New Tables)

```sql
-- Goals: the parent entity
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('long_term', 'habit', 'maintenance')),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',

    -- Frequency (for habit/maintenance)
    frequency TEXT DEFAULT 'daily' CHECK(frequency IN ('daily', 'weekly', 'custom')),
    custom_interval_days INTEGER,  -- only for frequency='custom'

    -- Long-term specific
    target_days INTEGER,            -- expected days to completion
    progress_pct REAL DEFAULT 0.0,  -- 0.0–100.0, AI-estimated + user-clarified

    -- State
    paused INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,

    -- Metadata
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tasks: individual work items generated from goals
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',

    -- Date assignment
    date TEXT NOT NULL,              -- ISO date 'YYYY-MM-DD'
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'doing', 'done', 'skipped', 'carried_over')),

    -- Carry-over tracking
    carry_over_count INTEGER DEFAULT 0,
    parent_task_id INTEGER,          -- if generated from splitting another task

    -- AI suggestions (stored for transparency)
    ai_suggested INTEGER DEFAULT 0,  -- was this task AI-generated?

    -- Metadata
    sort_order INTEGER DEFAULT 0,    -- priority order within a day
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tasks_date ON tasks(date);
CREATE INDEX idx_tasks_goal_id ON tasks(goal_id);
CREATE INDEX idx_tasks_status ON tasks(status);

-- Task completions log (append-only, for analysis)
CREATE TABLE IF NOT EXISTS task_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    action TEXT NOT NULL CHECK(action IN ('done', 'skipped', 'carried_over', 'split', 'auto_generated')),
    date TEXT NOT NULL,
    ai_analysis TEXT,                -- JSON: why this action was taken/suggested
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 2.3 Schema Version

```sql
PRAGMA user_version = 3;  -- existing v2 from activity tracking, bump to v3
```

---

## 3. Core Engine: Task Generation (`goal_engine.py`)

A new module following the `scheduler.py` pattern (asyncio background loop).

### 3.1 Generation Rules (Per User Clarifications)

```
DAILY_GENERATION (triggered on first board open of the day, or at 04:00):

  1. long_term goals:
     a. If NO task exists in the last N days (N = max(3, suggested_frequency)):
        → Generate a "reminder" task with AI-suggested title
        → Suggest a concrete next step via DeepSeek
     b. If previous task was done:
        → Generate next step (same title or AI-suggested progression)
     c. If previous task was skipped/carried_over:
        → Generate same title again, increment carry_over_count
        → If carry_over_count > 3 → flag for intervention (see §5)

  2. habit goals:
     a. If no task exists for today → generate one
     b. Previous day's incomplete habit tasks → marked 'skipped' (they disappear)

  3. maintenance goals:
     a. Check interval: if today >= last_task_date + interval → generate
     b. Previous incomplete maintenance tasks → marked 'skipped' (no carry-over)

CLEANUP (runs at 04:00 daily):
  - Delete (or mark skipped) yesterday's incomplete habit + maintenance tasks
  - Long-term tasks are NOT cleaned — they carry over
```

### 3.2 Public API Surface

```python
# goal_engine.py

async def start_engine() -> None:
    """Start the background generation loop. Runs until cancelled."""

async def generate_today_tasks() -> dict:
    """Manually trigger generation for today. Returns {created: int, skipped: int}."""

async def get_board(date: str = None) -> dict:
    """Return full board state for a given date (default today):
    {
        "columns": {
            "today": [...],
            "in_progress": [...],
            "overdue": [...],
            "done": [...]
        },
        "goals": [...],
        "stats": {...}
    }
    """

async def get_today_list() -> list[dict]:
    """Return today's tasks in priority order (simplified view)."""

def get_goal_progress(goal_id: int) -> dict:
    """Return {progress_pct, days_active, tasks_done, tasks_skipped, trend}."""

async def ai_split_task(task_id: int) -> list[dict]:
    """Call DeepSeek to split a task into subtasks. Returns created task list."""

async def ai_suggest_frequency(goal_id: int) -> dict:
    """AI analyzes goal + completion data and suggests an optimal frequency."""
```

### 3.3 Engine Configuration

```python
# Constants / env vars
GOAL_REMINDER_GAP_DAYS = int(os.getenv("GOAL_REMINDER_GAP_DAYS", "3"))
GOAL_MAX_CARRY_OVER = int(os.getenv("GOAL_MAX_CARRY_OVER", "3"))
GOAL_CLEANUP_TIME = os.getenv("GOAL_CLEANUP_TIME", "04:00")  # 24h format
GOAL_GENERATE_ON_OPEN = os.getenv("GOAL_GENERATE_ON_OPEN", "true").lower() in ("true", "1")
```

---

## 4. API Endpoints

### 4.1 Goal CRUD

| Method | Path | Description |
|---|---|---|
| GET | `/api/goals` | List all active goals (filter: `?type=long_term`, `?archived=true`) |
| POST | `/api/goals` | Create a goal. Body: `{title, type, frequency?, custom_interval_days?, target_days?, description?}` |
| GET | `/api/goals/{id}` | Get single goal with progress data |
| PUT | `/api/goals/{id}` | Update goal (pause, archive, adjust frequency, clarify progress) |
| DELETE | `/api/goals/{id}` | Soft-delete (archive) a goal |

### 4.2 Task Management

| Method | Path | Description |
|---|---|---|
| GET | `/api/board` | Full kanban board state for today |
| GET | `/api/board?date=2025-01-15` | Board state for a specific date |
| GET | `/api/today` | **Simplified view**: today's tasks in priority order (replaces existing `/api/today`) |
| PATCH | `/api/tasks/{id}` | Update task: `{status: "done"|"skipped"|"doing"}` or `{sort_order: N}` |
| POST | `/api/tasks/{id}/split` | AI-split a task into subtasks. Returns new task list |
| POST | `/api/tasks/{id}/suggest` | AI-suggest a next step/frequency for the parent goal |

### 4.3 Analysis & Nudge

| Method | Path | Description |
|---|---|---|
| GET | `/api/analysis` | Return performance signals: completion rate (daily/weekly), carry-over stats, trend, nudge suggestions |
| GET | `/api/analysis/goal/{id}` | Per-goal analysis |
| POST | `/api/analysis/refresh` | Force re-run analysis (e.g., after user clarifies) |

### 4.4 Goal Engine Control

| Method | Path | Description |
|---|---|---|
| POST | `/api/engine/generate` | Manually trigger daily generation |
| GET | `/api/engine/status` | Engine status: last run, tasks generated, next cleanup |

---

## 5. Performance Analysis & Adaptive System

### 5.1 Detected Signals

| Signal | Formula | Threshold | Meaning |
|---|---|---|---|
| **completion_rate** | `done / (done + skipped)` over period | >90% = "coast", <40% = "drowning" | Overall load adequacy |
| **carry_over_depth** | `MAX(carry_over_count)` across active tasks | >3 | Goal too large or unmotivating |
| **carry_over_breadth** | `COUNT(carried_over) / COUNT(pending)` | >50% | Too many goals, systemic overload |
| **stagnation_days** | Days since last `done` for a long_term goal | >7 | Goal lost momentum |
| **skip_pattern** | Same goal skipped repeatedly in a row | >3 consecutive | Goal is a "fake goal" — user doesn't actually want it |
| **completion_time** | Hour of day when most tasks are done | Consistent pattern | User has a productive rhythm (positive signal) |
| **overdue_accumulation** | Total overdue tasks growing week-over-week | Positive trend | Escalating overload — needs intervention |

### 5.2 Nudge System

Nudges are delivered **passively** — one line of text in the board UI, never a popup.

```
┌──────────────────────────────────────────────────────┐
│  Kanban Board                                         │
│                                                       │
│  💡 You're at 92% this week. Room for a new goal?    │
│     [Add Goal →]  [Dismiss]                           │
│                                                       │
│  (or)                                                 │
│                                                       │
│  💡 3 tasks have been overdue for 5+ days.            │
│     Want to pause "Learn Japanese"? [Yes] [Not now]   │
└──────────────────────────────────────────────────────┘
```

**Nudge priority levels:**

| Level | Style | Trigger | Max Frequency |
|---|---|---|---|
| Info | Single line, subtle | completion_rate >85% or <50% | Once per session |
| Suggestion | Line + one button | carry_over_depth >3 on a single goal | Once per goal per day |
| Alert | Line + two buttons | carry_over_breadth >50% OR accumulation trend up | Once per day |

**Design principles:**
- Never make character judgments (no "you're being lazy")
- Every nudge is actionable (has a button) or one-tap dismissible
- Nudges are logged in `task_log` so users can see what the system recommended
- User can disable nudges entirely (`NUDGES_ENABLED=false`)

---

## 6. AI Integration Points

### 6.1 DeepSeek Usage

| Operation | Prompt Pattern | Timing |
|---|---|---|
| **Goal classification** | "Given this text '{title}', classify as long_term, habit, or maintenance. If habit/maintenance, suggest frequency." | On goal creation |
| **Task splitting** | "Split '{task_title}' into 2-5 concrete subtasks that can each be done in one sitting." | On user clicking "Split" button |
| **Progress estimation** | "Goal: '{title}'. Tasks done: {N}. Last task: '{last_title}'. Estimate progress 0-100 and suggest next step." | On board load for long_term goals |
| **Frequency suggestion** | "Goal: '{title}' type {type}. Completion rate: {rate}%. User's last frequency: {freq}. Suggest optimal frequency." | On user request or when analysis detects poor fit |
| **Reminder generation** | "Goal: '{title}'. Last activity {N} days ago. Generate a brief, specific reminder for what to do next." | When goal has been idle > reminder_gap_days |
| **Nudge copywriting** | "User signals: {signals_json}. Generate one brief, non-judgmental suggestion sentence." | When analysis engine decides to nudge |

### 6.2 Caching & Rate Limiting

- AI calls are async and non-blocking (same pattern as existing `/api/classify`)
- Results are cached in-memory for the session (TTL: 5 minutes)
- If DeepSeek API key is not set, all AI features degrade gracefully:
  - "Split" button hidden
  - Progress defaults to 0%
  - Reminders use the goal title verbatim (no AI suggestion)
  - Nudges use hardcoded templates

---

## 7. Frontend Design

### 7.1 Layout

Building on the existing glassmorphism + Canvas nebula style:

```
┌────────────────────────────────────────────────────────────────┐
│  [Main Quest]                                  [⚙️] [🧊]    │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Chaos Inbox (collapsed by default)                     │   │
│  │  "Here are some thoughts, help me organize them..."     │   │
│  │  [Paste or type...]                        [Tame →]    │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                                │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐│
│  │ 📋 Today│ │ 🔄 Doing │ │ ✅ Done  │ │ ⏰ Over  │ │📊    ││
│  │─────────│ │──────────│ │──────────│ │──────────│ │      ││
│  │ 🟢 Ch3  │ │ 🟡 Fix   │ │ ✅ Walk  │ │ 🔴 Ch2  │ │3/5  ││
│  │   [↗]   │ │   bug    │ │   🕐 9am │ │  (2d)   │ │68%  ││
│  │ 🟢 Walk │ │          │ │ ✅ Exer  │ │ 🔴 Notes│ │      ││
│  │ 🟢 Tidy │ │          │ │   🕐 7am │ │  (5d)   │ │💡... ││
│  │─────────│ │          │ │          │ │          │ │      ││
│  │ [+ Add] │ │          │ │          │ │          │ │ +    ││
│  └─────────┘ └──────────┘ └──────────┘ └──────────┘ └──────┘│
└────────────────────────────────────────────────────────────────┘
```

### 7.2 Interaction Model

| Gesture | Action | Feedback |
|---|---|---|
| **Tap** card | Mark as done | Satisfying check animation (card shrinks, fades to Done column) |
| **Swipe right** on Today card | Skip/delete | Card slides off, briefly shows undo option |
| **Drag** card between columns | Move to Doing/Done | Smooth drag with ghost preview |
| **Long press** | Open mini-edit (title + status) | Inline input replaces card text |
| **Tap ↗** on card | AI split | Card shimmers, splits into 2-5 cards in same column |
| **Tap +** button | Quick-add goal | Inline input: type title → AI auto-detects type + frequency → goal created |
| **Double tap** overdue card | Snooze (carry over again) | Card stays, carry_over_count increments |

### 7.3 Chaos Inbox (Updated Role)

The existing chaos textarea shrinks to a **collapsible strip** at the top of the board. Its purpose changes from "classify your entire brain dump" to **"quick capture → organize into system"**:

1. User pastes/typs loose thoughts
2. Clicks "Tame" → existing `/api/classify` runs
3. Resulting classified cards (now/later/trash) appear as **temporary draggable items** below the inbox
4. User drags "now" cards into Today column, "later" cards become new goals (auto-create via AI)
5. "Trash" items auto-dismiss after 10 seconds

This preserves the existing chaos mechanic but makes it an inflow channel rather than the main interface.

### 7.4 Today Priority List (Simplified View)

When user clicks the **📋** icon in the top bar, the board collapses to a simple numbered list:

```
Today's Tasks — Wed Jan 15
──────────────────────────
1. 🟢 Study Chapter 3          ← priority order
2. 🟢 Walk the dog
3. 🟢 Do 20 pushups
4. 🔴 Fix login bug (overdue 2d)
──────────────────────────
[+ Quick Add]    [Back to Board]
```

This is the same data as the board's "Today" column, but rendered as a minimal ordered list — akin to the existing `/api/today` endpoint but with all tasks and priority order.

### 7.5 Visual Theme

Cards inherit the existing glassmorphism style:

```css
.goal-card {
  background: rgba(255, 255, 255, 0.05);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 10px 14px;
  cursor: grab;
  transition: transform 0.15s, opacity 0.2s;
}
.goal-card.done {
  opacity: 0.5;
  text-decoration: line-through;
  border-color: rgba(100, 255, 100, 0.2);
}
.goal-card.overdue {
  border-left: 3px solid #ff6b6b;
}
```

---

## 8. Non-Goals (Explicitly Out of Scope for v0.3)

- ❌ Calendar integration (Google Calendar, etc.)
- ❌ Push notifications / mobile app
- ❌ Multi-user / collaboration
- ❌ Gamification (XP, levels, achievements) — that's v0.5 "The Journey"
- ❌ Drag-and-drop reordering library (use native HTML5 drag or simple click-to-move)
- ❌ React / Vue / framework migration — keep vanilla JS
- ❌ Cloud sync — stays local SQLite
- ❌ Habit streaks / graphs — future enhancement
- ❌ Automated goal creation from activity data (e.g., "you spend 3h on YouTube, maybe make a goal to reduce it") — deferred

---

## 9. File Structure After Refactor

```
/main.py                        ← Add goal/task/board routes, start engine in lifespan
/scheduler.py                   ← Unchanged
/db.py                          ← Add goals + tasks + task_log tables + DB functions
/goal_engine.py                 ← NEW: Task generation engine, analysis, nudges
/deepseek_client.py             ← Add goal-specific prompt patterns
/monitor.py                     ← Unchanged (analysis engine reads from it)
/sse.py                         ← Unchanged
/desktop_app.py                 ← Unchanged
/build.py                       ← Unchanged
/static/
  index.html                    ← Major rewrite: kanban board, chaos inbox, today list
/tests/
  test_goal_engine.py           ← NEW
  test_goals_api.py             ← NEW
  test_db.py                    ← Add goal/task DB tests
  test_api.py                   ← Update existing tests for changed /api/today
  test_deepseek_client.py       ← Add goal prompt tests
  test_monitor.py               ← Unchanged
  test_scheduler.py             ← Unchanged
/.env                           ← Add new env vars (see §3.3)
```

---

## 10. Implementation Phases

### Phase 1: Database Foundation (1–2 hours)

- [ ] Design and add `goals`, `tasks`, `task_log` tables to `db.py`
- [ ] Add `PRAGMA user_version` migration (v2 → v3)
- [ ] Implement all DB functions: CRUD goals, CRUD tasks, insert task_log, query board data
- [ ] Write and pass `test_db.py` additions
- [ ] Ensure backward compatibility: existing `chaos` table and functions untouched

### Phase 2: Goal Engine (2–3 hours)

- [ ] Create `goal_engine.py` with the generation loop
- [ ] Implement daily generation logic for all three goal types
- [ ] Implement carry-over manager (auto-increment, cap at threshold)
- [ ] Implement cleanup routine (remove incomplete habit/maintenance tasks)
- [ ] Implement lazy generation (trigger on board open if today not yet generated)
- [ ] Implement analysis engine: signal detection from task_log
- [ ] Implement nudge system: signal → nudge level → UI message
- [ ] Write and pass `tests/test_goal_engine.py`

### Phase 3: API Endpoints (1–2 hours)

- [ ] Add `/api/goals` CRUD routes
- [ ] Add `/api/board` route with column assembly logic
- [ ] Update `/api/today` to return priority-ordered task list (breaking change — update existing frontend)
- [ ] Add `/api/tasks/{id}` PATCH route
- [ ] Add `/api/tasks/{id}/split` + `/api/tasks/{id}/suggest` routes
- [ ] Add `/api/analysis` and `/api/analysis/goal/{id}` routes
- [ ] Add `/api/engine/generate` and `/api/engine/status` routes
- [ ] Start goal engine in `lifespan`
- [ ] Write and pass `tests/test_goals_api.py`

### Phase 4: AI Integration (1–2 hours)

- [ ] Add goal-specific prompt patterns to `deepseek_client.py`
- [ ] Implement classification: raw text → goal type + frequency
- [ ] Implement task splitting: task title → subtask list
- [ ] Implement progress estimation: goal + task history → progress % + next step
- [ ] Implement frequency suggestion: goal + completion data → optimal frequency
- [ ] Implement reminder generation: idle goal → reminder text
- [ ] Implement nudge copywriting: signals → suggestion sentence
- [ ] Ensure graceful degradation when API key is missing
- [ ] Write and pass `tests/test_deepseek_client.py` additions

### Phase 5: Frontend — Kanban Board (3–4 hours)

- [ ] Replace existing chaos card area with 4-column kanban layout
- [ ] Implement card rendering with goal type colors
- [ ] Implement tap-to-complete with animation
- [ ] Implement swipe-to-skip (with undo)
- [ ] Implement drag between columns (native HTML5 drag or lightweight touch)
- [ ] Implement long-press inline edit
- [ ] Implement quick-add (+ button) with AI type detection
- [ ] Implement overdue indicator (red border, day counter)
- [ ] Implement analytics column (completion rate, nudge text)
- [ ] Collapse chaos inbox to a small strip at top
- [ ] Implement drag-from-inbox to board (capture → goal/task creation)
- [ ] Style everything with existing glassmorphism theme
- [ ] Ensure responsive: adapts from full 5-column to 2-column on narrow screens

### Phase 6: Frontend — Today List + Polish (1–2 hours)

- [ ] Implement simplified today list view (toggle from board)
- [ ] Implement priority reorder in today list
- [ ] Add SSE streaming for real-time board updates
- [ ] Add loading states for AI operations (split, classify)
- [ ] Add undo toast for destructive actions (skip, delete)
- [ ] Test all interactions in both browser and desktop modes
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Verify no regressions in existing features

---

## 11. Acceptance Criteria

1. **Goal creation works:** Type a goal title, AI classifies it as long_term/habit/maintenance and sets a reasonable default frequency. User can override any field.

2. **Daily generation works:** Open the board in the morning — long_term goals have a task (or reminder), habits have a check-item, maintenance items appear on their interval.

3. **Carry-over works:** Skip a long-term task → it appears again the next day with carry_over_count incremented. Skip a habit → it disappears (no carry-over).

4. **AI split works:** Tap the split button on a task → it breaks into 2-5 subtasks in the same column. Each subtask is individually completable.

5. **Today list is correct:** The simplified view shows all tasks for today in priority order. Completing a task in one view updates the other.

6. **Analysis detects signals:** Run 3 days with 100% completion → nudge suggests adding a goal. Run 3 days with 30% completion → nudge suggests reducing load.

7. **Chaos inbox still works:** Paste text → classify → drag into board creates goals/tasks appropriately. Old chaos endpoint still returns data.

8. **All existing tests still pass:** `pytest tests/ -v` exits 0.

9. **All new tests pass:** Phase-specific test suites pass before moving to next phase.

10. **Gracious degradation without API key:** No DeepSeek key → no AI features visible. Board works with manual entry only.

11. **No UI regression:** Canvas nebula, sunrise button, main quest field all still visible and functional. The app still looks and feels like Horizon Chamber.

12. **DB migration:** Existing databases with v2 schema auto-migrate to v3 without data loss. Chaos entries preserved.
