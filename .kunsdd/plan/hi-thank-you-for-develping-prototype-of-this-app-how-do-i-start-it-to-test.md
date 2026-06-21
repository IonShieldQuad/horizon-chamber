I've gathered all the info from the project. Here's how to start the **Horizon** prototype:

---

## Quick Start

### 1. Install dependencies
Open a terminal in the project folder (`H:\Misc\horizon`) and run:

```bash
pip install -r requirements.txt
```

### 2. Set up your environment variables
The `.env` file already exists with placeholder values. Edit it:

- **`DEEPSEEK_API_KEY`** — Set this to your actual DeepSeek API key (the classify feature needs it; without it, `/api/classify` will return a 503, but the rest of the app will work)
- **`DATABASE_PATH=./horizon.db`** — Already set, just leave it

### 3. Start the server
```bash
uvicorn main:app --reload
```

The `--reload` flag enables hot-reload during development — the server restarts automatically when you change any `.py` file.

### 4. Open the app
Visit **http://localhost:8000** in your browser.

---

## What you'll see

Once it loads, you should get a **dark glassmorphism UI** with:

| Feature | What it does |
|---|---|
| **Canvas Nebula** | Animated starfield background that adapts to the time of day |
| **Main Quest** | A single daily intention — click to edit, stored in localStorage |
| **Chaos Tamer** | Paste messy text / browser tabs, click "Tame the Chaos" to have AI categorise them into "Now", "Later", and "Trash" draggable cards |
| **Sunrise Button** | Bottom-right — click for a 60-second audiovisual sunrise (canvas fades to gold + 440→880Hz tone) |
| **Auto-Sunrise** | Gear icon ⚙️ to set a daily alarm; server polls every 30s and triggers at the configured time |

---

## If you run into issues

- **`ModuleNotFoundError`** for any package → Run `pip install -r requirements.txt` again (preferably in a fresh virtual environment)
- **`DEEPSEEK_API_KEY` not set** → The API key is only needed for the Chaos Tamer AI classify feature; set it to a real key if you want that to work
- **Port 8000 already in use** → Use a different port: `uvicorn main:app --reload --port 8001`
- **Want to run tests first?** → `pytest tests/ -v` (should pass)

Would you like me to elaborate on any part of the setup, or help you get it running?