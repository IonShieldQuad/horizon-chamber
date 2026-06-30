# Contributing to Horizon Chamber

Thanks for your interest! This is an MVP project, and contributions are welcome.

## Getting Started

1. Fork the repository.
2. Clone your fork and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your `DEEPSEEK_API_KEY`.
4. Run the test suite to verify everything works:
   ```bash
   pytest tests/ -v
   ```
5. Start the dev server:
   ```bash
   uvicorn main:app --reload --port 8001
   ```

## Making Changes

- **Keep it focused.** One PR = one logical change.
- **Add tests.** New features should include tests. Bug fixes should add a test that reproduces the issue.
- **Run the full suite.** `pytest tests/ -v` must pass before opening a PR.
- **Follow the existing style.** The backend uses standard Python typing, async/await, and FastAPI patterns. The frontend is vanilla JS with no CDN dependencies.

## Code Structure

```
horizon/
├── main.py              # FastAPI app, routes, lifespan
├── db.py                # Async SQLite database layer
├── deepseek_client.py   # DeepSeek AI API client
├── monitor.py           # OS activity tracking (Win32)
├── scheduler.py         # Sunrise schedule checker
├── feed.py              # n8n feed aggregation
├── goal_engine.py       # Kanban goals system
├── desktop_app.py       # PyWebView desktop wrapper
├── static/
│   └── index.html       # All-in-one frontend (CSS + HTML + JS)
├── tests/               # pytest test suite
└── docs/                # (future) architecture diagrams, screenshots
```

## Reporting Issues

Open a GitHub issue with:
- A clear summary of the problem
- Steps to reproduce
- Expected vs actual behavior
- Environment (OS, Python version, browser/desktop mode)

## Thank You

Your time and effort help make this project better. ❤️
