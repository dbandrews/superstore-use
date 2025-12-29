"""
Flask Chat UI for the Grocery Shopping Agent.

This provides a web-based chat interface that wraps the LangGraph agent
and uses Modal for browser automation.

Run with: uv run chat_app.py
"""

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
        .main-container {
            flex: 1;
            display: flex;
            max-width: 1200px;
            width: 100%;
            margin: 0 auto;
            overflow: hidden;
        }
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: white;
            box-shadow: 0 0 20px rgba(0,0,0,0.1);
            min-width: 0;
        }
        .sidebar {
            width: 320px;
            background: white;
            border-left: 1px solid #e0e0e0;
            display: flex;
            flex-direction: column;
            box-shadow: -2px 0 10px rgba(0,0,0,0.05);
        }
        .sidebar-header {
            padding: 15px 20px;
            background: #f8f8f8;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .sidebar-header h2 {
            font-size: 1rem;
            font-weight: 600;
            color: #333;
        }
        .item-count {
            background: #e31837;
            color: white;
            font-size: 0.75rem;
            padding: 2px 8px;
            border-radius: 10px;
            font-weight: 600;
        }
        .grocery-list {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }
        .grocery-item {
            display: flex;
            align-items: center;
            padding: 12px 15px;
            background: #f9f9f9;
            border-radius: 8px;
            margin-bottom: 8px;
            transition: all 0.2s;
        }
        .grocery-item:hover {
            background: #f0f0f0;
        }
        .grocery-item .item-info {
            flex: 1;
            min-width: 0;
        }
        .grocery-item .item-name {
            font-size: 0.9rem;
            font-weight: 500;
            color: #333;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .grocery-item .item-qty {
            font-size: 0.8rem;
            color: #666;
            margin-top: 2px;
        }
        .grocery-item .remove-btn {
            background: none;
            border: none;
            color: #999;
            cursor: pointer;
            padding: 5px;
            font-size: 1.2rem;
            line-height: 1;
            transition: color 0.2s;
        }
        .grocery-item .remove-btn:hover {
            color: #e31837;
        }
        .grocery-item .edit-qty {
            display: flex;
            align-items: center;
            gap: 5px;
            margin-right: 10px;
        }
        .grocery-item .qty-btn {
            width: 24px;
            height: 24px;
            border: 1px solid #ddd;
            background: white;
            border-radius: 4px;
            cursor: pointer;
            font-size: 1rem;
            line-height: 1;
            color: #666;
            transition: all 0.2s;
        }
        .grocery-item .qty-btn:hover {
            border-color: #e31837;
            color: #e31837;
        }
        .grocery-item .qty-display {
            font-size: 0.85rem;
            font-weight: 600;
            min-width: 20px;
            text-align: center;
        }
        .empty-list {
            text-align: center;
            padding: 40px 20px;
            color: #999;
        }
        .empty-list .icon {
            font-size: 3rem;
            margin-bottom: 10px;
        }
        .empty-list p {
            font-size: 0.9rem;
        }
        .sidebar-footer {
            padding: 15px;
            border-top: 1px solid #e0e0e0;
            background: #f8f8f8;
        }
        .add-item-form {
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
        }
        .add-item-form input {
            flex: 1;
            padding: 10px 12px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 0.9rem;
            outline: none;
        }
        .add-item-form input:focus {
            border-color: #e31837;
        }
        .add-item-form button {
            padding: 10px 15px;
            background: #f0f0f0;
            border: 1px solid #ddd;
            border-radius: 6px;
            cursor: pointer;
            font-size: 1rem;
            transition: all 0.2s;
        }
        .add-item-form button:hover {
            background: #e0e0e0;
        }
        .sidebar-actions {
            display: flex;
            gap: 8px;
        }
        .clear-btn {
            flex: 1;
            padding: 12px;
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .clear-btn:hover {
            border-color: #e31837;
            color: #e31837;
        }
        .add-all-btn {
            flex: 2;
            padding: 12px;
            background: #e31837;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 0.9rem;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }
        .add-all-btn:hover:not(:disabled) {
            background: #c41530;
        }
        .add-all-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
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
            max-width: 85%;
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
        @media (max-width: 768px) {
            .main-container {
                flex-direction: column;
            }
            .sidebar {
                width: 100%;
                max-height: 40vh;
                border-left: none;
                border-top: 1px solid #e0e0e0;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Superstore Shopping Assistant</h1>
        <div class="subtitle">Tell me what you'd like to cook or buy</div>
    </div>
    <div class="main-container">
        <div class="chat-container">
            <div class="messages" id="messages">
                <div class="message assistant">Hi! I'm your grocery shopping assistant for Real Canadian Superstore.

Tell me what you'd like to make or buy, and I'll help build your grocery list.

For example, try saying:
- "I want to make spaghetti bolognese"
- "What do I need for banana pancakes?"
- "Add milk, eggs, and bread to my list"

Items will appear in your grocery list on the right. When you're ready, click "Add All to Cart" to have me add everything to your Superstore cart!</div>
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
        <div class="sidebar">
            <div class="sidebar-header">
                <h2>Grocery List</h2>
                <span class="item-count" id="item-count">0</span>
            </div>
            <div class="grocery-list" id="grocery-list">
                <div class="empty-list" id="empty-list">
                    <div class="icon">🛒</div>
                    <p>Your grocery list is empty.<br>Chat with me to add items!</p>
                </div>
            </div>
            <div class="sidebar-footer">
                <div class="add-item-form">
                    <input type="text" id="manual-item-input" placeholder="Add item manually..." onkeypress="handleManualItemKeyPress(event)">
                    <button onclick="addManualItem()">+</button>
                </div>
                <div class="sidebar-actions">
                    <button class="clear-btn" onclick="clearList()">Clear All</button>
                    <button class="add-all-btn" id="add-all-btn" onclick="addAllToCart()" disabled>Add All to Cart</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const threadId = 'session-' + Math.random().toString(36).substr(2, 9);
        let isProcessing = false;
        let groceryList = [];

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
            showTyping();

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
                    // Clear the list after successful addition
                    if (!data.message.toLowerCase().includes('error') &&
                        !data.message.toLowerCase().includes('failed')) {
                        groceryList = [];
                        renderGroceryList();
                        saveListToStorage();
                    }
                }
            } catch (error) {
                hideTyping();
                addMessage('Error: Failed to add items to cart. Please try again.', 'error');
            }

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

                    // Parse and add items from response
                    if (data.grocery_items) {
                        data.grocery_items.forEach(item => {
                            addToGroceryList(item.name, item.qty || 1);
                        });
                    } else {
                        // Try to parse items from the message text
                        const parsedItems = parseItemsFromResponse(data.message);
                        parsedItems.forEach(item => {
                            addToGroceryList(item.name, item.qty);
                        });
                    }
                }
            } catch (error) {
                hideTyping();
                //addMessage('Error: Failed to send message. Please try again.', 'error');
                console.error('Error: Failed to send message. Please try again.', error);
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

        // Initialize
        document.getElementById('message-input').focus();
        renderGroceryList();
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
        result = agent.invoke({"messages": [HumanMessage(content=message)]}, config=config)

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
