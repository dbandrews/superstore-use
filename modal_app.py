"""
Modal deployment for the Superstore Shopping Agent.

Single unified deployment with:
- Core browser automation functions (login, add items)
- Chat-based web UI with LangGraph agent

Deploy with: modal deploy modal_app.py
Run locally: modal serve modal_app.py
"""

import asyncio
import json
import os
import threading
import uuid
from typing import Optional

import modal

# =============================================================================
# Modal App Configuration
# =============================================================================

app = modal.App("superstore-agent")
MODAL_APP_NAME = "superstore-agent"

# Persistent volume for storing session cookies
session_volume = modal.Volume.from_name("superstore-session", create_if_missing=True)

# Distributed Dict for storing job state (persists across function invocations)
job_state_dict = modal.Dict.from_name("superstore-job-state", create_if_missing=True)


# =============================================================================
# Shared Configuration (imported from core module)
# =============================================================================

# Import shared config from core to avoid duplication
from core.browser import STEALTH_ARGS, get_proxy_config

# Model configuration - single place to set the model
MODEL_NAME = "openai/gpt-oss-120b"

# Success indicators for detecting if item was added to cart
SUCCESS_INDICATORS = [
    "added to cart",
    "add to cart",
    "item added",
    "cart updated",
    "in your cart",
    "added to your cart",
    "quantity updated",
]


# =============================================================================
# Modal Image Definition
# =============================================================================

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        # Playwright browser dependencies
        "wget",
        "gnupg",
        "libglib2.0-0",
        "libnss3",
        "libnspr4",
        "libdbus-1-3",
        "libatk1.0-0",
        "libatk-bridge2.0-0",
        "libcups2",
        "libdrm2",
        "libxkbcommon0",
        "libxcomposite1",
        "libxdamage1",
        "libxfixes3",
        "libxrandr2",
        "libgbm1",
        "libasound2",
        "libpango-1.0-0",
        "libcairo2",
        "libatspi2.0-0",
        "libgtk-3-0",
        "libx11-xcb1",
        "libxcb1",
        "fonts-liberation",
        "xdg-utils",
    )
    .uv_sync(uv_project_dir="./")
    .env({"PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright"})
    .run_commands(
        "mkdir -p /ms-playwright",
        "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright uv run playwright install chromium",
        # Workaround for browser-use bug: https://github.com/browser-use/browser-use/issues/3779
        """bash -c 'for dir in /ms-playwright/chromium-*/; do \
            if [ -d "${dir}chrome-linux64" ] && [ ! -e "${dir}chrome-linux" ]; then \
                ln -s chrome-linux64 "${dir}chrome-linux"; \
            fi; \
        done'""",
    )
    # Copy the local browser profile directory as a fallback profile
    .add_local_dir(
        "./superstore-profile",
        remote_path="/app/superstore-profile",
        copy=True,
    )
    # Add core module for shared utilities
    .add_local_python_source("core")
)

# Lighter image for chat UI (doesn't need browser)
chat_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "flask",
        "langchain-core",
        "langchain-openai",
        "langgraph",
        "python-dotenv",
        "modal",
    )
    .add_local_python_source("core")
)


# =============================================================================
# Browser Creation (Modal-specific)
# =============================================================================


def create_browser(shared_profile: bool = False, use_proxy: bool = True):
    """Create browser configured for Modal's containerized environment.

    Args:
        shared_profile: If True, use profile on volume for persistence across containers.
                        If False, use profile from image (for isolated containers).
        use_proxy: If True, use proxy settings from environment. Default True.
                   Set to False for login to avoid proxy IP blocking by auth servers.
    """
    from browser_use import Browser
    from browser_use.browser.profile import ProxySettings

    # Use shared profile on volume for persistence, or local profile from image
    user_data_dir = "/session/profile" if shared_profile else "/app/superstore-profile"

    # Build browser config with optional proxy
    proxy_settings = None
    if use_proxy:
        proxy_config = get_proxy_config()
        if proxy_config:
            proxy_settings = ProxySettings(
                server=proxy_config["server"],
                username=proxy_config["username"],
                password=proxy_config["password"],
            )

    return Browser(
        headless=True,
        window_size={"width": 1920, "height": 1080},
        wait_between_actions=1.5,
        minimum_wait_page_load_time=1.5,
        wait_for_network_idle_page_load_time=1.5,
        user_data_dir=user_data_dir,
        proxy=proxy_settings,
        args=STEALTH_ARGS,
        enable_default_extensions=False,  # Skip downloading uBlock, cookie consent, etc.
    )


# =============================================================================
# Success Detection
# =============================================================================


def detect_success_from_history(agent) -> tuple[bool, str | None]:
    """Parse browser-use agent history to detect if item was added successfully."""
    try:
        extracted = agent.history.extracted_content()
        for content in extracted:
            content_lower = str(content).lower()
            for indicator in SUCCESS_INDICATORS:
                if indicator in content_lower:
                    return True, str(content)[:100]

        thoughts = agent.history.model_thoughts()
        for thought in thoughts:
            thought_lower = str(thought).lower()
            if any(ind in thought_lower for ind in SUCCESS_INDICATORS):
                return True, str(thought)[:100]

        urls = agent.history.urls()
        if urls and "cart" in urls[-1].lower():
            return True, f"Ended on cart page: {urls[-1]}"

    except Exception as e:
        print(f"[Success Detection] Error: {e}")

    return False, None


# =============================================================================
# Core Modal Functions
# =============================================================================


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("groq-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    volumes={"/session": session_volume},
    timeout=600,
    env={
        "TIMEOUT_BrowserStartEvent": "120",
        "TIMEOUT_BrowserLaunchEvent": "120",
        "TIMEOUT_BrowserStateRequestEvent": "120",
        "IN_DOCKER": "True",
    },
    cpu=1,
    memory=4096,
)
def login_remote() -> dict:
    """Log in to Superstore and save session to shared volume.

    This must be called before add_item_remote will work.
    """
    from browser_use import Agent, ChatGroq

    async def _login():
        username = os.environ.get("SUPERSTORE_USER")
        password = os.environ.get("SUPERSTORE_PASSWORD")

        if not username or not password:
            return {"status": "failed", "message": "Missing credentials"}

        print("[Login] Starting login process on Modal...")
        print(f"[Login] Profile: /session/profile")

        browser = create_browser(shared_profile=True)

        try:
            agent = Agent(
                task=f"""
                Navigate to https://www.realcanadiansuperstore.ca/en and log in.

                Steps:
                1. Go to https://www.realcanadiansuperstore.ca/en
                2. If you see "My Shop" and "let's get started by shopping your regulars",
                   you are already logged in - call done.
                3. Otherwise, click "Sign in" at top right.
                4. IMPORTANT: If you see an email address ({username}) displayed on the login page
                   (this indicates a saved login), simply click on that email to proceed.
                   Then wait patiently for the login to complete - this may take several seconds.
                5. If you don't see the email displayed, enter username: {username}
                6. Then enter password: {password}
                7. Click the sign in button.
                8. After clicking to sign in, wait patiently for as long as needed for the login
                   to complete. Do not rush - the page may take several seconds to load.
                9. Wait for "My Account" at top right to confirm login.

                Complete when logged in successfully.
                """,
                llm=ChatGroq(model=MODEL_NAME),
                use_vision=False,
                browser_session=browser,
            )

            await agent.run(max_steps=50)

            # Commit the volume to persist session
            session_volume.commit()
            print("[Login] Session committed to Modal volume.")

            return {"status": "success", "message": "Login successful"}

        except Exception as e:
            print(f"[Login] Error: {e}")
            return {"status": "failed", "message": str(e)}
        finally:
            await browser.kill()

    return asyncio.run(_login())


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("groq-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    volumes={"/session": session_volume},
    timeout=600,
    env={
        "TIMEOUT_BrowserStartEvent": "120",
        "TIMEOUT_BrowserLaunchEvent": "120",
        "TIMEOUT_BrowserStateRequestEvent": "120",
        "IN_DOCKER": "True",
    },
    cpu=1,
    memory=4096,
)
def login_remote_streaming():
    """Streaming version of login that yields progress events."""
    import queue
    import threading

    from browser_use import Agent, ChatGroq

    step_events: queue.Queue[dict] = queue.Queue()
    result_holder: dict[str, str | dict | None] = {"result": None, "error": None}

    async def _login():
        username = os.environ.get("SUPERSTORE_USER")
        password = os.environ.get("SUPERSTORE_PASSWORD")

        if not username or not password:
            return {"status": "failed", "message": "Missing credentials"}

        print("[Login] Starting login process on Modal...")
        browser = create_browser(shared_profile=True)
        step_count = 0

        async def on_step_end(agent):
            nonlocal step_count
            step_count += 1

            model_outputs = agent.history.model_outputs()
            latest_output = model_outputs[-1] if model_outputs else None

            thinking = None
            next_goal = None

            if latest_output:
                thinking = latest_output.thinking
                next_goal = latest_output.next_goal

            step_events.put({
                "type": "step",
                "step": step_count,
                "thinking": thinking,
                "next_goal": next_goal,
            })

        try:
            agent = Agent(
                task=f"""
                Navigate to https://www.realcanadiansuperstore.ca/en and log in.

                Steps:
                1. Go to https://www.realcanadiansuperstore.ca/en
                2. If you see "My Shop" and "let's get started by shopping your regulars",
                   you are already logged in - call done.
                3. Otherwise, click "Sign in" at top right.
                4. IMPORTANT: If you see an email address ({username}) displayed on the login page
                   (this indicates a saved login), simply click on that email to proceed.
                   Then wait patiently for the login to complete - this may take several seconds.
                5. If you don't see the email displayed, enter username: {username}
                6. Then enter password: {password}
                7. Click the sign in button.
                8. After clicking to sign in, wait patiently for as long as needed for the login
                   to complete. Do not rush - the page may take several seconds to load.
                9. Wait for "My Account" at top right to confirm login.

                Complete when logged in successfully.
                """,
                llm=ChatGroq(model=MODEL_NAME),
                use_vision=False,
                browser_session=browser,
            )

            await agent.run(max_steps=50, on_step_end=on_step_end)

            session_volume.commit()
            print("[Login] Session committed to Modal volume.")

            return {"status": "success", "message": "Login successful", "steps": step_count}

        except Exception as e:
            print(f"[Login] Error: {e}")
            return {"status": "failed", "message": str(e), "steps": step_count}
        finally:
            await browser.kill()

    def run_async():
        try:
            result_holder["result"] = asyncio.run(_login())
        except Exception as e:
            result_holder["error"] = str(e)

    worker_thread = threading.Thread(target=run_async)
    worker_thread.start()

    yield json.dumps({"type": "start"})

    while worker_thread.is_alive():
        try:
            event = step_events.get(timeout=0.5)
            yield json.dumps(event)
        except queue.Empty:
            pass

    while not step_events.empty():
        try:
            event = step_events.get_nowait()
            yield json.dumps(event)
        except queue.Empty:
            break

    if result_holder["error"]:
        yield json.dumps({
            "type": "complete",
            "status": "failed",
            "message": result_holder["error"],
            "steps": 0,
        })
    elif result_holder["result"]:
        yield json.dumps({"type": "complete", **result_holder["result"]})
    else:
        yield json.dumps({
            "type": "complete",
            "status": "failed",
            "message": "Unknown error",
            "steps": 0,
        })


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("groq-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    volumes={"/session": session_volume},
    timeout=600,
    env={
        "TIMEOUT_BrowserStartEvent": "120",
        "TIMEOUT_BrowserLaunchEvent": "120",
        "TIMEOUT_BrowserStateRequestEvent": "120",
        "IN_DOCKER": "True",
    },
    cpu=1,
    memory=4096,
)
def add_item_remote(item: str, index: int) -> dict:
    """Add a single item to cart in a separate Modal container (parallelizable).

    Uses shared profile on volume (created by login_remote).
    """
    from browser_use import Agent, ChatGroq

    async def _add_item():
        print(f"[Container {index}] Starting to add item: {item}")
        browser = create_browser(shared_profile=True)

        try:
            agent = Agent(
                task=f"""
                You need to add "{item}" to the shopping cart on Real Canadian Superstore.

                Go to https://www.realcanadiansuperstore.ca/en.

                IMPORTANT: Before starting, check the page contains "My Shop" and "let's get started by shopping your regulars".
                If you see these, you are already logged in.

                UNDERSTANDING THE ITEM REQUEST:
                The item "{item}" may include a quantity (e.g., "6 apples", "2 liters milk", "500g chicken breast").
                - Extract the product name to search for (e.g., "apples", "milk", "chicken breast")
                - Note the quantity requested (e.g., 6, 2 liters, 500g)

                Steps:
                1. Use the search bar to search for the PRODUCT NAME (not the full quantity string)
                   - For "6 apples", search for "apples"
                   - For "2 liters milk", search for "milk"
                   - For "500g chicken breast", search for "chicken breast"
                2. From the search results, select the most relevant item that matches the quantity/size if possible
                   - If looking for "2 liters milk", prefer 2L milk containers
                   - If looking for "500g chicken", prefer ~500g packages
                3. If a specific quantity is requested (like "6 apples"):
                   - Look for a quantity selector/input field on the product
                   - Adjust the quantity before adding to cart
                   - If no quantity selector, you may need to click "Add to Cart" multiple times
                4. Click "Add to Cart" or similar button
                5. Wait for confirmation that item was added (look for cart update or confirmation message)

                Complete when you see confirmation the item was added to cart with the correct quantity.

                NOTE: If you see a login page or are not logged in, report this as an error.
                """,
                llm=ChatGroq(model=MODEL_NAME),
                use_vision=False,
                browser_session=browser,
            )

            await agent.run(max_steps=30)

            success, evidence = detect_success_from_history(agent)

            if success:
                print(f"[Container {index}] SUCCESS: {evidence}")
                return {
                    "item": item,
                    "index": index,
                    "status": "success",
                    "message": f"Added {item}",
                    "evidence": evidence,
                }
            else:
                print(f"[Container {index}] No success indicator found")
                return {
                    "item": item,
                    "index": index,
                    "status": "uncertain",
                    "message": f"Completed but could not confirm {item} was added",
                }

        except Exception as e:
            print(f"[Container {index}] ERROR: {e}")
            return {"item": item, "index": index, "status": "failed", "message": str(e)}
        finally:
            await browser.kill()

    return asyncio.run(_add_item())


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("groq-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    volumes={"/session": session_volume},
    timeout=600,
    env={
        "TIMEOUT_BrowserStartEvent": "120",
        "TIMEOUT_BrowserLaunchEvent": "120",
        "TIMEOUT_BrowserStateRequestEvent": "120",
        "IN_DOCKER": "True",
    },
    cpu=1,
    memory=4096,
)
def add_item_remote_streaming(item: str, index: int):
    """Generator version that yields JSON progress events in real-time."""
    import queue
    import threading

    from browser_use import Agent, ChatGroq

    step_events: queue.Queue[dict] = queue.Queue()
    result_holder: dict[str, str | dict | None] = {"result": None, "error": None}

    async def _add_item():
        print(f"[Container {index}] Starting to add item: {item}")
        browser = create_browser(shared_profile=True)
        step_count = 0

        async def on_step_end(agent):
            nonlocal step_count
            step_count += 1

            # Get the agent's thinking/reasoning from model outputs
            model_outputs = agent.history.model_outputs()
            latest_output = model_outputs[-1] if model_outputs else None

            thinking = None
            evaluation = None
            next_goal = None
            action_str = "..."

            if latest_output:
                thinking = latest_output.thinking
                evaluation = latest_output.evaluation_previous_goal
                next_goal = latest_output.next_goal
                # Get action from the output's action list
                if latest_output.action:
                    action_str = str(latest_output.action[0])[:80]

            step_events.put({
                "type": "step",
                "item": item,
                "index": index,
                "step": step_count,
                "action": action_str,
                "thinking": thinking,
                "evaluation": evaluation,
                "next_goal": next_goal,
            })

        try:
            agent = Agent(
                task=f"""
                You need to add "{item}" to the shopping cart on Real Canadian Superstore.
                Go to https://www.realcanadiansuperstore.ca/en

                UNDERSTANDING THE ITEM REQUEST:
                The item "{item}" may include a quantity (e.g., "6 apples", "2 liters milk", "500g chicken breast").
                - Extract the product name to search for (e.g., "apples", "milk", "chicken breast")
                - Note the quantity requested (e.g., 6, 2 liters, 500g)

                Steps:
                1. Use the search bar to search for the PRODUCT NAME (not the full quantity string)
                   - For "6 apples", search for "apples"
                   - For "2 liters milk", search for "milk"
                   - For "500g chicken breast", search for "chicken breast"
                2. From the search results, select the most relevant item that matches the quantity/size if possible
                   - If looking for "2 liters milk", prefer 2L milk containers
                   - If looking for "500g chicken", prefer ~500g packages
                3. If a specific quantity is requested (like "6 apples"):
                   - Look for a quantity selector/input field on the product
                   - Adjust the quantity before adding to cart
                   - If no quantity selector, you may need to click "Add to Cart" multiple times
                4. Click "Add to Cart" or similar button
                5. Wait for confirmation that item was added

                Complete when you see confirmation the item was added to cart with the correct quantity.
                """,
                llm=ChatGroq(model=MODEL_NAME),
                use_vision=False,
                browser_session=browser,
            )

            await agent.run(max_steps=30, on_step_end=on_step_end)

            success, evidence = detect_success_from_history(agent)

            if success:
                return {
                    "item": item,
                    "index": index,
                    "status": "success",
                    "message": f"Added {item}",
                    "evidence": evidence,
                    "steps": step_count,
                }
            else:
                return {
                    "item": item,
                    "index": index,
                    "status": "uncertain",
                    "message": f"Completed but could not confirm {item} was added",
                    "steps": step_count,
                }

        except Exception as e:
            return {
                "item": item,
                "index": index,
                "status": "failed",
                "message": str(e),
                "steps": step_count,
            }
        finally:
            await browser.kill()

    def run_async():
        try:
            result_holder["result"] = asyncio.run(_add_item())
        except Exception as e:
            result_holder["error"] = str(e)

    worker_thread = threading.Thread(target=run_async)
    worker_thread.start()

    yield json.dumps({"type": "start", "item": item, "index": index})

    while worker_thread.is_alive():
        try:
            event = step_events.get(timeout=0.5)
            yield json.dumps(event)
        except queue.Empty:
            pass

    while not step_events.empty():
        try:
            event = step_events.get_nowait()
            yield json.dumps(event)
        except queue.Empty:
            break

    if result_holder["error"]:
        yield json.dumps({
            "type": "complete",
            "item": item,
            "index": index,
            "status": "failed",
            "message": result_holder["error"],
            "steps": 0,
        })
    elif result_holder["result"]:
        yield json.dumps({"type": "complete", **result_holder["result"]})
    else:
        yield json.dumps({
            "type": "complete",
            "item": item,
            "index": index,
            "status": "failed",
            "message": "Unknown error",
            "steps": 0,
        })


# =============================================================================
# Browser Session for User Control
# =============================================================================

# Store for active browser sessions
browser_session_dict = modal.Dict.from_name("superstore-browser-sessions", create_if_missing=True)


@app.cls(
    image=image,
    secrets=[
        modal.Secret.from_name("groq-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    volumes={"/session": session_volume},
    timeout=1800,  # 30 minutes for user interaction
    env={
        "TIMEOUT_BrowserStartEvent": "120",
        "TIMEOUT_BrowserLaunchEvent": "120",
        "TIMEOUT_BrowserStateRequestEvent": "120",
        "IN_DOCKER": "True",
    },
    cpu=1,
    memory=4096,
    allow_concurrent_inputs=100,
)
class BrowserSession:
    """Interactive browser session that allows user control via screenshot streaming and input events."""

    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.session_id = None

    @modal.enter()
    async def setup(self):
        """Initialize browser on container start."""
        import uuid
        from playwright.async_api import async_playwright

        self.session_id = str(uuid.uuid4())[:8]
        print(f"[BrowserSession {self.session_id}] Initializing...")

        self.playwright = await async_playwright().start()

        # Get proxy config
        proxy_config = get_proxy_config()
        proxy_settings = None
        if proxy_config:
            proxy_settings = {
                "server": proxy_config["server"],
                "username": proxy_config["username"],
                "password": proxy_config["password"],
            }

        # Launch browser with stealth args
        self.browser = await self.playwright.chromium.launch_persistent_context(
            user_data_dir="/session/profile",
            headless=True,
            viewport={"width": 1280, "height": 800},
            args=STEALTH_ARGS,
            proxy=proxy_settings,
        )

        # Get or create page
        if self.browser.pages:
            self.page = self.browser.pages[0]
        else:
            self.page = await self.browser.new_page()

        print(f"[BrowserSession {self.session_id}] Browser ready")

    @modal.exit()
    async def cleanup(self):
        """Clean up browser on container shutdown."""
        if self.browser:
            await self.browser.close()
        if hasattr(self, 'playwright') and self.playwright:
            await self.playwright.stop()
        print(f"[BrowserSession {self.session_id}] Cleaned up")

    @modal.method()
    async def navigate(self, url: str) -> dict:
        """Navigate to a URL."""
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return {"status": "success", "url": self.page.url}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @modal.method()
    async def get_screenshot(self) -> bytes:
        """Get current page screenshot as PNG bytes."""
        try:
            return await self.page.screenshot(type="png")
        except Exception as e:
            print(f"[BrowserSession] Screenshot error: {e}")
            return b""

    @modal.method()
    async def get_state(self) -> dict:
        """Get current browser state."""
        try:
            return {
                "session_id": self.session_id,
                "url": self.page.url,
                "title": await self.page.title(),
            }
        except Exception as e:
            return {"session_id": self.session_id, "error": str(e)}

    @modal.method()
    async def click(self, x: int, y: int) -> dict:
        """Click at coordinates."""
        try:
            await self.page.mouse.click(x, y)
            await self.page.wait_for_timeout(500)  # Brief wait for page reaction
            return {"status": "success", "x": x, "y": y}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @modal.method()
    async def type_text(self, text: str) -> dict:
        """Type text."""
        try:
            await self.page.keyboard.type(text, delay=50)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @modal.method()
    async def press_key(self, key: str) -> dict:
        """Press a key (e.g., 'Enter', 'Tab', 'Backspace')."""
        try:
            await self.page.keyboard.press(key)
            return {"status": "success", "key": key}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @modal.method()
    async def scroll(self, delta_x: int, delta_y: int) -> dict:
        """Scroll the page."""
        try:
            await self.page.mouse.wheel(delta_x, delta_y)
            await self.page.wait_for_timeout(200)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# =============================================================================
# Chat UI Flask App
# =============================================================================


@app.function(
    image=chat_image,
    secrets=[modal.Secret.from_name("openai-secret")],
    timeout=600,
    cpu=1,
    memory=2048,
)
@modal.wsgi_app()
def flask_app():
    """Flask app for the chat UI."""
    import time

    from flask import Flask, Response, jsonify, request
    from langchain_core.messages import AIMessage, HumanMessage

    from core.agent import create_chat_agent

    flask_app = Flask(__name__)

    # Store agent instances per session
    agents = {}

    def get_or_create_agent(thread_id: str):
        if thread_id not in agents:
            agents[thread_id] = create_chat_agent()
        return agents[thread_id]

    # Job state management helpers
    def create_job(thread_id: str, message: str) -> str:
        job_id = str(uuid.uuid4())[:8]
        job_state_dict[job_id] = {
            "id": job_id,
            "thread_id": thread_id,
            "message": message,
            "status": "running",
            "created_at": time.time(),
            "updated_at": time.time(),
            "items_processed": [],
            "items_in_progress": {},
            "final_message": None,
            "error": None,
        }
        return job_id

    def update_job_progress(job_id: str, event: dict):
        try:
            job = job_state_dict.get(job_id)
            if not job:
                return

            event_type = event.get("type", "")
            job["updated_at"] = time.time()

            if event_type == "item_start":
                job["items_in_progress"][event["item"]] = {"step": 0, "action": "Starting..."}
            elif event_type == "step":
                if event.get("item") in job["items_in_progress"]:
                    job["items_in_progress"][event["item"]] = {
                        "step": event.get("step", 0),
                        "action": event.get("action", "...")
                    }
            elif event_type == "item_complete":
                item_name = event.get("item")
                if item_name in job["items_in_progress"]:
                    del job["items_in_progress"][item_name]
                job["items_processed"].append({
                    "item": item_name,
                    "status": event.get("status", "unknown"),
                    "steps": event.get("steps", 0)
                })
            elif event_type == "complete":
                job["status"] = "completed"
                job["success_count"] = event.get("success_count", 0)
            elif event_type == "message":
                job["final_message"] = event.get("content")
            elif event_type == "error":
                job["status"] = "error"
                job["error"] = event.get("message")

            job_state_dict[job_id] = job
        except Exception as e:
            print(f"[JobState] Error updating job {job_id}: {e}")

    def get_job_status(job_id: str) -> dict | None:
        try:
            job = job_state_dict.get(job_id)
            if job and time.time() - job.get("created_at", 0) > 600:
                job["status"] = "expired"
            return job
        except Exception:
            return None

    # Chat UI HTML template (inline for single-file deployment)
    CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Superstore Shopping Assistant</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', monospace;
            background: #0d1117;
            color: rgba(255,255,255,0.9);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            background: transparent;
            padding: 20px 24px;
            border-bottom: 1px solid rgba(255,255,255,0.06);
        }
        .header h1 {
            font-size: 0.75rem;
            font-weight: 400;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: rgba(255,255,255,0.4);
        }
        .main-container { flex: 1; display: flex; overflow: hidden; }
        .chat-container { flex: 1; display: flex; flex-direction: column; min-width: 0; }
        .sidebar {
            width: 280px;
            background: rgba(255,255,255,0.02);
            border-left: 1px solid rgba(255,255,255,0.06);
            display: flex;
            flex-direction: column;
        }
        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid rgba(255,255,255,0.06);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .sidebar-header h2 {
            font-size: 0.7rem;
            font-weight: 400;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: rgba(255,255,255,0.4);
        }
        .item-count {
            background: rgba(255,255,255,0.1);
            color: rgba(255,255,255,0.6);
            font-size: 0.65rem;
            padding: 3px 8px;
            border-radius: 2px;
        }
        .item-count.has-items { background: rgba(255,255,255,0.9); color: #0d1117; }
        .grocery-list { flex: 1; overflow-y: auto; padding: 12px; }
        .grocery-item {
            display: flex;
            align-items: center;
            padding: 12px 14px;
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.04);
            border-radius: 4px;
            margin-bottom: 6px;
        }
        .grocery-item .item-info { flex: 1; min-width: 0; }
        .grocery-item .item-name { font-size: 0.8rem; color: rgba(255,255,255,0.8); }
        .grocery-item .item-qty { font-size: 0.7rem; color: rgba(255,255,255,0.3); margin-top: 2px; }
        .grocery-item .remove-btn { background: none; border: none; color: rgba(255,255,255,0.2); cursor: pointer; padding: 4px; font-size: 1rem; }
        .grocery-item .remove-btn:hover { color: rgba(255,255,255,0.6); }
        .grocery-item .edit-qty { display: flex; align-items: center; gap: 6px; margin-right: 10px; }
        .grocery-item .qty-btn { width: 22px; height: 22px; border: 1px solid rgba(255,255,255,0.1); background: transparent; border-radius: 2px; cursor: pointer; font-size: 0.85rem; color: rgba(255,255,255,0.4); }
        .grocery-item .qty-btn:hover { border-color: rgba(255,255,255,0.3); color: rgba(255,255,255,0.8); }
        .grocery-item .qty-display { font-size: 0.75rem; min-width: 18px; text-align: center; color: rgba(255,255,255,0.6); }
        .empty-list { text-align: center; padding: 40px 20px; color: rgba(255,255,255,0.25); }
        .empty-list .icon { font-size: 1.5rem; margin-bottom: 12px; opacity: 0.5; }
        .empty-list p { font-size: 0.75rem; }
        .sidebar-footer { padding: 16px; border-top: 1px solid rgba(255,255,255,0.06); }
        .add-item-form { display: flex; gap: 8px; margin-bottom: 10px; }
        .add-item-form input { flex: 1; padding: 10px 12px; border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; font-size: 0.8rem; font-family: inherit; background: rgba(255,255,255,0.03); color: rgba(255,255,255,0.8); outline: none; }
        .add-item-form input::placeholder { color: rgba(255,255,255,0.25); }
        .add-item-form input:focus { border-color: rgba(255,255,255,0.2); }
        .add-item-form button { padding: 10px 14px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; cursor: pointer; font-size: 0.9rem; color: rgba(255,255,255,0.5); }
        .add-item-form button:hover { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.8); }
        .sidebar-actions { display: flex; gap: 8px; }
        .clear-btn { flex: 1; padding: 11px; background: transparent; border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; font-size: 0.75rem; font-family: inherit; color: rgba(255,255,255,0.4); cursor: pointer; }
        .clear-btn:hover { border-color: rgba(255,255,255,0.2); color: rgba(255,255,255,0.7); }
        .add-all-btn { flex: 2; padding: 11px; background: rgba(255,255,255,0.9); color: #0d1117; border: none; border-radius: 4px; font-size: 0.75rem; font-weight: 500; font-family: inherit; cursor: pointer; }
        .add-all-btn:hover:not(:disabled) { background: rgba(255,255,255,1); }
        .add-all-btn:disabled { opacity: 0.3; cursor: not-allowed; }
        .messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
        .message { max-width: 80%; padding: 14px 18px; border-radius: 4px; line-height: 1.6; white-space: pre-wrap; font-size: 0.85rem; }
        .message.user { align-self: flex-end; background: rgba(255,255,255,0.9); color: #0d1117; }
        .message.assistant { align-self: flex-start; background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.85); border: 1px solid rgba(255,255,255,0.06); }
        .message.error { align-self: center; background: rgba(255,100,100,0.1); color: rgba(255,150,150,0.9); font-size: 0.8rem; border: 1px solid rgba(255,100,100,0.15); }
        .typing-indicator { align-self: flex-start; background: rgba(255,255,255,0.05); padding: 14px 18px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.06); }
        .typing-indicator span { display: inline-block; width: 6px; height: 6px; background: rgba(255,255,255,0.4); border-radius: 50%; margin-right: 4px; animation: pulse 1.4s infinite ease-in-out both; }
        .typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
        .typing-indicator span:nth-child(2) { animation-delay: -0.16s; }
        @keyframes pulse { 0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); } 40% { opacity: 1; transform: scale(1); } }
        .input-area { padding: 20px 24px; border-top: 1px solid rgba(255,255,255,0.06); display: flex; gap: 12px; }
        .input-area input { flex: 1; padding: 14px 18px; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; font-size: 0.85rem; font-family: inherit; background: rgba(255,255,255,0.03); color: rgba(255,255,255,0.9); outline: none; }
        .input-area input::placeholder { color: rgba(255,255,255,0.25); }
        .input-area input:focus { border-color: rgba(255,255,255,0.25); }
        .input-area input:disabled { background: rgba(255,255,255,0.02); }
        .input-area button { padding: 14px 28px; background: rgba(255,255,255,0.9); color: #0d1117; border: none; border-radius: 4px; font-size: 0.8rem; font-weight: 500; font-family: inherit; cursor: pointer; }
        .input-area button:hover:not(:disabled) { background: rgba(255,255,255,1); }
        .input-area button:disabled { opacity: 0.3; cursor: not-allowed; }
        .suggestions { padding: 12px 24px; border-top: 1px solid rgba(255,255,255,0.06); display: flex; flex-wrap: wrap; gap: 8px; }
        .suggestion { padding: 8px 14px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; font-size: 0.75rem; color: rgba(255,255,255,0.5); cursor: pointer; }
        .suggestion:hover { border-color: rgba(255,255,255,0.2); color: rgba(255,255,255,0.8); }
        @media (max-width: 768px) {
            body { overflow: hidden; position: fixed; width: 100%; height: 100%; }
            .main-container { flex-direction: column; height: calc(100vh - 53px); overflow: hidden; }
            .chat-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; height: 100%; }
            .messages { flex: 1; overflow-y: auto; -webkit-overflow-scrolling: touch; padding: 16px; padding-bottom: 130px; gap: 12px; }
            .message { max-width: 90%; }
            .suggestions { display: none; }
            .input-area { position: fixed; bottom: 56px; left: 0; right: 0; background: #0d1117; z-index: 50; padding: 12px 16px; }
            .input-area input { padding: 12px 14px; font-size: 16px; }
            .sidebar { position: fixed; bottom: 0; left: 0; right: 0; width: 100%; height: auto; max-height: 70vh; border-left: none; border-top: 1px solid rgba(255,255,255,0.1); border-radius: 16px 16px 0 0; transform: translateY(calc(100% - 56px)); transition: transform 0.3s ease; z-index: 100; background: #0d1117; }
            .sidebar.expanded { transform: translateY(0); }
            .sidebar-header { cursor: pointer; padding-top: 20px; position: relative; }
            .sidebar-header::before { content: ''; position: absolute; top: 8px; left: 50%; transform: translateX(-50%); width: 36px; height: 4px; background: rgba(255,255,255,0.15); border-radius: 2px; }
            .sidebar-toggle-icon { display: inline-block; transition: transform 0.3s ease; color: rgba(255,255,255,0.4); font-size: 0.8rem; }
            .sidebar.expanded .sidebar-toggle-icon { transform: rotate(180deg); }
            .grocery-list { max-height: calc(70vh - 140px); }
        }
        @media (min-width: 769px) { .sidebar-toggle-icon { display: none; } }
        /* Browser View Styles */
        .browser-container {
            display: none;
            flex: 1;
            flex-direction: column;
            background: #0a0c10;
            border-left: 1px solid rgba(255,255,255,0.06);
        }
        .browser-container.active { display: flex; }
        .browser-header {
            padding: 12px 16px;
            border-bottom: 1px solid rgba(255,255,255,0.06);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .browser-header h2 {
            font-size: 0.7rem;
            font-weight: 400;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: rgba(255,255,255,0.4);
        }
        .browser-url {
            flex: 1;
            margin: 0 12px;
            padding: 6px 10px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            font-size: 0.7rem;
            color: rgba(255,255,255,0.5);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .browser-close {
            background: none;
            border: 1px solid rgba(255,255,255,0.1);
            color: rgba(255,255,255,0.4);
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.7rem;
        }
        .browser-close:hover { border-color: rgba(255,255,255,0.3); color: rgba(255,255,255,0.8); }
        .browser-viewport {
            flex: 1;
            position: relative;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #000;
        }
        .browser-viewport img {
            max-width: 100%;
            max-height: 100%;
            cursor: crosshair;
        }
        .browser-loading {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: rgba(255,255,255,0.4);
            font-size: 0.8rem;
        }
        .browser-toolbar {
            padding: 10px 16px;
            border-top: 1px solid rgba(255,255,255,0.06);
            display: flex;
            gap: 8px;
            align-items: center;
        }
        .browser-toolbar input {
            flex: 1;
            padding: 8px 12px;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            font-size: 0.8rem;
            font-family: inherit;
            background: rgba(255,255,255,0.03);
            color: rgba(255,255,255,0.8);
            outline: none;
        }
        .browser-toolbar input:focus { border-color: rgba(255,255,255,0.2); }
        .browser-toolbar button {
            padding: 8px 14px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.75rem;
            color: rgba(255,255,255,0.5);
            font-family: inherit;
        }
        .browser-toolbar button:hover { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.8); }
        .browser-status {
            font-size: 0.7rem;
            color: rgba(255,255,255,0.3);
        }
        .open-browser-btn {
            margin-top: 10px;
            padding: 11px;
            background: rgba(100,200,255,0.1);
            color: rgba(100,200,255,0.9);
            border: 1px solid rgba(100,200,255,0.2);
            border-radius: 4px;
            font-size: 0.75rem;
            font-family: inherit;
            cursor: pointer;
            width: 100%;
        }
        .open-browser-btn:hover { background: rgba(100,200,255,0.15); }
        .main-container.browser-active .sidebar { display: none; }
        .main-container.browser-active .chat-container { max-width: 400px; }
        @media (max-width: 768px) {
            .main-container.browser-active .chat-container { max-width: 100%; }
            .browser-container { position: fixed; top: 53px; left: 0; right: 0; bottom: 0; z-index: 200; }
        }
    </style>
</head>
<body>
    <div class="header"><h1>superstore-use</h1></div>
    <div class="main-container">
        <div class="chat-container">
            <div class="messages" id="messages">
                <div class="message assistant">What would you like to cook or buy?</div>
            </div>
            <div class="suggestions" id="suggestions">
                <span class="suggestion" onclick="sendSuggestion(this)">pasta carbonara</span>
                <span class="suggestion" onclick="sendSuggestion(this)">milk, eggs, bread</span>
                <span class="suggestion" onclick="sendSuggestion(this)">banana pancakes</span>
            </div>
            <div class="input-area">
                <input type="text" id="message-input" placeholder="Message..." onkeypress="handleKeyPress(event)">
                <button id="send-btn" onclick="sendMessage()">Send</button>
            </div>
        </div>
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header" onclick="toggleSidebar()">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <h2>Grocery List</h2>
                    <span class="sidebar-toggle-icon" id="toggle-icon">&#9650;</span>
                </div>
                <span class="item-count" id="item-count">0</span>
            </div>
            <div class="grocery-list" id="grocery-list">
                <div class="empty-list" id="empty-list"><div class="icon">—</div><p>No items yet</p></div>
            </div>
            <div class="sidebar-footer">
                <div class="add-item-form">
                    <input type="text" id="manual-item-input" placeholder="Add item..." onkeypress="handleManualItemKeyPress(event)">
                    <button onclick="addManualItem()">+</button>
                </div>
                <div class="sidebar-actions">
                    <button class="clear-btn" onclick="clearList()">Clear</button>
                    <button class="add-all-btn" id="add-all-btn" onclick="addAllToCart()" disabled>Add to Cart</button>
                </div>
                <button class="open-browser-btn" id="open-browser-btn" onclick="openBrowser()">Open Browser</button>
            </div>
        </div>
        <div class="browser-container" id="browser-container">
            <div class="browser-header">
                <h2>Browser Control</h2>
                <div class="browser-url" id="browser-url">Loading...</div>
                <button class="browser-close" onclick="closeBrowser()">Close</button>
            </div>
            <div class="browser-viewport" id="browser-viewport">
                <div class="browser-loading" id="browser-loading">Starting browser...</div>
                <img id="browser-screenshot" style="display: none;" onclick="handleBrowserClick(event)" />
            </div>
            <div class="browser-toolbar">
                <input type="text" id="browser-input" placeholder="Type here and press Enter..." onkeydown="handleBrowserKeydown(event)" />
                <button onclick="sendBrowserKey('Enter')">Enter</button>
                <button onclick="sendBrowserKey('Tab')">Tab</button>
                <button onclick="sendBrowserKey('Backspace')">Back</button>
                <button onclick="refreshScreenshot()">Refresh</button>
                <span class="browser-status" id="browser-status"></span>
            </div>
        </div>
    </div>
    <script>
        const threadId = 'session-' + Math.random().toString(36).substr(2, 9);
        let isProcessing = false;
        let groceryList = [];
        let currentJobId = null;
        let currentAbortController = null;

        function saveJobId(jobId) { currentJobId = jobId; localStorage.setItem('currentJobId_' + threadId, jobId); localStorage.setItem('currentJobTime_' + threadId, Date.now().toString()); }
        function clearJobId() { currentJobId = null; localStorage.removeItem('currentJobId_' + threadId); localStorage.removeItem('currentJobTime_' + threadId); }
        function getSavedJobId() { const jobId = localStorage.getItem('currentJobId_' + threadId); const jobTime = localStorage.getItem('currentJobTime_' + threadId); if (jobId && jobTime && (Date.now() - parseInt(jobTime)) < 600000) return jobId; return null; }

        async function pollJobStatus(jobId) { try { const r = await fetch(`/api/job/${jobId}/status`); return r.ok ? await r.json() : null; } catch (e) { return null; } }

        function addMessage(content, type) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + type;
            div.textContent = content;
            messages.appendChild(div);
            messages.scrollTop = messages.scrollHeight;
        }

        function setInputEnabled(enabled) {
            document.getElementById('message-input').disabled = !enabled;
            document.getElementById('send-btn').disabled = !enabled;
            document.getElementById('add-all-btn').disabled = !enabled || groceryList.length === 0;
            isProcessing = !enabled;
        }

        function renderGroceryList() {
            const listEl = document.getElementById('grocery-list');
            const emptyEl = document.getElementById('empty-list');
            const countEl = document.getElementById('item-count');
            const addAllBtn = document.getElementById('add-all-btn');
            countEl.textContent = groceryList.length;
            addAllBtn.disabled = isProcessing || groceryList.length === 0;
            countEl.classList.toggle('has-items', groceryList.length > 0);
            if (groceryList.length === 0) { if (emptyEl) emptyEl.style.display = 'block'; listEl.querySelectorAll('.grocery-item').forEach(el => el.remove()); return; }
            if (emptyEl) emptyEl.style.display = 'none';
            listEl.innerHTML = '';
            groceryList.forEach((item, index) => {
                const itemEl = document.createElement('div');
                itemEl.className = 'grocery-item';
                itemEl.innerHTML = `<div class="item-info"><div class="item-name">${escapeHtml(item.name)}</div><div class="item-qty">Qty: ${item.qty}</div></div><div class="edit-qty"><button class="qty-btn" onclick="updateQty(${index}, -1)">−</button><span class="qty-display">${item.qty}</span><button class="qty-btn" onclick="updateQty(${index}, 1)">+</button></div><button class="remove-btn" onclick="removeItem(${index})">×</button>`;
                listEl.appendChild(itemEl);
            });
        }

        function escapeHtml(text) { const div = document.createElement('div'); div.textContent = text; return div.innerHTML; }
        function addToGroceryList(name, qty = 1) { const existing = groceryList.find(item => item.name.toLowerCase() === name.toLowerCase()); if (existing) existing.qty += qty; else groceryList.push({ name, qty }); renderGroceryList(); saveListToStorage(); if (window.innerWidth <= 768) { document.getElementById('sidebar').classList.add('expanded'); setTimeout(() => document.getElementById('sidebar').classList.remove('expanded'), 2000); } }
        function removeItem(index) { groceryList.splice(index, 1); renderGroceryList(); saveListToStorage(); }
        function updateQty(index, delta) { groceryList[index].qty += delta; if (groceryList[index].qty <= 0) removeItem(index); else { renderGroceryList(); saveListToStorage(); } }
        function clearList() { if (groceryList.length === 0) return; if (confirm('Clear all items?')) { groceryList = []; renderGroceryList(); saveListToStorage(); } }
        function addManualItem() { const input = document.getElementById('manual-item-input'); const name = input.value.trim(); if (name) { addToGroceryList(name, 1); input.value = ''; } }
        function handleManualItemKeyPress(event) { if (event.key === 'Enter') addManualItem(); }
        function saveListToStorage() { localStorage.setItem('groceryList_' + threadId, JSON.stringify(groceryList)); }
        function loadListFromStorage() { const saved = localStorage.getItem('groceryList_' + threadId); if (saved) { groceryList = JSON.parse(saved); renderGroceryList(); } }

        function parseItemsFromResponse(text) {
            const items = [];
            const lines = text.split(/\\r?\\n|\\r/);
            for (const line of lines) {
                const trimmed = line.trim();
                const bulletMatch = trimmed.match(/^[-•*]\\s+(.+)$/) || trimmed.match(/^\\d+[.)]\\s+(.+)$/);
                if (bulletMatch) {
                    let itemText = bulletMatch[1].trim();
                    let qty = 1;
                    const qtyPatterns = [/^(\\d+)\\s*x\\s+(.+)$/i, /^(.+?)\\s*x\\s*(\\d+)$/i, /^(.+?)\\s*\\((\\d+)\\)$/];
                    for (const pat of qtyPatterns) { const m = itemText.match(pat); if (m) { if (/^\\d+$/.test(m[1])) { qty = parseInt(m[1]); itemText = m[2]; } else { qty = parseInt(m[2]); itemText = m[1]; } break; } }
                    itemText = itemText.replace(/\\*\\*/g, '').replace(/[,;:]$/, '').trim();
                    if (itemText.length > 0 && itemText.length < 100) items.push({ name: itemText, qty });
                }
            }
            return items;
        }

        let itemStepProgress = {};
        let loginProgress = null;

        function handleStreamEvent(event, progressDiv, itemsProcessed) {
            const eventType = event.type || '';
            switch (eventType) {
                case 'job_id': saveJobId(event.job_id); break;
                case 'message': progressDiv.remove(); addMessage(event.content, 'assistant'); clearJobId(); parseItemsFromResponse(event.content).forEach(item => addToGroceryList(item.name, item.qty)); break;
                case 'error': progressDiv.remove(); addMessage('Error: ' + event.message, 'error'); clearJobId(); break;
                case 'done': if (progressDiv.parentNode) progressDiv.remove(); itemStepProgress = {}; loginProgress = null; clearJobId(); break;
                case 'status': progressDiv.innerHTML = `<span style="opacity: 0.7;">${escapeHtml(event.message || 'Processing...')}</span>`; break;
                case 'login_start': loginProgress = { step: 0, thinking: null, next_goal: null }; updateProgressDisplay(progressDiv, itemsProcessed); break;
                case 'login_step': loginProgress = { step: event.step || 0, thinking: event.thinking || null, next_goal: event.next_goal || null }; updateProgressDisplay(progressDiv, itemsProcessed); break;
                case 'login_complete':
                    loginProgress = null;
                    if (event.status === 'success') {
                        updateProgressDisplay(progressDiv, itemsProcessed);
                    }
                    break;
                case 'item_start': itemStepProgress[event.item] = { step: 0, action: 'Starting...', thinking: null, next_goal: null }; updateProgressDisplay(progressDiv, itemsProcessed); break;
                case 'step': itemStepProgress[event.item] = { step: event.step || 0, action: event.action || '...', thinking: event.thinking || null, next_goal: event.next_goal || null }; updateProgressDisplay(progressDiv, itemsProcessed); break;
                case 'item_complete':
                    delete itemStepProgress[event.item];
                    const icon = event.status === 'success' ? '<span style="color: #4ade80;">&#10003;</span>' : event.status === 'uncertain' ? '<span style="color: #fbbf24;">?</span>' : '<span style="color: #f87171;">&#10007;</span>';
                    itemsProcessed.push({ item: event.item, status: event.status, icon: icon, steps: event.steps || 0 });
                    updateProgressDisplay(progressDiv, itemsProcessed);
                    break;
                case 'complete':
                    progressDiv.innerHTML = `<span style="opacity: 0.7;">${escapeHtml(event.message || 'Complete')}</span>`;
                    // Auto-open browser when items are added
                    if (event.success_count > 0) {
                        setTimeout(() => {
                            addMessage('Items added! Opening browser for you to review and checkout...', 'assistant');
                            openBrowser();
                        }, 1000);
                    }
                    break;
            }
            document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
        }

        function updateProgressDisplay(progressDiv, itemsProcessed) {
            let html = '<div style="font-size: 0.85rem;">';

            // Show login progress if active
            if (loginProgress) {
                let statusText = loginProgress.step > 0 ? `Step ${loginProgress.step}` : 'Starting';
                let thinkingText = loginProgress.next_goal ? loginProgress.next_goal.substring(0, 60) : (loginProgress.thinking ? loginProgress.thinking.substring(0, 60) : null);
                html += `<div style="opacity: 0.7; margin-bottom: 8px;"><span class="typing-indicator" style="display: inline-block; vertical-align: middle; margin-right: 6px; padding: 0;"><span></span><span></span><span></span></span><strong>Logging in</strong> <span style="font-size: 0.7rem; opacity: 0.6;">${escapeHtml(statusText)}</span>`;
                if (thinkingText) html += `<div style="margin-left: 24px; font-size: 0.75rem; opacity: 0.5; font-style: italic;">${escapeHtml(thinkingText)}${thinkingText.length >= 60 ? '...' : ''}</div>`;
                html += `</div>`;
            }

            const inProgress = Object.keys(itemStepProgress).length;
            const completed = itemsProcessed.length;
            const total = inProgress + completed;
            if (total > 0) html += `<div style="font-size: 0.75rem; opacity: 0.6; margin-bottom: 8px;">Items: ${completed}/${total}</div>`;
            itemsProcessed.forEach(p => { const stepsInfo = p.steps ? ` <span style="opacity: 0.5; font-size: 0.7rem;">(${p.steps} steps)</span>` : ''; html += `<div>${p.icon} ${escapeHtml(p.item)}${stepsInfo}</div>`; });
            for (const [item, progress] of Object.entries(itemStepProgress)) {
                let statusText = progress.step > 0 ? `Step ${progress.step}` : 'Starting';
                let thinkingText = progress.next_goal ? progress.next_goal.substring(0, 60) : (progress.thinking ? progress.thinking.substring(0, 60) : null);
                html += `<div style="opacity: 0.7; margin-bottom: 4px;"><span class="typing-indicator" style="display: inline-block; vertical-align: middle; margin-right: 6px; padding: 0;"><span></span><span></span><span></span></span><strong>${escapeHtml(item)}</strong> <span style="font-size: 0.7rem; opacity: 0.6;">${escapeHtml(statusText)}</span>`;
                if (thinkingText) html += `<div style="margin-left: 24px; font-size: 0.75rem; opacity: 0.5; font-style: italic;">${escapeHtml(thinkingText)}${thinkingText.length >= 60 ? '...' : ''}</div>`;
                html += `</div>`;
            }
            html += '</div>';
            progressDiv.innerHTML = html;
        }

        async function sendMessage() {
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            if (!message || isProcessing) return;
            input.value = '';
            addMessage(message, 'user');
            setInputEnabled(false);
            document.getElementById('suggestions').style.display = 'none';

            // Clear any existing polling from restored job status
            if (pollingInterval) {
                clearInterval(pollingInterval);
                pollingInterval = null;
            }

            const progressDiv = document.createElement('div');
            progressDiv.className = 'message assistant';
            progressDiv.id = 'current-progress';
            progressDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
            document.getElementById('messages').appendChild(progressDiv);

            // Create AbortController for this request
            currentAbortController = new AbortController();
            const abortSignal = currentAbortController.signal;

            try {
                const response = await fetch('/api/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ thread_id: threadId, message: message }),
                    signal: abortSignal
                });
                if (!response.ok) throw new Error(`HTTP error: ${response.status}`);
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let itemsProcessed = [];

                try {
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\\n\\n');
                        buffer = lines.pop();
                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                try { handleStreamEvent(JSON.parse(line.slice(6)), progressDiv, itemsProcessed); } catch (e) { console.error('Parse error:', e); }
                            }
                        }
                    }
                } catch (streamError) {
                    // Handle stream reading errors (e.g., network interruptions, backgrounding)
                    if (streamError.name === 'AbortError') {
                        console.log('Stream aborted');
                    } else {
                        throw streamError;
                    }
                } finally {
                    // Always cancel the reader when done
                    try { await reader.cancel(); } catch (e) { /* ignore */ }
                }
            } catch (error) {
                // Only show error message if it wasn't an intentional abort
                if (error.name !== 'AbortError') {
                    progressDiv.remove();
                    addMessage('Error: ' + error.message, 'error');
                    clearJobId();
                } else {
                    progressDiv.remove();
                }
            } finally {
                currentAbortController = null;
                setInputEnabled(true);
                document.getElementById('message-input').focus();
            }
        }

        async function addAllToCart() {
            if (groceryList.length === 0 || isProcessing) return;
            const itemList = groceryList.map(item => item.qty > 1 ? `${item.qty}x ${item.name}` : item.name).join(', ');
            const message = `Please add these items to my Superstore cart: ${itemList}`;
            document.getElementById('message-input').value = message;
            sendMessage();
        }

        function sendSuggestion(el) { document.getElementById('message-input').value = el.textContent; sendMessage(); }
        function handleKeyPress(event) { if (event.key === 'Enter' && !isProcessing) sendMessage(); }
        function toggleSidebar() { document.getElementById('sidebar').classList.toggle('expanded'); }

        document.addEventListener('click', function(e) { const sidebar = document.getElementById('sidebar'); if (window.innerWidth <= 768 && sidebar.classList.contains('expanded') && !sidebar.contains(e.target)) sidebar.classList.remove('expanded'); });

        // Handle page visibility changes (mobile backgrounding)
        let pollingInterval = null;

        function displayJobState(job, progressDiv, itemsProcessed) {
            // Restore items_processed from job state
            if (job.items_processed && job.items_processed.length > 0) {
                itemsProcessed.length = 0; // Clear existing
                job.items_processed.forEach(p => {
                    const icon = p.status === 'success' ? '<span style="color: #4ade80;">&#10003;</span>' : p.status === 'uncertain' ? '<span style="color: #fbbf24;">?</span>' : '<span style="color: #f87171;">&#10007;</span>';
                    itemsProcessed.push({ item: p.item, status: p.status, icon: icon, steps: p.steps || 0 });
                });
            }
            // Restore items_in_progress
            itemStepProgress = {};
            if (job.items_in_progress) {
                for (const [item, progress] of Object.entries(job.items_in_progress)) {
                    itemStepProgress[item] = { step: progress.step || 0, action: progress.action || '...', thinking: null, next_goal: null };
                }
            }
            updateProgressDisplay(progressDiv, itemsProcessed);
        }

        async function restoreJobStatus() {
            const savedJobId = getSavedJobId();
            if (!savedJobId || isProcessing) return;

            console.log('Restoring job status for:', savedJobId);
            const job = await pollJobStatus(savedJobId);
            if (!job) {
                clearJobId();
                return;
            }

            // Create progress div if job is still relevant
            let progressDiv = document.getElementById('current-progress');
            let itemsProcessed = [];

            if (!progressDiv) {
                progressDiv = document.createElement('div');
                progressDiv.className = 'message assistant';
                progressDiv.id = 'current-progress';
                progressDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
                document.getElementById('messages').appendChild(progressDiv);
            }

            if (job.status === 'running') {
                setInputEnabled(false);
                displayJobState(job, progressDiv, itemsProcessed);

                // Start polling for updates
                if (pollingInterval) clearInterval(pollingInterval);
                pollingInterval = setInterval(async () => {
                    const updatedJob = await pollJobStatus(savedJobId);
                    if (!updatedJob) {
                        clearInterval(pollingInterval);
                        pollingInterval = null;
                        clearJobId();
                        setInputEnabled(true);
                        return;
                    }

                    if (updatedJob.status === 'running') {
                        displayJobState(updatedJob, progressDiv, itemsProcessed);
                    } else {
                        clearInterval(pollingInterval);
                        pollingInterval = null;

                        if (updatedJob.status === 'completed') {
                            if (updatedJob.final_message) {
                                progressDiv.remove();
                                addMessage(updatedJob.final_message, 'assistant');
                                parseItemsFromResponse(updatedJob.final_message).forEach(item => addToGroceryList(item.name, item.qty));
                            } else {
                                const successCount = updatedJob.success_count || itemsProcessed.length;
                                progressDiv.innerHTML = `<span style="opacity: 0.7;">Complete - ${successCount} items added to cart</span>`;
                            }
                        } else if (updatedJob.status === 'error') {
                            progressDiv.remove();
                            addMessage('Error: ' + (updatedJob.error || 'Unknown error'), 'error');
                        } else if (updatedJob.status === 'expired') {
                            progressDiv.innerHTML = '<span style="opacity: 0.7;">Job expired</span>';
                        }
                        clearJobId();
                        setInputEnabled(true);
                        itemStepProgress = {};
                    }
                }, 2000);
            } else if (job.status === 'completed') {
                if (job.final_message) {
                    progressDiv.remove();
                    addMessage(job.final_message, 'assistant');
                    parseItemsFromResponse(job.final_message).forEach(item => addToGroceryList(item.name, item.qty));
                } else {
                    displayJobState(job, progressDiv, itemsProcessed);
                    const successCount = job.success_count || itemsProcessed.length;
                    progressDiv.innerHTML = `<span style="opacity: 0.7;">Complete - ${successCount} items added to cart</span>`;
                }
                clearJobId();
                setInputEnabled(true);
            } else if (job.status === 'error') {
                progressDiv.remove();
                addMessage('Error: ' + (job.error || 'Unknown error'), 'error');
                clearJobId();
                setInputEnabled(true);
            } else {
                // expired or unknown status
                progressDiv.innerHTML = '<span style="opacity: 0.7;">Job expired or unavailable</span>';
                clearJobId();
                setInputEnabled(true);
            }
        }

        document.addEventListener('visibilitychange', function() {
            if (document.hidden && currentAbortController) {
                console.log('Page hidden, aborting active stream');
                currentAbortController.abort();
            } else if (!document.hidden) {
                // Page became visible - check for active job and restore status
                restoreJobStatus();
            }
        });

        document.getElementById('message-input').focus();
        renderGroceryList();

        // =====================================================================
        // Browser Control Functions
        // =====================================================================
        let browserSessionId = null;
        let screenshotInterval = null;
        const SCREENSHOT_INTERVAL = 1500; // ms between screenshot refreshes

        async function openBrowser() {
            const container = document.getElementById('browser-container');
            const mainContainer = document.querySelector('.main-container');
            const loading = document.getElementById('browser-loading');
            const screenshot = document.getElementById('browser-screenshot');
            const urlDisplay = document.getElementById('browser-url');
            const statusEl = document.getElementById('browser-status');

            // Show browser panel
            container.classList.add('active');
            mainContainer.classList.add('browser-active');
            loading.style.display = 'block';
            screenshot.style.display = 'none';
            statusEl.textContent = 'Starting browser...';

            try {
                // Start browser session
                const response = await fetch('/api/browser/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: 'https://www.realcanadiansuperstore.ca/cart' })
                });

                const data = await response.json();
                if (data.status !== 'success') {
                    throw new Error(data.message || 'Failed to start browser');
                }

                browserSessionId = data.session_id;
                statusEl.textContent = 'Connected';

                // Start screenshot polling
                await refreshScreenshot();
                startScreenshotPolling();

            } catch (error) {
                console.error('Browser start error:', error);
                statusEl.textContent = 'Error: ' + error.message;
                loading.textContent = 'Failed to start browser: ' + error.message;
            }
        }

        function closeBrowser() {
            const container = document.getElementById('browser-container');
            const mainContainer = document.querySelector('.main-container');

            stopScreenshotPolling();
            container.classList.remove('active');
            mainContainer.classList.remove('browser-active');
            browserSessionId = null;
        }

        function startScreenshotPolling() {
            stopScreenshotPolling();
            screenshotInterval = setInterval(refreshScreenshot, SCREENSHOT_INTERVAL);
        }

        function stopScreenshotPolling() {
            if (screenshotInterval) {
                clearInterval(screenshotInterval);
                screenshotInterval = null;
            }
        }

        async function refreshScreenshot() {
            if (!browserSessionId) return;

            const loading = document.getElementById('browser-loading');
            const screenshot = document.getElementById('browser-screenshot');
            const urlDisplay = document.getElementById('browser-url');
            const statusEl = document.getElementById('browser-status');

            try {
                // Get screenshot
                const response = await fetch(`/api/browser/${browserSessionId}/screenshot`);
                const data = await response.json();

                if (data.screenshot) {
                    screenshot.src = 'data:image/png;base64,' + data.screenshot;
                    screenshot.style.display = 'block';
                    loading.style.display = 'none';
                }

                // Get state for URL
                const stateResponse = await fetch(`/api/browser/${browserSessionId}/state`);
                const stateData = await stateResponse.json();
                if (stateData.url) {
                    urlDisplay.textContent = stateData.url;
                }

            } catch (error) {
                console.error('Screenshot error:', error);
                statusEl.textContent = 'Error refreshing';
            }
        }

        async function handleBrowserClick(event) {
            if (!browserSessionId) return;

            const img = event.target;
            const rect = img.getBoundingClientRect();

            // Calculate click position relative to the image
            const scaleX = 1280 / img.clientWidth;  // Browser viewport width
            const scaleY = 800 / img.clientHeight;  // Browser viewport height
            const x = Math.round((event.clientX - rect.left) * scaleX);
            const y = Math.round((event.clientY - rect.top) * scaleY);

            const statusEl = document.getElementById('browser-status');
            statusEl.textContent = `Clicking (${x}, ${y})...`;

            try {
                const response = await fetch(`/api/browser/${browserSessionId}/click`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ x, y })
                });

                const data = await response.json();
                if (data.status === 'success') {
                    statusEl.textContent = 'Clicked';
                    // Refresh screenshot after click
                    setTimeout(refreshScreenshot, 500);
                } else {
                    statusEl.textContent = 'Click failed';
                }
            } catch (error) {
                console.error('Click error:', error);
                statusEl.textContent = 'Click error';
            }
        }

        async function handleBrowserKeydown(event) {
            if (!browserSessionId) return;

            const input = document.getElementById('browser-input');

            if (event.key === 'Enter') {
                event.preventDefault();
                const text = input.value;
                if (text) {
                    await sendBrowserText(text);
                    input.value = '';
                }
                await sendBrowserKey('Enter');
            }
        }

        async function sendBrowserText(text) {
            if (!browserSessionId || !text) return;

            const statusEl = document.getElementById('browser-status');
            statusEl.textContent = 'Typing...';

            try {
                const response = await fetch(`/api/browser/${browserSessionId}/type`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text })
                });

                const data = await response.json();
                statusEl.textContent = data.status === 'success' ? 'Typed' : 'Type failed';
                setTimeout(refreshScreenshot, 300);
            } catch (error) {
                console.error('Type error:', error);
                statusEl.textContent = 'Type error';
            }
        }

        async function sendBrowserKey(key) {
            if (!browserSessionId) return;

            const statusEl = document.getElementById('browser-status');
            statusEl.textContent = `Pressing ${key}...`;

            try {
                const response = await fetch(`/api/browser/${browserSessionId}/key`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key })
                });

                const data = await response.json();
                statusEl.textContent = data.status === 'success' ? `Pressed ${key}` : 'Key failed';
                setTimeout(refreshScreenshot, 500);
            } catch (error) {
                console.error('Key error:', error);
                statusEl.textContent = 'Key error';
            }
        }

        // Handle scroll on browser viewport
        document.getElementById('browser-viewport').addEventListener('wheel', async (event) => {
            if (!browserSessionId) return;
            event.preventDefault();

            const statusEl = document.getElementById('browser-status');
            statusEl.textContent = 'Scrolling...';

            try {
                const response = await fetch(`/api/browser/${browserSessionId}/scroll`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ deltaX: event.deltaX, deltaY: event.deltaY })
                });

                const data = await response.json();
                statusEl.textContent = data.status === 'success' ? 'Scrolled' : 'Scroll failed';
                setTimeout(refreshScreenshot, 300);
            } catch (error) {
                console.error('Scroll error:', error);
                statusEl.textContent = 'Scroll error';
            }
        }, { passive: false });
    </script>
</body>
</html>"""

    @flask_app.route("/")
    def index():
        return CHAT_HTML

    @flask_app.route("/api/chat/stream", methods=["POST"])
    def chat_stream():
        """Handle chat messages with SSE streaming for progress updates."""
        import asyncio
        import queue
        import threading

        data = request.json
        thread_id = data.get("thread_id")
        message = data.get("message")

        if not thread_id or not message:
            return jsonify({"error": "Missing thread_id or message"}), 400

        job_id = create_job(thread_id, message)
        event_queue = queue.Queue()

        def run_agent_async():
            try:
                agent = get_or_create_agent(thread_id)
                config = {"configurable": {"thread_id": thread_id}}

                async def stream_agent():
                    final_content = None
                    async for chunk in agent.astream(
                        {"messages": [HumanMessage(content=message)]},
                        config=config,
                        stream_mode=["updates", "custom"],
                    ):
                        if isinstance(chunk, tuple) and len(chunk) == 2:
                            mode, chunk_data = chunk
                            if mode == "custom" and isinstance(chunk_data, dict) and "progress" in chunk_data:
                                progress_event = chunk_data["progress"]
                                event_queue.put(progress_event)
                                update_job_progress(job_id, progress_event)
                            elif mode == "updates" and isinstance(chunk_data, dict) and "chat" in chunk_data:
                                msgs = chunk_data["chat"].get("messages", [])
                                for msg in msgs:
                                    if isinstance(msg, AIMessage):
                                        final_content = msg.content
                    return final_content

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    final_content = loop.run_until_complete(stream_agent())
                finally:
                    loop.close()

                if final_content:
                    msg_event = {"type": "message", "content": final_content}
                    event_queue.put(msg_event)
                    update_job_progress(job_id, msg_event)

                event_queue.put({"type": "done"})
                update_job_progress(job_id, {"type": "complete"})

            except Exception as e:
                import traceback
                print(f"[ChatStream] Error: {e}")
                print(f"[ChatStream] Traceback: {traceback.format_exc()}")
                event_queue.put({"type": "error", "message": str(e)})
                event_queue.put({"type": "done"})
                update_job_progress(job_id, {"type": "error", "message": str(e)})

        def generate():
            yield f"data: {json.dumps({'type': 'job_id', 'job_id': job_id})}\n\n"
            agent_thread = threading.Thread(target=run_agent_async)
            agent_thread.start()
            while True:
                try:
                    event = event_queue.get(timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("done", "error"):
                        break
                except queue.Empty:
                    if not agent_thread.is_alive():
                        break
                    yield ": keepalive\n\n"
            agent_thread.join(timeout=5.0)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @flask_app.route("/api/job/<job_id>/status", methods=["GET"])
    def job_status(job_id):
        job = get_job_status(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(job)

    @flask_app.route("/api/reset", methods=["POST"])
    def reset():
        data = request.json
        thread_id = data.get("thread_id")
        if thread_id in agents:
            del agents[thread_id]
        return jsonify({"status": "reset"})

    @flask_app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    # =========================================================================
    # Browser Control API Endpoints
    # =========================================================================

    # Store browser session references
    browser_sessions = {}

    @flask_app.route("/api/browser/start", methods=["POST"])
    def browser_start():
        """Start a new browser session and navigate to cart."""
        import asyncio

        data = request.json or {}
        session_id = data.get("session_id", str(uuid.uuid4())[:8])
        url = data.get("url", "https://www.realcanadiansuperstore.ca/cart")

        try:
            # Get the BrowserSession class from Modal
            BrowserSessionCls = modal.Cls.from_name(MODAL_APP_NAME, "BrowserSession")
            browser = BrowserSessionCls()

            # Store reference
            browser_sessions[session_id] = browser

            # Navigate to the specified URL
            result = browser.navigate.remote(url)

            return jsonify({
                "status": "success",
                "session_id": session_id,
                "navigate_result": result,
            })
        except Exception as e:
            import traceback
            print(f"[Browser] Start error: {e}")
            print(f"[Browser] Traceback: {traceback.format_exc()}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @flask_app.route("/api/browser/<session_id>/screenshot")
    def browser_screenshot(session_id):
        """Get current screenshot as PNG."""
        import base64

        browser = browser_sessions.get(session_id)
        if not browser:
            return jsonify({"error": "Session not found"}), 404

        try:
            screenshot_bytes = browser.get_screenshot.remote()
            if not screenshot_bytes:
                return jsonify({"error": "Failed to get screenshot"}), 500

            # Return as base64 for easy embedding
            b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return jsonify({"screenshot": b64})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/browser/<session_id>/screenshot.png")
    def browser_screenshot_png(session_id):
        """Get current screenshot as raw PNG."""
        browser = browser_sessions.get(session_id)
        if not browser:
            return "", 404

        try:
            screenshot_bytes = browser.get_screenshot.remote()
            if not screenshot_bytes:
                return "", 500
            return Response(screenshot_bytes, mimetype="image/png")
        except Exception as e:
            return "", 500

    @flask_app.route("/api/browser/<session_id>/state")
    def browser_state(session_id):
        """Get current browser state."""
        browser = browser_sessions.get(session_id)
        if not browser:
            return jsonify({"error": "Session not found"}), 404

        try:
            state = browser.get_state.remote()
            return jsonify(state)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/browser/<session_id>/click", methods=["POST"])
    def browser_click(session_id):
        """Click at coordinates."""
        browser = browser_sessions.get(session_id)
        if not browser:
            return jsonify({"error": "Session not found"}), 404

        data = request.json
        x = data.get("x", 0)
        y = data.get("y", 0)

        try:
            result = browser.click.remote(int(x), int(y))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/browser/<session_id>/type", methods=["POST"])
    def browser_type(session_id):
        """Type text."""
        browser = browser_sessions.get(session_id)
        if not browser:
            return jsonify({"error": "Session not found"}), 404

        data = request.json
        text = data.get("text", "")

        try:
            result = browser.type_text.remote(text)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/browser/<session_id>/key", methods=["POST"])
    def browser_key(session_id):
        """Press a key."""
        browser = browser_sessions.get(session_id)
        if not browser:
            return jsonify({"error": "Session not found"}), 404

        data = request.json
        key = data.get("key", "Enter")

        try:
            result = browser.press_key.remote(key)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/browser/<session_id>/scroll", methods=["POST"])
    def browser_scroll(session_id):
        """Scroll the page."""
        browser = browser_sessions.get(session_id)
        if not browser:
            return jsonify({"error": "Session not found"}), 404

        data = request.json
        delta_x = data.get("deltaX", 0)
        delta_y = data.get("deltaY", 0)

        try:
            result = browser.scroll.remote(int(delta_x), int(delta_y))
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/browser/<session_id>/navigate", methods=["POST"])
    def browser_navigate(session_id):
        """Navigate to a URL."""
        browser = browser_sessions.get(session_id)
        if not browser:
            return jsonify({"error": "Session not found"}), 404

        data = request.json
        url = data.get("url", "")

        try:
            result = browser.navigate.remote(url)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return flask_app
