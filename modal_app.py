import asyncio
import json
import os
import threading
import uuid
from typing import Optional

import modal

app = modal.App("superstore-shopping-agent")

# Create a persistent volume for storing session cookies
session_volume = modal.Volume.from_name("superstore-session", create_if_missing=True)


def get_proxy_config() -> Optional[dict]:
    """Get proxy configuration from environment variables."""
    proxy_server = os.environ.get("PROXY_SERVER")
    proxy_username = os.environ.get("PROXY_USERNAME")
    proxy_password = os.environ.get("PROXY_PASSWORD")

    if not all([proxy_server, proxy_username, proxy_password]):
        return None

    return {"server": proxy_server, "username": proxy_username, "password": proxy_password}


# Stealth arguments to avoid bot detection (shared with login.py)
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-accelerated-2d-canvas",
    "--disable-gpu",
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


# Image with all dependencies
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
        # Fixed in PR #3778 but not yet released (as of 0.11.2)
        # Can remove once browser-use properly detects chrome-linux64/
        """bash -c 'for dir in /ms-playwright/chromium-*/; do \
            if [ -d "${dir}chrome-linux64" ] && [ ! -e "${dir}chrome-linux" ]; then \
                ln -s chrome-linux64 "${dir}chrome-linux"; \
            fi; \
        done'""",
    )
    # Copy the local browser profile directory as a fallback profile
    # This is used by create_browser(shared_profile=False) for isolated containers
    # For persistent login across containers, use /session/profile on the volume instead
    .add_local_dir(
        "./superstore-profile",
        remote_path="/app/superstore-profile",
        copy=True,
    )
    # Copy login.py so login_remote() can import it
    .add_local_file("login.py", remote_path="/root/login.py")
)


def create_browser(shared_profile: bool = False):
    """Create browser configured for Modal's containerized environment.

    Args:
        shared_profile: If True, use profile on volume for persistence across containers.
                        If False, use profile from image (for isolated containers).
    """
    from browser_use import Browser
    from browser_use.browser.profile import ProxySettings

    # Use shared profile on volume for persistence, or local profile from image
    user_data_dir = "/session/profile" if shared_profile else "/app/superstore-profile"

    # Build browser config with optional proxy
    proxy_config = get_proxy_config()
    proxy_settings = None
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
    )


# Store for tracking job statuses (in-memory, reset on container restart)
jobs = {}
checkout_browser = None


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("openai-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    volumes={"/session": session_volume},  # Shared profile for persistent login
    timeout=600,  # 10 minute timeout per item
    env={
        "TIMEOUT_BrowserStartEvent": "120",
        "TIMEOUT_BrowserLaunchEvent": "120",
        "TIMEOUT_BrowserStateRequestEvent": "120",
        "IN_DOCKER": "True",  # Required for browser-use in containers
    },
    cpu=1,
    memory=4096,
)
def add_item_remote(item: str, index: int) -> dict:
    """Add a single item to cart in a separate Modal container (parallelizable).

    Uses shared profile on volume for persistent login across containers.
    """
    from browser_use import Agent, ChatOpenAI

    async def _add_item():
        print(f"[Container {index}] Starting to add item: {item}")

        # Check if shared profile exists (indicates login has been done)
        profile_exists = os.path.exists("/session/profile")
        print(f"[Container {index}] Shared profile exists: {profile_exists}")

        # Use shared profile on volume for persistent login state
        print(f"[Container {index}] Creating browser with shared profile...")
        browser = create_browser(shared_profile=True)
        try:
            print(f"[Container {index}] Browser created, initializing agent...")
            agent = Agent(
                task=f"""
                You need to add "{item}" to the shopping cart on Real Canadian Superstore.

                You should already be logged in. Go to https://www.realcanadiansuperstore.ca/en

                Steps:
                1. Use the search bar to search for "{item}"
                2. From the search results, select the most relevant item
                3. Click "Add to Cart" or similar button
                4. Wait for confirmation that item was added (look for cart update or confirmation message)

                Complete when you see confirmation the item was added to cart.

                NOTE: If you see a login page or are not logged in, report this as an error - the session should already be authenticated.
                """,
                llm=ChatOpenAI(model="gpt-4.1"),
                browser_session=browser,
            )
            print(f"[Container {index}] Running agent (max 30 steps)...")
            result = await agent.run(max_steps=30)
            print(f"[Container {index}] Agent completed. Result: {result}")
            print(f"[Container {index}] SUCCESS: Added {item}")
            return {"item": item, "index": index, "status": "success", "message": f"Added {item}"}
        except Exception as e:
            error_msg = str(e)
            print(f"[Container {index}] ERROR: {error_msg}")
            import traceback

            print(f"[Container {index}] Traceback: {traceback.format_exc()}")
            return {"item": item, "index": index, "status": "failed", "message": error_msg}
        finally:
            print(f"[Container {index}] Cleaning up browser...")
            await browser.kill()
            print(f"[Container {index}] Browser closed")

    return asyncio.run(_add_item())


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("openai-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    volumes={"/session": session_volume},
    timeout=600,  # 10 minute timeout for login
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
    Uses the shared login_and_save() function from login.py which
    automatically detects Modal environment and saves to /session/profile.
    """
    from login import login_and_save

    print("[Login] Starting login process on Modal...")
    result = asyncio.run(login_and_save())
    print(f"[Login] Login completed with status: {result.get('status')}")
    return result


def run_add_items_job(job_id: str, items: list[str]):
    """Add items to cart in parallel using separate Modal containers.

    Each container shares the same login session via volume mount.
    """
    jobs[job_id]["status"] = "running"
    jobs[job_id]["total"] = len(items)
    jobs[job_id]["completed"] = 0
    jobs[job_id]["results"] = []

    print(f"[AddItems Job {job_id}] Starting parallel execution with {len(items)} items")

    # Create inputs for starmap: [(item, index), ...]
    inputs = [(item, i) for i, item in enumerate(items, 1)]

    # Process items in parallel across Modal containers
    # Each container will use the shared profile on the volume
    for result in add_item_remote.starmap(inputs, order_outputs=False):
        print(f"[AddItems Job {job_id}] Got result: {result}")
        jobs[job_id]["results"].append(result)
        jobs[job_id]["completed"] += 1

    jobs[job_id]["status"] = "completed"
    print(f"[AddItems Job {job_id}] Job complete!")


def run_checkout_job(job_id: str):
    """Background thread for checkout process."""
    from browser_use import Agent, ChatOpenAI

    async def _checkout():
        global checkout_browser
        jobs[job_id]["status"] = "running"
        jobs[job_id]["step"] = "Starting checkout..."

        browser = create_browser()
        checkout_browser = browser

        try:
            jobs[job_id]["step"] = "Navigating to cart..."

            agent = Agent(
                task="""
                Go to https://www.realcanadiansuperstore.ca/ and proceed through the checkout process.
                The cart option should be at top right of the main page.

                Navigate through all checkout steps:
                - Delivery details: Click "select a time" and pick the next available time slot.
                - Item details
                - Contact details
                - Driver tip
                - Payment

                Each step will need interaction and need to hit "Save & Continue" after each one.
                Stop when you reach the final order review page where the "Place Order" button is visible.
                """,
                llm=ChatOpenAI(model="gpt-4.1"),
                browser_session=browser,
            )

            jobs[job_id]["step"] = "Processing checkout steps..."
            await agent.run(max_steps=100)

            jobs[job_id]["status"] = "awaiting_confirmation"
            jobs[job_id]["step"] = "Ready to place order - awaiting confirmation"
            jobs[job_id]["agent"] = agent

        except Exception as e:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["step"] = f"Checkout failed: {str(e)}"
            if browser:
                await browser.kill()

    asyncio.run(_checkout())


def run_place_order_job(job_id: str, checkout_job_id: str):
    """Place the final order."""
    from browser_use import Agent, ChatOpenAI

    async def _place_order():
        global checkout_browser
        jobs[job_id]["status"] = "running"
        jobs[job_id]["step"] = "Placing order..."

        try:
            checkout_job = jobs.get(checkout_job_id)
            if not checkout_job or "agent" not in checkout_job:
                browser = create_browser()
                agent = Agent(
                    task="Go to https://www.realcanadiansuperstore.ca/, navigate to cart and click 'Place Order' button.",
                    llm=ChatOpenAI(model="gpt-4.1"),
                    browser_session=browser,
                )
                await agent.run(max_steps=20)
                await browser.kill()
            else:
                agent = checkout_job["agent"]
                agent.add_new_task("Click the 'Place Order' or 'Submit Order' button to complete the purchase.")
                await agent.run(max_steps=10)
                if checkout_browser:
                    await checkout_browser.kill()
                    checkout_browser = None

            jobs[job_id]["status"] = "completed"
            jobs[job_id]["step"] = "Order placed successfully!"

        except Exception as e:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["step"] = f"Failed to place order: {str(e)}"

    asyncio.run(_place_order())


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("openai-secret"),
        modal.Secret.from_name("oxy-proxy"),
        modal.Secret.from_name("superstore"),
    ],
    timeout=3600,  # 1 hour timeout for long-running shopping sessions
    env={
        "TIMEOUT_BrowserStartEvent": "120",
        "TIMEOUT_BrowserLaunchEvent": "120",
        "TIMEOUT_BrowserStateRequestEvent": "120",
        "IN_DOCKER": "True",  # Required for browser-use in containers
    },
    volumes={"/session": session_volume},  # Mount volume for persistent session
)
@modal.concurrent(max_inputs=100)
@modal.wsgi_app()
def flask_app():
    import time

    from flask import Flask, Response, jsonify, request

    flask_app = Flask(__name__)

    # Inline the template since we can't easily serve from templates folder
    INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Superstore Shopping Agent</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; min-height: 100vh; padding: 20px; }
        .container { max-width: 600px; margin: 0 auto; }
        h1 { text-align: center; color: #333; margin-bottom: 20px; }
        .card { background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 20px; margin-bottom: 20px; }
        .card h2 { font-size: 1.1rem; color: #666; margin-bottom: 15px; }
        textarea { width: 100%; height: 150px; padding: 12px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 14px; resize: vertical; font-family: inherit; }
        textarea:focus { outline: none; border-color: #e31837; }
        .hint { font-size: 12px; color: #999; margin-top: 8px; }
        .buttons { display: flex; gap: 10px; margin-top: 15px; }
        button { flex: 1; padding: 12px 20px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
        button:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-primary { background: #e31837; color: white; }
        .btn-primary:hover:not(:disabled) { background: #c41530; }
        .btn-secondary { background: #333; color: white; }
        .btn-secondary:hover:not(:disabled) { background: #444; }
        .btn-success { background: #28a745; color: white; }
        .btn-success:hover:not(:disabled) { background: #218838; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-danger:hover:not(:disabled) { background: #c82333; }
        .status-panel { display: none; }
        .status-panel.active { display: block; }
        .status-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .status-header h2 { margin: 0; }
        .progress-bar { height: 8px; background: #e0e0e0; border-radius: 4px; overflow: hidden; margin-bottom: 15px; }
        .progress-bar-fill { height: 100%; background: #e31837; transition: width 0.3s ease; }
        .item-list { list-style: none; }
        .item-list li { padding: 10px 0; border-bottom: 1px solid #f0f0f0; display: flex; justify-content: space-between; align-items: center; }
        .item-list li:last-child { border-bottom: none; }
        .status-badge { padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
        .status-pending { background: #f0f0f0; color: #666; }
        .status-processing { background: #fff3cd; color: #856404; }
        .status-success { background: #d4edda; color: #155724; }
        .status-failed { background: #f8d7da; color: #721c24; }
        .checkout-status { text-align: center; padding: 20px; }
        .checkout-status .step { font-size: 16px; color: #333; margin-bottom: 10px; }
        .spinner { display: inline-block; width: 20px; height: 20px; border: 2px solid #f3f3f3; border-top: 2px solid #e31837; border-radius: 50%; animation: spin 1s linear infinite; margin-right: 8px; vertical-align: middle; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .confirmation-panel { text-align: center; padding: 20px; }
        .confirmation-panel h3 { color: #333; margin-bottom: 10px; }
        .confirmation-panel p { color: #666; margin-bottom: 20px; }
        .confirmation-buttons { display: flex; gap: 10px; justify-content: center; }
        .confirmation-buttons button { flex: none; min-width: 120px; }
        .summary { background: #f9f9f9; padding: 15px; border-radius: 8px; margin-top: 15px; }
        .summary-row { display: flex; justify-content: space-between; margin-bottom: 5px; }
        .summary-row.success { color: #28a745; }
        .summary-row.failed { color: #dc3545; }
        .modal-badge { background: #6366f1; color: white; padding: 4px 8px; border-radius: 4px; font-size: 11px; margin-left: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Superstore Shopping Agent <span class="modal-badge">Modal</span></h1>
        <div class="card" id="login-panel">
            <h2>Session Status</h2>
            <p id="login-status" style="margin-bottom: 15px; color: #666;">Click "Login" to authenticate before adding items.</p>
            <div class="buttons">
                <button class="btn-primary" id="login-btn" onclick="doLogin()">Login to Superstore</button>
            </div>
        </div>
        <div class="card" id="input-panel">
            <h2>Enter your grocery items</h2>
            <textarea id="items-input" placeholder="Enter each item on a new line, e.g.:
Milk
Bread
Eggs
Bananas"></textarea>
            <p class="hint">One item per line. The agent will search and add each item to your cart.</p>
            <div class="buttons">
                <button class="btn-primary" id="add-to-cart-btn" onclick="addToCart()">Add to Cart</button>
            </div>
        </div>
        <div class="card status-panel" id="cart-status-panel">
            <div class="status-header">
                <h2>Adding items to cart...</h2>
                <span id="cart-progress-text">0/0</span>
            </div>
            <div class="progress-bar">
                <div class="progress-bar-fill" id="cart-progress-bar" style="width: 0%"></div>
            </div>
            <ul class="item-list" id="item-status-list"></ul>
            <div class="summary" id="cart-summary" style="display: none;">
                <div class="summary-row success"><span>Successfully added:</span><span id="success-count">0</span></div>
                <div class="summary-row failed"><span>Failed:</span><span id="failed-count">0</span></div>
            </div>
            <div class="buttons" id="checkout-buttons" style="display: none;">
                <button class="btn-secondary" onclick="resetToInput()">Add More Items</button>
                <button class="btn-primary" onclick="startCheckout()">Proceed to Checkout</button>
            </div>
        </div>
        <div class="card status-panel" id="checkout-status-panel">
            <h2>Checkout Progress</h2>
            <div class="checkout-status">
                <p class="step" id="checkout-step"><span class="spinner"></span>Initializing checkout...</p>
            </div>
        </div>
        <div class="card status-panel" id="confirmation-panel">
            <div class="confirmation-panel">
                <h3>Ready to Place Order</h3>
                <p>The checkout process is complete. Click "Place Order" to finalize.</p>
                <div class="confirmation-buttons">
                    <button class="btn-danger" onclick="cancelCheckout()">Cancel</button>
                    <button class="btn-success" onclick="placeOrder()">Place Order</button>
                </div>
            </div>
        </div>
        <div class="card status-panel" id="complete-panel">
            <div class="confirmation-panel">
                <h3 id="complete-title">Order Placed!</h3>
                <p id="complete-message">Your order has been successfully placed.</p>
                <div class="buttons">
                    <button class="btn-primary" onclick="resetToInput()">Start New Order</button>
                </div>
            </div>
        </div>
    </div>
    <script>
        let currentCartJobId = null;
        let currentCheckoutJobId = null;
        let items = [];
        async function doLogin() {
            const btn = document.getElementById('login-btn');
            const status = document.getElementById('login-status');
            btn.disabled = true;
            btn.textContent = 'Logging in...';
            status.innerHTML = '<span class="spinner"></span> Logging in to Superstore... (this may take a minute)';
            status.style.color = '#856404';
            try {
                const response = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
                const data = await response.json();
                if (data.status === 'success') {
                    status.textContent = '✓ Logged in successfully! You can now add items.';
                    status.style.color = '#28a745';
                    btn.textContent = 'Logged In';
                    btn.classList.remove('btn-primary');
                    btn.classList.add('btn-success');
                } else {
                    status.textContent = '✗ Login failed: ' + data.message;
                    status.style.color = '#dc3545';
                    btn.textContent = 'Retry Login';
                    btn.disabled = false;
                }
            } catch (error) {
                status.textContent = '✗ Login error: ' + error.message;
                status.style.color = '#dc3545';
                btn.textContent = 'Retry Login';
                btn.disabled = false;
            }
        }
        function showPanel(panelId) {
            document.querySelectorAll('.status-panel').forEach(p => p.classList.remove('active'));
            document.getElementById('input-panel').style.display = panelId === 'input-panel' ? 'block' : 'none';
            if (panelId !== 'input-panel') document.getElementById(panelId).classList.add('active');
        }
        function resetToInput() {
            showPanel('input-panel');
            document.getElementById('items-input').value = '';
            document.getElementById('add-to-cart-btn').disabled = false;
            currentCartJobId = null;
            currentCheckoutJobId = null;
        }
        async function addToCart() {
            const input = document.getElementById('items-input').value.trim();
            items = input.split('\\n').map(i => i.trim()).filter(i => i.length > 0);
            if (items.length === 0) { alert('Please enter at least one item.'); return; }
            document.getElementById('add-to-cart-btn').disabled = true;
            const statusList = document.getElementById('item-status-list');
            statusList.innerHTML = items.map((item, i) => `<li id="item-${i}"><span>${item}</span><span class="status-badge status-pending">Pending</span></li>`).join('');
            document.getElementById('cart-progress-text').textContent = `0/${items.length}`;
            document.getElementById('cart-progress-bar').style.width = '0%';
            document.getElementById('cart-summary').style.display = 'none';
            document.getElementById('checkout-buttons').style.display = 'none';
            showPanel('cart-status-panel');
            try {
                const response = await fetch('/api/add-to-cart', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }) });
                const data = await response.json();
                currentCartJobId = data.job_id;
                pollCartStatus();
            } catch (error) {
                alert('Failed to start adding items: ' + error.message);
                document.getElementById('add-to-cart-btn').disabled = false;
            }
        }
        function pollCartStatus() {
            const eventSource = new EventSource(`/api/job/${currentCartJobId}/stream`);
            eventSource.onmessage = function(event) {
                const job = JSON.parse(event.data);
                if (job.error) { eventSource.close(); alert('Error: ' + job.error); return; }
                const progress = (job.completed / job.total) * 100;
                document.getElementById('cart-progress-bar').style.width = progress + '%';
                document.getElementById('cart-progress-text').textContent = `${job.completed}/${job.total}`;
                job.results.forEach(result => {
                    const itemIndex = items.indexOf(result.item);
                    if (itemIndex >= 0) {
                        const itemEl = document.getElementById(`item-${itemIndex}`);
                        if (itemEl) {
                            const badge = itemEl.querySelector('.status-badge');
                            badge.className = 'status-badge ' + (result.status === 'success' ? 'status-success' : 'status-failed');
                            badge.textContent = result.status === 'success' ? 'Added' : 'Failed';
                        }
                    }
                });
                items.forEach((item, i) => {
                    const itemEl = document.getElementById(`item-${i}`);
                    const badge = itemEl.querySelector('.status-badge');
                    if (badge.classList.contains('status-pending') && job.status === 'running') {
                        const isComplete = job.results.some(r => r.item === item);
                        if (!isComplete) { badge.className = 'status-badge status-processing'; badge.textContent = 'Processing...'; }
                    }
                });
                if (job.status === 'completed') {
                    eventSource.close();
                    const successes = job.results.filter(r => r.status === 'success').length;
                    const failures = job.results.filter(r => r.status === 'failed').length;
                    document.getElementById('success-count').textContent = successes;
                    document.getElementById('failed-count').textContent = failures;
                    document.getElementById('cart-summary').style.display = 'block';
                    document.getElementById('checkout-buttons').style.display = 'flex';
                    document.querySelector('#cart-status-panel .status-header h2').textContent = 'Items added to cart';
                }
            };
            eventSource.onerror = function() { eventSource.close(); };
        }
        async function startCheckout() {
            showPanel('checkout-status-panel');
            document.getElementById('checkout-step').innerHTML = '<span class="spinner"></span> Starting checkout process...';
            try {
                const response = await fetch('/api/checkout', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
                const data = await response.json();
                currentCheckoutJobId = data.job_id;
                pollCheckoutStatus();
            } catch (error) { alert('Failed to start checkout: ' + error.message); }
        }
        function pollCheckoutStatus() {
            const eventSource = new EventSource(`/api/job/${currentCheckoutJobId}/stream`);
            eventSource.onmessage = function(event) {
                const job = JSON.parse(event.data);
                if (job.error) { eventSource.close(); alert('Error: ' + job.error); return; }
                document.getElementById('checkout-step').innerHTML = (job.status === 'running' ? '<span class="spinner"></span> ' : '') + job.step;
                if (job.status === 'awaiting_confirmation') { eventSource.close(); showPanel('confirmation-panel'); }
                else if (job.status === 'failed') { eventSource.close(); showComplete('Checkout Failed', job.step, false); }
            };
            eventSource.onerror = function() { eventSource.close(); };
        }
        async function placeOrder() {
            showPanel('checkout-status-panel');
            document.getElementById('checkout-step').innerHTML = '<span class="spinner"></span> Placing order...';
            try {
                const response = await fetch('/api/place-order', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ checkout_job_id: currentCheckoutJobId }) });
                const data = await response.json();
                pollPlaceOrderStatus(data.job_id);
            } catch (error) { alert('Failed to place order: ' + error.message); }
        }
        function pollPlaceOrderStatus(jobId) {
            const eventSource = new EventSource(`/api/job/${jobId}/stream`);
            eventSource.onmessage = function(event) {
                const job = JSON.parse(event.data);
                if (job.error) { eventSource.close(); alert('Error: ' + job.error); return; }
                document.getElementById('checkout-step').innerHTML = (job.status === 'running' ? '<span class="spinner"></span> ' : '') + job.step;
                if (job.status === 'completed') { eventSource.close(); showComplete('Order Placed!', 'Your order has been successfully placed.', true); }
                else if (job.status === 'failed') { eventSource.close(); showComplete('Order Failed', job.step, false); }
            };
            eventSource.onerror = function() { eventSource.close(); };
        }
        async function cancelCheckout() {
            try { await fetch('/api/cancel-checkout', { method: 'POST' }); showComplete('Order Cancelled', 'Your order was not placed.', false); }
            catch (error) { alert('Error cancelling: ' + error.message); }
        }
        function showComplete(title, message, success) {
            document.getElementById('complete-title').textContent = title;
            document.getElementById('complete-message').textContent = message;
            document.getElementById('complete-title').style.color = success ? '#28a745' : '#dc3545';
            showPanel('complete-panel');
        }
    </script>
</body>
</html>"""

    @flask_app.route("/")
    def index():
        return INDEX_HTML

    @flask_app.route("/api/login", methods=["POST"])
    def login():
        """Trigger login to populate shared session profile.

        Call this endpoint first before adding items to ensure session is authenticated.
        """
        print("[Flask] Login endpoint called")
        try:
            result = login_remote.remote()
            print(f"[Flask] Login result: {result}")
            return jsonify(result)
        except Exception as e:
            error_msg = str(e)
            print(f"[Flask] Login error: {error_msg}")
            return jsonify({"status": "failed", "message": error_msg}), 500

    @flask_app.route("/api/add-to-cart", methods=["POST"])
    def add_to_cart():
        data = request.json
        items = data.get("items", [])

        if not items:
            return jsonify({"error": "No items provided"}), 400

        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            "type": "add_to_cart",
            "status": "pending",
            "items": items,
            "total": len(items),
            "completed": 0,
            "results": [],
        }

        thread = threading.Thread(target=run_add_items_job, args=(job_id, items))
        thread.start()

        return jsonify({"job_id": job_id})

    @flask_app.route("/api/checkout", methods=["POST"])
    def start_checkout():
        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            "type": "checkout",
            "status": "pending",
            "step": "Initializing...",
        }

        thread = threading.Thread(target=run_checkout_job, args=(job_id,))
        thread.start()

        return jsonify({"job_id": job_id})

    @flask_app.route("/api/place-order", methods=["POST"])
    def place_order():
        data = request.json
        checkout_job_id = data.get("checkout_job_id")

        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            "type": "place_order",
            "status": "pending",
            "step": "Initializing...",
        }

        thread = threading.Thread(target=run_place_order_job, args=(job_id, checkout_job_id))
        thread.start()

        return jsonify({"job_id": job_id})

    @flask_app.route("/api/cancel-checkout", methods=["POST"])
    def cancel_checkout():
        global checkout_browser

        async def _cancel():
            global checkout_browser
            if checkout_browser:
                await checkout_browser.kill()
                checkout_browser = None

        asyncio.run(_cancel())
        return jsonify({"status": "cancelled"})

    @flask_app.route("/api/job/<job_id>")
    def get_job_status(job_id):
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404

        response = {k: v for k, v in job.items() if k != "agent"}
        return jsonify(response)

    @flask_app.route("/api/job/<job_id>/stream")
    def stream_job_status(job_id):
        def generate():
            last_state = None
            while True:
                job = jobs.get(job_id)
                if not job:
                    yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                    break

                current_state = {k: v for k, v in job.items() if k != "agent"}
                state_json = json.dumps(current_state)

                if state_json != last_state:
                    yield f"data: {state_json}\n\n"
                    last_state = state_json

                if job["status"] in ["completed", "failed", "awaiting_confirmation"]:
                    break

                time.sleep(0.5)

        return Response(generate(), mimetype="text/event-stream")

    return flask_app
