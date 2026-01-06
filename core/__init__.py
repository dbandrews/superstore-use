"""Core utilities for the Superstore shopping agent.

This module provides shared browser configuration, success detection, and agent
functionality used by both local CLI and Modal deployments.
"""

from core.browser import STEALTH_ARGS, create_browser, get_profile_dir, get_proxy_config
from core.success import SUCCESS_INDICATORS, detect_success_from_history

__all__ = [
    "STEALTH_ARGS",
    "create_browser",
    "get_profile_dir",
    "get_proxy_config",
    "SUCCESS_INDICATORS",
    "detect_success_from_history",
]
