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
        eval/                # Evaluation harness module
            __init__.py
            cli.py           # Entry point: uv run -m src.eval.cli (Hydra-powered)
            config.py        # EvalConfig, EvalRun, LLMConfig Pydantic models
            hydra_config.py  # Hydra structured configs + conversion layer
            harness.py       # EvalHarness runner, run_quick_eval()
            results.py       # EvalResult, CartItem, RunMetrics
            cart_checker.py  # Cart verification after runs
        prompts/             # Prompt templates (markdown)
            login.md
            add_item.md
            add_item_concise.md  # Shorter prompt variant for testing
            checkout.md
            chat_system.md
    conf/                    # Hydra configuration (YAML)
        config.yaml          # Main config with defaults
        llm/                 # LLM config group (gpt4, llama_70b, etc.)
        browser/             # Browser config group (headless, headed, stealth)
        prompt/              # Prompt config group (default, concise)
        judge/               # LLM-as-a-judge config group (default, llama_70b, etc.)
        experiment/          # Experiment presets (quick_test, full_comparison)
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
| `uv run -m src.eval.cli` | Run evaluation harness (Hydra-powered) |
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
| `src/eval/harness.py` | Evaluation harness for testing different LLMs/prompts |
| `src/eval/cart_checker.py` | Cart verification after evaluation runs |
| `modal/app.py` | Modal functions (login, add_item, upload_profile) + Chat UI Flask app |

## Tech Stack

- **Python 3.11** with `uv` package manager
- **browser-use** - AI browser automation framework
- **Groq** - LLM provider (Llama 3.3 70B for chat, GPT-OSS-120B for browser)
- **LangGraph** - Chat agent framework
- **Hydra** - Configuration management for evaluation harness
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

## Evaluation Harness

The evaluation harness (`src/eval/`) allows testing browser agents with different LLMs, prompts, and configurations. It tracks timing, success rates, and verifies cart contents after each run.

Configuration is managed via **Hydra** (YAML files in `conf/` directory).

### Key Features

- **Multiple LLMs**: Test with different models (GPT-4.1, Llama 70B, etc.)
- **Custom Prompts**: Compare different prompt templates
- **Timing Metrics**: Track duration per item, steps taken, total time
- **Cart Verification**: Opens cart after test to verify what was actually added
- **Temporary Profiles**: Each run uses a fresh, isolated browser profile (no auth needed)
- **Profile Browsing**: Launch browser with leftover temp profile to inspect cart state
- **Cost Tracking**: Automatic token usage and cost tracking per item and run
- **Result Export**: JSON output saved to Hydra output directory for analysis
- **Hydra Config**: YAML-based config with composition and command-line overrides
- **Multirun Support**: Sweep across LLMs/configs with `--multirun`

### Quick Start

```bash
# Run with default config (conf/config.yaml)
uv run -m src.eval.cli

# Override LLM
uv run -m src.eval.cli llm=llama_70b

# Override items
uv run -m src.eval.cli 'items=[bread,eggs,butter]'

# Run with visible browser (for debugging)
# Profiles are kept by default for inspection
uv run -m src.eval.cli browser=headed

# Run and clean up temp profile afterward
uv run -m src.eval.cli cleanup_profile=true

# Use experiment preset
uv run -m src.eval.cli +experiment=quick_test

# Multirun across LLMs
uv run -m src.eval.cli --multirun llm=gpt4,llama_70b

# View resolved config without running
uv run -m src.eval.cli --cfg job

# Preview config changes (dry run)
uv run -m src.eval.cli dry_run=true

# List available LLM configs
uv run -m src.eval.cli list-models

# List recent evaluation runs (with costs)
uv run -m src.eval.cli list-runs

# View results from previous run
uv run -m src.eval.cli view outputs/2026-01-27/12-00-00/eval_result.json

# Compare multiple runs (shows costs, tokens, success rates)
uv run -m src.eval.cli compare outputs/*/eval_result.json

# Browse temp profile from previous eval run (inspect cart state)
uv run -m src.eval.cli browse /tmp/eval-profile-abc123/profile

# Browse with custom URL
uv run -m src.eval.cli browse /tmp/eval-profile-abc123/profile https://www.realcanadiansuperstore.ca/
```

### Hydra Config Structure

Configuration is in `conf/` directory with composable groups:

```
conf/
├── config.yaml           # Main config (defaults composition)
├── llm/                   # LLM config group
│   ├── gpt4.yaml         # GPT-4.1 via Groq
│   ├── gpt_oss_120b.yaml # GPT-OSS-120B via Groq
│   ├── llama_70b.yaml    # Llama 3.3 70B via Groq
│   └── llama_8b.yaml     # Llama 3.1 8B via Groq
├── browser/               # Browser config group
│   ├── headless.yaml     # Default headless
│   ├── headed.yaml       # For debugging
│   └── stealth.yaml      # Bot detection avoidance
├── prompt/                # Prompt config group
│   ├── default.yaml
│   └── concise.yaml
├── judge/                 # LLM-as-a-judge config group
│   ├── default.yaml      # GPT-4o via OpenAI (default)
│   ├── gpt4.yaml         # GPT-4.1 via Groq
│   ├── llama_70b.yaml    # Llama 3.3 70B via Groq
│   ├── claude.yaml       # Claude Sonnet via Anthropic
│   └── disabled.yaml     # Disable judge evaluation
└── experiment/            # Experiment presets
    ├── quick_test.yaml
    └── full_comparison.yaml
```

### Config Overrides

```bash
# Select config groups
uv run -m src.eval.cli llm=llama_70b browser=headed prompt=concise

# Override specific values
uv run -m src.eval.cli llm.temperature=0.5 max_steps=50

# Use experiment preset
uv run -m src.eval.cli +experiment=full_comparison

# Combine overrides
uv run -m src.eval.cli llm=llama_70b 'items=[bread]' max_steps=20

# Configure LLM judge (see below for details)
uv run -m src.eval.cli judge=llama_70b
uv run -m src.eval.cli judge.model=gpt-4-turbo judge.temperature=0.2
uv run -m src.eval.cli judge=disabled  # Skip judge evaluation
```

### LLM-as-a-Judge Configuration

The evaluation harness uses an LLM judge to semantically verify cart contents against requested items. This allows for fuzzy matching (e.g., "apples" matches "Naturally Imperfect Apples, 6 lb bag").

**Judge config options:**

| Field | Description | Default |
|-------|-------------|---------|
| `model` | Model identifier (e.g., `gpt-4o`, `llama-3.3-70b-versatile`) | `gpt-4o` |
| `provider` | LLM provider (`openai`, `groq`, `anthropic`) | `openai` |
| `temperature` | Sampling temperature (0.0 for deterministic) | `0.0` |
| `prompt_template` | Path to custom judge prompt template | `null` (uses default) |
| `enabled` | Whether to run LLM judge evaluation | `true` |

**Available presets:**

```bash
# Use OpenAI GPT-4o (default)
uv run -m src.eval.cli judge=default

# Use Groq GPT-4.1 (faster, cost-effective)
uv run -m src.eval.cli judge=gpt4

# Use Llama 3.3 70B via Groq
uv run -m src.eval.cli judge=llama_70b

# Use Claude Sonnet via Anthropic
uv run -m src.eval.cli judge=claude

# Disable judge entirely
uv run -m src.eval.cli judge=disabled
```

**Custom judge prompt:**

Create a custom prompt template file with `{requested_items}` and `{cart_contents}` placeholders:

```bash
uv run -m src.eval.cli judge.prompt_template=path/to/custom_judge.md
```

**Using different models for agent vs judge:**

```bash
# Agent uses Llama 70B, judge uses GPT-4o
uv run -m src.eval.cli llm=llama_70b judge=default

# Agent uses GPT-4.1, judge uses Claude
uv run -m src.eval.cli llm=gpt4 judge=claude
```

### Python API

```python
from src.eval import EvalHarness, EvalConfig, run_quick_eval

# Quick evaluation
result = await run_quick_eval(["apples", "milk"], llm_model="gpt-4.1")
print(result.get_summary())

# Full configuration via Pydantic models
config = EvalConfig.quick(items=["apples", "milk"])
harness = EvalHarness(config)
session = await harness.run_all()
```

### Result Structure

Each evaluation produces:
- `EvalResult`: Overall run outcome with success rate and cost metrics
- `ItemResult`: Per-item status, timing, steps, and token usage
- `CartItem`: Items found in cart after verification
- `RunMetrics`: Timing breakdown and averages
- `CostMetrics`: Token usage and estimated costs (aggregated and per-item)
- `TokenUsage`: Input/output token breakdown

### Cost Tracking

The evaluation harness automatically tracks token usage for each agent run:

```python
result = await run_quick_eval(["apples", "milk"])

# Access token usage
print(f"Total tokens: {result.cost_metrics.token_usage.total_tokens:,}")
print(f"Input tokens: {result.cost_metrics.token_usage.input_tokens:,}")
print(f"Output tokens: {result.cost_metrics.token_usage.output_tokens:,}")

# Per-item breakdown
for item, usage in result.cost_metrics.tokens_per_item.items():
    print(f"  {item}: {usage.total_tokens:,} tokens")
```

The summary output includes token usage:
```
Evaluation: quick_eval
Status: SUCCESS
Success Rate: 100.0%

Items:
  [+] apples (45.2s, 12 steps)
  [+] milk (38.1s, 10 steps)

Token Usage:
  Input: 15,432
  Output: 2,841
  Total: 18,273
  Avg per Item: 9,136
```
