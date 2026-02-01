"""
Modal deployment for the Superstore Shopping Agent.

Single unified deployment with:
- Core browser automation functions (login, add items)
- Chat-based web UI with LangGraph agent

Deploy with: modal deploy modal/app.py
Run locally: modal serve modal/app.py
"""

import asyncio
import json
import os
import threading
import uuid

import modal

# =============================================================================
# Modal App Configuration
# =============================================================================
# Import config first to get app name
from src.core.config import load_config

_config = load_config()

app = modal.App(_config.app.name)

# Persistent volume for storing session cookies
session_volume = modal.Volume.from_name("superstore-session", create_if_missing=True)

# Distributed Dict for storing job state (persists across function invocations)
job_state_dict = modal.Dict.from_name("superstore-job-state", create_if_missing=True)


# =============================================================================
# Shared Configuration (imported from core module)
# =============================================================================

# Import shared config from core module
from src.core.browser import create_browser, start_xvfb
from src.core.success import detect_success_from_history

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
        # Xvfb for running browser non-headless in virtual display
        "xvfb",
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
    # Add src module for shared utilities
    .add_local_dir("src", remote_path="/root/src", copy=True)
    # Copy the local browser profile directory as a fallback profile
    .add_local_dir(
        "superstore-profile",
        remote_path="/app/superstore-profile",
        copy=True,
    )
    # Add config file and prompts directory
    .add_local_file("config.toml", remote_path="/app/config.toml", copy=True)
    .add_local_dir("src/prompts", remote_path="/app/prompts", copy=True)
)

# Lighter image for chat UI (doesn't need browser)
chat_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "flask",
        "langchain-core",
        "langchain-groq",
        "langgraph",
        "pydantic",
        "python-dotenv",
        "modal",
    )
    .add_local_dir("src", remote_path="/root/src", copy=True)
    .add_local_file("config.toml", remote_path="/app/config.toml", copy=True)
    .add_local_dir("src/prompts", remote_path="/app/prompts", copy=True)
    .add_local_dir("modal/templates", remote_path="/app/templates", copy=True)
    .add_local_dir("modal/static", remote_path="/app/static", copy=True)
)


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
        "TIMEOUT_BrowserStartEvent": str(_config.browser.timeout_browser_start),
        "TIMEOUT_BrowserLaunchEvent": str(_config.browser.timeout_browser_launch),
        "TIMEOUT_BrowserStateRequestEvent": str(_config.browser.timeout_browser_state_request),
        "IN_DOCKER": "True",
    },
    cpu=2,
    memory=4096,
)
def login_remote_streaming():
    """Streaming version of login that yields progress events."""
    import queue

    from browser_use import Agent, ChatGroq

    # Start xvfb for non-headless browser in Modal
    start_xvfb()

    config = load_config()
    step_events: queue.Queue[dict] = queue.Queue()
    result_holder: dict[str, str | dict | None] = {"result": None, "error": None}

    async def _login():
        username = os.environ.get("SUPERSTORE_USER")
        password = os.environ.get("SUPERSTORE_PASSWORD")

        if not username or not password:
            return {"status": "failed", "message": "Missing credentials"}

        print("[Login] Starting login process on Modal...")
        # Use longer delays for login to handle auth server latency
        browser = create_browser(shared_profile=True, task_type="login")
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

            step_events.put(
                {
                    "type": "step",
                    "step": step_count,
                    "thinking": thinking,
                    "next_goal": next_goal,
                }
            )

        try:
            # Load login prompt from config
            try:
                task = config.load_prompt(
                    "login",
                    base_url=config.app.base_url,
                    username=username,
                    password=password,
                )
            except FileNotFoundError:
                # Fallback to inline prompt
                task = f"""
                Navigate to {config.app.base_url} and log in.
                Steps:
                1. Go to {config.app.base_url}
                2. Check if "Sign In" appears. If not, return success.
                3. Click "Sign in" at top right.
                4. If you see an email address ({username}) displayed, click on it.
                5. Otherwise, enter username: {username} and password: {password}
                6. Click the sign in button and wait for login to complete.
                Complete when logged in successfully.
                """

            agent = Agent(
                task=task,
                llm=ChatGroq(model=config.llm.browser_model),
                use_vision=config.llm.browser_use_vision,
                browser_session=browser,
            )

            await agent.run(max_steps=config.agent.max_steps_login, on_step_end=on_step_end)

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
        yield json.dumps(
            {
                "type": "complete",
                "status": "failed",
                "message": result_holder["error"],
                "steps": 0,
            }
        )
    elif result_holder["result"] and isinstance(result_holder["result"], dict):
        yield json.dumps({"type": "complete", **result_holder["result"]})
    else:
        yield json.dumps(
            {
                "type": "complete",
                "status": "failed",
                "message": "Unknown error",
                "steps": 0,
            }
        )


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
        "TIMEOUT_BrowserStartEvent": str(_config.browser.timeout_browser_start),
        "TIMEOUT_BrowserLaunchEvent": str(_config.browser.timeout_browser_launch),
        "TIMEOUT_BrowserStateRequestEvent": str(_config.browser.timeout_browser_state_request),
        "IN_DOCKER": "True",
    },
    cpu=2,
    memory=4096,
)
def add_item_remote_streaming(item: str, index: int):
    """Generator version that yields JSON progress events in real-time."""
    import queue

    from browser_use import Agent, ChatGroq

    # Start xvfb for non-headless browser in Modal
    start_xvfb()

    config = load_config()
    step_events: queue.Queue[dict] = queue.Queue()
    result_holder: dict[str, str | dict | None] = {"result": None, "error": None}

    async def _add_item():
        print(f"[Container {index}] Starting to add item: {item}")
        browser = create_browser(shared_profile=True, task_type="add_item")
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

            step_events.put(
                {
                    "type": "step",
                    "item": item,
                    "index": index,
                    "step": step_count,
                    "action": action_str,
                    "thinking": thinking,
                    "evaluation": evaluation,
                    "next_goal": next_goal,
                }
            )

        try:
            # Load add_item prompt from config
            try:
                task = config.load_prompt(
                    "add_item",
                    item=item,
                    base_url=config.app.base_url,
                )
            except FileNotFoundError:
                # Fallback to inline prompt
                task = f"""
                Add "{item}" to the shopping cart on Real Canadian Superstore.
                Go to {config.app.base_url}
                Steps:
                1. Search for the product
                2. Select the most relevant item
                3. Click "Add to Cart"
                Complete when the item is added to cart.
                """

            agent = Agent(
                task=task,
                llm=ChatGroq(model=config.llm.browser_model),
                use_vision=config.llm.browser_use_vision,
                browser_session=browser,
            )

            await agent.run(max_steps=config.agent.max_steps_add_item, on_step_end=on_step_end)

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
        yield json.dumps(
            {
                "type": "complete",
                "item": item,
                "index": index,
                "status": "failed",
                "message": result_holder["error"],
                "steps": 0,
            }
        )
    elif result_holder["result"] and isinstance(result_holder["result"], dict):
        yield json.dumps({"type": "complete", **result_holder["result"]})
    else:
        yield json.dumps(
            {
                "type": "complete",
                "item": item,
                "index": index,
                "status": "failed",
                "message": "Unknown error",
                "steps": 0,
            }
        )


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
        "TIMEOUT_BrowserStartEvent": str(_config.browser.timeout_browser_start),
        "TIMEOUT_BrowserLaunchEvent": str(_config.browser.timeout_browser_launch),
        "TIMEOUT_BrowserStateRequestEvent": str(_config.browser.timeout_browser_state_request),
        "IN_DOCKER": "True",
    },
    cpu=2,
    memory=4096,
)
def view_cart_remote_streaming():
    """Generator version that yields JSON progress events for viewing cart contents."""
    import queue

    from browser_use import Agent, ChatGroq

    # Start xvfb for non-headless browser in Modal
    start_xvfb()

    config = load_config()
    step_events: queue.Queue[dict] = queue.Queue()
    result_holder: dict[str, str | dict | None] = {"result": None, "error": None}

    async def _view_cart():
        print("[ViewCart] Starting to view cart contents...")
        browser = create_browser(shared_profile=True, task_type="add_item")
        step_count = 0
        cart_contents = ""

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

            step_events.put(
                {
                    "type": "step",
                    "step": step_count,
                    "thinking": thinking,
                    "next_goal": next_goal,
                }
            )

        try:
            cart_url = f"{config.app.base_url}/cartReview"

            try:
                task = config.load_prompt(
                    "view_cart",
                    cart_url=cart_url,
                )
            except FileNotFoundError:
                task = f"""
                Navigate to {cart_url} and extract all items in the shopping cart.
                Return a bullet point list of all items with quantities and prices.
                If the cart is empty, return "Your cart is empty."
                """

            agent = Agent(
                task=task,
                llm=ChatGroq(model=config.llm.browser_model),
                use_vision=config.llm.browser_use_vision,
                browser_session=browser,
            )

            await agent.run(max_steps=config.agent.max_steps_view_cart, on_step_end=on_step_end)

            # Extract cart contents from agent's extracted_content (primary source)
            extracted = agent.history.extracted_content()
            if extracted:
                cart_contents = "\n".join(extracted)

            # Fallback: check model outputs for any relevant text
            if not cart_contents:
                model_outputs = agent.history.model_outputs()
                if model_outputs:
                    last_output = model_outputs[-1]
                    if last_output.thinking:
                        cart_contents = last_output.thinking

            return {
                "status": "success",
                "cart_contents": cart_contents or "Unable to extract cart contents.",
                "steps": step_count,
            }

        except Exception as e:
            return {
                "status": "failed",
                "message": str(e),
                "cart_contents": "",
                "steps": step_count,
            }
        finally:
            await browser.kill()

    def run_async():
        try:
            result_holder["result"] = asyncio.run(_view_cart())
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
        yield json.dumps(
            {
                "type": "complete",
                "status": "failed",
                "message": result_holder["error"],
                "cart_contents": "",
                "steps": 0,
            }
        )
    elif result_holder["result"] and isinstance(result_holder["result"], dict):
        yield json.dumps({"type": "complete", **result_holder["result"]})
    else:
        yield json.dumps(
            {
                "type": "complete",
                "status": "failed",
                "message": "Unknown error",
                "cart_contents": "",
                "steps": 0,
            }
        )


# =============================================================================
# Profile Upload (Local -> Modal Volume)
# =============================================================================
@app.function(
    image=chat_image,
    volumes={"/session": session_volume},
    timeout=300,
)
def _write_profile_to_volume(files: list[tuple[str, bytes]]):
    """Write profile files to the Modal volume.

    This function runs remotely on Modal to write the uploaded files
    to the persistent session volume.

    Args:
        files: List of (relative_path, content) tuples to write.
    """
    from pathlib import Path

    profile_dir = Path("/session/profile")
    profile_dir.mkdir(parents=True, exist_ok=True)

    for relative_path, content in files:
        file_path = profile_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(content)
        print(f"  Wrote: {relative_path} ({len(content)} bytes)")

    # Commit the volume to persist changes
    session_volume.commit()
    print(f"\n[Upload] Successfully wrote {len(files)} files to Modal volume")
    return len(files)


@app.local_entrypoint()
def upload_profile():
    """Upload local browser profile to Modal's persistent volume.

    Run this after logging in locally to sync your authenticated profile
    to Modal, so deployed functions can use your saved login session.

    Usage:
        1. First login locally: uv run -m src.local.cli login
        2. Then upload to Modal: uv run modal run modal/app.py::upload_profile

    This solves the CI/CD deployment issue where the image-embedded profile
    is empty. The Modal volume persists independently of deployments.
    """
    from pathlib import Path

    # Chrome lock files to skip (can't be copied while browser might be running)
    lock_files = {
        "SingletonLock",
        "SingletonCookie",
        "SingletonSocket",
        "lockfile",
        "parent.lock",
    }

    local_profile = Path("./superstore-profile")

    if not local_profile.exists():
        print("[Error] Local profile directory not found: ./superstore-profile")
        print("        Run 'uv run -m src.local.cli login' first to create a profile.")
        return

    # Collect all files from the local profile
    files_to_upload: list[tuple[str, bytes]] = []

    for file_path in local_profile.rglob("*"):
        if file_path.is_file():
            # Skip lock files
            if file_path.name in lock_files:
                print(f"  Skipping lock file: {file_path.name}")
                continue

            relative_path = file_path.relative_to(local_profile)
            content = file_path.read_bytes()
            files_to_upload.append((str(relative_path), content))

    if not files_to_upload:
        print("[Warning] No files found in local profile directory.")
        print("          Run 'uv run -m src.local.cli login' first to create a profile.")
        return

    print(f"[Upload] Uploading {len(files_to_upload)} files from ./superstore-profile to Modal volume...")

    # Call the remote function to write files to the volume
    num_written = _write_profile_to_volume.remote(files_to_upload)

    print(f"\n[Success] Profile uploaded to Modal volume ({num_written} files)")
    print("          Your Modal functions will now use this authenticated profile.")


# =============================================================================
# Chat UI Flask App
# =============================================================================


@app.function(
    image=chat_image,
    secrets=[
        modal.Secret.from_name("groq-secret"),
        modal.Secret.from_name("web-auth", required_keys=["WEB_AUTH_TOKEN"]),
    ],
    timeout=600,
    cpu=1,
    memory=2048,
)
@modal.wsgi_app(label=_config.app.name)  # Change this label to something unique for your deployment
def flask_app():
    """Flask app for the chat UI."""
    import time

    from flask import Flask, Response, jsonify, render_template, request
    from langchain_core.messages import AIMessage, HumanMessage

    from src.core.agent import create_chat_agent

    # Explicitly set template and static folders to match Modal paths
    flask_app = Flask(__name__, template_folder="/app/templates", static_folder="/app/static")

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
            "login_progress": None,
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

            if event_type == "login_start":
                job["login_progress"] = {"step": 0, "thinking": None, "next_goal": None}
            elif event_type == "login_step":
                job["login_progress"] = {
                    "step": event.get("step", 0),
                    "thinking": event.get("thinking"),
                    "next_goal": event.get("next_goal"),
                }
            elif event_type == "login_complete":
                job["login_progress"] = None
            elif event_type == "item_start":
                job["items_in_progress"][event["item"]] = {"step": 0, "action": "Starting..."}
            elif event_type == "step":
                if event.get("item") in job["items_in_progress"]:
                    job["items_in_progress"][event["item"]] = {
                        "step": event.get("step", 0),
                        "action": event.get("action", "..."),
                        "thinking": event.get("thinking"),
                        "next_goal": event.get("next_goal"),
                    }
            elif event_type == "item_complete":
                item_name = event.get("item")
                if item_name in job["items_in_progress"]:
                    del job["items_in_progress"][item_name]
                job["items_processed"].append(
                    {"item": item_name, "status": event.get("status", "unknown"), "steps": event.get("steps", 0)}
                )
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

    # Simple token-based authentication
    def check_auth():
        """Check if request has valid auth token."""
        expected_token = os.environ.get("WEB_AUTH_TOKEN", "")
        if not expected_token:
            return True  # No auth required if token not set

        # Check query param or header
        token = request.args.get("token") or request.headers.get("X-Auth-Token")
        return token == expected_token

    @flask_app.route("/")
    def index():
        if not check_auth():
            return jsonify({"error": "Unauthorized"}), 401
        return render_template("chat.html")

    @flask_app.route("/api/chat/stream", methods=["POST"])
    def chat_stream():
        """Handle chat messages with SSE streaming for progress updates."""
        if not check_auth():
            return jsonify({"error": "Unauthorized"}), 401

        import asyncio
        import queue

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

                # Run the async function properly
                final_content = asyncio.run(stream_agent())

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

    return flask_app
