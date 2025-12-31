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

        // Scroll to bottom helper
        function scrollToBottom() {
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
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
