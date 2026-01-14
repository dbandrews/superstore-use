# AGENTS.md - LLM Guide

## Project Overview

This is an AI-powered grocery shopping agent for **Real Canadian Superstore**. It uses the `browser-use` library to automate browser interactions with GPT-4.1, enabling automated grocery ordering.

## Architecture

### File Structure

```
superstore-use/
    conf/                    # Hydra configuration
        config.yaml          # Main config (model, browser, eval settings)
        model/               # Model-specific configs
            openai.yaml      # OpenAI gpt-4.1 (default)
            anthropic.yaml   # Anthropic claude-sonnet-4-0
            openrouter.yaml  # OpenRouter models
    core/                    # Shared utilities
        __init__.py
        browser.py           # Browser config: STEALTH_ARGS, create_browser()
        llm.py               # Configurable LLM factory: create_llm()
        success.py           # Success detection: detect_success_from_history()
        agent.py             # LangGraph chat agent + Modal tools
    local/                   # Local CLI module
        __init__.py
        cli.py               # Entry point: uv run -m local.cli
    eval.py                  # Model evaluation script
    modal_app.py             # Single unified Modal deployment
    agent_docs/              # Documentation for Modal deployment
    superstore-profile/      # Persisted browser state (cookies, login session)
    eval_results/            # Evaluation output (JSON files)
```

### Entry Points

| Command | Purpose |
|---------|---------|
| `uv run -m local.cli login` | Local login to save browser profile |
| `uv run -m local.cli shop` | Local shopping with parallel browser windows |
| `uv run python eval.py` | Run model evaluation with configurable models |
| `modal deploy modal_app.py` | Deploy unified Modal app (chat UI + automation) |

### Core Flow

1. **Login** - Run `uv run -m local.cli login` (local) or use chat UI (Modal)
2. **Add Items** - Agent searches for items and adds them to cart (parallel processing)
3. **Checkout** - Agent navigates through checkout steps, stops at final confirmation
4. **Place Order** - Requires explicit user confirmation before submitting

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `core/browser.py` | Browser creation, stealth args, profile detection |
| `core/llm.py` | Configurable LLM factory for OpenAI/Anthropic/OpenRouter |
| `core/success.py` | Success indicators and detection from agent history |
| `core/agent.py` | LangGraph chat agent with Modal tool wrappers |
| `local/cli.py` | Local CLI with login and parallel shopping commands |
| `eval.py` | Model evaluation with Hydra config and cart verification |
| `modal_app.py` | Modal functions (login, add_item) + Chat UI Flask app |

## Tech Stack

- **Python 3.11** with `uv` package manager
- **browser-use** - AI browser automation framework
- **Hydra** - Configuration management for model swapping
- **OpenAI/Anthropic/OpenRouter** - Configurable LLM providers
- **LangGraph** - Chat agent framework
- **Flask** - Web interface (Modal deployment)
- **Modal** - Serverless cloud deployment
- **Playwright** - Browser automation backend

## Environment Variables

Required in `.env`:
```
OPENAI_API_KEY=...
SUPERSTORE_USER=...
SUPERSTORE_PASSWORD=...
```

For Anthropic models:
```
ANTHROPIC_API_KEY=...
```

For OpenRouter models:
```
OPENROUTER_API_KEY=...
```

For Modal (proxy support):
```
PROXY_SERVER=...
PROXY_USERNAME=...
PROXY_PASSWORD=...
```

## Model Configuration (Hydra)

The project uses Hydra for configurable model swapping. Configuration files are in `conf/`.

### Available Model Configs

| Config | Provider | Model |
|--------|----------|-------|
| `model=openai` (default) | OpenAI | gpt-4.1 |
| `model=openai_gpt4o` | OpenAI | gpt-4o |
| `model=openai_o3` | OpenAI | o3 |
| `model=anthropic` | Anthropic | claude-sonnet-4-0 |
| `model=anthropic_opus` | Anthropic | claude-opus-4-0 |
| `model=openrouter` | OpenRouter | anthropic/claude-sonnet-4 |
| `model=openrouter_gpt4` | OpenRouter | openai/gpt-4-turbo |
| `model=openrouter_gemini` | OpenRouter | google/gemini-2.0-flash-001 |

### Creating Custom Model Configs

Create a new YAML file in `conf/model/`:

```yaml
# conf/model/my_model.yaml
provider: openai  # or "anthropic" or "openrouter"
name: gpt-4o-mini
temperature: 0
api_key_env: OPENAI_API_KEY
# base_url: https://custom-api.example.com/v1  # optional
```

### Using Models in Code

```python
from core.llm import create_llm, create_llm_from_config

# Direct creation
llm = create_llm(provider="openai", name="gpt-4.1")
llm = create_llm(provider="anthropic", name="claude-sonnet-4-0")
llm = create_llm(
    provider="openrouter",
    name="anthropic/claude-sonnet-4",
    base_url="https://openrouter.ai/api/v1"
)

# From Hydra config
llm = create_llm_from_config(cfg.model)
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
uv run -m local.cli login          # Headless
uv run -m local.cli login --headed # Visible browser for debugging
```

**Run locally (CLI with parallel browsers):**
```bash
uv run -m local.cli shop
uv run -m local.cli shop --monitor-offset 0  # Adjust for your monitor setup
```

## Model Evaluation

Run the eval script to test models on a grocery shopping task:

**Run evaluation with default model (OpenAI gpt-4.1):**
```bash
uv run python eval.py
```

**Run with different models:**
```bash
uv run python eval.py model=anthropic
uv run python eval.py model=openrouter
uv run python eval.py model=openai_gpt4o
```

**Custom grocery list:**
```bash
uv run python eval.py groceries='["milk", "bread", "apples"]'
```

**Run multiple models (Hydra multirun):**
```bash
uv run python eval.py --multirun model=openai,anthropic,openrouter
```

**Run with visible browser (for debugging):**
```bash
uv run python eval.py browser.headless=false
```

**Evaluation Output:**
- Results are saved to `./eval_results/` as JSON files
- Each run includes:
  - Success/failure for each item
  - Steps taken and duration
  - Cart verification results
  - Overall success rate

## Modal Deployment

See `agent_docs/modal.md` for detailed Modal rules and guidelines.

**Deploy to Modal:**
```bash
uv run modal deploy modal_app.py
```

**Serve locally with hot-reload (for development):**
```bash
uv run modal serve modal_app.py  # Ctrl+C to stop
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
