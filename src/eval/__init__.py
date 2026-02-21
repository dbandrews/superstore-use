"""Browser agent evaluation harness.

Provides tools for evaluating browser-use agents with different LLMs,
prompts, and configurations. Measures timing, success rates, and
verifies cart contents after test runs.

Configuration is managed via Hydra YAML files in conf/ directory.

Usage (CLI):
    # Run with default config
    uv run -m src.eval.cli

    # Override LLM
    uv run -m src.eval.cli llm=llama_70b

    # Override items
    uv run -m src.eval.cli 'items=[bread,eggs,butter]'

    # Use headed browser
    uv run -m src.eval.cli browser=headed

    # Multirun across LLMs
    uv run -m src.eval.cli --multirun llm=gpt4,llama_70b

    # View results
    uv run -m src.eval.cli view ./eval_results/eval_result.json

    # List available LLM configs
    uv run -m src.eval.cli list-models

Usage (Python):
    from src.eval import EvalHarness, EvalConfig, run_quick_eval

    # Quick evaluation
    result = await run_quick_eval(["apples", "milk"], llm_model="gpt-4.1")
    print(result.get_summary())

    # Full configuration via Pydantic models
    config = EvalConfig.quick(items=["apples", "milk"])
    harness = EvalHarness(config)
    session = await harness.run_all()
"""

from src.eval.config import EvalConfig, EvalRun, LLMConfig, BrowserConfig, PromptConfig, JudgeConfig
from src.eval.harness import EvalHarness, run_quick_eval
from src.eval.results import EvalResult, EvalSession, CartItem, RunMetrics, ItemResult, TokenUsage, CostMetrics

__all__ = [
    # Configuration
    "EvalConfig",
    "EvalRun",
    "LLMConfig",
    "BrowserConfig",
    "PromptConfig",
    "JudgeConfig",
    # Harness
    "EvalHarness",
    "run_quick_eval",
    # Results
    "EvalResult",
    "EvalSession",
    "CartItem",
    "RunMetrics",
    "ItemResult",
    "TokenUsage",
    "CostMetrics",
]
