"""CLI for running browser agent evaluations.

Usage:
    uv run -m src.eval.cli run --items "apples" "milk" --llm gpt-4.1
    uv run -m src.eval.cli run --config eval_config.json
    uv run -m src.eval.cli run --items "bread" --headed --no-clear-cart
    uv run -m src.eval.cli list-models
    uv run -m src.eval.cli example-config > my_eval.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.eval.config import (
    EvalConfig,
    EvalRun,
    LLMConfig,
    LLM_PRESETS,
    BrowserConfig,
    PromptConfig,
)
from src.eval.harness import EvalHarness, cleanup_temp_profile
from src.eval.results import EvalResult

load_dotenv()


def run_eval(args) -> None:
    """Run an evaluation based on CLI arguments."""

    if args.config:
        # Load from config file
        config = EvalConfig.from_file(args.config)
        print(f"Loaded config from: {args.config}")
    else:
        # Build config from CLI args
        if not args.items:
            print("Error: --items required when not using --config", file=sys.stderr)
            sys.exit(1)

        # Build LLM config
        if args.llm in LLM_PRESETS:
            llm_config = LLM_PRESETS[args.llm].model_copy()
        else:
            llm_config = LLMConfig(model=args.llm)

        if args.temperature is not None:
            llm_config.temperature = args.temperature
        if args.vision:
            llm_config.use_vision = True

        # Build browser config
        browser_config = BrowserConfig(
            headless=not args.headed,
            use_stealth=args.stealth,
            wait_between_actions=args.wait_between_actions,
            min_wait_page_load=args.min_wait_page_load,
        )

        # Build prompt config
        prompt_config = PromptConfig(
            name=args.prompt_name or "cli",
            template_path=args.prompt_file,
        )

        # Create run
        run = EvalRun(
            name=args.name or "cli_eval",
            items=args.items,
            llm=llm_config,
            browser=browser_config,
            prompt=prompt_config,
            max_steps=args.max_steps,
            timeout_seconds=args.timeout,
        )

        config = EvalConfig(
            name=args.name or "cli_eval",
            runs=[run],
            output_dir=args.output_dir,
            source_profile_dir=args.profile_dir,
            clear_cart_before_run=not args.no_clear_cart,
        )

    # Print config summary
    print("\n" + "=" * 60)
    print("EVALUATION CONFIGURATION")
    print("=" * 60)
    print(f"Name: {config.name}")
    print(f"Runs: {len(config.runs)}")
    print(f"Output: {config.output_dir}")
    print(f"Clear cart: {config.clear_cart_before_run}")

    for i, run in enumerate(config.runs, 1):
        print(f"\nRun {i}: {run.name}")
        print(f"  Items: {run.items}")
        print(f"  LLM: {run.llm.model} ({run.llm.provider})")
        print(f"  Headless: {run.browser.headless}")
        print(f"  Max steps: {run.max_steps}")

    print("=" * 60 + "\n")

    if args.dry_run:
        print("Dry run - exiting without executing")
        return

    # Run the evaluation
    harness = EvalHarness(config)

    if len(config.runs) == 1:
        result = asyncio.run(harness.run_single(config.runs[0]))
        print("\n" + result.get_summary())

        # Clean up temp profile unless --keep-profile
        if not args.keep_profile and result.profile_dir:
            cleanup_temp_profile(Path(result.profile_dir))
            print(f"\nCleaned up temp profile")
        elif result.profile_dir:
            print(f"\nTemp profile kept at: {result.profile_dir}")
    else:
        session = asyncio.run(harness.run_all())
        print("\n" + session.get_summary())

        # Clean up temp profiles
        if not args.keep_profile:
            for result in session.results:
                if result.profile_dir:
                    cleanup_temp_profile(Path(result.profile_dir))


def list_models(args) -> None:
    """List available LLM model presets."""
    print("\nAvailable LLM Presets:")
    print("-" * 40)
    for name, config in LLM_PRESETS.items():
        print(f"  {name:20} -> {config.model} ({config.provider})")
    print("-" * 40)
    print("\nYou can also specify any model name directly with --llm")


def example_config(args) -> None:
    """Print an example configuration file."""
    example = EvalConfig(
        name="example_eval",
        runs=[
            EvalRun(
                name="gpt4_basic",
                items=["apples", "milk", "bread"],
                llm=LLMConfig(model="gpt-4.1", provider="groq"),
                browser=BrowserConfig(headless=True),
                max_steps=30,
            ),
            EvalRun(
                name="llama_basic",
                items=["apples", "milk", "bread"],
                llm=LLMConfig(model="llama-3.3-70b-versatile", provider="groq"),
                browser=BrowserConfig(headless=True),
                max_steps=30,
            ),
        ],
        output_dir="./eval_results",
        clear_cart_before_run=True,
    )
    print(json.dumps(example.model_dump(), indent=2))


def view_results(args) -> None:
    """View results from a previous evaluation run."""
    result_path = Path(args.result_file)

    if not result_path.exists():
        print(f"Error: Result file not found: {result_path}", file=sys.stderr)
        sys.exit(1)

    if result_path.name == "session.json":
        from src.eval.results import EvalSession
        with open(result_path) as f:
            data = json.load(f)
        session = EvalSession.model_validate(data)
        print(session.get_summary())
    else:
        result = EvalResult.from_file(result_path)
        print(result.get_summary())


def compare_results(args) -> None:
    """Compare results from multiple evaluation runs."""
    results = []
    for path in args.result_files:
        result = EvalResult.from_file(path)
        results.append(result)

    print("\n" + "=" * 60)
    print("COMPARISON")
    print("=" * 60)
    print(f"{'Run Name':<30} {'Status':<10} {'Success':<10} {'Duration':<10}")
    print("-" * 60)

    for result in results:
        duration = f"{result.metrics.total_duration_seconds:.1f}s" if result.metrics.total_duration_seconds else "N/A"
        print(f"{result.run_name:<30} {result.status:<10} {result.success_rate:.0%}       {duration}")

    print("=" * 60)

    # Show item-level comparison
    print("\nPer-Item Results:")
    all_items = set()
    for result in results:
        all_items.update(result.items_requested)

    for item in sorted(all_items):
        print(f"\n  {item}:")
        for result in results:
            item_result = next((r for r in result.item_results if r.item == item), None)
            if item_result:
                print(f"    {result.run_name}: {item_result.status} ({item_result.duration_seconds:.1f}s)")
            else:
                print(f"    {result.run_name}: (not tested)")


def main():
    """Main entry point for the evaluation CLI."""
    parser = argparse.ArgumentParser(
        description="Browser agent evaluation harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick test with specific items
  uv run -m src.eval.cli run --items "apples" "milk" --llm gpt-4.1

  # Run with visible browser
  uv run -m src.eval.cli run --items "bread" --headed

  # Run from config file
  uv run -m src.eval.cli run --config eval_config.json

  # Generate example config
  uv run -m src.eval.cli example-config > my_eval.json

  # List available model presets
  uv run -m src.eval.cli list-models

  # View results
  uv run -m src.eval.cli view ./eval_results/cli_eval_result.json
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run an evaluation")
    run_parser.add_argument(
        "--items",
        nargs="+",
        help="Items to add to cart (e.g., --items apples milk bread)",
    )
    run_parser.add_argument(
        "--config",
        type=str,
        help="Path to JSON config file (overrides other options)",
    )
    run_parser.add_argument(
        "--name",
        type=str,
        default="eval",
        help="Name for this evaluation run",
    )
    run_parser.add_argument(
        "--llm",
        type=str,
        default="gpt-4.1",
        help="LLM model or preset name (default: gpt-4.1)",
    )
    run_parser.add_argument(
        "--temperature",
        type=float,
        help="LLM temperature (default: 0.0)",
    )
    run_parser.add_argument(
        "--vision",
        action="store_true",
        help="Enable vision capabilities",
    )
    run_parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible window)",
    )
    run_parser.add_argument(
        "--stealth",
        action="store_true",
        help="Use stealth arguments to avoid bot detection",
    )
    run_parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum agent steps per item (default: 30)",
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Timeout in seconds per item (default: 300)",
    )
    run_parser.add_argument(
        "--wait-between-actions",
        type=float,
        default=2.0,
        help="Wait time between browser actions (default: 2.0)",
    )
    run_parser.add_argument(
        "--min-wait-page-load",
        type=float,
        default=1.5,
        help="Minimum wait for page loads (default: 1.5)",
    )
    run_parser.add_argument(
        "--no-clear-cart",
        action="store_true",
        help="Don't clear cart before running",
    )
    run_parser.add_argument(
        "--keep-profile",
        action="store_true",
        help="Keep temporary browser profile after run",
    )
    run_parser.add_argument(
        "--output-dir",
        type=str,
        default="./eval_results",
        help="Directory to save results (default: ./eval_results)",
    )
    run_parser.add_argument(
        "--profile-dir",
        type=str,
        default="./superstore-profile",
        help="Source browser profile directory (default: ./superstore-profile)",
    )
    run_parser.add_argument(
        "--prompt-file",
        type=str,
        help="Path to custom prompt template file",
    )
    run_parser.add_argument(
        "--prompt-name",
        type=str,
        help="Name for the prompt variant",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config and exit without running",
    )
    run_parser.set_defaults(func=run_eval)

    # List models command
    list_parser = subparsers.add_parser("list-models", help="List available LLM presets")
    list_parser.set_defaults(func=list_models)

    # Example config command
    example_parser = subparsers.add_parser("example-config", help="Print example config file")
    example_parser.set_defaults(func=example_config)

    # View results command
    view_parser = subparsers.add_parser("view", help="View results from a previous run")
    view_parser.add_argument("result_file", help="Path to result JSON file")
    view_parser.set_defaults(func=view_results)

    # Compare results command
    compare_parser = subparsers.add_parser("compare", help="Compare multiple evaluation results")
    compare_parser.add_argument("result_files", nargs="+", help="Paths to result JSON files")
    compare_parser.set_defaults(func=compare_results)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
