// public/app.ts
function clog(msg, level = "info") {
  console.log(`[client:${level}] ${msg}`);
  fetch("/api/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level, msg })
  }).catch(() => {
  });
}
var REALTIME_MODEL = "gpt-realtime-mini";
var REALTIME_URL = `https://api.openai.com/v1/realtime?model=${REALTIME_MODEL}`;
var state = {
  pc: null,
  dc: null,
  audioEl: null,
  cart_id: null,
  store_id: null,
  currentAssistantMsg: "",
  currentResponseId: null,
  productNames: {}
};
var startBtn = document.getElementById("start-btn");
var startSection = document.getElementById("start-section");
var sessionSection = document.getElementById("session-section");
var statusText = document.getElementById("status-text");
var statusTextActive = document.getElementById("status-text-active");
var statusDot = document.querySelector("#status-active .dot");
var transcript = document.getElementById("transcript");
var cartSection = document.getElementById("cart-section");
var cartItems = document.getElementById("cart-items");
var cartLink = document.getElementById("cart-link");
var stopBtn = document.getElementById("stop-btn");
startBtn.addEventListener("click", startSession);
stopBtn.addEventListener("click", endSession);
function setStatus(s) {
  const labels = {
    connecting: "Connecting...",
    listening: "Listening...",
    thinking: "Thinking...",
    speaking: "Speaking...",
    disconnected: "Disconnected"
  };
  statusDot.className = `dot ${s}`;
  statusTextActive.textContent = labels[s] || s;
}
function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  transcript.appendChild(el);
  transcript.scrollTop = transcript.scrollHeight;
  return el;
}
function addCartItem(name, qty) {
  cartSection.classList.add("active");
  const li = document.createElement("li");
  const nameSpan = document.createElement("span");
  nameSpan.textContent = name;
  li.appendChild(nameSpan);
  if (qty) {
    const qtySpan = document.createElement("span");
    qtySpan.textContent = qty;
    qtySpan.style.color = "#8888a0";
    li.appendChild(qtySpan);
  }
  cartItems.appendChild(li);
}
function showCartLink() {
  if (state.cart_id) {
    const a = document.getElementById("cart-link-a");
    a.href = `https://www.realcanadiansuperstore.ca/en/cartReview?forceCartId=${state.cart_id}`;
  }
  cartLink.style.display = "block";
}
async function startSession() {
  try {
    startSection.style.display = "none";
    sessionSection.classList.add("active");
    stopBtn.style.display = "block";
    setStatus("connecting");
    clog("Requesting ephemeral token...");
    const tokenRes = await fetch("/token");
    if (!tokenRes.ok) throw new Error(`Token request failed: ${tokenRes.status}`);
    const tokenData = await tokenRes.json();
    const ephemeralKey = tokenData.client_secret.value;
    const pc = new RTCPeerConnection();
    state.pc = pc;
    const audioEl = document.createElement("audio");
    audioEl.autoplay = true;
    state.audioEl = audioEl;
    pc.ontrack = (ev) => {
      audioEl.srcObject = ev.streams[0];
    };
    const localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));
    const dc = pc.createDataChannel("oai-events");
    state.dc = dc;
    dc.onopen = () => {
      clog("Data channel open, WebRTC connected");
      setStatus("listening");
      addMessage("system", "Connected - start speaking!");
    };
    dc.onclose = () => {
      setStatus("disconnected");
    };
    dc.onmessage = (ev) => {
      handleServerEvent(JSON.parse(ev.data));
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const sdpRes = await fetch(REALTIME_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ephemeralKey}`,
        "Content-Type": "application/sdp"
      },
      body: pc.localDescription.sdp
    });
    if (!sdpRes.ok) throw new Error(`SDP exchange failed: ${sdpRes.status}`);
    clog("SDP exchange complete");
    const answerSdp = await sdpRes.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    pc.oniceconnectionstatechange = () => {
      if (pc.iceConnectionState === "disconnected" || pc.iceConnectionState === "failed") {
        setStatus("disconnected");
      }
    };
  } catch (err) {
    clog(`Session start failed: ${err.message}`, "error");
    addMessage("system", `Error: ${err.message}`);
    setStatus("disconnected");
  }
}
function endSession() {
  if (state.pc) {
    state.pc.close();
    state.pc = null;
  }
  if (state.dc) {
    state.dc = null;
  }
  setStatus("disconnected");
  stopBtn.style.display = "none";
  addMessage("system", "Session ended.");
  if (state.cart_id) {
    showCartLink();
  }
}
var currentMsgEl = null;
function handleServerEvent(event) {
  switch (event.type) {
    case "response.audio_transcript.delta":
      setStatus("speaking");
      if (!currentMsgEl) {
        state.currentAssistantMsg = "";
        currentMsgEl = addMessage("assistant", "");
      }
      state.currentAssistantMsg += event.delta;
      currentMsgEl.textContent = state.currentAssistantMsg;
      transcript.scrollTop = transcript.scrollHeight;
      break;
    case "response.audio_transcript.done":
      if (currentMsgEl) {
        currentMsgEl.textContent = event.transcript;
      }
      currentMsgEl = null;
      state.currentAssistantMsg = "";
      setStatus("listening");
      break;
    case "conversation.item.input_audio_transcription.completed":
      if (event.transcript) {
        clog(`User said: "${event.transcript}"`);
        addMessage("user", event.transcript);
      }
      break;
    case "response.function_call_arguments.done":
      setStatus("thinking");
      handleToolCall(event);
      break;
    case "response.done":
      if (event.response?.output) {
        for (const item of event.response.output) {
          if (item.type === "function_call" && item.status === "completed") {
          }
        }
      }
      if (!currentMsgEl) {
        setStatus("listening");
      }
      break;
    case "error":
      console.error("Realtime error:", event.error);
      addMessage("system", `Error: ${event.error?.message || "Unknown error"}`);
      break;
  }
}
async function handleToolCall(event) {
  const { name, arguments: argsStr, call_id } = event;
  let args;
  try {
    args = JSON.parse(argsStr);
  } catch {
    args = {};
  }
  clog(`Tool call: ${name}(${argsStr})`);
  addMessage("system", `Looking up: ${name.replace(/_/g, " ")}...`);
  let result;
  try {
    result = await callBackend(name, args);
  } catch (err) {
    result = { error: err.message };
  }
  if (name === "select_store" && result.cart_id) {
    state.cart_id = result.cart_id;
    state.store_id = args.store_id || result.store_id;
    addMessage("system", `Store selected, cart created.`);
  }
  if (name === "search_products" && result.products) {
    for (const p of result.products) {
      if (p.code && p.name) {
        state.productNames[p.code] = p.brand ? `${p.brand} ${p.name}` : p.name;
      }
    }
  }
  if (name === "add_to_cart" && !result.error && args.items) {
    for (const item of args.items) {
      const displayName = state.productNames[item.product_code] || item.product_code;
      addCartItem(displayName, `x${item.quantity}`);
    }
  }
  if (name === "finish_shopping") {
    showCartLink();
    addMessage("system", "Shopping complete! Review your cart on Superstore.");
  }
  sendDataChannelMessage({
    type: "conversation.item.create",
    item: {
      type: "function_call_output",
      call_id,
      output: JSON.stringify(result)
    }
  });
  sendDataChannelMessage({
    type: "response.create"
  });
}
async function callBackend(fnName, args) {
  const endpointMap = {
    find_nearest_stores: "/api/find-stores",
    select_store: "/api/create-cart",
    search_products: "/api/search-products",
    add_to_cart: "/api/add-to-cart",
    finish_shopping: "/api/finish-shopping"
  };
  const endpoint = endpointMap[fnName];
  if (!endpoint) {
    return { error: `Unknown function: ${fnName}` };
  }
  const body = { ...args };
  if (state.cart_id && !body.cart_id) body.cart_id = state.cart_id;
  if (state.store_id && !body.store_id) body.store_id = state.store_id;
  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}
function sendDataChannelMessage(msg) {
  if (state.dc && state.dc.readyState === "open") {
    state.dc.send(JSON.stringify(msg));
  } else {
    console.warn("Data channel not open, cannot send:", msg);
  }
}
