"""Main evaluation harness for browser agents.

Provides the core evaluation runner that executes configured evaluation runs,
manages temporary browser profiles, tracks timing, and verifies cart contents.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from browser_use import Agent, Browser

from src.core.config import load_config
from src.core.success import detect_success_from_history
from src.eval.cart_checker import (
    clear_cart,
    extract_cart_contents,
    match_cart_to_requested,
)
from src.eval.config import EvalConfig, EvalRun, LLMConfig
from src.eval.results import (
    CartItem,
    EvalResult,
    EvalSession,
    ItemResult,
    RunMetrics,
)


def get_llm_instance(llm_config: LLMConfig):
    """Create an LLM instance from config.

    Args:
        llm_config: LLM configuration

    Returns:
        LLM instance compatible with browser-use Agent
    """
    if llm_config.provider == "groq":
        from browser_use import ChatGroq
        return ChatGroq(
            model=llm_config.model,
            temperature=llm_config.temperature,
        )
    elif llm_config.provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=llm_config.model,
            temperature=llm_config.temperature,
        )
    elif llm_config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=llm_config.model,
            temperature=llm_config.temperature,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {llm_config.provider}")


def copy_profile_to_temp(
    source_profile: Path,
    prefix: str = "eval-profile",
) -> Path:
    """Copy browser profile to a temporary directory.

    Args:
        source_profile: Path to the source profile directory
        prefix: Prefix for the temp directory name

    Returns:
        Path to the temporary profile directory
    """
    # Chrome lock files to skip during copy
    lock_files = {
        "SingletonLock",
        "SingletonCookie",
        "SingletonSocket",
        "lockfile",
        "parent.lock",
    }

    def ignore_lock_files(directory: str, files: list[str]) -> list[str]:
        return [f for f in files if f in lock_files]

    temp_dir = tempfile.mkdtemp(prefix=f"{prefix}-")
    temp_profile = Path(temp_dir) / "profile"

    if source_profile.exists():
        shutil.copytree(
            source_profile,
            temp_profile,
            ignore=ignore_lock_files,
            dirs_exist_ok=True,
        )

    return temp_profile


def cleanup_temp_profile(profile_path: Path) -> None:
    """Clean up a temporary profile directory.

    Args:
        profile_path: Path to the temp profile
    """
    try:
        temp_dir = profile_path.parent
        if temp_dir.exists() and "eval-profile" in str(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


class EvalHarness:
    """Evaluation harness for browser agents.

    Manages the lifecycle of evaluation runs including:
    - Creating temporary browser profiles
    - Running agents with different LLM/prompt configurations
    - Tracking timing and step counts
    - Verifying cart contents after runs
    """

    def __init__(
        self,
        config: EvalConfig,
        on_progress: Callable[[str], None] | None = None,
    ):
        """Initialize the evaluation harness.

        Args:
            config: Evaluation configuration
            on_progress: Optional callback for progress updates
        """
        self.config = config
        self.on_progress = on_progress or (lambda msg: print(f"[Eval] {msg}"))
        self._app_config = load_config()

    def _log(self, message: str) -> None:
        """Log a progress message."""
        self.on_progress(message)

    def _create_browser(
        self,
        run: EvalRun,
        profile_dir: str,
    ) -> Browser:
        """Create a browser instance for an evaluation run.

        Args:
            run: Evaluation run configuration
            profile_dir: Path to the browser profile directory

        Returns:
            Configured Browser instance
        """
        browser_config = run.browser

        # Build browser arguments
        args = ["--disable-features=LockProfileCookieDatabase"]
        if browser_config.use_stealth:
            args.extend([
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ])

        return Browser(
            headless=browser_config.headless,
            window_size={
                "width": browser_config.window_width,
                "height": browser_config.window_height,
            },
            wait_between_actions=browser_config.wait_between_actions,
            minimum_wait_page_load_time=browser_config.min_wait_page_load,
            wait_for_network_idle_page_load_time=browser_config.wait_for_network_idle,
            user_data_dir=profile_dir,
            args=args,
        )

    def _get_prompt_template(self, run: EvalRun) -> str:
        """Get the prompt template for a run.

        Args:
            run: Evaluation run configuration

        Returns:
            Prompt template string
        """
        try:
            # Try to get custom template from run config
            return run.prompt.get_template(
                default_path=self._app_config.prompts.add_item
            )
        except (FileNotFoundError, ValueError):
            # Fall back to loading from app config
            return self._app_config.load_prompt("add_item", item="{item}", base_url="{base_url}")

    async def _run_single_item(
        self,
        item: str,
        run: EvalRun,
        browser: Browser,
        llm,
    ) -> ItemResult:
        """Run the agent to add a single item to cart.

        Args:
            item: Item to add
            run: Evaluation run configuration
            browser: Browser instance
            llm: LLM instance

        Returns:
            ItemResult with outcome details
        """
        start_time = time.time()
        steps_taken = 0
        status = "uncertain"
        success_evidence = None
        error_message = None

        try:
            # Get and format prompt
            template = self._get_prompt_template(run)
            task = template.format(
                item=item,
                base_url=self.config.base_url,
            )

            # Create agent
            agent = Agent(
                task=task,
                llm=llm,
                browser_session=browser,
                use_vision=run.llm.use_vision,
            )

            # Track steps via callback
            step_count = [0]

            async def on_step_end(step_result):
                step_count[0] += 1
                self._log(f"  Step {step_count[0]}: {item}")

            # Run with timeout
            try:
                await asyncio.wait_for(
                    agent.run(max_steps=run.max_steps, on_step_end=on_step_end),
                    timeout=run.timeout_seconds,
                )
            except asyncio.TimeoutError:
                status = "timeout"
                error_message = f"Timed out after {run.timeout_seconds}s"

            steps_taken = step_count[0]

            # Check success if not timed out
            if status != "timeout":
                success, evidence = detect_success_from_history(agent)
                if success:
                    status = "success"
                    success_evidence = evidence
                else:
                    status = "uncertain"

        except Exception as e:
            status = "error"
            error_message = str(e)

        duration = time.time() - start_time

        return ItemResult(
            item=item,
            status=status,
            duration_seconds=duration,
            steps_taken=steps_taken,
            success_evidence=success_evidence,
            error_message=error_message,
        )

    async def run_single(self, run: EvalRun) -> EvalResult:
        """Execute a single evaluation run.

        Args:
            run: Evaluation run configuration

        Returns:
            EvalResult with complete outcome details
        """
        self._log(f"Starting run: {run.name}")
        self._log(f"Items: {run.items}")
        self._log(f"LLM: {run.llm.get_display_name()}")

        # Initialize result
        result = EvalResult(
            run_name=run.name,
            items_requested=run.items.copy(),
            config_summary={
                "llm_model": run.llm.model,
                "llm_provider": run.llm.provider,
                "prompt_name": run.prompt.name,
                "max_steps": run.max_steps,
                "headless": run.browser.headless,
            },
            metrics=RunMetrics(start_time=datetime.now()),
        )

        # Create temp profile from source
        source_profile = Path(self.config.source_profile_dir)
        temp_profile = copy_profile_to_temp(source_profile)
        result.profile_dir = str(temp_profile)
        self._log(f"Using temp profile: {temp_profile}")

        browser = None
        try:
            # Create browser with temp profile
            browser = self._create_browser(run, str(temp_profile))

            # Get LLM instance
            llm = get_llm_instance(run.llm)

            # Optionally clear cart before starting
            if self.config.clear_cart_before_run:
                self._log("Clearing cart before run...")
                await clear_cart(
                    browser=browser,
                    cart_url=self.config.cart_url,
                    llm=llm,
                    use_vision=run.llm.use_vision,
                )

            # Run agent for each item
            for i, item in enumerate(run.items, 1):
                self._log(f"Adding item {i}/{len(run.items)}: {item}")

                item_result = await self._run_single_item(
                    item=item,
                    run=run,
                    browser=browser,
                    llm=llm,
                )

                result.item_results.append(item_result)
                result.metrics.item_durations[item] = item_result.duration_seconds
                result.metrics.steps_per_item[item] = item_result.steps_taken

                status_icon = "[+]" if item_result.status == "success" else "[-]"
                self._log(f"  {status_icon} {item}: {item_result.status} ({item_result.duration_seconds:.1f}s)")

            # Verify cart contents
            self._log("Verifying cart contents...")
            cart_items, raw_content, cart_duration = await extract_cart_contents(
                browser=browser,
                cart_url=self.config.cart_url,
                llm=llm,
                use_vision=run.llm.use_vision,
            )

            result.cart_items = cart_items
            result.cart_raw_content = raw_content
            result.cart_verified = True
            result.metrics.cart_check_duration_seconds = cart_duration

            # Match cart items to requested items
            matches = match_cart_to_requested(cart_items, run.items)
            for item_result in result.item_results:
                matched = matches.get(item_result.item)
                if matched:
                    item_result.matched_cart_item = matched
                    # Upgrade uncertain to success if found in cart
                    if item_result.status == "uncertain":
                        item_result.status = "success"
                        item_result.success_evidence = f"Found in cart: {matched.name}"
                elif item_result.status == "success":
                    # Downgrade to uncertain if not found in cart
                    item_result.status = "uncertain"
                    item_result.success_evidence = None

            self._log(f"Cart contains {len(cart_items)} items")

        except Exception as e:
            result.error = str(e)
            self._log(f"Run error: {e}")

        finally:
            # Clean up browser
            if browser:
                try:
                    await browser.kill()
                except Exception:
                    pass

            # Note: We don't clean up the temp profile here so it can be inspected
            # The CLI will clean it up after the run if needed

        # Finalize metrics and calculate success rate
        result.metrics.finalize()
        result.calculate_success_rate()

        self._log(f"Run complete: {result.status} ({result.success_rate:.0%})")
        return result

    async def run_all(self) -> EvalSession:
        """Execute all configured evaluation runs.

        Returns:
            EvalSession with results from all runs
        """
        session = EvalSession(
            name=self.config.name,
            start_time=datetime.now(),
        )

        for i, run in enumerate(self.config.runs, 1):
            self._log(f"=== Run {i}/{len(self.config.runs)}: {run.name} ===")
            result = await self.run_single(run)
            session.add_result(result)

            # Save intermediate result
            output_dir = Path(self.config.output_dir)
            result.to_file(output_dir / f"{run.name}_result.json")

        session.finalize()

        # Save session summary
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        session.to_file(output_dir / "session.json")

        self._log("=== Session Complete ===")
        self._log(session.get_summary())

        return session


async def run_quick_eval(
    items: list[str],
    llm_model: str = "openai/gpt-oss-120b",
    headless: bool = True,
    clear_cart: bool = True,
) -> EvalResult:
    """Run a quick evaluation with minimal configuration.

    Args:
        items: List of items to add to cart
        llm_model: LLM model to use
        headless: Run browser in headless mode
        clear_cart: Clear cart before running

    Returns:
        EvalResult with outcome details
    """
    from src.eval.config import BrowserConfig

    config = EvalConfig.quick(items=items, llm_model=llm_model)
    config.clear_cart_before_run = clear_cart

    # Update browser config
    config.runs[0].browser = BrowserConfig(headless=headless)

    harness = EvalHarness(config)
    return await harness.run_single(config.runs[0])
