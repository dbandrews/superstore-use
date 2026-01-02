"""
Modal deployment of the Chat UI for the Grocery Shopping Agent.

This consolidates chat_agent.py and chat_app.py into a single Modal deployment.

Deploy with: modal deploy modal_chat_app.py
Run locally: modal serve modal_chat_app.py
"""

import modal

app = modal.App("superstore-chat-agent")

# Distributed Dict for storing job state (persists across function invocations)
job_state_dict = modal.Dict.from_name("superstore-job-state", create_if_missing=True)

# Create image with all dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "flask",
        "langchain-core",
        "langchain-openai",
        "langgraph",
        "python-dotenv",
        "modal",  # Required for modal_tools.py to call remote functions
    )
    # Add local Python modules
    .add_local_python_source("chat_agent")
    .add_local_python_source("modal_tools")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("openai-secret")],
    timeout=600,  # 10 minute timeout for long chat sessions
    cpu=1,
    memory=2048,
)
@modal.concurrent(max_inputs=100)
@modal.wsgi_app()
def flask_app():
    """Flask app for the chat UI."""
    import time
    import uuid

    from flask import Flask, jsonify, request
    from langchain_core.messages import AIMessage, HumanMessage

    from chat_agent import create_chat_agent

    flask_app = Flask(__name__)

    # Store agent instances per session
    agents = {}

    def get_or_create_agent(thread_id: str):
        """Get existing agent or create new one for the thread."""
        if thread_id not in agents:
            agents[thread_id] = create_chat_agent()
        return agents[thread_id]

    # Job state management helpers
    def create_job(thread_id: str, message: str) -> str:
        """Create a new job and return its ID."""
        job_id = str(uuid.uuid4())[:8]
        job_state_dict[job_id] = {
            "id": job_id,
            "thread_id": thread_id,
            "message": message,
            "status": "running",  # running, completed, error
            "created_at": time.time(),
            "updated_at": time.time(),
            "items_processed": [],  # [{item, status, icon, steps}]
            "items_in_progress": {},  # {item: {step, action}}
            "final_message": None,
            "error": None,
        }
        return job_id

    def update_job_progress(job_id: str, event: dict):
        """Update job state based on a progress event."""
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
                # Move from in_progress to processed
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
        """Get the current state of a job."""
        try:
            job = job_state_dict.get(job_id)
            if job:
                # Check if job is stale (older than 10 minutes)
                if time.time() - job.get("created_at", 0) > 600:
                    job["status"] = "expired"
            return job
        except Exception as e:
            print(f"[JobState] Error getting job {job_id}: {e}")
            return None

    def cleanup_old_jobs():
        """Remove jobs older than 10 minutes (called periodically)."""
        try:
            # Note: Modal Dict doesn't support iteration easily,
            # so we rely on the expiry check in get_job_status
            pass
        except Exception:
            pass

    # Inline HTML template for the chat UI
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
        .header .subtitle { display: none; }
        .main-container {
            flex: 1;
            display: flex;
            overflow: hidden;
        }
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: transparent;
            min-width: 0;
        }
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
            font-weight: 400;
            transition: all 0.2s ease;
        }
        .item-count.has-items {
            background: rgba(255,255,255,0.9);
            color: #0d1117;
        }
        .grocery-list {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
        }
        .grocery-list::-webkit-scrollbar { width: 4px; }
        .grocery-list::-webkit-scrollbar-track { background: transparent; }
        .grocery-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }
        .grocery-item {
            display: flex;
            align-items: center;
            padding: 12px 14px;
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.04);
            border-radius: 4px;
            margin-bottom: 6px;
            transition: all 0.15s ease;
        }
        .grocery-item:hover {
            background: rgba(255,255,255,0.05);
            border-color: rgba(255,255,255,0.08);
        }
        .grocery-item .item-info {
            flex: 1;
            min-width: 0;
        }
        .grocery-item .item-name {
            font-size: 0.8rem;
            font-weight: 400;
            color: rgba(255,255,255,0.8);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .grocery-item .item-qty {
            font-size: 0.7rem;
            color: rgba(255,255,255,0.3);
            margin-top: 2px;
        }
        .grocery-item .remove-btn {
            background: none;
            border: none;
            color: rgba(255,255,255,0.2);
            cursor: pointer;
            padding: 4px;
            font-size: 1rem;
            line-height: 1;
            transition: color 0.15s;
        }
        .grocery-item .remove-btn:hover {
            color: rgba(255,255,255,0.6);
        }
        .grocery-item .edit-qty {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-right: 10px;
        }
        .grocery-item .qty-btn {
            width: 22px;
            height: 22px;
            border: 1px solid rgba(255,255,255,0.1);
            background: transparent;
            border-radius: 2px;
            cursor: pointer;
            font-size: 0.85rem;
            line-height: 1;
            color: rgba(255,255,255,0.4);
            transition: all 0.15s;
        }
        .grocery-item .qty-btn:hover {
            border-color: rgba(255,255,255,0.3);
            color: rgba(255,255,255,0.8);
        }
        .grocery-item .qty-display {
            font-size: 0.75rem;
            font-weight: 400;
            min-width: 18px;
            text-align: center;
            color: rgba(255,255,255,0.6);
        }
        .empty-list {
            text-align: center;
            padding: 40px 20px;
            color: rgba(255,255,255,0.25);
        }
        .empty-list .icon {
            font-size: 1.5rem;
            margin-bottom: 12px;
            opacity: 0.5;
        }
        .empty-list p {
            font-size: 0.75rem;
            line-height: 1.6;
        }
        .sidebar-footer {
            padding: 16px;
            border-top: 1px solid rgba(255,255,255,0.06);
        }
        .add-item-form {
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
        }
        .add-item-form input {
            flex: 1;
            padding: 10px 12px;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            font-size: 0.8rem;
            font-family: inherit;
            outline: none;
            background: rgba(255,255,255,0.03);
            color: rgba(255,255,255,0.8);
            transition: border-color 0.15s;
        }
        .add-item-form input::placeholder { color: rgba(255,255,255,0.25); }
        .add-item-form input:focus {
            border-color: rgba(255,255,255,0.2);
        }
        .add-item-form button {
            padding: 10px 14px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.9rem;
            color: rgba(255,255,255,0.5);
            transition: all 0.15s;
        }
        .add-item-form button:hover {
            background: rgba(255,255,255,0.08);
            color: rgba(255,255,255,0.8);
        }
        .sidebar-actions {
            display: flex;
            gap: 8px;
        }
        .clear-btn {
            flex: 1;
            padding: 11px;
            background: transparent;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            font-size: 0.75rem;
            font-family: inherit;
            color: rgba(255,255,255,0.4);
            cursor: pointer;
            transition: all 0.15s;
        }
        .clear-btn:hover {
            border-color: rgba(255,255,255,0.2);
            color: rgba(255,255,255,0.7);
        }
        .add-all-btn {
            flex: 2;
            padding: 11px;
            background: rgba(255,255,255,0.9);
            color: #0d1117;
            border: none;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 500;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.15s;
        }
        .add-all-btn:hover:not(:disabled) {
            background: rgba(255,255,255,1);
        }
        .add-all-btn:disabled {
            opacity: 0.3;
            cursor: not-allowed;
        }
        .messages {
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            -webkit-overflow-scrolling: touch;
            overscroll-behavior: contain;
            scroll-behavior: smooth;
        }
        .messages::-webkit-scrollbar { width: 4px; }
        .messages::-webkit-scrollbar-track { background: transparent; }
        .messages::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }
        .message {
            max-width: 80%;
            padding: 14px 18px;
            border-radius: 4px;
            line-height: 1.6;
            white-space: pre-wrap;
            font-size: 0.85rem;
        }
        .message.user {
            align-self: flex-end;
            background: rgba(255,255,255,0.9);
            color: #0d1117;
        }
        .message.assistant {
            align-self: flex-start;
            background: rgba(255,255,255,0.05);
            color: rgba(255,255,255,0.85);
            border: 1px solid rgba(255,255,255,0.06);
        }
        .message.system {
            align-self: center;
            background: rgba(255,200,100,0.1);
            color: rgba(255,200,100,0.8);
            font-size: 0.8rem;
            max-width: 90%;
            border: 1px solid rgba(255,200,100,0.15);
        }
        .message.error {
            align-self: center;
            background: rgba(255,100,100,0.1);
            color: rgba(255,150,150,0.9);
            font-size: 0.8rem;
            border: 1px solid rgba(255,100,100,0.15);
        }
        .typing-indicator {
            align-self: flex-start;
            background: rgba(255,255,255,0.05);
            padding: 14px 18px;
            border-radius: 4px;
            border: 1px solid rgba(255,255,255,0.06);
        }
        .typing-indicator span {
            display: inline-block;
            width: 6px;
            height: 6px;
            background: rgba(255,255,255,0.4);
            border-radius: 50%;
            margin-right: 4px;
            animation: pulse 1.4s infinite ease-in-out both;
        }
        .typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
        .typing-indicator span:nth-child(2) { animation-delay: -0.16s; }
        @keyframes pulse {
            0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
            40% { opacity: 1; transform: scale(1); }
        }
        .input-area {
            padding: 20px 24px;
            background: transparent;
            border-top: 1px solid rgba(255,255,255,0.06);
            display: flex;
            gap: 12px;
        }
        .input-area input {
            flex: 1;
            padding: 14px 18px;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 4px;
            font-size: 0.85rem;
            font-family: inherit;
            outline: none;
            background: rgba(255,255,255,0.03);
            color: rgba(255,255,255,0.9);
            transition: border-color 0.15s;
        }
        .input-area input::placeholder { color: rgba(255,255,255,0.25); }
        .input-area input:focus { border-color: rgba(255,255,255,0.25); }
        .input-area input:disabled { background: rgba(255,255,255,0.02); }
        .input-area button {
            padding: 14px 28px;
            background: rgba(255,255,255,0.9);
            color: #0d1117;
            border: none;
            border-radius: 4px;
            font-size: 0.8rem;
            font-weight: 500;
            font-family: inherit;
            cursor: pointer;
            transition: all 0.15s;
        }
        .input-area button:hover:not(:disabled) { background: rgba(255,255,255,1); }
        .input-area button:disabled { opacity: 0.3; cursor: not-allowed; }
        .suggestions {
            padding: 12px 24px;
            background: transparent;
            border-top: 1px solid rgba(255,255,255,0.06);
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .suggestion {
            padding: 8px 14px;
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 4px;
            font-size: 0.75rem;
            color: rgba(255,255,255,0.5);
            cursor: pointer;
            transition: all 0.15s;
        }
        .suggestion:hover {
            border-color: rgba(255,255,255,0.2);
            color: rgba(255,255,255,0.8);
        }
        @media (max-width: 768px) {
            body {
                overflow: hidden;
                position: fixed;
                width: 100%;
                height: 100%;
            }
            .main-container {
                flex-direction: column;
                height: calc(100vh - 53px);
                overflow: hidden;
            }
            .chat-container {
                flex: 1;
                display: flex;
                flex-direction: column;
                height: 100%;
                overflow: hidden;
                padding-bottom: 0;
            }
            .messages {
                flex: 1;
                min-height: 0;
                padding: 16px;
                padding-bottom: 130px;
                gap: 12px;
                touch-action: pan-y;
            }
            .message {
                max-width: 90%;
            }
            .suggestions {
                display: none;
            }
            .input-area {
                position: fixed;
                bottom: 56px;
                left: 0;
                right: 0;
                background: #0d1117;
                border-top: 1px solid rgba(255,255,255,0.06);
                z-index: 50;
                padding: 12px 16px;
            }
            .input-area input {
                padding: 12px 14px;
                font-size: 16px;
            }
            .input-area button {
                padding: 12px 20px;
            }
            .sidebar {
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                width: 100%;
                height: auto;
                max-height: 70vh;
                border-left: none;
                border-top: 1px solid rgba(255,255,255,0.1);
                border-radius: 16px 16px 0 0;
                transform: translateY(calc(100% - 56px));
                transition: transform 0.3s ease;
                z-index: 100;
                background: #0d1117;
            }
            .sidebar.expanded {
                transform: translateY(0);
            }
            .sidebar-header {
                cursor: pointer;
                padding: 16px 20px;
                padding-top: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                position: relative;
            }
            .sidebar-header::before {
                content: '';
                position: absolute;
                top: 8px;
                left: 50%;
                transform: translateX(-50%);
                width: 36px;
                height: 4px;
                background: rgba(255,255,255,0.15);
                border-radius: 2px;
            }
            .sidebar-toggle {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .sidebar-toggle-icon {
                display: inline-block;
                transition: transform 0.3s ease;
                color: rgba(255,255,255,0.4);
                font-size: 0.8rem;
            }
            .sidebar.expanded .sidebar-toggle-icon {
                transform: rotate(180deg);
            }
            .grocery-list {
                max-height: calc(70vh - 140px);
                -webkit-overflow-scrolling: touch;
            }
        }
        @media (min-width: 769px) {
            .sidebar-toggle-icon {
                display: none;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>superstore-use</h1>
    </div>
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
                <div class="sidebar-toggle">
                    <h2>Grocery List</h2>
                    <span class="sidebar-toggle-icon" id="toggle-icon">&#9650;</span>
                </div>
                <span class="item-count" id="item-count">0</span>
            </div>
            <div class="grocery-list" id="grocery-list">
                <div class="empty-list" id="empty-list">
                    <div class="icon">—</div>
                    <p>No items yet</p>
                </div>
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
            </div>
        </div>
    </div>

    <script>
        const threadId = 'session-' + Math.random().toString(36).substr(2, 9);
        let isProcessing = false;
        let groceryList = [];

        // Mobile app-switch handling
        let currentAbortController = null;
        let wasInterruptedByVisibility = false;
        let pendingRequest = null;  // Track in-flight request for recovery
        let currentJobId = null;  // Current job ID for reconnection

        // Job ID persistence for reconnection
        function saveJobId(jobId) {
            currentJobId = jobId;
            localStorage.setItem('currentJobId_' + threadId, jobId);
            localStorage.setItem('currentJobTime_' + threadId, Date.now().toString());
        }

        function clearJobId() {
            currentJobId = null;
            localStorage.removeItem('currentJobId_' + threadId);
            localStorage.removeItem('currentJobTime_' + threadId);
        }

        function getSavedJobId() {
            const jobId = localStorage.getItem('currentJobId_' + threadId);
            const jobTime = localStorage.getItem('currentJobTime_' + threadId);
            // Only return if job is less than 10 minutes old
            if (jobId && jobTime && (Date.now() - parseInt(jobTime)) < 600000) {
                return jobId;
            }
            return null;
        }

        // Poll job status for reconnection
        async function pollJobStatus(jobId) {
            try {
                const response = await fetch(`/api/job/${jobId}/status`);
                if (!response.ok) {
                    return null;
                }
                return await response.json();
            } catch (e) {
                console.error('Failed to poll job status:', e);
                return null;
            }
        }

        // Render job state to progress div
        function renderJobState(job, progressDiv) {
            if (!job || !progressDiv) return;

            const status = job.status;
            const itemsProcessed = job.items_processed || [];
            const itemsInProgress = job.items_in_progress || {};

            if (status === 'completed' || status === 'error') {
                // Job finished - show final message or error
                if (job.final_message) {
                    progressDiv.remove();
                    addMessage(job.final_message, 'assistant');
                } else if (job.error) {
                    progressDiv.remove();
                    addMessage('Error: ' + job.error, 'error');
                } else {
                    // Show completion summary
                    let html = '<div style="font-size: 0.85rem;">';
                    itemsProcessed.forEach(p => {
                        const icon = p.status === 'success' ? '<span style="color: #4ade80;">&#10003;</span>'
                            : p.status === 'uncertain' ? '<span style="color: #fbbf24;">?</span>'
                            : '<span style="color: #f87171;">&#10007;</span>';
                        html += `<div>${icon} ${escapeHtml(p.item)}</div>`;
                    });
                    html += '</div>';
                    progressDiv.innerHTML = html;
                }
                clearJobId();
                setInputEnabled(true);
                return true;  // Job complete
            }

            // Job still running - show current progress
            let html = '';
            const inProgressCount = Object.keys(itemsInProgress).length;
            const completedCount = itemsProcessed.length;
            const total = inProgressCount + completedCount;

            if (total > 0) {
                html += `<div style="font-size: 0.75rem; opacity: 0.6; margin-bottom: 8px;">Progress: ${completedCount}/${total}</div>`;
            }

            html += '<div style="font-size: 0.85rem;">';

            // Show completed items
            itemsProcessed.forEach(p => {
                const icon = p.status === 'success' ? '<span style="color: #4ade80;">&#10003;</span>'
                    : p.status === 'uncertain' ? '<span style="color: #fbbf24;">?</span>'
                    : '<span style="color: #f87171;">&#10007;</span>';
                html += `<div>${icon} ${escapeHtml(p.item)}</div>`;
            });

            // Show in-progress items
            for (const [item, progress] of Object.entries(itemsInProgress)) {
                let statusText = 'In Progress';
                if (progress.step > 0) {
                    statusText = `Step ${progress.step}`;
                    if (progress.action && progress.action !== 'Starting...') {
                        statusText += `: ${progress.action.substring(0, 40)}`;
                    }
                }
                html += `<div style="opacity: 0.7;">`;
                html += `<span class="typing-indicator" style="display: inline-block; vertical-align: middle; margin-right: 6px; padding: 0;"><span></span><span></span><span></span></span>`;
                html += `${escapeHtml(item)} <span style="font-size: 0.7rem; opacity: 0.6;">${escapeHtml(statusText)}</span>`;
                html += `</div>`;
            }

            html += '</div>';
            progressDiv.innerHTML = html;
            return false;  // Job still running
        }

        // Check and resume any pending job on page load or visibility return
        async function checkAndResumeJob() {
            const savedJobId = getSavedJobId();
            if (!savedJobId) return;

            console.log('Found saved job:', savedJobId);

            // Get or create progress div
            let progressDiv = document.getElementById('current-progress');
            if (!progressDiv) {
                progressDiv = document.createElement('div');
                progressDiv.className = 'message assistant';
                progressDiv.id = 'current-progress';
                progressDiv.innerHTML = '<div style="opacity: 0.7;">Reconnecting to your request...</div>';
                document.getElementById('messages').appendChild(progressDiv);
                scrollToBottom();
            } else {
                progressDiv.innerHTML = '<div style="opacity: 0.7;">Reconnecting to your request...</div>';
            }

            setInputEnabled(false);

            // Poll for status
            const job = await pollJobStatus(savedJobId);
            if (!job) {
                progressDiv.innerHTML = '<div style="opacity: 0.6;">Could not reconnect. Your request may have completed - check your cart.</div>';
                clearJobId();
                setInputEnabled(true);
                return;
            }

            // Render current state
            const isComplete = renderJobState(job, progressDiv);

            // If still running, keep polling
            if (!isComplete) {
                currentJobId = savedJobId;
                pollUntilComplete(savedJobId, progressDiv);
            }
        }

        // Poll until job completes
        async function pollUntilComplete(jobId, progressDiv) {
            const pollInterval = 2000;  // 2 seconds
            const maxPolls = 150;  // 5 minutes max
            let polls = 0;

            const poll = async () => {
                if (polls >= maxPolls) {
                    progressDiv.innerHTML = '<div style="opacity: 0.6;">Request timed out. Check your cart for results.</div>';
                    clearJobId();
                    setInputEnabled(true);
                    return;
                }

                const job = await pollJobStatus(jobId);
                if (!job) {
                    polls++;
                    setTimeout(poll, pollInterval);
                    return;
                }

                const isComplete = renderJobState(job, progressDiv);
                if (!isComplete) {
                    polls++;
                    setTimeout(poll, pollInterval);
                }
            };

            poll();
        }

        function addMessage(content, type) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + type;
            div.textContent = content;
            messages.appendChild(div);
            // Smooth scroll to bottom with slight delay for render
            requestAnimationFrame(() => {
                messages.scrollTo({
                    top: messages.scrollHeight,
                    behavior: 'smooth'
                });
            });
        }

        function showTyping() {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'typing-indicator';
            div.id = 'typing';
            div.innerHTML = '<span></span><span></span><span></span>';
            messages.appendChild(div);
            requestAnimationFrame(() => {
                messages.scrollTo({
                    top: messages.scrollHeight,
                    behavior: 'smooth'
                });
            });
        }

        function hideTyping() {
            const typing = document.getElementById('typing');
            if (typing) typing.remove();
        }

        function setInputEnabled(enabled) {
            document.getElementById('message-input').disabled = !enabled;
            document.getElementById('send-btn').disabled = !enabled;
            document.getElementById('add-all-btn').disabled = !enabled || groceryList.length === 0;
            isProcessing = !enabled;
        }

        // Grocery list management
        function renderGroceryList() {
            const listEl = document.getElementById('grocery-list');
            const emptyEl = document.getElementById('empty-list');
            const countEl = document.getElementById('item-count');
            const addAllBtn = document.getElementById('add-all-btn');

            countEl.textContent = groceryList.length;
            addAllBtn.disabled = isProcessing || groceryList.length === 0;

            // Highlight badge when items exist
            if (groceryList.length > 0) {
                countEl.classList.add('has-items');
            } else {
                countEl.classList.remove('has-items');
            }

            if (groceryList.length === 0) {
                if (emptyEl) emptyEl.style.display = 'block';
                listEl.querySelectorAll('.grocery-item').forEach(el => el.remove());
                return;
            }

            if (emptyEl) emptyEl.style.display = 'none';
            listEl.innerHTML = '';

            groceryList.forEach((item, index) => {
                const itemEl = document.createElement('div');
                itemEl.className = 'grocery-item';
                itemEl.innerHTML = `
                    <div class="item-info">
                        <div class="item-name">${escapeHtml(item.name)}</div>
                        <div class="item-qty">Qty: ${item.qty}</div>
                    </div>
                    <div class="edit-qty">
                        <button class="qty-btn" onclick="updateQty(${index}, -1)">−</button>
                        <span class="qty-display">${item.qty}</span>
                        <button class="qty-btn" onclick="updateQty(${index}, 1)">+</button>
                    </div>
                    <button class="remove-btn" onclick="removeItem(${index})">×</button>
                `;
                listEl.appendChild(itemEl);
            });
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function addToGroceryList(name, qty = 1) {
            // Check if item already exists
            const existing = groceryList.find(item => item.name.toLowerCase() === name.toLowerCase());
            if (existing) {
                existing.qty += qty;
            } else {
                groceryList.push({ name, qty });
            }
            renderGroceryList();
            saveListToStorage();
            expandSidebarOnMobile();
        }

        function removeItem(index) {
            groceryList.splice(index, 1);
            renderGroceryList();
            saveListToStorage();
        }

        function updateQty(index, delta) {
            groceryList[index].qty += delta;
            if (groceryList[index].qty <= 0) {
                removeItem(index);
            } else {
                renderGroceryList();
                saveListToStorage();
            }
        }

        function clearList() {
            if (groceryList.length === 0) return;
            if (confirm('Clear all items from your grocery list?')) {
                groceryList = [];
                renderGroceryList();
                saveListToStorage();
            }
        }

        function addManualItem() {
            const input = document.getElementById('manual-item-input');
            const name = input.value.trim();
            if (name) {
                addToGroceryList(name, 1);
                input.value = '';
            }
        }

        function handleManualItemKeyPress(event) {
            if (event.key === 'Enter') {
                addManualItem();
            }
        }

        function saveListToStorage() {
            localStorage.setItem('groceryList_' + threadId, JSON.stringify(groceryList));
        }

        function loadListFromStorage() {
            const saved = localStorage.getItem('groceryList_' + threadId);
            if (saved) {
                groceryList = JSON.parse(saved);
                renderGroceryList();
            }
        }

        // Parse items from assistant response
        function parseItemsFromResponse(text) {
            const items = [];

            // Split by any newline variant
            const lines = text.split(/\\r?\\n|\\r/);

            for (const line of lines) {
                const trimmed = line.trim();

                // Match bullet points: -, •, *, or numbered lists: 1. or 1)
                const bulletMatch = trimmed.match(/^[-•*]\\s+(.+)$/) ||
                                   trimmed.match(/^\\d+[.)]\\s+(.+)$/);

                if (bulletMatch) {
                    let itemText = bulletMatch[1].trim();
                    let qty = 1;

                    // Try to extract quantity: "2x milk", "milk x2", "milk (2)"
                    const qtyPatterns = [
                        /^(\\d+)\\s*x\\s+(.+)$/i,      // "2x milk" or "2 x milk"
                        /^(.+?)\\s*x\\s*(\\d+)$/i,     // "milk x2" or "milk x 2"
                        /^(.+?)\\s*\\((\\d+)\\)$/      // "milk (2)"
                    ];

                    for (const pat of qtyPatterns) {
                        const qtyMatch = itemText.match(pat);
                        if (qtyMatch) {
                            if (/^\\d+$/.test(qtyMatch[1])) {
                                qty = parseInt(qtyMatch[1]);
                                itemText = qtyMatch[2];
                            } else {
                                qty = parseInt(qtyMatch[2]);
                                itemText = qtyMatch[1];
                            }
                            break;
                        }
                    }

                    // Clean up: remove markdown bold, trailing punctuation
                    itemText = itemText.replace(/\\*\\*/g, '').trim();
                    itemText = itemText.replace(/[,;:]$/, '').trim();

                    if (itemText.length > 0 && itemText.length < 100) {
                        items.push({ name: itemText, qty });
                    }
                }
            }

            return items;
        }

        async function addAllToCart() {
            if (groceryList.length === 0 || isProcessing) return;

            const itemList = groceryList.map(item =>
                item.qty > 1 ? `${item.qty}x ${item.name}` : item.name
            ).join(', ');

            const message = `Please add these items to my Superstore cart: ${itemList}`;

            document.getElementById('message-input').value = '';
            addMessage(message, 'user');
            setInputEnabled(false);
            wasInterruptedByVisibility = false;

            // Hide suggestions after first message
            document.getElementById('suggestions').style.display = 'none';

            // Create progress indicator (same as sendMessage)
            const progressDiv = document.createElement('div');
            progressDiv.className = 'message assistant';
            progressDiv.id = 'current-progress';
            progressDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
            document.getElementById('messages').appendChild(progressDiv);
            scrollToBottom();

            // Create AbortController for this request
            currentAbortController = new AbortController();
            let itemsProcessed = [];
            pendingRequest = { message, progressDiv, itemsProcessed, isCartAdd: true };

            try {
                // Use streaming endpoint (same as sendMessage)
                const response = await fetch('/api/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ thread_id: threadId, message: message }),
                    signal: currentAbortController.signal
                });

                if (!response.ok) {
                    throw new Error(`HTTP error: ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let shouldClearList = false;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\\n\\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const event = JSON.parse(line.slice(6));
                                handleStreamEvent(event, progressDiv, itemsProcessed);

                                // Track if we should clear the list (on successful completion)
                                if (event.type === 'complete' && event.success_count > 0) {
                                    shouldClearList = true;
                                }
                            } catch (e) {
                                console.error('Failed to parse SSE event:', e);
                            }
                        }
                    }
                }

                // Clear the grocery list after successful addition
                if (shouldClearList) {
                    groceryList = [];
                    renderGroceryList();
                    saveListToStorage();
                }

                // Successfully completed
                pendingRequest = null;

            } catch (error) {
                if (error.name === 'AbortError' || wasInterruptedByVisibility) {
                    // Connection interrupted by app switch - show reconnection message
                    // Keep job ID saved so we can poll for status on visibility return
                    progressDiv.innerHTML = `
                        <div style="opacity: 0.8;">
                            <div style="margin-bottom: 8px;">Connection paused while app was in background.</div>
                            <div style="font-size: 0.75rem; opacity: 0.6;">Will automatically reconnect when you return.</div>
                        </div>
                    `;
                    console.log('Stream interrupted by visibility change, job ID preserved:', currentJobId);
                    // Don't clear job ID - it will be polled on visibility return
                } else {
                    progressDiv.remove();
                    addMessage('Error: Failed to add items to cart. Please try again.', 'error');
                    clearJobId();  // Clear on real errors
                    console.error('Streaming error:', error);
                }
                pendingRequest = null;
            }

            currentAbortController = null;
            setInputEnabled(true);
            document.getElementById('message-input').focus();
        }

        async function sendMessage() {
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            if (!message || isProcessing) return;

            input.value = '';
            addMessage(message, 'user');
            setInputEnabled(false);
            wasInterruptedByVisibility = false;

            // Hide suggestions after first message
            document.getElementById('suggestions').style.display = 'none';

            // Create progress indicator
            const progressDiv = document.createElement('div');
            progressDiv.className = 'message assistant';
            progressDiv.id = 'current-progress';
            progressDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
            document.getElementById('messages').appendChild(progressDiv);
            scrollToBottom();

            // Create AbortController for this request
            currentAbortController = new AbortController();
            pendingRequest = { message, progressDiv, itemsProcessed: [] };

            try {
                // Use streaming endpoint
                const response = await fetch('/api/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ thread_id: threadId, message: message }),
                    signal: currentAbortController.signal
                });

                if (!response.ok) {
                    throw new Error(`HTTP error: ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let itemsProcessed = pendingRequest.itemsProcessed;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\\n\\n');
                    buffer = lines.pop(); // Keep incomplete data in buffer

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const event = JSON.parse(line.slice(6));
                                handleStreamEvent(event, progressDiv, itemsProcessed);
                            } catch (e) {
                                console.error('Failed to parse SSE event:', e);
                            }
                        }
                    }
                }

                // Successfully completed
                pendingRequest = null;

            } catch (error) {
                if (error.name === 'AbortError' || wasInterruptedByVisibility) {
                    // Connection interrupted by app switch - show reconnection message
                    // Keep job ID saved so we can poll for status on visibility return
                    progressDiv.innerHTML = `
                        <div style="opacity: 0.8;">
                            <div style="margin-bottom: 8px;">Connection paused while app was in background.</div>
                            <div style="font-size: 0.75rem; opacity: 0.6;">Will automatically reconnect when you return.</div>
                        </div>
                    `;
                    console.log('Stream interrupted by visibility change, job ID preserved:', currentJobId);
                    // Don't clear job ID - it will be polled on visibility return
                } else {
                    progressDiv.remove();
                    addMessage('Error: ' + error.message, 'error');
                    clearJobId();  // Clear on real errors
                    console.error('Streaming error:', error);
                }
                pendingRequest = null;
            }

            currentAbortController = null;
            setInputEnabled(true);
            document.getElementById('message-input').focus();
        }

        // Track per-item step progress for live updates
        let itemStepProgress = {};

        function handleStreamEvent(event, progressDiv, itemsProcessed) {
            const eventType = event.type || '';

            switch (eventType) {
                case 'job_id':
                    // Save job ID for reconnection
                    saveJobId(event.job_id);
                    console.log('Job started:', event.job_id);
                    break;

                case 'message':
                    // Final message from assistant
                    progressDiv.remove();
                    addMessage(event.content, 'assistant');
                    clearJobId();  // Job complete

                    // Parse and add items from response
                    const parsedItems = parseItemsFromResponse(event.content);
                    parsedItems.forEach(item => {
                        addToGroceryList(item.name, item.qty);
                    });
                    break;

                case 'error':
                    progressDiv.remove();
                    addMessage('Error: ' + event.message, 'error');
                    clearJobId();  // Job failed
                    break;

                case 'done':
                    // Stream complete - remove progress if still showing
                    if (progressDiv.parentNode) {
                        progressDiv.remove();
                    }
                    // Reset step tracking for next request
                    itemStepProgress = {};
                    clearJobId();  // Ensure cleared
                    break;

                case 'status':
                    // Status update (e.g., "Checking login status...")
                    progressDiv.innerHTML = `<span style="opacity: 0.7;">${escapeHtml(event.message || 'Processing...')}</span>`;
                    break;

                case 'item_start':
                    // Starting to process an item - initialize step tracking
                    itemStepProgress[event.item] = { step: 0, action: 'Starting...' };
                    updateProgressDisplay(progressDiv, itemsProcessed);
                    break;

                case 'step':
                    // Step progress within an item's processing
                    itemStepProgress[event.item] = {
                        step: event.step || 0,
                        action: event.action || '...'
                    };
                    updateProgressDisplay(progressDiv, itemsProcessed);
                    break;

                case 'item_complete':
                    // Item completed - remove from step tracking, add to completed
                    delete itemStepProgress[event.item];

                    const icon = event.status === 'success' ? '<span style="color: #4ade80;">&#10003;</span>'
                        : event.status === 'uncertain' ? '<span style="color: #fbbf24;">?</span>'
                        : '<span style="color: #f87171;">&#10007;</span>';

                    itemsProcessed.push({
                        item: event.item,
                        status: event.status,
                        icon: icon,
                        steps: event.steps || 0
                    });

                    updateProgressDisplay(progressDiv, itemsProcessed);
                    break;

                case 'complete':
                    // All items complete - show summary briefly before final message
                    progressDiv.innerHTML = `<span style="opacity: 0.7;">${escapeHtml(event.message || 'Complete')}</span>`;
                    break;

                default:
                    // Unknown event type - log for debugging
                    console.log('Unknown stream event:', event);
            }
            scrollToBottom();
        }

        function updateProgressDisplay(progressDiv, itemsProcessed) {
            // Calculate totals
            const inProgress = Object.keys(itemStepProgress).length;
            const completed = itemsProcessed.length;
            const total = inProgress + completed;

            let html = '';

            // Header showing overall progress
            if (total > 0) {
                html += `<div style="font-size: 0.75rem; opacity: 0.6; margin-bottom: 8px;">Progress: ${completed}/${total}</div>`;
            }

            html += '<div style="font-size: 0.85rem;">';

            // Show completed items first
            itemsProcessed.forEach(p => {
                const stepsInfo = p.steps ? ` <span style="opacity: 0.5; font-size: 0.7rem;">(${p.steps} steps)</span>` : '';
                html += `<div>${p.icon} ${escapeHtml(p.item)}${stepsInfo}</div>`;
            });

            // Show in-progress items with their current step
            for (const [item, progress] of Object.entries(itemStepProgress)) {
                let statusText = 'In Progress';
                if (progress.step > 0) {
                    statusText = `Step ${progress.step}`;
                    if (progress.action && progress.action !== 'Starting...') {
                        statusText += `: ${progress.action.substring(0, 40)}`;
                    }
                }
                html += `<div style="opacity: 0.7;">`;
                html += `<span class="typing-indicator" style="display: inline-block; vertical-align: middle; margin-right: 6px; padding: 0;"><span></span><span></span><span></span></span>`;
                html += `${escapeHtml(item)} <span style="font-size: 0.7rem; opacity: 0.6;">${escapeHtml(statusText)}</span>`;
                html += `</div>`;
            }

            html += '</div>';
            progressDiv.innerHTML = html;
        }

        function sendSuggestion(el) {
            document.getElementById('message-input').value = el.textContent;
            sendMessage();
        }

        function handleKeyPress(event) {
            if (event.key === 'Enter' && !isProcessing) {
                sendMessage();
            }
        }

        // Sidebar toggle for mobile
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('expanded');
        }

        // Close sidebar when clicking outside on mobile
        document.addEventListener('click', function(e) {
            const sidebar = document.getElementById('sidebar');
            if (window.innerWidth <= 768 &&
                sidebar.classList.contains('expanded') &&
                !sidebar.contains(e.target)) {
                sidebar.classList.remove('expanded');
            }
        });

        // Auto-expand sidebar when items are added on mobile
        function expandSidebarOnMobile() {
            if (window.innerWidth <= 768) {
                const sidebar = document.getElementById('sidebar');
                sidebar.classList.add('expanded');
                // Auto-collapse after 2 seconds
                setTimeout(() => {
                    sidebar.classList.remove('expanded');
                }, 2000);
            }
        }

        // Swipe gesture support for mobile bottom sheet
        let touchStartY = 0;
        let touchEndY = 0;

        document.getElementById('sidebar').addEventListener('touchstart', function(e) {
            touchStartY = e.changedTouches[0].screenY;
        }, { passive: true });

        document.getElementById('sidebar').addEventListener('touchend', function(e) {
            touchEndY = e.changedTouches[0].screenY;
            handleSwipe();
        }, { passive: true });

        function handleSwipe() {
            const sidebar = document.getElementById('sidebar');
            const swipeDistance = touchStartY - touchEndY;

            if (swipeDistance > 50) {
                // Swipe up - expand
                sidebar.classList.add('expanded');
            } else if (swipeDistance < -50) {
                // Swipe down - collapse
                sidebar.classList.remove('expanded');
            }
        }

        // Handle mobile keyboard / viewport resize
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', () => {
                const viewport = window.visualViewport;
                const messages = document.getElementById('messages');

                // Adjust for keyboard
                if (viewport.height < window.innerHeight * 0.8) {
                    // Keyboard is open
                    document.body.style.height = viewport.height + 'px';
                    messages.scrollTop = messages.scrollHeight;
                } else {
                    document.body.style.height = '100%';
                }
            });
        }

        // Handle page visibility changes (mobile app switching)
        document.addEventListener('visibilitychange', () => {
            if (document.hidden && isProcessing) {
                // Page is being hidden while processing - mark as interrupted
                wasInterruptedByVisibility = true;
                console.log('Page hidden while processing - marking as interrupted');

                // Note: We don't abort immediately - the connection may survive brief switches
                // The abort will happen naturally if the connection drops
            } else if (!document.hidden) {
                // Page is visible again - check for pending jobs to resume
                console.log('Page visible again');

                // Check if we have a saved job to resume
                const savedJobId = getSavedJobId();
                if (savedJobId && !isProcessing) {
                    console.log('Resuming saved job:', savedJobId);
                    checkAndResumeJob();
                } else if (wasInterruptedByVisibility && currentJobId) {
                    // We were interrupted and have a current job - poll for its status
                    console.log('Reconnecting to interrupted job:', currentJobId);
                    const progressDiv = document.getElementById('current-progress');
                    if (progressDiv) {
                        progressDiv.innerHTML = '<div style="opacity: 0.7;">Reconnecting...</div>';
                        pollUntilComplete(currentJobId, progressDiv);
                    }
                }
                wasInterruptedByVisibility = false;
            }
        });

        // Scroll to bottom helper
        function scrollToBottom() {
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }

        // Initialize
        document.getElementById('message-input').focus();
        renderGroceryList();

        // Check for any pending jobs to resume (e.g., after page refresh)
        checkAndResumeJob();
    </script>
</body>
</html>
"""

    @flask_app.route("/")
    def index():
        """Serve the chat UI."""
        return CHAT_HTML

    @flask_app.route("/api/chat", methods=["POST"])
    def chat():
        """Handle chat messages from the UI."""
        data = request.json
        thread_id = data.get("thread_id")
        message = data.get("message")

        if not thread_id or not message:
            return jsonify({"error": "Missing thread_id or message"}), 400

        try:
            agent = get_or_create_agent(thread_id)
            config = {"configurable": {"thread_id": thread_id}}

            # Invoke the agent
            result = agent.invoke(
                {"messages": [HumanMessage(content=message)]}, config=config
            )

            # Get the last AI message
            last_message = result["messages"][-1]
            if isinstance(last_message, AIMessage):
                return jsonify({"message": last_message.content})
            else:
                return jsonify({"message": "Processing complete."})

        except Exception as e:
            import traceback

            print(f"[Chat] Error: {e}")
            print(f"[Chat] Traceback: {traceback.format_exc()}")
            return jsonify({"error": str(e)}), 500

    @flask_app.route("/api/chat/stream", methods=["POST"])
    def chat_stream():
        """Handle chat messages with SSE streaming for progress updates.

        Returns Server-Sent Events with progress updates as the agent works.
        Event types:
            - {"type": "job_id", "job_id": str}: Job ID for reconnection
            - {"type": "status|item_complete|complete", ...}: Progress from tools
            - {"type": "message", "content": str}: Final assistant message
            - {"type": "done"}: Stream complete
            - {"type": "error", "message": str}: Error occurred
        """
        import asyncio
        import json
        import queue
        import threading

        from flask import Response

        data = request.json
        thread_id = data.get("thread_id")
        message = data.get("message")

        if not thread_id or not message:
            return jsonify({"error": "Missing thread_id or message"}), 400

        # Create a job for tracking
        job_id = create_job(thread_id, message)

        # Thread-safe queue for streaming events
        event_queue = queue.Queue()

        def run_agent_async():
            """Run the async agent in a separate thread, pushing events to queue."""
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
                        # Handle custom progress events
                        if isinstance(chunk, tuple) and len(chunk) == 2:
                            mode, chunk_data = chunk
                            if mode == "custom" and isinstance(chunk_data, dict):
                                if "progress" in chunk_data:
                                    progress_event = chunk_data["progress"]
                                    # Push progress event to queue immediately
                                    event_queue.put(progress_event)
                                    # Also update job state for reconnection
                                    update_job_progress(job_id, progress_event)
                            elif mode == "updates" and isinstance(chunk_data, dict):
                                # Check for final message in updates
                                if "chat" in chunk_data:
                                    msgs = chunk_data["chat"].get("messages", [])
                                    for msg in msgs:
                                        if isinstance(msg, AIMessage):
                                            final_content = msg.content

                    return final_content

                # Run the async function
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    final_content = loop.run_until_complete(stream_agent())
                finally:
                    loop.close()

                # Push final message
                if final_content:
                    msg_event = {"type": "message", "content": final_content}
                    event_queue.put(msg_event)
                    update_job_progress(job_id, msg_event)
                else:
                    # Fallback: get message from state
                    agent = get_or_create_agent(thread_id)
                    config = {"configurable": {"thread_id": thread_id}}
                    state = agent.get_state(config)
                    if state.values.get("messages"):
                        last_msg = state.values["messages"][-1]
                        if isinstance(last_msg, AIMessage):
                            msg_event = {"type": "message", "content": last_msg.content}
                            event_queue.put(msg_event)
                            update_job_progress(job_id, msg_event)

                # Signal completion
                event_queue.put({"type": "done"})
                update_job_progress(job_id, {"type": "complete"})

            except Exception as e:
                import traceback

                print(f"[ChatStream] Error: {e}")
                print(f"[ChatStream] Traceback: {traceback.format_exc()}")
                error_event = {"type": "error", "message": str(e)}
                event_queue.put(error_event)
                event_queue.put({"type": "done"})
                update_job_progress(job_id, error_event)

        def generate():
            """Generator that yields SSE events from the queue."""
            # First, send the job ID so client can save it for reconnection
            yield f"data: {json.dumps({'type': 'job_id', 'job_id': job_id})}\n\n"

            # Start the agent in a background thread
            agent_thread = threading.Thread(target=run_agent_async)
            agent_thread.start()

            # Yield events as they arrive
            while True:
                try:
                    # Wait for event with timeout to allow checking if thread is alive
                    event = event_queue.get(timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"

                    # Stop on done or error
                    if event.get("type") in ("done", "error"):
                        break

                except queue.Empty:
                    # Check if thread is still running
                    if not agent_thread.is_alive():
                        break
                    # Send keepalive comment to prevent timeout
                    yield ": keepalive\n\n"

            agent_thread.join(timeout=5.0)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @flask_app.route("/api/job/<job_id>/status", methods=["GET"])
    def job_status(job_id):
        """Get the current status of a job for reconnection."""
        job = get_job_status(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify(job)

    @flask_app.route("/api/reset", methods=["POST"])
    def reset():
        """Reset a conversation thread."""
        data = request.json
        thread_id = data.get("thread_id")

        if thread_id in agents:
            del agents[thread_id]

        return jsonify({"status": "reset"})

    @flask_app.route("/health")
    def health():
        """Health check endpoint."""
        return jsonify({"status": "ok"})

    return flask_app
