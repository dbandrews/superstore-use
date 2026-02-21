"""Evaluation configuration models.

Defines the configuration schema for evaluation runs, including
LLM settings, prompt templates, and timing parameters.

Note: Configuration is now managed via Hydra (see conf/ directory).
These Pydantic models are still used internally by EvalHarness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM configuration for an evaluation run."""

    model: str = Field(
        default="openai/gpt-oss-120b",
        description="Model identifier (e.g., 'gpt-4.1', 'openai/gpt-oss-120b', 'llama-3.3-70b-versatile')",
    )
    provider: Literal["groq", "openai", "anthropic", "openrouter"] = Field(
        default="groq",
        description="LLM provider to use",
    )
    temperature: float = Field(
        default=0.0,
        description="Sampling temperature (0.0 for deterministic)",
    )
    use_vision: bool = Field(
        default=False,
        description="Whether to enable vision capabilities",
    )
    base_url: str | None = Field(
        default=None,
        description="Custom base URL for API (e.g., OpenRouter). If None, uses provider default.",
    )
    api_key_env: str | None = Field(
        default=None,
        description="Environment variable name for API key (e.g., 'OPENROUTER_API_KEY'). If None, uses provider default.",
    )

    def get_display_name(self) -> str:
        """Get a display-friendly name for this LLM config."""
        return f"{self.provider}/{self.model}"


class JudgeConfig(BaseModel):
    """LLM-as-a-judge configuration for evaluating cart contents."""

    model: str = Field(
        default="gpt-4o",
        description="Model identifier for the judge LLM",
    )
    provider: Literal["groq", "openai", "anthropic", "openrouter"] = Field(
        default="openai",
        description="LLM provider for the judge",
    )
    temperature: float = Field(
        default=0.0,
        description="Sampling temperature (0.0 for deterministic judging)",
    )
    prompt_template: str | None = Field(
        default=None,
        description="Path to custom judge prompt template. If None, uses default prompt.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether to run LLM judge evaluation",
    )
    base_url: str | None = Field(
        default=None,
        description="Custom base URL for API (e.g., OpenRouter). If None, uses provider default.",
    )
    api_key_env: str | None = Field(
        default=None,
        description="Environment variable name for API key (e.g., 'OPENROUTER_API_KEY'). If None, uses provider default.",
    )

    def get_display_name(self) -> str:
        """Get a display-friendly name for this judge config."""
        return f"{self.provider}/{self.model}"

    def get_prompt_template(self) -> str | None:
        """Load custom prompt template if specified.

        Returns:
            Template content string or None if using default
        """
        if not self.prompt_template:
            return None

        path = Path(self.prompt_template)
        if path.exists():
            return path.read_text()

        # Try relative to project root
        for base in [Path.cwd(), Path(__file__).parent.parent.parent]:
            full_path = base / self.prompt_template
            if full_path.exists():
                return full_path.read_text()

        raise FileNotFoundError(f"Judge prompt template not found: {self.prompt_template}")


class PromptConfig(BaseModel):
    """Prompt configuration for an evaluation run."""

    template_path: str | None = Field(
        default=None,
        description="Path to custom prompt template file. If None, uses default from config.toml",
    )
    template_content: str | None = Field(
        default=None,
        description="Inline prompt template content. Overrides template_path if set.",
    )
    name: str = Field(
        default="default",
        description="Name identifier for this prompt variant",
    )

    def get_template(self, default_path: str | None = None) -> str:
        """Get the prompt template content.

        Args:
            default_path: Default template path if no custom template specified

        Returns:
            Template content string

        Raises:
            FileNotFoundError: If template file not found
        """
        if self.template_content:
            return self.template_content

        template_path = self.template_path or default_path
        if template_path:
            path = Path(template_path)
            if path.exists():
                return path.read_text()
            # Try relative to project root
            for base in [Path.cwd(), Path(__file__).parent.parent.parent]:
                full_path = base / template_path
                if full_path.exists():
                    return full_path.read_text()
            raise FileNotFoundError(f"Prompt template not found: {template_path}")

        raise ValueError("No template path or content specified")


class BrowserConfig(BaseModel):
    """Browser configuration for an evaluation run."""

    headless: bool = Field(
        default=True,
        description="Run browser in headless mode",
    )
    use_stealth: bool = Field(
        default=False,
        description="Use stealth arguments to avoid bot detection",
    )
    wait_between_actions: float = Field(
        default=2.0,
        description="Seconds to wait between browser actions",
    )
    min_wait_page_load: float = Field(
        default=1.5,
        description="Minimum seconds to wait for page loads",
    )
    wait_for_network_idle: float = Field(
        default=1.5,
        description="Seconds to wait for network idle",
    )
    window_width: int = Field(default=1920, description="Browser window width")
    window_height: int = Field(default=1080, description="Browser window height")
    api_key: str | None = Field(
        default=None,
        description="API key for cart extraction (defaults to known static key)",
    )

    def get_display_name(self) -> str:
        """Get a display-friendly name for this browser config."""
        mode = "headed" if not self.headless else "headless"
        return mode


class EvalRun(BaseModel):
    """Configuration for a single evaluation run."""

    name: str = Field(
        description="Unique name for this evaluation run",
    )
    items: list[str] = Field(
        description="List of items to add to cart (e.g., ['apples', '2 liters milk'])",
    )
    llm: LLMConfig = Field(
        default_factory=LLMConfig,
        description="LLM configuration",
    )
    prompt: PromptConfig = Field(
        default_factory=PromptConfig,
        description="Prompt configuration",
    )
    browser: BrowserConfig = Field(
        default_factory=BrowserConfig,
        description="Browser configuration",
    )
    judge: JudgeConfig = Field(
        default_factory=JudgeConfig,
        description="LLM-as-a-judge configuration for cart verification",
    )
    max_steps: int = Field(
        default=30,
        description="Maximum agent steps per item",
    )
    timeout_seconds: float = Field(
        default=300.0,
        description="Overall timeout for the run in seconds",
    )
    max_retries: int = Field(
        default=2,
        description="Number of retry attempts per item on CDP/browser errors (0 = no retries)",
    )
    retry_delay: float = Field(
        default=3.0,
        description="Seconds to wait between retry attempts",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Optional tags for filtering/grouping results",
    )


class EvalConfig(BaseModel):
    """Root configuration for an evaluation session."""

    name: str = Field(
        default="eval",
        description="Name for this evaluation session",
    )
    runs: list[EvalRun] = Field(
        default_factory=list,
        description="List of evaluation runs to execute",
    )
    base_url: str = Field(
        default="https://www.realcanadiansuperstore.ca/en",
        description="Base URL for the grocery store",
    )
    cart_url: str = Field(
        default="https://www.realcanadiansuperstore.ca/en/cartReview",
        description="URL for the shopping cart page",
    )
    output_dir: str = Field(
        default="./eval_results",
        description="Directory to save evaluation results",
    )
    parallel: bool = Field(
        default=False,
        description="Run multiple items in parallel (not recommended for eval)",
    )

    @classmethod
    def quick(
        cls,
        items: list[str],
        llm_model: str = "openai/gpt-oss-120b",
        name: str = "quick_eval",
    ) -> EvalConfig:
        """Create a quick evaluation config for testing.

        Args:
            items: List of items to add to cart
            llm_model: LLM model to use
            name: Name for the run

        Returns:
            EvalConfig with a single run
        """
        return cls(
            name=name,
            runs=[
                EvalRun(
                    name=name,
                    items=items,
                    llm=LLMConfig(model=llm_model),
                )
            ],
        )
