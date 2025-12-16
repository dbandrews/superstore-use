import asyncio
import json
import multiprocessing
import os
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor

from flask import Flask, Response, jsonify, render_template, request

from browser_use import Agent, Browser, ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Store for tracking job statuses
jobs = {}
checkout_browser = None


def create_browser(user_data_dir: str = "./superstore-profile") -> Browser:
    return Browser(
        headless=False,
        window_size={"width": 500, "height": 700},
        wait_between_actions=1.5,
        minimum_wait_page_load_time=1.5,
        wait_for_network_idle_page_load_time=1.5,
        user_data_dir=user_data_dir,
    )


def add_single_item(item: str, index: int, worker_id: int) -> dict:
    """Worker function to add a single item with its own browser profile."""

    async def _add_item():
        # Create a unique temporary profile for this worker
        # Copy from main profile to preserve login state
        base_profile = "./superstore-profile"
        temp_profile = f"./superstore-profile-worker-{worker_id}-{os.getpid()}"

        try:
            # Copy the base profile if it exists to preserve login cookies
            if os.path.exists(base_profile):
                if os.path.exists(temp_profile):
                    shutil.rmtree(temp_profile)
                shutil.copytree(base_profile, temp_profile)

            browser = create_browser(user_data_dir=temp_profile)
            try:
                agent = Agent(
                    task=f"Go to https://www.realcanadiansuperstore.ca, search for {item} and add it to the cart",
                    llm=ChatOpenAI(model="gpt-4.1"),
                    browser_session=browser,
                )
                await agent.run(max_steps=50)
                return {"item": item, "index": index, "status": "success", "message": f"Added {item}"}
            except Exception as e:
                return {"item": item, "index": index, "status": "failed", "message": str(e)}
            finally:
                await browser.kill()
        finally:
            # Clean up temp profile
            if os.path.exists(temp_profile):
                try:
                    shutil.rmtree(temp_profile)
                except Exception:
                    pass  # Ignore cleanup errors

    return asyncio.run(_add_item())


def process_item_worker(args: tuple) -> dict:
    """Wrapper for multiprocessing."""
    item, index, worker_id = args
    return add_single_item(item, index, worker_id)


def run_add_items_job(job_id: str, items: list[str]):
    """Background thread to manage adding items in parallel."""
    jobs[job_id]["status"] = "running"
    jobs[job_id]["total"] = len(items)
    jobs[job_id]["completed"] = 0
    jobs[job_id]["results"] = []

    # Each item gets a unique worker_id for its own browser profile
    process_args = [(item, i, i) for i, item in enumerate(items, 1)]

    with ProcessPoolExecutor(max_workers=min(len(items), 4)) as executor:
        futures = {executor.submit(process_item_worker, args): args[0] for args in process_args}

        for future in futures:
            try:
                result = future.result()
                jobs[job_id]["results"].append(result)
                jobs[job_id]["completed"] += 1
            except Exception as e:
                item = futures[future]
                jobs[job_id]["results"].append({
                    "item": item,
                    "status": "failed",
                    "message": str(e)
                })
                jobs[job_id]["completed"] += 1

    jobs[job_id]["status"] = "completed"


def run_checkout_job(job_id: str):
    """Background thread for checkout process."""

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

    async def _place_order():
        global checkout_browser
        jobs[job_id]["status"] = "running"
        jobs[job_id]["step"] = "Placing order..."

        try:
            # Get the agent from the checkout job
            checkout_job = jobs.get(checkout_job_id)
            if not checkout_job or "agent" not in checkout_job:
                # Create new browser and agent if checkout agent not available
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/add-to-cart", methods=["POST"])
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


@app.route("/api/checkout", methods=["POST"])
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


@app.route("/api/place-order", methods=["POST"])
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


@app.route("/api/cancel-checkout", methods=["POST"])
def cancel_checkout():
    global checkout_browser

    async def _cancel():
        global checkout_browser
        if checkout_browser:
            await checkout_browser.kill()
            checkout_browser = None

    asyncio.run(_cancel())
    return jsonify({"status": "cancelled"})


@app.route("/api/job/<job_id>")
def get_job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    # Don't include agent object in response
    response = {k: v for k, v in job.items() if k != "agent"}
    return jsonify(response)


@app.route("/api/job/<job_id>/stream")
def stream_job_status(job_id):
    def generate():
        last_state = None
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break

            # Create response without agent object
            current_state = {k: v for k, v in job.items() if k != "agent"}
            state_json = json.dumps(current_state)

            if state_json != last_state:
                yield f"data: {state_json}\n\n"
                last_state = state_json

            if job["status"] in ["completed", "failed", "awaiting_confirmation"]:
                break

            import time
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    app.run(debug=True, port=5000, threaded=True)
