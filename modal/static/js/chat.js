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
    const lines = text.split(/\r?\n|\r/);
    for (const line of lines) {
        const trimmed = line.trim();
        const bulletMatch = trimmed.match(/^[-•*]\s+(.+)$/) || trimmed.match(/^\d+[.)]\s+(.+)$/);
        if (bulletMatch) {
            let itemText = bulletMatch[1].trim();
            let qty = 1;
            const qtyPatterns = [/^(\d+)\s*x\s+(.+)$/i, /^(.+?)\s*x\s*(\d+)$/i, /^(.+?)\s*\((\d+)\)$/];
            for (const pat of qtyPatterns) { const m = itemText.match(pat); if (m) { if (/^\d+$/.test(m[1])) { qty = parseInt(m[1]); itemText = m[2]; } else { qty = parseInt(m[2]); itemText = m[1]; } break; } }
            itemText = itemText.replace(/\*\*/g, '').replace(/[,;:]$/, '').trim();
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
        case 'complete': progressDiv.innerHTML = `<span style="opacity: 0.7;">${escapeHtml(event.message || 'Complete')}</span>`; break;
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
                const lines = buffer.split('\n\n');
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
