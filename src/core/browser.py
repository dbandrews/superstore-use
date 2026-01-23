"""Browser configuration and creation utilities.

Provides shared browser configuration for both local CLI and Modal deployments.
All settings are loaded from config.toml via the config module.
"""

import os
from typing import Optional

from src.core.config import get_stealth_args, is_modal_environment, load_config

# Load config and set browser timeouts
_config = load_config()
os.environ.setdefault("TIMEOUT_BrowserStartEvent", str(_config.browser.timeout_browser_start))
os.environ.setdefault("TIMEOUT_BrowserLaunchEvent", str(_config.browser.timeout_browser_launch))
os.environ.setdefault("TIMEOUT_BrowserStateRequestEvent", str(_config.browser.timeout_browser_state_request))

# Export stealth args for backward compatibility with modal_app.py
STEALTH_ARGS = get_stealth_args(_config)


def get_profile_dir() -> tuple[str, bool]:
    """Determine profile directory based on environment.

    Returns:
        tuple: (profile_path, is_modal)
            - profile_path: Path to the browser profile directory
            - is_modal: True if running on Modal, False if local
    """
    config = load_config()
    if is_modal_environment():
        return config.browser.modal.profile_dir, True
    return config.browser.local.profile_dir, False


def get_proxy_config() -> Optional[dict]:
    """Get proxy configuration from environment variables.

    Returns:
        dict with server, username, password keys or None if not configured.
    """
    proxy_server = os.environ.get("PROXY_SERVER")
    proxy_username = os.environ.get("PROXY_USERNAME")
    proxy_password = os.environ.get("PROXY_PASSWORD")

    if not all([proxy_server, proxy_username, proxy_password]):
        return None

    return {"server": proxy_server, "username": proxy_username, "password": proxy_password}


def create_browser(
    user_data_dir: str | None = None,
    headless: bool | None = None,
    position: tuple[int, int] | None = None,
    window_size: tuple[int, int] | None = None,
    use_proxy: bool | None = None,
    use_stealth: bool | None = None,
    wait_between_actions: float | None = None,
    minimum_wait_page_load_time: float | None = None,
    wait_for_network_idle: float | None = None,
    shared_profile: bool | None = None,
    task_type: str | None = None,
    enable_default_extensions: bool | None = None,
):
    """Create browser configured for Superstore automation.

    Works for both local development and Modal cloud deployment.
    All defaults are loaded from config.toml based on the detected environment.

    Args:
        user_data_dir: Profile directory. Auto-detected if None.
        headless: Run in headless mode. Auto-detected from config if None.
        position: Optional (x, y) window position for tiled windows.
        window_size: Window dimensions as (width, height). Auto-detected if None.
        use_proxy: Whether to use proxy settings from environment. Auto-detected if None.
        use_stealth: Whether to use stealth arguments for bot detection avoidance.
        wait_between_actions: Delay between browser actions in seconds.
        minimum_wait_page_load_time: Minimum wait for page loads in seconds.
        wait_for_network_idle: Wait for network idle in seconds.
        shared_profile: Modal-only. If True, use volume profile; if False, use image profile.
        task_type: Modal-only. "login" or "add_item" to select timing defaults.
        enable_default_extensions: Whether to enable default browser extensions.

    Returns:
        Browser instance configured for Superstore automation.
    """
    from browser_use import Browser

    config = load_config()
    is_modal = is_modal_environment()

    # Get environment-specific config
    if is_modal:
        env_config = config.browser.modal
    else:
        env_config = config.browser.local

    # Auto-detect profile directory
    if user_data_dir is None:
        if is_modal:
            # Modal: Use shared_profile parameter to choose between volume and image profile
            if shared_profile is True:
                user_data_dir = config.browser.modal.profile_dir  # /session/profile
            elif shared_profile is False:
                user_data_dir = config.browser.modal.fallback_profile_dir
            else:
                # Default to shared profile for Modal
                user_data_dir = config.browser.modal.profile_dir
        else:
            user_data_dir = env_config.profile_dir

    # Select timing configuration
    if is_modal:
        # Modal: Use task_type to select timing
        if task_type == "login":
            timing = config.browser.modal.login
        elif task_type == "add_item":
            timing = config.browser.modal.add_item
        else:
            # Default to add_item timing for backward compatibility
            timing = config.browser.modal.add_item
    else:
        timing = env_config.timing

    # Auto-detect headless mode
    if headless is None:
        headless = env_config.headless

    # Auto-detect window size
    if window_size is None:
        window_size = (env_config.window_width, env_config.window_height)

    # Auto-detect proxy usage
    if use_proxy is None:
        use_proxy = env_config.use_proxy

    # Auto-detect stealth mode
    if use_stealth is None:
        use_stealth = env_config.use_stealth

    # Auto-detect timing settings
    if wait_between_actions is None:
        wait_between_actions = timing.wait_between_actions
    if minimum_wait_page_load_time is None:
        minimum_wait_page_load_time = timing.min_wait_page_load
    if wait_for_network_idle is None:
        wait_for_network_idle = timing.wait_for_network_idle

    # Build browser arguments
    args = []
    if use_stealth:
        args.extend(get_stealth_args(config))
    else:
        # Minimal args for local development
        args.append("--disable-features=LockProfileCookieDatabase")

    # Auto-detect extension handling
    if enable_default_extensions is None:
        # Modal disables extensions by default, local enables them
        enable_default_extensions = not is_modal

    # Build browser kwargs
    browser_kwargs = {
        "headless": headless,
        "window_size": {"width": window_size[0], "height": window_size[1]},
        "wait_between_actions": wait_between_actions,
        "minimum_wait_page_load_time": minimum_wait_page_load_time,
        "wait_for_network_idle_page_load_time": wait_for_network_idle,
        "user_data_dir": user_data_dir,
        "args": args,
        "enable_default_extensions": enable_default_extensions,
    }

    # Add window position if specified (for tiled windows)
    if position:
        x, y = position
        browser_kwargs["window_position"] = {"width": x, "height": y}

    # Add proxy if requested
    if use_proxy:
        proxy_config = get_proxy_config()
        if proxy_config:
            from browser_use.browser.profile import ProxySettings

            browser_kwargs["proxy"] = ProxySettings(
                server=proxy_config["server"],
                username=proxy_config["username"],
                password=proxy_config["password"],
            )

    return Browser(**browser_kwargs)
