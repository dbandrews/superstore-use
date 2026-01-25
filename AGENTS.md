# AGENTS.md - LLM Guide

## Project Overview

This is an AI-powered grocery shopping agent for **Real Canadian Superstore**. It uses the `browser-use` library to automate browser interactions with GPT-4.1, enabling automated grocery ordering.

## Architecture

### File Structure

```
superstore-use/
    src/                     # Source package
        __init__.py
        core/                # Shared utilities
            __init__.py
            browser.py       # Browser config: STEALTH_ARGS, create_browser()
            config.py        # Configuration loading from config.toml
            success.py       # Success detection: detect_success_from_history()
            agent.py         # LangGraph chat agent + Modal tools
        local/               # Local CLI module
            __init__.py
            cli.py           # Entry point: uv run -m src.local.cli
        prompts/             # Prompt templates (markdown)
            login.md
            add_item.md
            checkout.md
            chat_system.md
    modal/app.py             # Single unified Modal deployment
    config.toml              # Configuration file
    agent_docs/              # Documentation for Modal deployment
    superstore-profile/      # Persisted browser state (cookies, login session)
```

### Entry Points

| Command | Purpose |
|---------|---------|
| `uv run -m src.local.cli login` | Local login to save browser profile |
| `uv run -m src.local.cli shop` | Local shopping with parallel browser windows |
| `uv run modal run modal/app.py::upload_profile` | Upload local profile to Modal volume |
| `uv run modal deploy modal/app.py` | Deploy unified Modal app (chat UI + automation) |

### Core Flow

1. **Login** - Run `uv run -m src.local.cli login` (local) or use chat UI (Modal)
2. **Add Items** - Agent searches for items and adds them to cart (parallel processing)
3. **Checkout** - Agent navigates through checkout steps, stops at final confirmation
4. **Place Order** - Requires explicit user confirmation before submitting

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `src/core/browser.py` | Browser creation, stealth args, profile detection |
| `src/core/config.py` | Configuration loading from config.toml |
| `src/core/success.py` | Success indicators and detection from agent history |
| `src/core/agent.py` | LangGraph chat agent with Modal tool wrappers |
| `src/local/cli.py` | Local CLI with login and parallel shopping commands |
| `modal/app.py` | Modal functions (login, add_item, upload_profile) + Chat UI Flask app |

## Tech Stack

- **Python 3.11** with `uv` package manager
- **browser-use** - AI browser automation framework
- **Groq** - LLM provider (Llama 3.3 70B for chat, GPT-OSS-120B for browser)
- **LangGraph** - Chat agent framework
- **Flask** - Web interface (Modal deployment)
- **Modal** - Serverless cloud deployment
- **Playwright** - Browser automation backend

## Environment Variables

Required in `.env`:
```
GROQ_API_KEY=...
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

- **Local**: Browser runs headed (`headless=False`) with tiled windows for demo
- **Modal**: Browser runs headless (`headless=True`) in containers
- **Local parallel**: Uses `multiprocessing.Pool` with max 4 workers, each with temp profile copy
- **Modal parallel**: Uses `starmap()` across separate containers with shared volume profile
- Always confirm order placement with user before final submission

## Common Tasks

**Setup:**
```bash
uv sync
uvx playwright install chromium --with-deps --no-shell
```

**First-time login (local):**
```bash
uv run -m src.local.cli login          # Headless
uv run -m src.local.cli login --headed # Visible browser for debugging
```

**Upload profile to Modal (required for Modal deployment):**
```bash
# After logging in locally, sync profile to Modal's persistent volume
uv run modal run modal/app.py::upload_profile
```

This uploads your authenticated browser profile to Modal's persistent volume,
so deployed functions use your saved login session instead of a blank profile.

**Run locally (CLI with parallel browsers):**
```bash
uv run -m src.local.cli shop
uv run -m src.local.cli shop --monitor-offset 0  # Adjust for your monitor setup
```

## Modal Deployment

See `agent_docs/modal.md` for detailed Modal rules and guidelines.

**Deploy to Modal:**
```bash
uv run modal deploy modal/app.py
```

**Serve locally with hot-reload (for development):**
```bash
uv run modal serve modal/app.py  # Ctrl+C to stop
```

**View deployed apps:**
```bash
uv run modal app list
```

**Stream logs for deployed app:**
```bash
uv run modal app logs superstore-agent  # Ctrl+C to stop
```

**Stop a deployed app:**
```bash
uv run modal app stop superstore-agent
```

**Get help on any command:**
```bash
uv run modal --help
uv run modal app --help
```

**Dashboard:** https://modal.com/apps

**Documentation:** https://modal.com/docs (or https://modal.com/llms-full.txt for LLM-friendly format)
