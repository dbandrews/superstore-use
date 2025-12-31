"""Login module for Real Canadian Superstore.

Provides shared browser configuration and login functionality for both
local development and Modal deployment.

Usage:
    Local:  uv run login.py
    Modal:  modal run modal_app.py::login_remote
"""

import asyncio
import os
import sys

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

# Browser timeouts (set before browser-use imports in some contexts)
os.environ.setdefault("TIMEOUT_BrowserStartEvent", "120")
os.environ.setdefault("TIMEOUT_BrowserLaunchEvent", "120")
os.environ.setdefault("TIMEOUT_BrowserStateRequestEvent", "120")

# Stealth arguments to avoid bot detection
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",  # Hide automation flag
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-accelerated-2d-canvas",
    "--disable-gpu",
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def get_profile_dir():
    """Determine profile directory based on environment.

    Returns:
        tuple: (profile_path, is_modal)
    """
    if os.path.exists("/session"):
        return "/session/profile", True
    return "./superstore-profile", False


def create_browser(user_data_dir: str | None = None, headless: bool = True):
    """Create browser with stealth arguments for bot detection avoidance.

    Args:
        user_data_dir: Profile directory. Auto-detected if None.
        headless: Run in headless mode. Default True.

    Returns:
        Browser instance configured for Superstore automation.
    """
    if user_data_dir is None:
        user_data_dir, _ = get_profile_dir()

    return Browser(
        headless=headless,
        window_size={"width": 1920, "height": 1080},
        wait_between_actions=1.5,
        minimum_wait_page_load_time=1.5,
        wait_for_network_idle_page_load_time=1.5,
        user_data_dir=user_data_dir,
        args=STEALTH_ARGS,
    )


async def login_and_save(headless: bool = True):
    """Log into Real Canadian Superstore and save browser state.

    Works in both local and Modal environments:
    - Local: Saves to ./superstore-profile/
    - Modal: Saves to /session/profile (Modal volume)

    Args:
        headless: Run browser in headless mode. Default True.

    Returns:
        dict with status and message
    """
    username = os.getenv("SUPERSTORE_USER")
    password = os.getenv("SUPERSTORE_PASSWORD")

    if not username or not password:
        print("Error: SUPERSTORE_USER and SUPERSTORE_PASSWORD must be set")
        return {"status": "failed", "message": "Missing credentials"}

    user_data_dir, is_modal = get_profile_dir()

    print(f"Environment: {'Modal' if is_modal else 'Local'}")
    print(f"Profile: {user_data_dir}")
    print(f"Headless: {headless}")

    browser = create_browser(user_data_dir=user_data_dir, headless=headless)

    try:
        agent = Agent(
            task=f"""
            Navigate to https://www.realcanadiansuperstore.ca/en and log in.

            Steps:
            1. Go to https://www.realcanadiansuperstore.ca/en
            2. If you see "My Shop" and "let's get started by shopping your regulars",
               you are already logged in - call done.
            3. Otherwise, click "Sign in" at top right.
            4. Enter username: {username}
            5. Enter password: {password}
            6. Click the sign in button.
            7. Wait for "My Account" at top right to confirm login.

            Complete when logged in.
            """,
            llm=ChatOpenAI(model="gpt-4.1"),
            browser_session=browser,
        )

        await agent.run(max_steps=50)

        # On Modal, commit the volume
        if is_modal:
            try:
                import modal

                volume = modal.Volume.from_name("superstore-session")
                volume.commit()
                print("Login successful! Session committed to Modal volume.")
            except Exception as e:
                print(f"Login succeeded but volume commit failed: {e}")
                return {"status": "partial", "message": str(e)}
        else:
            print(f"Login successful! Profile saved to {user_data_dir}")

        return {"status": "success", "message": "Login successful"}

    except Exception as e:
        print(f"Login failed: {e}")
        return {"status": "failed", "message": str(e)}
    finally:
        await browser.kill()


if __name__ == "__main__":
    # Parse --headed flag for local debugging
    headless = "--headed" not in sys.argv
    result = asyncio.run(login_and_save(headless=headless))
    if result.get("status") != "success":
        sys.exit(1)
