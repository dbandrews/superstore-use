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
var STATE_COLORS = {
  connecting: [0.94, 0.63, 0.19],
  listening: [0.2, 0.82, 0.41],
  speaking: [0.88, 0.24, 0.24],
  thinking: [0.29, 0.56, 0.96],
  disconnected: [0.35, 0.38, 0.5]
};
var state = {
  pc: null,
  dc: null,
  audioEl: null,
  localStream: null,
  cart_id: null,
  store_id: null,
  currentAssistantMsg: "",
  currentResponseId: null,
  productNames: {},
  // Audio analysis
  audioCtx: null,
  analyser: null,
  analyserData: null,
  smoothedAudioLevel: 0,
  remoteSource: null,
  // Orb
  currentStatus: "disconnected",
  orbColor: [0.35, 0.38, 0.5],
  orbAnimId: null,
  orbGL: null,
  // Caption
  captionTimeout: null,
  // Transcript
  hasTranscriptContent: false
};
var startBtn = document.getElementById("start-btn");
var startSection = document.getElementById("start-section");
var sessionSection = document.getElementById("session-section");
var statusTextActive = document.getElementById("status-text-active");
var statusDot = document.querySelector("#status-active .dot");
var orbGlow = document.getElementById("orb-glow");
var orbClip = document.getElementById("orb-clip");
var liveCaption = document.getElementById("live-caption");
var cartSection = document.getElementById("cart-section");
var cartItems = document.getElementById("cart-items");
var cartLink = document.getElementById("cart-link");
var transcriptToggle = document.getElementById("transcript-toggle");
var transcriptPanel = document.getElementById("transcript-panel");
var transcript = document.getElementById("transcript");
var stopBtn = document.getElementById("stop-btn");
startBtn.addEventListener("click", startSession);
stopBtn.addEventListener("click", endSession);
transcriptToggle.addEventListener("click", toggleTranscript);
var VERT_SRC = `
attribute vec2 position;
attribute vec2 uv;
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position, 0, 1);
}`;
var FRAG_SRC = `
precision highp float;
uniform float uTime;
uniform vec3 uColor;
uniform vec3 uResolution;
uniform float uAmplitude;
uniform float uSpeed;
varying vec2 vUv;
void main() {
  float mr = min(uResolution.x, uResolution.y);
  vec2 uv = (vUv * 2.0 - 1.0) * uResolution.xy / mr;
  float d = -uTime * 0.5 * uSpeed;
  float a = 0.0;
  for (float i = 0.0; i < 8.0; ++i) {
    a += cos(i - d - a * uv.x);
    d += sin(uv.y * i + a);
  }
  vec3 col = vec3(cos(uv * vec2(d, a)) * 0.6 + 0.4, cos(a + d) * 0.5 + 0.5);
  col = cos(col * cos(vec3(d, a, 2.5)) * 0.5 + 0.5) * uColor;
  gl_FragColor = vec4(col, 1.0);
}`;
function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    clog("Shader compile error: " + gl.getShaderInfoLog(shader), "error");
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}
function linkProgram(gl, vs, fs) {
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    clog("Program link error: " + gl.getProgramInfoLog(program), "error");
    return null;
  }
  return program;
}
function initOrbGL() {
  const canvas = document.getElementById("orb-canvas");
  const gl = canvas.getContext("webgl");
  if (!gl) {
    clog("WebGL not supported", "error");
    return;
  }
  const vs = compileShader(gl, gl.VERTEX_SHADER, VERT_SRC);
  const fs = compileShader(gl, gl.FRAGMENT_SHADER, FRAG_SRC);
  if (!vs || !fs) return;
  const program = linkProgram(gl, vs, fs);
  if (!program) return;
  const posBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const uvBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 2, 0, 0, 2]), gl.STATIC_DRAW);
  state.orbGL = {
    gl,
    program,
    posBuf,
    uvBuf,
    aPosition: gl.getAttribLocation(program, "position"),
    aUv: gl.getAttribLocation(program, "uv"),
    uTime: gl.getUniformLocation(program, "uTime"),
    uColor: gl.getUniformLocation(program, "uColor"),
    uResolution: gl.getUniformLocation(program, "uResolution"),
    uAmplitude: gl.getUniformLocation(program, "uAmplitude"),
    uSpeed: gl.getUniformLocation(program, "uSpeed")
  };
  resizeOrbCanvas();
  window.addEventListener("resize", resizeOrbCanvas);
}
function resizeOrbCanvas() {
  if (!state.orbGL) return;
  const canvas = state.orbGL.gl.canvas;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
}
function renderOrbFrame(time) {
  const o = state.orbGL;
  if (!o) return;
  const gl = o.gl;
  if (state.analyser && state.analyserData) {
    state.analyser.getByteFrequencyData(state.analyserData);
    let sum = 0;
    for (let i = 0; i < state.analyserData.length; i++) sum += state.analyserData[i];
    const avg = sum / state.analyserData.length;
    const norm = Math.min(1, Math.max(0, (avg - 16) / 90));
    state.smoothedAudioLevel += (norm - state.smoothedAudioLevel) * 0.06;
  } else {
    state.smoothedAudioLevel += (0 - state.smoothedAudioLevel) * 0.04;
  }
  const level = state.smoothedAudioLevel;
  const target = STATE_COLORS[state.currentStatus] || STATE_COLORS.disconnected;
  for (let i = 0; i < 3; i++) {
    state.orbColor[i] += (target[i] - state.orbColor[i]) * 0.04;
  }
  const amplitude = 0.18 + level * 0.25;
  const speed = 0.75 + level * 1.5;
  gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
  gl.useProgram(o.program);
  gl.bindBuffer(gl.ARRAY_BUFFER, o.posBuf);
  gl.enableVertexAttribArray(o.aPosition);
  gl.vertexAttribPointer(o.aPosition, 2, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, o.uvBuf);
  gl.enableVertexAttribArray(o.aUv);
  gl.vertexAttribPointer(o.aUv, 2, gl.FLOAT, false, 0, 0);
  gl.uniform1f(o.uTime, time * 1e-3);
  gl.uniform3f(o.uColor, state.orbColor[0], state.orbColor[1], state.orbColor[2]);
  gl.uniform3f(o.uResolution, gl.canvas.width, gl.canvas.height, gl.canvas.width / gl.canvas.height);
  gl.uniform1f(o.uAmplitude, amplitude);
  gl.uniform1f(o.uSpeed, speed);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  const c = state.orbColor;
  const r = Math.round(c[0] * 255);
  const g = Math.round(c[1] * 255);
  const b = Math.round(c[2] * 255);
  const glowOpacity = 0.15 + level * 0.25;
  orbGlow.style.background = `rgba(${r}, ${g}, ${b}, ${glowOpacity})`;
  orbClip.style.boxShadow = `0 0 ${60 + level * 15}px rgba(${r}, ${g}, ${b}, ${0.2 + level * 0.1})`;
  const scale = 1 + level * 0.06;
  orbClip.style.transform = `scale(${scale})`;
  state.orbAnimId = requestAnimationFrame(renderOrbFrame);
}
function startOrbLoop() {
  if (state.orbAnimId) return;
  state.orbAnimId = requestAnimationFrame(renderOrbFrame);
}
function stopOrbLoop() {
  if (state.orbAnimId) {
    cancelAnimationFrame(state.orbAnimId);
    state.orbAnimId = null;
  }
}
function setStatus(s) {
  state.currentStatus = s;
  const labels = {
    connecting: "Connecting...",
    listening: "Listening...",
    thinking: "Thinking...",
    speaking: "Speaking...",
    disconnected: "Disconnected"
  };
  statusDot.className = "dot " + s;
  statusTextActive.textContent = labels[s] || s;
}
function setCaption(text, role = "assistant") {
  liveCaption.textContent = text;
  liveCaption.className = "visible role-" + role;
  if (state.captionTimeout) {
    clearTimeout(state.captionTimeout);
    state.captionTimeout = null;
  }
  if (role !== "assistant") {
    state.captionTimeout = setTimeout(() => {
      liveCaption.classList.remove("visible");
    }, 3e3);
  }
}
function fadeCaption() {
  if (state.captionTimeout) {
    clearTimeout(state.captionTimeout);
  }
  state.captionTimeout = setTimeout(() => {
    liveCaption.classList.remove("visible");
  }, 2500);
}
function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.textContent = text;
  transcript.appendChild(el);
  if (transcriptPanel.classList.contains("expanded")) {
    transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
  }
  if (!state.hasTranscriptContent) {
    state.hasTranscriptContent = true;
    transcriptToggle.classList.add("visible");
  }
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
function toggleTranscript() {
  const isExpanded = transcriptPanel.classList.toggle("expanded");
  transcriptToggle.classList.toggle("expanded", isExpanded);
  transcriptToggle.querySelector("span").textContent = isExpanded ? "Hide transcript" : "Show transcript";
  if (isExpanded) {
    transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
  }
}
async function startSession() {
  try {
    startSection.style.display = "none";
    sessionSection.classList.add("active");
    stopBtn.style.display = "block";
    setStatus("connecting");
    initOrbGL();
    startOrbLoop();
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") {
      await audioCtx.resume();
    }
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    state.audioCtx = audioCtx;
    state.analyser = analyser;
    state.analyserData = new Uint8Array(analyser.frequencyBinCount);
    const audioEl = document.createElement("audio");
    audioEl.autoplay = true;
    audioEl.style.display = "none";
    document.body.appendChild(audioEl);
    state.audioEl = audioEl;
    const isFirefox = /Firefox/i.test(navigator.userAgent);
    if (!isFirefox) {
      const mediaSource = audioCtx.createMediaElementSource(audioEl);
      mediaSource.connect(analyser);
      analyser.connect(audioCtx.destination);
      state.remoteSource = mediaSource;
    }
    clog("Requesting ephemeral token...");
    const tokenRes = await fetch("/token");
    if (!tokenRes.ok) throw new Error("Token request failed: " + tokenRes.status);
    const tokenData = await tokenRes.json();
    const ephemeralKey = tokenData.client_secret.value;
    const pc = new RTCPeerConnection();
    state.pc = pc;
    pc.ontrack = (ev) => {
      audioEl.srcObject = ev.streams[0];
      audioEl.play().catch(() => {
      });
      clog("Remote audio track received");
      if (isFirefox && state.audioCtx && state.analyser && !state.remoteSource) {
        try {
          const source = state.audioCtx.createMediaStreamSource(ev.streams[0]);
          source.connect(state.analyser);
          state.remoteSource = source;
          clog("Audio analysis via createMediaStreamSource (Firefox)");
        } catch (e) {
          clog("createMediaStreamSource failed: " + e.message, "error");
        }
      }
    };
    const localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.localStream = localStream;
    localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));
    const dc = pc.createDataChannel("oai-events");
    state.dc = dc;
    dc.onopen = () => {
      clog("Data channel open, WebRTC connected");
      setStatus("listening");
      addMessage("system", "Connected - start speaking!");
      setCaption("Connected - start speaking!", "system");
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
        Authorization: "Bearer " + ephemeralKey,
        "Content-Type": "application/sdp"
      },
      body: pc.localDescription.sdp
    });
    if (!sdpRes.ok) throw new Error("SDP exchange failed: " + sdpRes.status);
    clog("SDP exchange complete");
    const answerSdp = await sdpRes.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
    pc.oniceconnectionstatechange = () => {
      if (pc.iceConnectionState === "disconnected" || pc.iceConnectionState === "failed") {
        setStatus("disconnected");
      }
    };
  } catch (err) {
    clog("Session start failed: " + err.message, "error");
    addMessage("system", "Error: " + err.message);
    setCaption("Error: " + err.message, "system");
    setStatus("disconnected");
  }
}
function endSession() {
  if (state.localStream) {
    state.localStream.getTracks().forEach((t) => t.stop());
    state.localStream = null;
  }
  if (state.pc) {
    state.pc.close();
    state.pc = null;
  }
  state.dc = null;
  if (state.audioEl) {
    state.audioEl.srcObject = null;
    state.audioEl.remove();
    state.audioEl = null;
  }
  if (state.remoteSource) {
    try {
      state.remoteSource.disconnect();
    } catch (_) {
    }
    state.remoteSource = null;
  }
  if (state.audioCtx) {
    state.audioCtx.close().catch(() => {
    });
    state.audioCtx = null;
    state.analyser = null;
    state.analyserData = null;
  }
  setStatus("disconnected");
  stopBtn.style.display = "none";
  addMessage("system", "Session ended.");
  setCaption("Session ended.", "system");
  if (state.cart_id) {
    showCartLink();
  }
  setTimeout(() => {
    stopOrbLoop();
  }, 2e3);
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
      setCaption(state.currentAssistantMsg, "assistant");
      break;
    case "response.audio_transcript.done":
      if (currentMsgEl) {
        currentMsgEl.textContent = event.transcript;
      }
      currentMsgEl = null;
      state.currentAssistantMsg = "";
      setStatus("listening");
      fadeCaption();
      break;
    case "conversation.item.input_audio_transcription.completed":
      if (event.transcript) {
        clog(`User said: "${event.transcript}"`);
        const userEl = document.createElement("div");
        userEl.className = "msg user";
        userEl.textContent = event.transcript;
        if (currentMsgEl && currentMsgEl.parentNode) {
          currentMsgEl.parentNode.insertBefore(userEl, currentMsgEl);
        } else {
          transcript.appendChild(userEl);
        }
        if (!state.hasTranscriptContent) {
          state.hasTranscriptContent = true;
          transcriptToggle.classList.add("visible");
        }
        setCaption(event.transcript, "user");
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
      addMessage("system", "Error: " + (event.error?.message || "Unknown error"));
      setCaption("Error: " + (event.error?.message || "Unknown"), "system");
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
  const label = name.replace(/_/g, " ");
  addMessage("system", `Looking up: ${label}...`);
  setCaption(`Looking up: ${label}...`, "system");
  let result;
  try {
    result = await callBackend(name, args);
  } catch (err) {
    result = { error: err.message };
  }
  if (name === "select_store" && result.cart_id) {
    state.cart_id = result.cart_id;
    state.store_id = args.store_id || result.store_id;
    addMessage("system", "Store selected, cart created.");
    setCaption("Store selected, cart created.", "system");
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
    setCaption("Shopping complete!", "system");
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
    return { error: "Unknown function: " + fnName };
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
    throw new Error("API error " + res.status + ": " + text);
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
