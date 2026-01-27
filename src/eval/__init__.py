"""Browser agent evaluation harness.

Provides tools for evaluating browser-use agents with different LLMs,
prompts, and configurations. Measures timing, success rates, and
verifies cart contents after test runs.

Usage (CLI):
    uv run -m src.eval.cli run --items "apples" "milk" --llm gpt-4.1
    uv run -m src.eval.cli run --config eval_config.json
    uv run -m src.eval.cli run --items "bread" --headed --keep-profile
    uv run -m src.eval.cli list-models
    uv run -m src.eval.cli example-config > my_eval.json
    uv run -m src.eval.cli view ./eval_results/eval_result.json
    uv run -m src.eval.cli compare result1.json result2.json

Usage (Python):
    from src.eval import EvalHarness, EvalConfig, run_quick_eval

    # Quick evaluation
    result = await run_quick_eval(["apples", "milk"], llm_model="gpt-4.1")
    print(result.get_summary())

    # Full configuration
    config = EvalConfig.from_file("eval_config.json")
    harness = EvalHarness(config)
    session = await harness.run_all()
"""

from src.eval.config import EvalConfig, EvalRun, LLMConfig, BrowserConfig, PromptConfig
from src.eval.harness import EvalHarness, run_quick_eval
from src.eval.results import EvalResult, EvalSession, CartItem, RunMetrics, ItemResult

__all__ = [
    # Configuration
    "EvalConfig",
    "EvalRun",
    "LLMConfig",
    "BrowserConfig",
    "PromptConfig",
    # Harness
    "EvalHarness",
    "run_quick_eval",
    # Results
    "EvalResult",
    "EvalSession",
    "CartItem",
    "RunMetrics",
    "ItemResult",
]
