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

## Modal Deployment

See `agent_docs/modal.md` for detailed Modal rules and guidelines.

**Deploy chat app to Modal:**
```bash
uv run modal deploy modal_chat_app.py
```

**Serve locally with hot-reload (for development):**
```bash
uv run modal serve modal_chat_app.py  # Ctrl+C to stop
```

**Run a Modal app (one-off execution):**
```bash
uv run modal run modal_chat_app.py
```

**View deployed apps:**
```bash
uv run modal app list
```

**Stream logs for deployed app:**
```bash
uv run modal app logs superstore-chat-agent  # Ctrl+C to stop
```

**Stop a deployed app:**
```bash
uv run modal app stop superstore-chat-agent
```

**Get help on any command:**
```bash
uv run modal --help
uv run modal app --help
```

**Dashboard:** https://modal.com/apps

**Documentation:** https://modal.com/docs (or https://modal.com/llms-full.txt for LLM-friendly format)
