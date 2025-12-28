"""
Flask Chat UI for the Grocery Shopping Agent.

This provides a web-based chat interface that wraps the LangGraph agent
and uses Modal for browser automation.

Run with: uv run chat_app.py
"""

import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from langchain_core.messages import AIMessage, HumanMessage

from chat_agent import create_chat_agent

load_dotenv()

app = Flask(__name__)

# Store agent instances per session
agents = {}


def get_or_create_agent(thread_id: str):
    """Get existing agent or create new one for the thread."""
    if thread_id not in agents:
        agents[thread_id] = create_chat_agent()
    return agents[thread_id]


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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f5f5;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            background: #e31837;
            color: white;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .header h1 { font-size: 1.3rem; font-weight: 600; }
        .header .subtitle { font-size: 0.85rem; opacity: 0.9; margin-top: 4px; }
        .chat-container {
            flex: 1;
            max-width: 800px;
            width: 100%;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            background: white;
            box-shadow: 0 0 20px rgba(0,0,0,0.1);
        }
        .messages {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        .message {
            max-width: 80%;
            padding: 12px 16px;
            border-radius: 16px;
            line-height: 1.5;
            white-space: pre-wrap;
        }
        .message.user {
            align-self: flex-end;
            background: #e31837;
            color: white;
            border-bottom-right-radius: 4px;
        }
        .message.assistant {
            align-self: flex-start;
            background: #f0f0f0;
            color: #333;
            border-bottom-left-radius: 4px;
        }
        .message.system {
            align-self: center;
            background: #fff3cd;
            color: #856404;
            font-size: 0.9rem;
            max-width: 90%;
        }
        .message.error {
            align-self: center;
            background: #f8d7da;
            color: #721c24;
            font-size: 0.9rem;
        }
        .typing-indicator {
            align-self: flex-start;
            background: #f0f0f0;
            padding: 12px 16px;
            border-radius: 16px;
            border-bottom-left-radius: 4px;
        }
        .typing-indicator span {
            display: inline-block;
            width: 8px;
            height: 8px;
            background: #999;
            border-radius: 50%;
            margin-right: 4px;
            animation: bounce 1.4s infinite ease-in-out both;
        }
        .typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
        .typing-indicator span:nth-child(2) { animation-delay: -0.16s; }
        @keyframes bounce {
            0%, 80%, 100% { transform: scale(0); }
            40% { transform: scale(1); }
        }
        .input-area {
            padding: 15px 20px;
            background: #fff;
            border-top: 1px solid #e0e0e0;
            display: flex;
            gap: 10px;
        }
        .input-area input {
            flex: 1;
            padding: 12px 16px;
            border: 2px solid #e0e0e0;
            border-radius: 24px;
            font-size: 15px;
            outline: none;
            transition: border-color 0.2s;
        }
        .input-area input:focus { border-color: #e31837; }
        .input-area input:disabled { background: #f5f5f5; }
        .input-area button {
            padding: 12px 24px;
            background: #e31837;
            color: white;
            border: none;
            border-radius: 24px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }
        .input-area button:hover:not(:disabled) { background: #c41530; }
        .input-area button:disabled { opacity: 0.5; cursor: not-allowed; }
        .suggestions {
            padding: 10px 20px;
            background: #fafafa;
            border-top: 1px solid #e0e0e0;
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .suggestion {
            padding: 8px 14px;
            background: white;
            border: 1px solid #ddd;
            border-radius: 16px;
            font-size: 13px;
            color: #666;
            cursor: pointer;
            transition: all 0.2s;
        }
        .suggestion:hover {
            border-color: #e31837;
            color: #e31837;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Superstore Shopping Assistant</h1>
        <div class="subtitle">Tell me what you'd like to cook or buy</div>
    </div>
    <div class="chat-container">
        <div class="messages" id="messages">
            <div class="message assistant">Hi! I'm your grocery shopping assistant for Real Canadian Superstore.

Tell me what you'd like to make or buy, and I'll help you add items to your cart.

For example, try saying:
- "I want to make spaghetti bolognese"
- "Add milk, eggs, and bread to my cart"
- "What do I need for banana pancakes?"</div>
        </div>
        <div class="suggestions" id="suggestions">
            <span class="suggestion" onclick="sendSuggestion(this)">I want to make pasta carbonara</span>
            <span class="suggestion" onclick="sendSuggestion(this)">Add milk and eggs</span>
            <span class="suggestion" onclick="sendSuggestion(this)">What do I need for pancakes?</span>
        </div>
        <div class="input-area">
            <input type="text" id="message-input" placeholder="Type your message..." onkeypress="handleKeyPress(event)">
            <button id="send-btn" onclick="sendMessage()">Send</button>
        </div>
    </div>

    <script>
        const threadId = 'session-' + Math.random().toString(36).substr(2, 9);
        let isProcessing = false;

        function addMessage(content, type) {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'message ' + type;
            div.textContent = content;
            messages.appendChild(div);
            messages.scrollTop = messages.scrollHeight;
        }

        function showTyping() {
            const messages = document.getElementById('messages');
            const div = document.createElement('div');
            div.className = 'typing-indicator';
            div.id = 'typing';
            div.innerHTML = '<span></span><span></span><span></span>';
            messages.appendChild(div);
            messages.scrollTop = messages.scrollHeight;
        }

        function hideTyping() {
            const typing = document.getElementById('typing');
            if (typing) typing.remove();
        }

        function setInputEnabled(enabled) {
            document.getElementById('message-input').disabled = !enabled;
            document.getElementById('send-btn').disabled = !enabled;
            isProcessing = !enabled;
        }

        async function sendMessage() {
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            if (!message || isProcessing) return;

            input.value = '';
            addMessage(message, 'user');
            setInputEnabled(false);
            showTyping();

            // Hide suggestions after first message
            document.getElementById('suggestions').style.display = 'none';

            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ thread_id: threadId, message: message })
                });

                const data = await response.json();
                hideTyping();

                if (data.error) {
                    addMessage('Error: ' + data.error, 'error');
                } else if (data.message) {
                    addMessage(data.message, 'assistant');
                }
            } catch (error) {
                hideTyping();
                addMessage('Error: Failed to send message. Please try again.', 'error');
            }

            setInputEnabled(true);
            document.getElementById('message-input').focus();
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

        // Focus input on load
        document.getElementById('message-input').focus();
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    """Serve the chat UI."""
    return CHAT_HTML


@app.route("/api/chat", methods=["POST"])
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
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def reset():
    """Reset a conversation thread."""
    data = request.json
    thread_id = data.get("thread_id")

    if thread_id in agents:
        del agents[thread_id]

    return jsonify({"status": "reset"})


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("\n🛒 Superstore Shopping Chat")
    print("=" * 40)
    print("Open http://localhost:5001 in your browser")
    print("Make sure Modal app is deployed: modal deploy modal_app.py")
    print("=" * 40 + "\n")
    app.run(host="0.0.0.0", port=5001, debug=True)
