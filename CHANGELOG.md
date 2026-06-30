# Changelog

All notable changes to Horizon Chamber are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2025-01-15

### Added

- **Dynamic Nebula** — Full-viewport canvas with drifting particles; background color shifts with time of day (sunrise gold → daylight blue → sunset orange → night purple).
- **Sunrise Nudge** — 60-second audiovisual wake-up sequence (canvas fade to gold + 440→880 Hz sine tone via Web Audio API). Manual and auto-scheduled modes.
- **Capture & Sort** — AI-powered thought capture inside the kanban board. Paste ideas, URLs, or notes; AI classifies and injects them directly into your task workflow.
- **Main Quest** — Single editable daily intention stored in localStorage.
- **Activity Tracking (v0.2)** — Native Win32 active-window tracking, idle detection, focus-time summary with live bar chart. macOS/Linux stubs with graceful degradation.
- **AI-Powered Feed Aggregation (v0.5)** — n8n integration for summarized content (YouTube, LessWrong, Discord, RSS). Streamed SSE updates.
- **Smart Goals & Kanban Board (v0.3)** — Long-term goals, habits, maintenance tasks with AI type detection. Drag-and-drop kanban with today/doing/done/overdue columns. Auto-carry-over, progress bars, pause/archive.
- **Desktop App** — Native PyWebView window (Edge WebView2 on Windows, WebKit on macOS). No browser tabs, no address bar.
- **Standalone Build** — PyInstaller-based single-file `.exe` for portable distribution.

### Technical

- FastAPI backend with async SQLite (`aiosqlite`).
- Modular architecture: `db.py`, `monitor.py`, `scheduler.py`, `feed.py`, `goal_engine.py`, `deepseek_client.py`.
- All settings environment-variable configurable with `.env.example` template.
- Comprehensive pytest suite with 11 test files covering all modules.
