"""Browser configuration and creation utilities.

Provides shared browser configuration for both local CLI and Modal deployments.
"""

import os
from typing import Optional

# Browser timeouts (set before browser-use imports in some contexts)
os.environ.setdefault("TIMEOUT_BrowserStartEvent", "120")
os.environ.setdefault("TIMEOUT_BrowserLaunchEvent", "120")
os.environ.setdefault("TIMEOUT_BrowserStateRequestEvent", "120")

# Stealth arguments to avoid bot detection (single source of truth)
# Configured to mimic Chrome on Linux desktop to match actual browser fingerprint
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",  # Hide automation flag
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-accelerated-2d-canvas",
    "--disable-gpu",
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def get_profile_dir() -> tuple[str, bool]:
    """Determine profile directory based on environment.

    Returns:
        tuple: (profile_path, is_modal)
            - profile_path: Path to the browser profile directory
            - is_modal: True if running on Modal, False if local
    """
    if os.path.exists("/session"):
        return "/session/profile", True
    return "./superstore-profile", False


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
    headless: bool = True,
    position: tuple[int, int] | None = None,
    window_size: tuple[int, int] = (1920, 1080),
    use_proxy: bool = False,
    use_stealth: bool = True,
    fast_mode: bool = False,
):
    """Create browser configured for Superstore automation.

    Works for both local development and Modal cloud deployment.

    Args:
        user_data_dir: Profile directory. Auto-detected if None.
        headless: Run in headless mode. Default True for Modal, False for local.
        position: Optional (x, y) window position for tiled windows.
        window_size: Window dimensions as (width, height). Default (1920, 1080).
        use_proxy: Whether to use proxy settings from environment. Default False.
        use_stealth: Whether to use stealth arguments for bot detection avoidance.
        fast_mode: If True, use faster timing for local development.

    Returns:
        Browser instance configured for Superstore automation.
    """
    from browser_use import Browser

    # Auto-detect profile directory
    if user_data_dir is None:
        user_data_dir, _ = get_profile_dir()

    # Build browser arguments
    args = []
    if use_stealth:
        args.extend(STEALTH_ARGS)
    else:
        # Minimal args for local development
        args.append("--disable-features=LockProfileCookieDatabase")

    # Timing settings: faster for local, slower for Modal
    if fast_mode:
        wait_between_actions = 0.1
        minimum_wait_page_load_time = 0.1
        wait_for_network_idle = 1.5
    else:
        wait_between_actions = 1.5
        minimum_wait_page_load_time = 1.5
        wait_for_network_idle = 1.5

    # Build browser kwargs
    browser_kwargs = {
        "headless": headless,
        "window_size": {"width": window_size[0], "height": window_size[1]},
        "wait_between_actions": wait_between_actions,
        "minimum_wait_page_load_time": minimum_wait_page_load_time,
        "wait_for_network_idle_page_load_time": wait_for_network_idle,
        "user_data_dir": user_data_dir,
        "args": args,
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
