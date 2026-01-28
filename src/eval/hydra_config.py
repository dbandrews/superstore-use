"""Hydra configuration integration for the evaluation harness.

This module provides:
- Dataclass-based structured configs for Hydra
- Registration of config schemas with Hydra's ConfigStore
- Conversion function from Hydra DictConfig to Pydantic models

Usage:
    The configs are automatically registered when this module is imported.
    Use convert_to_pydantic() to convert a resolved Hydra config to the
    existing Pydantic EvalConfig model for use with EvalHarness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hydra.core.config_store import ConfigStore
from omegaconf import DictConfig, OmegaConf


@dataclass
class LLMConfig:
    """LLM configuration for an evaluation run."""

    model: str = "gpt-4.1"
    provider: str = "groq"  # groq, openai, anthropic
    temperature: float = 0.0
    use_vision: bool = False


@dataclass
class BrowserConfig:
    """Browser configuration for an evaluation run."""

    headless: bool = True
    use_stealth: bool = False
    wait_between_actions: float = 2.0
    min_wait_page_load: float = 1.5
    wait_for_network_idle: float = 1.5
    window_width: int = 1920
    window_height: int = 1080
    use_deterministic_extraction: bool = True


@dataclass
class PromptConfig:
    """Prompt configuration for an evaluation run."""

    name: str = "default"
    template_path: str | None = None
    template_content: str | None = None


@dataclass
class JudgeConfig:
    """LLM-as-a-judge configuration for evaluating cart contents."""

    model: str = "gpt-4o"
    provider: str = "openai"  # groq, openai, anthropic
    temperature: float = 0.0
    prompt_template: str | None = None
    enabled: bool = True


@dataclass
class EvalConfig:
    """Root configuration for an evaluation session."""

    name: str = "eval"
    items: list[str] = field(default_factory=lambda: ["apples"])

    # Nested config groups
    llm: LLMConfig = field(default_factory=LLMConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)

    # Run parameters
    max_steps: int = 30
    timeout_seconds: float = 300.0

    # URLs
    base_url: str = "https://www.realcanadiansuperstore.ca/en"
    cart_url: str = "https://www.realcanadiansuperstore.ca/en/cartReview"

    # Paths
    output_dir: str = "./eval_results"



def register_configs() -> None:
    """Register all structured configs with Hydra's ConfigStore.

    This should be called before Hydra's main decorator runs.
    """
    cs = ConfigStore.instance()

    # Register the main config schema
    cs.store(name="config_schema", node=EvalConfig)

    # Register config group schemas
    cs.store(group="llm", name="base_llm", node=LLMConfig)
    cs.store(group="browser", name="base_browser", node=BrowserConfig)
    cs.store(group="prompt", name="base_prompt", node=PromptConfig)
    cs.store(group="judge", name="base_judge", node=JudgeConfig)


def convert_to_pydantic(cfg: DictConfig) -> Any:
    """Convert a Hydra DictConfig to the Pydantic EvalConfig model.

    This bridges the Hydra configuration system with the existing
    Pydantic-based EvalConfig/EvalRun models used by EvalHarness.

    Args:
        cfg: Resolved Hydra configuration

    Returns:
        Pydantic EvalConfig instance ready for use with EvalHarness
    """
    from src.eval.config import (
        BrowserConfig as PydanticBrowserConfig,
        EvalConfig as PydanticEvalConfig,
        EvalRun as PydanticEvalRun,
        JudgeConfig as PydanticJudgeConfig,
        LLMConfig as PydanticLLMConfig,
        PromptConfig as PydanticPromptConfig,
    )

    # Convert entire config to plain dict first to avoid method name conflicts
    cfg_dict = OmegaConf.to_container(cfg, resolve=True)

    # Extract nested configs
    llm_dict = cfg_dict["llm"]
    browser_dict = cfg_dict["browser"]
    prompt_dict = cfg_dict["prompt"]
    judge_dict = cfg_dict.get("judge", {})

    # Create Pydantic config objects
    llm_config = PydanticLLMConfig(**llm_dict)
    browser_config = PydanticBrowserConfig(**browser_dict)
    prompt_config = PydanticPromptConfig(**prompt_dict)
    judge_config = PydanticJudgeConfig(**judge_dict) if judge_dict else PydanticJudgeConfig()

    # Get items list from the plain dict
    items = cfg_dict.get("items", ["apples"])
    if not items:
        items = ["apples"]

    # Create a single EvalRun from the flat Hydra config
    run = PydanticEvalRun(
        name=cfg_dict["name"],
        items=items,
        llm=llm_config,
        browser=browser_config,
        prompt=prompt_config,
        judge=judge_config,
        max_steps=cfg_dict["max_steps"],
        timeout_seconds=cfg_dict["timeout_seconds"],
    )

    # Create the EvalConfig with a single run
    return PydanticEvalConfig(
        name=cfg_dict["name"],
        runs=[run],
        base_url=cfg_dict["base_url"],
        cart_url=cfg_dict["cart_url"],
        output_dir=cfg_dict["output_dir"],
    )


# Register configs on module import
register_configs()
