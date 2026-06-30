# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.x     | ✅ (MVP, active development) |

## Reporting a Vulnerability

This is a personal portfolio/MVP project. If you discover a security vulnerability:

1. **Do not** open a public GitHub issue — send details privately.
2. Reach out via email or by opening a draft security advisory on GitHub.
3. Include a description of the vulnerability and steps to reproduce.

## What to Expect

- I'll acknowledge receipt within 48 hours.
- I'll work on a fix and coordinate disclosure.
- For critical issues, I'll release a patched version as soon as possible.

## Security Notes

- **API keys** (`DEEPSEEK_API_KEY`, `FEED_API_KEY`, `N8N_API_KEY`) are stored only in the local `.env` file, which is excluded from version control via `.gitignore`.
- **Activity data** stays local in SQLite; no cloud upload.
- **AI inference** is sent to DeepSeek's API only when you explicitly use the capture/sort, goal suggestion, and task splitting or goal features.
- **n8n integration** is optional and requires explicit configuration.
