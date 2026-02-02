"""CLI for running browser agent evaluations.

This CLI uses Hydra for configuration management. The main `run` command
is powered by Hydra, while utility commands (list-models, view, compare)
use standard Python.

Usage:
    # Run evaluation with default config
    uv run -m src.eval.cli

    # Override LLM
    uv run -m src.eval.cli llm=llama_70b

    # Override items
    uv run -m src.eval.cli 'items=[bread,eggs,butter]'

    # Use headed browser
    uv run -m src.eval.cli browser=headed

    # Multirun across LLMs (sequential)
    uv run -m src.eval.cli --multirun llm=gpt4,llama_70b

    # Multirun across LLMs (parallel)
    uv run -m src.eval.cli --multirun hydra/launcher=joblib llm=gpt4,llama_70b

    # Use experiment preset
    uv run -m src.eval.cli +experiment=quick_test

    # View resolved config without running
    uv run -m src.eval.cli --cfg job

    # Utility commands (not Hydra-powered)
    uv run -m src.eval.cli list-models
    uv run -m src.eval.cli view ./eval_results/result.json
    uv run -m src.eval.cli compare result1.json result2.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def run_eval_hydra() -> None:
    """Run evaluation using Hydra configuration."""
    import hydra
    from hydra.core.hydra_config import HydraConfig
    from omegaconf import DictConfig, OmegaConf

    # Import to register configs before Hydra runs
    from src.eval.hydra_config import convert_to_pydantic
    from src.eval.harness import EvalHarness, cleanup_temp_profile

    @hydra.main(version_base=None, config_path="../../conf", config_name="config")
    def hydra_main(cfg: DictConfig) -> None:
        """Hydra entry point for running evaluations."""
        # Convert Hydra config to Pydantic models
        config = convert_to_pydantic(cfg)

        # Get Hydra output directory for saving results
        hydra_output_dir = Path(HydraConfig.get().runtime.output_dir)

        # Print config summary
        print("\n" + "=" * 60)
        print("EVALUATION CONFIGURATION")
        print("=" * 60)
        print(f"Name: {config.name}")
        print(f"Runs: {len(config.runs)}")
        print(f"Output: {hydra_output_dir}")

        for i, run in enumerate(config.runs, 1):
            print(f"\nRun {i}: {run.name}")
            print(f"  Items: {run.items}")
            print(f"  LLM: {run.llm.model} ({run.llm.provider})")
            print(f"  Headless: {run.browser.headless}")
            print(f"  Max steps: {run.max_steps}")

        print("=" * 60 + "\n")

        # Check for dry run via Hydra override
        if OmegaConf.select(cfg, "dry_run", default=False):
            print("Dry run - exiting without executing")
            return

        # Run the evaluation
        harness = EvalHarness(config)

        if len(config.runs) == 1:
            result = asyncio.run(harness.run_single(config.runs[0]))
            print("\n" + result.get_summary())

            # Save result to Hydra output directory
            result_path = hydra_output_dir / "eval_result.json"
            result.to_file(result_path)
            print(f"\nResult saved to: {result_path}")

            # Clean up temp profile if cleanup_profile is set (defaults to false = keep profiles)
            cleanup_profile = OmegaConf.select(cfg, "cleanup_profile", default=False)
            if cleanup_profile and result.profile_dir:
                cleanup_temp_profile(Path(result.profile_dir))
                print("Cleaned up temp profile")
            elif result.profile_dir:
                print(f"Temp profile kept at: {result.profile_dir}")
        else:
            session = asyncio.run(harness.run_all())
            print("\n" + session.get_summary())

            # Save session to Hydra output directory
            session_path = hydra_output_dir / "session.json"
            session.to_file(session_path)
            print(f"\nSession saved to: {session_path}")

            # Clean up temp profiles if cleanup_profile is set (defaults to false = keep profiles)
            cleanup_profile = OmegaConf.select(cfg, "cleanup_profile", default=False)
            for result in session.results:
                if cleanup_profile and result.profile_dir:
                    cleanup_temp_profile(Path(result.profile_dir))
                    print(f"Cleaned up temp profile: {result.profile_dir}")
                elif result.profile_dir:
                    print(f"Temp profile kept at: {result.profile_dir}")

    # Run Hydra
    hydra_main()


def list_models() -> None:
    """List available LLM model presets (from YAML config files)."""
    conf_dir = Path(__file__).parent.parent.parent / "conf" / "llm"

    print("\nAvailable LLM Configs:")
    print("-" * 50)

    if conf_dir.exists():
        for yaml_file in sorted(conf_dir.glob("*.yaml")):
            name = yaml_file.stem
            # Read and parse YAML to show model details
            try:
                import yaml
                with open(yaml_file) as f:
                    config = yaml.safe_load(f)
                model = config.get("model", "unknown")
                provider = config.get("provider", "unknown")
                print(f"  {name:20} -> {model} ({provider})")
            except Exception:
                print(f"  {name:20} -> (error reading config)")
    else:
        print("  (no LLM configs found)")

    print("-" * 50)
    print("\nUsage: uv run -m src.eval.cli llm=<name>")
    print("Example: uv run -m src.eval.cli llm=llama_70b")


def view_results(result_file: str) -> None:
    """View results from a previous evaluation run."""
    from src.eval.results import EvalResult, EvalSession

    result_path = Path(result_file)

    if not result_path.exists():
        print(f"Error: Result file not found: {result_path}", file=sys.stderr)
        sys.exit(1)

    if result_path.name == "session.json":
        with open(result_path) as f:
            data = json.load(f)
        session = EvalSession.model_validate(data)
        print(session.get_summary())
    else:
        result = EvalResult.from_file(result_path)
        print(result.get_summary())


def compare_results(result_files: list[str]) -> None:
    """Compare results from multiple evaluation runs."""
    from src.eval.results import EvalResult

    results = []
    for path in result_files:
        result = EvalResult.from_file(path)
        results.append((path, result))

    print("\n" + "=" * 110)
    print("COMPARISON SUMMARY")
    print("=" * 110)
    print(f"{'Run Name':<25} {'LLM':<20} {'Status':<10} {'Success':<8} {'In/Out/Cached':<20} {'Cost':<12} {'Duration':<10}")
    print("-" * 110)

    for path, result in results:
        duration = f"{result.metrics.total_duration_seconds:.1f}s" if result.metrics.total_duration_seconds else "N/A"
        llm = result.config_summary.get("llm_model", "unknown")[:18]
        usage = result.cost_metrics.token_usage
        if usage.total_tokens > 0:
            cached_str = f"+{usage.cached_tokens//1000}k" if usage.cached_tokens > 0 else ""
            tokens = f"{usage.input_tokens//1000}k/{usage.output_tokens//1000}k{cached_str}"
        else:
            tokens = "N/A"
        # Prefer total_cost from token usage (more detailed), fall back to estimated_cost_usd
        cost = usage.total_cost if usage.total_cost > 0 else result.cost_metrics.estimated_cost_usd
        cost_str = f"${cost:.4f}" if cost else "N/A"
        print(f"{result.run_name:<25} {llm:<20} {result.status:<10} {result.success_rate:.0%}      {tokens:<20} {cost_str:<12} {duration}")

    print("=" * 110)

    # Show per-item comparison with costs
    print("\nPer-Item Breakdown:")
    all_items = set()
    for _, result in results:
        all_items.update(result.items_requested)

    for item in sorted(all_items):
        print(f"\n  {item}:")
        for path, result in results:
            item_result = next((r for r in result.item_results if r.item == item), None)
            if item_result:
                usage = item_result.token_usage
                if usage.total_tokens > 0:
                    cached_str = f" ({usage.cached_tokens:,} cached)" if usage.cached_tokens > 0 else ""
                    tokens_str = f", {usage.total_tokens:,} tokens{cached_str}"
                else:
                    tokens_str = ""
                cost = usage.total_cost if usage.total_cost > 0 else item_result.estimated_cost_usd
                cost_str = f", ${cost:.4f}" if cost else ""
                llm = result.config_summary.get("llm_model", "")[:15]
                print(f"    [{llm}] {item_result.status} ({item_result.duration_seconds:.1f}s{tokens_str}{cost_str})")
            else:
                print(f"    {result.run_name}: (not tested)")

    # Show cart verification summary
    print("\n" + "-" * 90)
    print("Cart Verification:")
    for path, result in results:
        llm = result.config_summary.get("llm_model", "unknown")[:20]
        judge_enabled = result.config_summary.get("judge_enabled", True)
        print(f"\n  [{llm}]")
        if result.cart_items:
            for cart_item in result.cart_items:
                print(f"    - {cart_item.quantity}x {cart_item.name}" + (f" ({cart_item.price})" if cart_item.price else ""))
        else:
            print("    (no cart items)")
        if not judge_enabled:
            print("    (judge disabled)")

    # Detailed token breakdown
    print("\n" + "-" * 110)
    print("Token Breakdown:")
    for path, result in results:
        llm = result.config_summary.get("llm_model", "unknown")[:20]
        usage = result.cost_metrics.token_usage
        if usage.total_tokens > 0:
            print(f"\n  [{llm}]")
            print(f"    Input:  {usage.input_tokens:,}" + (f" ({usage.cached_tokens:,} cached, {usage.non_cached_input_tokens:,} new)" if usage.cached_tokens > 0 else ""))
            print(f"    Output: {usage.output_tokens:,}")
            print(f"    Total:  {usage.total_tokens:,}")
            if usage.total_cost > 0:
                print(f"    Cost:   ${usage.total_cost:.4f}" + (f" (in: ${usage.input_cost:.4f}, out: ${usage.output_cost:.4f}, cache: ${usage.cached_cost:.4f})" if usage.input_cost > 0 else ""))
            if usage.entry_count > 0:
                print(f"    Calls:  {usage.entry_count}")
            # Show by-model if available
            if usage.by_model:
                for model, model_usage in usage.by_model.items():
                    model_tokens = model_usage.get("input_tokens", 0) + model_usage.get("output_tokens", 0)
                    model_cached = model_usage.get("cached_tokens", 0)
                    model_cost = model_usage.get("total_cost", 0)
                    cached_str = f" ({model_cached:,} cached)" if model_cached > 0 else ""
                    cost_str = f", ${model_cost:.4f}" if model_cost > 0 else ""
                    print(f"      {model}: {model_tokens:,} tokens{cached_str}{cost_str}")
        else:
            print(f"\n  [{llm}] (no token data)")

    # Cost efficiency summary
    print("\n" + "-" * 110)
    print("Cost Efficiency (per successful item):")
    for path, result in results:
        llm = result.config_summary.get("llm_model", "unknown")[:20]
        successful_items = sum(1 for r in result.item_results if r.status == "success")
        usage = result.cost_metrics.token_usage
        total_tokens = usage.total_tokens
        total_cost = usage.total_cost if usage.total_cost > 0 else (result.cost_metrics.estimated_cost_usd or 0)
        if successful_items > 0 and total_tokens > 0:
            tokens_per_success = total_tokens / successful_items
            cost_per_success = total_cost / successful_items if total_cost > 0 else 0
            cost_str = f", ${cost_per_success:.4f}/success" if cost_per_success > 0 else ""
            print(f"  [{llm}] {tokens_per_success:,.0f} tokens/success{cost_str}")
        else:
            print(f"  [{llm}] N/A (no successful items or no token data)")


def list_runs(outputs_dir: str = "outputs", limit: int = 10) -> None:
    """List recent evaluation runs from Hydra outputs directory."""
    from src.eval.results import EvalResult

    outputs_path = Path(outputs_dir)
    if not outputs_path.exists():
        print(f"Outputs directory not found: {outputs_path}")
        return

    # Find all eval_result.json files
    result_files = list(outputs_path.rglob("eval_result.json"))
    if not result_files:
        print("No evaluation results found")
        return

    # Sort by modification time (most recent first)
    result_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    result_files = result_files[:limit]

    print("\n" + "=" * 115)
    print(f"RECENT EVALUATION RUNS (last {len(result_files)})")
    print("=" * 115)
    print(f"{'Path':<50} {'LLM':<18} {'Success':<8} {'In/Out/Cached':<18} {'Cost':<12}")
    print("-" * 115)

    for result_path in result_files:
        try:
            result = EvalResult.from_file(result_path)
            # Get relative path from outputs dir
            rel_path = str(result_path.relative_to(outputs_path.parent))[:48]
            llm = result.config_summary.get("llm_model", "unknown")[:16]
            usage = result.cost_metrics.token_usage
            if usage.total_tokens > 0:
                cached_str = f"+{usage.cached_tokens//1000}k" if usage.cached_tokens > 0 else ""
                tokens = f"{usage.input_tokens//1000}k/{usage.output_tokens//1000}k{cached_str}"
            else:
                tokens = "N/A"
            # Prefer total_cost from token usage, fall back to estimated_cost_usd
            cost = usage.total_cost if usage.total_cost > 0 else result.cost_metrics.estimated_cost_usd
            cost_str = f"${cost:.4f}" if cost else "N/A"
            print(f"{rel_path:<50} {llm:<18} {result.success_rate:.0%}     {tokens:<18} {cost_str}")
        except Exception as e:
            print(f"{result_path}: (error: {e})")

    print("=" * 115)
    print(f"\nTo compare runs, use:")
    print(f"  uv run -m src.eval.cli compare <path1> <path2> ...")


def browse_profile(profile_path: str, url: str | None = None) -> None:
    """Launch a browser with an existing profile for inspection.

    Args:
        profile_path: Path to the browser profile directory (e.g., from an eval run)
        url: Optional URL to navigate to (defaults to cart URL from config)
    """
    from playwright.sync_api import sync_playwright

    from src.core.config import load_config

    profile = Path(profile_path)
    if not profile.exists():
        print(f"Error: Profile directory not found: {profile_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    target_url = url or f"{config.app.base_url}/cartReview"

    print(f"\nLaunching browser with profile: {profile_path}")
    print(f"Navigating to: {target_url}")
    print("Close the browser window when done.\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            args=["--disable-features=LockProfileCookieDatabase"],
        )
        page = context.new_page()
        page.goto(target_url, wait_until="load")

        # Wait for user to close the browser
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass

        context.close()

    print("Browser closed.")


def print_help() -> None:
    """Print help message for the CLI."""
    help_text = """
Browser Agent Evaluation CLI (Hydra-powered)

USAGE:
    uv run -m src.eval.cli [HYDRA_ARGS...]
    uv run -m src.eval.cli <COMMAND> [ARGS...]

HYDRA COMMANDS (for running evaluations):
    (default)           Run evaluation with config from conf/
    --help              Show Hydra help
    --cfg job           Show resolved configuration
    --multirun          Run multiple configurations

HYDRA OVERRIDES:
    llm=NAME            Use LLM config (gpt4, llama_70b, etc.)
    browser=NAME        Use browser config (headless, headed, stealth)
    prompt=NAME         Use prompt config (default, concise)
    +experiment=NAME    Use experiment preset (quick_test, full_comparison)
    'items=[a,b,c]'     Override items list
    max_steps=N         Override max steps
    dry_run=true        Print config without running
    cleanup_profile=true  Remove temp browser profile after run (default: keep it)
    hydra/launcher=joblib  Run multirun jobs in parallel (use with --multirun)

UTILITY COMMANDS:
    list-models         List available LLM configurations
    list-runs [DIR] [N] List recent N evaluation runs from outputs directory
    view FILE           View results from a previous run
    compare FILE...     Compare multiple evaluation results (with costs/tokens)
    browse PATH [URL]   Launch browser with temp profile from eval run

EXAMPLES:
    # Run with default config
    uv run -m src.eval.cli

    # Override LLM
    uv run -m src.eval.cli llm=llama_70b

    # Run with visible browser
    uv run -m src.eval.cli browser=headed

    # Override items
    uv run -m src.eval.cli 'items=[bread,eggs]'

    # Multirun across LLMs (sequential)
    uv run -m src.eval.cli --multirun llm=gpt4,llama_70b

    # Multirun across LLMs (parallel)
    uv run -m src.eval.cli --multirun hydra/launcher=joblib llm=gpt4,llama_70b

    # Use experiment preset
    uv run -m src.eval.cli +experiment=quick_test

    # View results
    uv run -m src.eval.cli view ./eval_results/eval_result.json

    # Browse temp profile from eval run
    uv run -m src.eval.cli browse /tmp/eval-profile-abc123/profile
"""
    print(help_text)


def main() -> None:
    """Main entry point - routes to appropriate command."""
    # Check for utility commands that don't use Hydra
    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == "list-models":
            list_models()
            return

        if cmd == "view":
            if len(sys.argv) < 3:
                print("Error: view requires a result file path", file=sys.stderr)
                sys.exit(1)
            view_results(sys.argv[2])
            return

        if cmd == "compare":
            if len(sys.argv) < 3:
                print("Error: compare requires at least one result file", file=sys.stderr)
                sys.exit(1)
            compare_results(sys.argv[2:])
            return

        if cmd == "list-runs":
            outputs_dir = sys.argv[2] if len(sys.argv) > 2 else "outputs"
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
            list_runs(outputs_dir, limit)
            return

        if cmd == "browse":
            if len(sys.argv) < 3:
                print("Error: browse requires a profile path", file=sys.stderr)
                print("Usage: uv run -m src.eval.cli browse <profile_path> [url]", file=sys.stderr)
                sys.exit(1)
            profile_path = sys.argv[2]
            url = sys.argv[3] if len(sys.argv) > 3 else None
            browse_profile(profile_path, url)
            return

        # Show custom help for our CLI before falling through to Hydra
        if cmd == "help":
            print_help()
            return

    # Fall through to Hydra for everything else (including --help, --cfg, overrides)
    run_eval_hydra()


if __name__ == "__main__":
    main()
