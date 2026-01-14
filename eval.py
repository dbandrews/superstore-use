"""
Model Evaluation Script for Superstore Shopping Agent.

Tests different LLM models on a grocery shopping task and verifies
if items are successfully added to the cart.

Usage:
    # Run with default config (OpenAI gpt-4.1)
    uv run python eval.py

    # Run with Anthropic Claude
    uv run python eval.py model=anthropic

    # Run with OpenRouter
    uv run python eval.py model=openrouter

    # Custom grocery list
    uv run python eval.py groceries='["milk", "bread", "eggs"]'

    # Run multiple models (creates separate runs)
    uv run python eval.py --multirun model=openai,anthropic,openrouter
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import hydra
from dotenv import load_dotenv
from omegaconf import DictConfig, OmegaConf

load_dotenv()


@dataclass
class EvalResult:
    """Result of evaluating a single grocery item."""

    item: str
    success: bool
    status: str  # "success", "failed", "uncertain"
    message: str = ""
    steps: int = 0
    duration_seconds: float = 0


@dataclass
class EvalRun:
    """Complete evaluation run results."""

    model_provider: str
    model_name: str
    timestamp: str
    groceries: list[str]
    results: list[EvalResult] = field(default_factory=list)
    cart_verification: dict = field(default_factory=dict)
    total_duration_seconds: float = 0
    success_rate: float = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "model": f"{self.model_provider}/{self.model_name}",
            "timestamp": self.timestamp,
            "groceries": self.groceries,
            "results": [
                {
                    "item": r.item,
                    "success": r.success,
                    "status": r.status,
                    "message": r.message,
                    "steps": r.steps,
                    "duration_seconds": r.duration_seconds,
                }
                for r in self.results
            ],
            "cart_verification": self.cart_verification,
            "summary": {
                "total_items": len(self.groceries),
                "successful": sum(1 for r in self.results if r.success),
                "failed": sum(1 for r in self.results if not r.success),
                "success_rate": self.success_rate,
                "total_duration_seconds": self.total_duration_seconds,
            },
        }


# Chrome lock files that should be removed when copying profiles
CHROME_LOCK_FILES = [
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "lockfile",
    "parent.lock",
]


def _ignore_chrome_lock_files(directory: str, files: list[str]) -> list[str]:
    """Ignore function for shutil.copytree to skip Chrome lock files."""
    return [f for f in files if f in CHROME_LOCK_FILES]


def copy_profile_to_temp(source_profile: Path, prefix: str = "eval-worker") -> Path:
    """Copy browser profile to a temp directory, skipping Chrome lock files."""
    temp_dir = tempfile.mkdtemp(prefix=f"{prefix}-")
    temp_profile = Path(temp_dir) / "profile"

    if source_profile.exists():
        shutil.copytree(
            source_profile,
            temp_profile,
            ignore=_ignore_chrome_lock_files,
            dirs_exist_ok=True,
        )

    return temp_profile


async def add_item_with_agent(
    item: str,
    llm,
    browser,
    max_steps: int = 50,
) -> EvalResult:
    """
    Add a single item to cart using browser-use agent.

    Args:
        item: Grocery item to add
        llm: LLM instance to use
        browser: Browser instance
        max_steps: Maximum steps for agent

    Returns:
        EvalResult with success/failure info
    """
    from browser_use import Agent

    from core.success import detect_success_from_history

    start_time = time.time()

    try:
        agent = Agent(
            task=f"""
            Add "{item}" to the shopping cart on Real Canadian Superstore.
            Go to https://www.realcanadiansuperstore.ca/en

            UNDERSTANDING THE ITEM REQUEST:
            The item "{item}" may include a quantity (e.g., "6 apples", "2 liters milk").
            - Extract the product name to search for
            - Note the quantity requested

            Steps:
            1. Search for the PRODUCT NAME (not the full quantity string)
            2. Select the most relevant item that matches
            3. If a specific quantity is requested, adjust before adding
            4. Click "Add to Cart" and wait for confirmation

            Complete when the item is added to cart.
            """,
            llm=llm,
            browser_session=browser,
        )

        await agent.run(max_steps=max_steps)

        duration = time.time() - start_time
        success, evidence = detect_success_from_history(agent)

        if success:
            return EvalResult(
                item=item,
                success=True,
                status="success",
                message=evidence or "Item added successfully",
                steps=len(agent.history.model_outputs()) if agent.history else 0,
                duration_seconds=duration,
            )
        else:
            return EvalResult(
                item=item,
                success=False,
                status="uncertain",
                message="Could not confirm item was added",
                steps=len(agent.history.model_outputs()) if agent.history else 0,
                duration_seconds=duration,
            )

    except Exception as e:
        duration = time.time() - start_time
        return EvalResult(
            item=item,
            success=False,
            status="failed",
            message=str(e),
            duration_seconds=duration,
        )


async def verify_cart_contents(
    expected_items: list[str],
    llm,
    browser,
) -> dict:
    """
    Navigate to cart and verify items are present.

    Args:
        expected_items: List of items that should be in cart
        llm: LLM instance
        browser: Browser instance

    Returns:
        Dict with verification results
    """
    from browser_use import Agent

    try:
        agent = Agent(
            task=f"""
            Navigate to the shopping cart at Real Canadian Superstore and verify its contents.

            Expected items: {', '.join(expected_items)}

            Steps:
            1. Go to https://www.realcanadiansuperstore.ca/en
            2. Click on the cart icon at the top right
            3. Read all items currently in the cart
            4. Report which expected items are present and which are missing

            Extract the exact names of all items in the cart.
            """,
            llm=llm,
            browser_session=browser,
        )

        await agent.run(max_steps=30)

        # Extract cart contents from agent history
        extracted = agent.history.extracted_content() if agent.history else []
        cart_contents = " ".join(str(c) for c in extracted).lower()

        found_items = []
        missing_items = []

        for item in expected_items:
            # Simple substring match - could be improved with fuzzy matching
            item_keywords = item.lower().split()
            if any(kw in cart_contents for kw in item_keywords):
                found_items.append(item)
            else:
                missing_items.append(item)

        return {
            "verified": True,
            "found_items": found_items,
            "missing_items": missing_items,
            "found_count": len(found_items),
            "total_expected": len(expected_items),
            "verification_rate": len(found_items) / len(expected_items)
            if expected_items
            else 0,
        }

    except Exception as e:
        return {
            "verified": False,
            "error": str(e),
            "found_items": [],
            "missing_items": expected_items,
        }


async def run_eval(cfg: DictConfig) -> EvalRun:
    """
    Run evaluation with the given configuration.

    Args:
        cfg: Hydra configuration

    Returns:
        EvalRun with complete results
    """
    from core.browser import create_browser, get_profile_dir
    from core.llm import create_llm_from_config, get_model_info

    # Get model info
    model_info = get_model_info(cfg.model)
    print(f"\n{'='*60}")
    print(f"EVAL: {model_info['display_name']}")
    print(f"{'='*60}")

    # Parse groceries from config
    groceries = list(cfg.get("groceries", ["milk", "bread", "eggs"]))
    print(f"Groceries to add: {groceries}")

    # Create eval run
    run = EvalRun(
        model_provider=model_info["provider"],
        model_name=model_info["name"],
        timestamp=datetime.now().isoformat(),
        groceries=groceries,
    )

    start_time = time.time()

    # Create LLM
    try:
        llm = create_llm_from_config(cfg.model)
        print(f"LLM created: {model_info['display_name']}")
    except Exception as e:
        print(f"ERROR: Failed to create LLM: {e}")
        run.results.append(
            EvalResult(
                item="[LLM Creation]",
                success=False,
                status="failed",
                message=str(e),
            )
        )
        return run

    # Get profile directory
    base_profile, _ = get_profile_dir()
    base_profile = Path(base_profile)

    if not base_profile.exists():
        print(f"WARNING: Browser profile not found at {base_profile}")
        print("Run 'uv run -m local.cli login' first to create a session.")

    # Process each item
    for i, item in enumerate(groceries, 1):
        print(f"\n[{i}/{len(groceries)}] Adding: {item}")

        # Create temp profile copy for this item
        temp_profile = copy_profile_to_temp(base_profile, prefix=f"eval-{i}")

        browser = create_browser(
            user_data_dir=str(temp_profile),
            headless=cfg.browser.get("headless", True),
            use_stealth=True,
            fast_mode=cfg.browser.get("fast_mode", False),
        )

        try:
            result = await add_item_with_agent(
                item=item,
                llm=llm,
                browser=browser,
                max_steps=cfg.browser.get("max_steps", 50),
            )
            run.results.append(result)

            status_icon = "✓" if result.success else "✗"
            print(f"  {status_icon} {result.status}: {result.message[:50]}")
            print(f"    Steps: {result.steps}, Duration: {result.duration_seconds:.1f}s")

        finally:
            await browser.kill()
            # Clean up temp profile
            shutil.rmtree(temp_profile.parent, ignore_errors=True)

    # Verify cart contents
    print(f"\n{'='*40}")
    print("Verifying cart contents...")

    temp_profile = copy_profile_to_temp(base_profile, prefix="eval-verify")
    browser = create_browser(
        user_data_dir=str(temp_profile),
        headless=cfg.browser.get("headless", True),
        use_stealth=True,
    )

    try:
        successful_items = [r.item for r in run.results if r.success]
        if successful_items:
            run.cart_verification = await verify_cart_contents(
                expected_items=successful_items,
                llm=llm,
                browser=browser,
            )
            print(f"Cart verification: {run.cart_verification}")
        else:
            run.cart_verification = {
                "verified": False,
                "error": "No items were successfully added",
            }
    finally:
        await browser.kill()
        shutil.rmtree(temp_profile.parent, ignore_errors=True)

    # Calculate summary
    run.total_duration_seconds = time.time() - start_time
    run.success_rate = (
        sum(1 for r in run.results if r.success) / len(run.results)
        if run.results
        else 0
    )

    return run


def save_results(run: EvalRun, output_dir: str) -> str:
    """Save evaluation results to JSON file."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Create filename with model and timestamp
    safe_model = f"{run.model_provider}_{run.model_name}".replace("/", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"eval_{safe_model}_{timestamp}.json"

    filepath = output_path / filename

    with open(filepath, "w") as f:
        json.dump(run.to_dict(), f, indent=2)

    return str(filepath)


def print_summary(run: EvalRun):
    """Print evaluation summary to console."""
    print(f"\n{'='*60}")
    print(f"EVAL SUMMARY: {run.model_provider}/{run.model_name}")
    print(f"{'='*60}")
    print(f"Timestamp: {run.timestamp}")
    print(f"Total Duration: {run.total_duration_seconds:.1f}s")
    print(f"\nItems Tested: {len(run.groceries)}")

    success_count = sum(1 for r in run.results if r.success)
    print(f"Successful: {success_count}/{len(run.results)} ({run.success_rate*100:.1f}%)")

    print(f"\nResults by Item:")
    for r in run.results:
        icon = "✓" if r.success else "✗"
        print(f"  {icon} {r.item}: {r.status} ({r.steps} steps, {r.duration_seconds:.1f}s)")

    if run.cart_verification.get("verified"):
        print(f"\nCart Verification:")
        print(f"  Found: {run.cart_verification['found_count']}/{run.cart_verification['total_expected']}")
        if run.cart_verification.get("missing_items"):
            print(f"  Missing: {', '.join(run.cart_verification['missing_items'])}")
    else:
        print(f"\nCart Verification: Failed")
        if run.cart_verification.get("error"):
            print(f"  Error: {run.cart_verification['error']}")

    print(f"{'='*60}\n")


@hydra.main(version_base=None, config_path="conf", config_name="config")
def main(cfg: DictConfig):
    """Main entry point for evaluation."""
    print(f"Configuration:\n{OmegaConf.to_yaml(cfg)}")

    # Run evaluation
    run = asyncio.run(run_eval(cfg))

    # Print summary
    print_summary(run)

    # Save results
    output_dir = cfg.eval.get("output_dir", "./eval_results")
    filepath = save_results(run, output_dir)
    print(f"Results saved to: {filepath}")

    return run.success_rate


if __name__ == "__main__":
    main()
