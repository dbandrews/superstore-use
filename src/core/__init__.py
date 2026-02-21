"""Core utilities for the Superstore shopping agent.

This module provides shared browser configuration, success detection, agent
functionality, and centralized configuration used by both local CLI and Modal
deployments.
"""

from src.core.browser import STEALTH_ARGS, create_browser, get_profile_dir, get_proxy_config
from src.core.config import get_config, get_stealth_args, is_modal_environment, load_config
from src.core.success import detect_success_from_history, get_success_indicators

__all__ = [
    # Browser utilities
    "STEALTH_ARGS",
    "create_browser",
    "get_profile_dir",
    "get_proxy_config",
    # Config utilities
    "load_config",
    "get_config",
    "get_stealth_args",
    "is_modal_environment",
    # Success detection
    "detect_success_from_history",
    "get_success_indicators",
]
