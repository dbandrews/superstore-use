# AGENTS.md - LLM Guide

## Project Overview

This is an AI-powered grocery shopping agent for **Real Canadian Superstore**. It uses the `browser-use` library to automate browser interactions with GPT-4.1, enabling automated grocery ordering.

## Architecture

### Entry Points

| File | Purpose | Run Command |
|------|---------|-------------|
| `main.py` | CLI - Interactive terminal shopping | `uv run main.py` |
| `app.py` | Local Flask web UI | `uv run app.py` |
| `modal_app.py` | Cloud deployment on Modal | `modal deploy modal_app.py` |
| `login.py` | One-time login to save session | `uv run login.py` |

### Core Flow

1. **Login** - Run `login.py` first to authenticate and save browser profile
2. **Add Items** - Agent searches for items and adds them to cart (parallel processing supported)
3. **Checkout** - Agent navigates through checkout steps, stops at final confirmation
4. **Place Order** - Requires explicit user confirmation before submitting

### Key Directories

- `./superstore-profile/` - Persisted browser state (cookies, login session)
- `./templates/` - Flask HTML templates (local app only)
- `./agent_docs/` - Documentation for Modal deployment

## Tech Stack

- **Python 3.11** with `uv` package manager
- **browser-use** - AI browser automation framework
- **OpenAI GPT-4.1** - LLM for agent decision-making
- **Flask** - Web interface
- **Modal** - Serverless cloud deployment
- **Playwright** - Browser automation backend

## Environment Variables

Required in `.env`:
```
OPENAI_API_KEY=...
SUPERSTORE_USER=...
SUPERSTORE_PASSWORD=...
```

For Modal (proxy support):
```
PROXY_SERVER=...
PROXY_USERNAME=...
PROXY_PASSWORD=...
```

## Development Notes

- Browser runs in headed mode locally (`headless=False`) for debugging
- Browser runs headless in Modal (`headless=True`)
- Parallel item adding uses `multiprocessing.Pool` with max 4 workers
- Each worker copies the base profile to preserve login state
- Always confirm order placement with user before final submission

## Common Tasks

**Setup:**
```bash
uv sync
uvx playwright install chromium --with-deps --no-shell
```

**First-time login:**
```bash
uv run login.py
```

**Run locally (CLI):**
```bash
uv run main.py
```

**Run locally (Web UI):**
```bash
uv run app.py  # Access at http://localhost:5000
```
