"""Core utilities for the Superstore shopping agent.

This module provides shared browser configuration, success detection, LLM factory,
prompt templates, and agent functionality used by both local CLI and Modal deployments.
"""

from core.browser import STEALTH_ARGS, create_browser, get_profile_dir, get_proxy_config
from core.llm import create_llm, create_llm_from_config, get_model_info
from core.prompts import (
    PromptConfig,
    default_prompts,
    get_add_item_prompt,
    get_checkout_prompt,
    get_login_prompt,
    get_verify_cart_prompt,
)
from core.success import SUCCESS_INDICATORS, detect_success_from_history

__all__ = [
    "STEALTH_ARGS",
    "create_browser",
    "get_profile_dir",
    "get_proxy_config",
    "SUCCESS_INDICATORS",
    "detect_success_from_history",
    "create_llm",
    "create_llm_from_config",
    "get_model_info",
    "PromptConfig",
    "default_prompts",
    "get_login_prompt",
    "get_add_item_prompt",
    "get_checkout_prompt",
    "get_verify_cart_prompt",
]
