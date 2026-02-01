## Project Overview

AI-powered grocery shopping agent for **Real Canadian Superstore** using `browser-use` for browser automation.

## Architecture

```
src/
├── core/           # Shared utilities (browser.py, config.py, success.py, agent.py)
├── local/cli.py    # Local CLI: uv run -m src.local.cli
├── eval/           # Evaluation harness: uv run -m src.eval.cli
└── prompts/        # Prompt templates (login.md, add_item.md, checkout.md)
conf/               # Hydra configs (llm/, browser/, prompt/, judge/, experiment/)
modal/app.py        # Modal deployment
```

## Entry Points

| Command | Purpose |
|---------|---------|
| `uv run -m src.local.cli login` | Save browser profile |
| `uv run -m src.local.cli shop` | Local shopping |
| `uv run -m src.eval.cli` | Run evaluation harness |
| `uv run modal deploy modal/app.py` | Deploy to Modal |

## Quick Start

ALWAYS use `uv` to run python scripts.

```bash
# Setup
uv sync && uvx playwright install chromium --with-deps --no-shell

# Login and shop locally
uv run -m src.local.cli login
uv run -m src.local.cli shop

# Run eval
uv run -m src.eval.cli llm=llama_70b 'items=[bread,milk]'
```

## Environment Variables

See `.env.example` or set:
- `GROQ_API_KEY` - Required for Groq models
- `OPENROUTER_API_KEY` - Required for OpenRouter models
- `SUPERSTORE_USER`, `SUPERSTORE_PASSWORD` - For login

## Key Files

| Area | Files |
|------|-------|
| Browser setup | [src/core/browser.py](src/core/browser.py) |
| Eval harness | [src/eval/harness.py](src/eval/harness.py), [src/eval/config.py](src/eval/config.py) |
| Cart verification | [src/eval/cart_checker.py](src/eval/cart_checker.py) |
| Hydra config | [conf/config.yaml](conf/config.yaml) |
| LLM configs | [conf/llm/](conf/llm/) - see `uv run -m src.eval.cli list-models` |
| Judge configs | [conf/judge/](conf/judge/) |
| Modal docs | [agent_docs/modal.md](agent_docs/modal.md) |
| Prompts | [src/prompts/](src/prompts/) |

## Evaluation CLI

```bash
uv run -m src.eval.cli                           # Default config
uv run -m src.eval.cli llm=llama_70b             # Override LLM
uv run -m src.eval.cli browser=headed            # Visible browser
uv run -m src.eval.cli --multirun llm=gpt41,llama_70b  # Compare models
uv run -m src.eval.cli list-models               # Available LLMs
uv run -m src.eval.cli list-runs                 # Recent runs
uv run -m src.eval.cli --help                    # Full options
```

## Modal Deployment

See [agent_docs/modal.md](agent_docs/modal.md) for detailed guidelines.

```bash
uv run modal deploy modal/app.py      # Deploy
uv run modal serve modal/app.py       # Dev with hot-reload
uv run modal app logs superstore-agent  # Stream logs
```
