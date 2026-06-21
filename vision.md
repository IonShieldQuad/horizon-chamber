# Vision & Context - Horizon Chamber

## 1. The "Why" (Personal Pain Points)

This project was born from a very real struggle:

-   **Digital Overwhelm:** Countless open browser tabs, unwatched YouTube videos, and unread posts creating a constant background hum of anxiety.
-   **Organizational Friction:** Existing planners require manual data entry, which feels like too much overhead when you're already overwhelmed.
-   **Circadian Dysregulation:** Difficulty waking up (sleeping until noon) and poor emotional stability, where nice visuals and music provide tangible relief but are hard to integrate into a daily routine.
-   **The "Messy PC" Syndrome:** The desktop itself feels chaotic, making it hard to focus.

The core hypothesis is that **environment shapes behavior**. By turning the desktop into a beautiful, responsive "journey" rather than a static grid of icons, we can gently nudge the user toward better habits without adding cognitive load.

## 2. Market Research (What already exists)

| Product | What it does | Where it falls short for our vision |
| :--- | :--- | :--- |
| **Deep Focus** | Powerful task/habit management with Tauri. | Lacks immersive visuals and ambient audio. Feels like a tool, not a "world." |
| **Taskade** | AI-powered life coach dashboard. | Cloud-heavy, subscription-based. Not a local, privacy-first environment. |
| **Rainmeter** | Highly customizable desktop widgets. | Requires manual scripting; no AI integration or coherent visual theme. |
| **Poppy / Axorith** | Proactive OS-level automation. | Focused on utility, not emotional regulation or sensory experience. |

**The Gap:** No existing product combines *immersive ambient visuals*, *audio therapy*, *sleep/wake nudges*, and *AI-assisted thought organization* into a single, cohesive local-first application.

## 3. Long-Term Roadmap (The "Desktop 2.0" Dream)

This MVP (v0.1) is the foundation for a much larger vision. Future phases include:

-   **v0.2 - The Feed:** RSS, YouTube, and Discord summary aggregator (LLM-generated digests) so you never have to open 50 tabs again.
-   **v0.3 - The Companion:** Deeper AI assistant integration for companionship, therapy-style reflection, budgeting advice, and proactive problem-spotting.
-   **v0.4 - The System:** OS-level hooks (global hotkeys, file system organization, monitor brightness control).
-   **v0.5 - The Journey:** Procedural "quests" mapped to long-term goals, turning daily planning into an RPG-style experience.

## 4. Design Principles

-   **Zero Overhead:** No data entry just for the sake of it. Paste once, forget.
-   **Emotion-First:** Aesthetics and audio are not "nice-to-haves"—they are core functionality.
-   **Local-First:** Your data stays on your machine (SQLite). Cloud is only used for AI inference (DeepSeek API).
-   **Incremental:** Every version must be a fully usable, self-contained product.