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
    extract_cart_contents,
    judge_cart_contents,
    match_cart_to_requested,
)
from src.eval.config import EvalConfig, EvalRun, JudgeConfig, LLMConfig
from src.eval.results import (
    CartItem,
    CostMetrics,
    EvalResult,
    EvalSession,
    ItemResult,
    RunMetrics,
    TokenUsage,
)


def get_llm_instance(llm_config: LLMConfig):
    """Create an LLM instance from config.

    Args:
        llm_config: LLM configuration

    Returns:
        LLM instance compatible with browser-use Agent
    """
    import os

    # Use browser-use's model wrappers for compatibility
    if llm_config.provider == "groq":
        from browser_use import ChatGroq
        return ChatGroq(
            model=llm_config.model,
            temperature=llm_config.temperature,
        )
    elif llm_config.provider == "openai":
        from browser_use import ChatOpenAI
        return ChatOpenAI(
            model=llm_config.model,
            temperature=llm_config.temperature,
        )
    elif llm_config.provider == "anthropic":
        from browser_use import ChatAnthropic
        return ChatAnthropic(
            model=llm_config.model,
            temperature=llm_config.temperature,
        )
    elif llm_config.provider == "openrouter":
        # OpenRouter uses OpenAI-compatible API with custom base URL
        # Note: Free models (with :free suffix) don't work with browser-use because
        # free tier providers don't support structured output (response_format)
        # which browser-use requires. Use paid tier models instead.
        from browser_use import ChatOpenAI
        api_key_env = llm_config.api_key_env or "OPENROUTER_API_KEY"
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ValueError(f"OpenRouter API key not found in environment variable: {api_key_env}")
        base_url = llm_config.base_url or "https://openrouter.ai/api/v1"
        return ChatOpenAI(
            model=llm_config.model,
            base_url=base_url,
            api_key=api_key,
            temperature=llm_config.temperature,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {llm_config.provider}")


def create_temp_profile(prefix: str = "eval-profile") -> Path:
    """Create a blank temporary browser profile directory.

    Each eval run gets a fresh, isolated browser profile with no
    pre-existing state. Items can be added to cart without authentication.

    Args:
        prefix: Prefix for the temp directory name

    Returns:
        Path to the temporary profile directory
    """
    temp_dir = tempfile.mkdtemp(prefix=f"{prefix}-")
    temp_profile = Path(temp_dir) / "profile"
    temp_profile.mkdir(parents=True, exist_ok=True)
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
        token_usage = TokenUsage()
        estimated_cost_usd = None

        try:
            # Get and format prompt
            template = self._get_prompt_template(run)
            task = template.format(
                item=item,
                base_url=self.config.base_url,
            )

            # Create agent with cost tracking enabled
            agent = Agent(
                task=task,
                llm=llm,
                browser_session=browser,
                use_vision=run.llm.use_vision,
                calculate_cost=True,
            )

            # Track steps via callback
            step_count = [0]

            async def on_step_end(step_result):
                step_count[0] += 1
                self._log(f"  Step {step_count[0]}: {item}")

            # Run with timeout
            history = None
            try:
                history = await asyncio.wait_for(
                    agent.run(max_steps=run.max_steps, on_step_end=on_step_end),
                    timeout=run.timeout_seconds,
                )
            except asyncio.TimeoutError:
                status = "timeout"
                error_message = f"Timed out after {run.timeout_seconds}s"

            steps_taken = step_count[0]

            # Extract token usage from history using the comprehensive TokenUsage model
            if history and hasattr(history, "usage") and history.usage:
                token_usage = TokenUsage.from_usage_summary(history.usage)
                if token_usage.total_cost > 0:
                    estimated_cost_usd = token_usage.total_cost

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
            token_usage=token_usage,
            estimated_cost_usd=estimated_cost_usd,
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
                "judge_model": run.judge.model,
                "judge_provider": run.judge.provider,
                "judge_enabled": run.judge.enabled,
            },
            metrics=RunMetrics(start_time=datetime.now()),
        )

        # Create blank temp profile (no authentication needed for cart)
        temp_profile = create_temp_profile()
        result.profile_dir = str(temp_profile)
        self._log(f"Using temp profile: {temp_profile}")

        try:
            # Get LLM instance
            llm = get_llm_instance(run.llm)

            # Run agent for each item (new browser per item for isolation)
            for i, item in enumerate(run.items, 1):
                self._log(f"Adding item {i}/{len(run.items)}: {item}")

                # Create fresh browser for this item
                item_browser = self._create_browser(run, str(temp_profile))
                try:
                    item_result = await self._run_single_item(
                        item=item,
                        run=run,
                        browser=item_browser,
                        llm=llm,
                    )
                finally:
                    # Wait for profile to sync before killing browser
                    await asyncio.sleep(2)
                    try:
                        await item_browser.kill()
                    except Exception:
                        pass
                    # Additional delay for disk sync
                    await asyncio.sleep(1)

                result.item_results.append(item_result)
                result.metrics.item_durations[item] = item_result.duration_seconds
                result.metrics.steps_per_item[item] = item_result.steps_taken

                # Track cost metrics per item
                result.cost_metrics.tokens_per_item[item] = item_result.token_usage
                result.cost_metrics.token_usage = result.cost_metrics.token_usage + item_result.token_usage
                if item_result.estimated_cost_usd is not None:
                    result.cost_metrics.cost_per_item[item] = item_result.estimated_cost_usd
                    if result.cost_metrics.estimated_cost_usd is None:
                        result.cost_metrics.estimated_cost_usd = 0.0
                    result.cost_metrics.estimated_cost_usd += item_result.estimated_cost_usd

                status_icon = "[+]" if item_result.status == "success" else "[-]"
                tokens_str = f", {item_result.token_usage.total_tokens} tokens" if item_result.token_usage.total_tokens > 0 else ""
                cost_str = f", ${item_result.estimated_cost_usd:.4f}" if item_result.estimated_cost_usd else ""
                self._log(f"  {status_icon} {item}: {item_result.status} ({item_result.duration_seconds:.1f}s{tokens_str}{cost_str})")

            # Verify cart contents via API using Playwright directly
            self._log("Extracting cart contents via API...")
            # Wait for profile data to fully sync to disk
            await asyncio.sleep(2)

            cart_extraction_error: str | None = None
            try:
                cart_items, raw_content, cart_duration = await extract_cart_contents(
                    profile_path=str(temp_profile),
                    cart_url=self.config.cart_url,
                    api_key=run.browser.api_key,
                    headless=run.browser.headless,
                )

                result.cart_items = cart_items
                result.cart_raw_content = raw_content
                result.cart_verified = True
                result.metrics.cart_check_duration_seconds = cart_duration

                self._log(f"Cart contains {len(cart_items)} items")
            except Exception as e:
                cart_extraction_error = str(e)
                result.cart_verified = False
                result.cart_extraction_error = cart_extraction_error
                self._log(f"Cart extraction error: {e}")
                # Mark all items that were "success" as "uncertain" since we can't verify
                for item_result in result.item_results:
                    if item_result.status == "success":
                        item_result.status = "uncertain"
                        item_result.error_message = f"Cart extraction failed: {cart_extraction_error}"

            # Use LLM judge to evaluate cart contents (only if cart extraction succeeded)
            if cart_extraction_error is None and run.judge.enabled:
                self._log(f"Judging cart contents with LLM ({run.judge.get_display_name()})...")
                judge_llm_config = {
                    "provider": run.judge.provider,
                    "model": run.judge.model,
                    "temperature": run.judge.temperature,
                    "base_url": getattr(run.judge, "base_url", None),
                    "api_key_env": getattr(run.judge, "api_key_env", None),
                }

                # Get custom prompt template if configured
                custom_prompt = run.judge.get_prompt_template()

                judgment = await judge_cart_contents(
                    requested_items=run.items,
                    cart_items=result.cart_items,
                    llm_config=judge_llm_config,
                    custom_prompt=custom_prompt,
                )

                self._log(f"LLM Judge: {judgment.summary}")

                # Check if judge encountered an error (empty item_judgments with error summary)
                if not judgment.item_judgments and "error" in judgment.summary.lower():
                    result.judge_error = judgment.summary
                    self._log(f"LLM Judge error: {judgment.summary}")
                    # Mark all items that were "success" as "uncertain" since we can't verify
                    for item_result in result.item_results:
                        if item_result.status == "success":
                            item_result.status = "uncertain"
                            item_result.error_message = f"Judge error: {judgment.summary}"
                else:
                    # Update item results based on LLM judgment
                    for item_judgment in judgment.item_judgments:
                        # Find the matching item result
                        for item_result in result.item_results:
                            if item_result.item == item_judgment.requested_item:
                                if item_judgment.found and item_judgment.correct_quantity:
                                    item_result.status = "success"
                                    item_result.success_evidence = f"Found: {item_judgment.matched_cart_item} (qty: {item_judgment.matched_quantity}) - {item_judgment.reasoning}"
                                    # Create matched cart item
                                    if item_judgment.matched_cart_item:
                                        item_result.matched_cart_item = CartItem(
                                            name=item_judgment.matched_cart_item,
                                            quantity=item_judgment.matched_quantity or 1,
                                        )
                                elif item_judgment.found:
                                    # Found but wrong quantity
                                    item_result.status = "failed"
                                    item_result.success_evidence = None
                                    item_result.error_message = f"Mismatch: qty: wanted {item_judgment.requested_quantity}, got {item_judgment.matched_quantity} - {item_judgment.reasoning}"
                                else:
                                    # Not found
                                    item_result.status = "failed"
                                    item_result.success_evidence = None
                                    item_result.error_message = f"Not found in cart - {item_judgment.reasoning}"
                                break
            elif cart_extraction_error is None and not run.judge.enabled:
                self._log("LLM judge disabled, skipping judgment")
            # If cart_extraction_error is set, items were already marked as uncertain above

        except Exception as e:
            result.error = str(e)
            self._log(f"Run error: {e}")
            # Mark all items that were "success" as "uncertain" since we hit an unexpected error
            for item_result in result.item_results:
                if item_result.status == "success":
                    item_result.status = "uncertain"
                    item_result.error_message = f"Run error: {str(e)}"

        # Finalize metrics and calculate success rate
        result.metrics.finalize()
        result.calculate_success_rate()

        # Log final summary including token usage and cost
        tokens_summary = ""
        if result.cost_metrics.token_usage.total_tokens > 0:
            tokens_summary = f", {result.cost_metrics.token_usage.total_tokens:,} tokens"
        cost_summary = ""
        if result.cost_metrics.estimated_cost_usd is not None:
            cost_summary = f", ${result.cost_metrics.estimated_cost_usd:.4f}"
        self._log(f"Run complete: {result.status} ({result.success_rate:.0%}{tokens_summary}{cost_summary})")
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
) -> EvalResult:
    """Run a quick evaluation with minimal configuration.

    Args:
        items: List of items to add to cart
        llm_model: LLM model to use
        headless: Run browser in headless mode

    Returns:
        EvalResult with outcome details
    """
    from src.eval.config import BrowserConfig

    config = EvalConfig.quick(items=items, llm_model=llm_model)

    # Update browser config
    config.runs[0].browser = BrowserConfig(headless=headless)

    harness = EvalHarness(config)
    return await harness.run_single(config.runs[0])
