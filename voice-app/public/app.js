// ═══════════════════════════════════════════════════════════════
// Superstore Voice — app.js
// Voice-reactive iridescent orb + live caption + collapsible transcript
// ═══════════════════════════════════════════════════════════════

// ─── Logging ───
function clog(msg, level = "info") {
  console.log(`[client:${level}] ${msg}`);
  fetch("/api/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level, msg })
  }).catch(() => {});
}

// ─── Constants ───
var REALTIME_MODEL = "gpt-realtime-mini";
var REALTIME_URL = `https://api.openai.com/v1/realtime?model=${REALTIME_MODEL}`;

// Orb color per status — [r, g, b] floats 0-1
var STATE_COLORS = {
  connecting:   [0.94, 0.63, 0.19],
  listening:    [0.20, 0.82, 0.41],
  speaking:     [0.88, 0.24, 0.24],
  thinking:     [0.29, 0.56, 0.96],
  disconnected: [0.35, 0.38, 0.50],
};

// ─── State ───
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
  // Orb
  currentStatus: "disconnected",
  orbColor: [0.35, 0.38, 0.50],
  orbAnimId: null,
  orbGL: null,
  // Caption
  captionTimeout: null,
  // Transcript
  hasTranscriptContent: false,
};

// ─── DOM References ───
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

// ─── Event Listeners ───
startBtn.addEventListener("click", startSession);
stopBtn.addEventListener("click", endSession);
transcriptToggle.addEventListener("click", toggleTranscript);

// ═══════════════════════════════════════════════════════════════
// WebGL Iridescent Orb
// Shader based on the iridescent fragment shader pattern
// ═══════════════════════════════════════════════════════════════

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
  var shader = gl.createShader(type);
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
  var program = gl.createProgram();
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
  var canvas = document.getElementById("orb-canvas");
  var gl = canvas.getContext("webgl");
  if (!gl) {
    clog("WebGL not supported", "error");
    return;
  }

  var vs = compileShader(gl, gl.VERTEX_SHADER, VERT_SRC);
  var fs = compileShader(gl, gl.FRAGMENT_SHADER, FRAG_SRC);
  if (!vs || !fs) return;
  var program = linkProgram(gl, vs, fs);
  if (!program) return;

  // Full-screen triangle covering clip space
  var posBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);

  var uvBuf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 2, 0, 0, 2]), gl.STATIC_DRAW);

  state.orbGL = {
    gl: gl,
    program: program,
    posBuf: posBuf,
    uvBuf: uvBuf,
    aPosition: gl.getAttribLocation(program, "position"),
    aUv: gl.getAttribLocation(program, "uv"),
    uTime: gl.getUniformLocation(program, "uTime"),
    uColor: gl.getUniformLocation(program, "uColor"),
    uResolution: gl.getUniformLocation(program, "uResolution"),
    uAmplitude: gl.getUniformLocation(program, "uAmplitude"),
    uSpeed: gl.getUniformLocation(program, "uSpeed"),
  };

  resizeOrbCanvas();
  window.addEventListener("resize", resizeOrbCanvas);
}

function resizeOrbCanvas() {
  if (!state.orbGL) return;
  var canvas = state.orbGL.gl.canvas;
  var rect = canvas.getBoundingClientRect();
  var dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
}

function renderOrbFrame(time) {
  var o = state.orbGL;
  if (!o) return;
  var gl = o.gl;

  // Update audio level
  if (state.analyser && state.analyserData) {
    state.analyser.getByteFrequencyData(state.analyserData);
    var sum = 0;
    for (var i = 0; i < state.analyserData.length; i++) sum += state.analyserData[i];
    var avg = sum / state.analyserData.length;
    var norm = Math.min(1, Math.max(0, (avg - 16) / 90));
    state.smoothedAudioLevel += (norm - state.smoothedAudioLevel) * 0.15;
  } else {
    state.smoothedAudioLevel += (0 - state.smoothedAudioLevel) * 0.08;
  }

  var level = state.smoothedAudioLevel;

  // Lerp orb color toward target state color
  var target = STATE_COLORS[state.currentStatus] || STATE_COLORS.disconnected;
  for (var i = 0; i < 3; i++) {
    state.orbColor[i] += (target[i] - state.orbColor[i]) * 0.04;
  }

  // Audio-reactive uniforms (from the article)
  var amplitude = 0.18 + level * 1.7;
  var speed = 0.75 + level * 0.5;

  // Render WebGL
  gl.viewport(0, 0, gl.canvas.width, gl.canvas.height);
  gl.useProgram(o.program);

  gl.bindBuffer(gl.ARRAY_BUFFER, o.posBuf);
  gl.enableVertexAttribArray(o.aPosition);
  gl.vertexAttribPointer(o.aPosition, 2, gl.FLOAT, false, 0, 0);

  gl.bindBuffer(gl.ARRAY_BUFFER, o.uvBuf);
  gl.enableVertexAttribArray(o.aUv);
  gl.vertexAttribPointer(o.aUv, 2, gl.FLOAT, false, 0, 0);

  gl.uniform1f(o.uTime, time * 0.001);
  gl.uniform3f(o.uColor, state.orbColor[0], state.orbColor[1], state.orbColor[2]);
  gl.uniform3f(o.uResolution, gl.canvas.width, gl.canvas.height, gl.canvas.width / gl.canvas.height);
  gl.uniform1f(o.uAmplitude, amplitude);
  gl.uniform1f(o.uSpeed, speed);

  gl.drawArrays(gl.TRIANGLES, 0, 3);

  // Update glow color and intensity
  var c = state.orbColor;
  var r = Math.round(c[0] * 255);
  var g = Math.round(c[1] * 255);
  var b = Math.round(c[2] * 255);
  var glowOpacity = 0.15 + level * 1.2;
  orbGlow.style.background = `rgba(${r}, ${g}, ${b}, ${glowOpacity})`;
  orbClip.style.boxShadow = `0 0 ${60 + level * 40}px rgba(${r}, ${g}, ${b}, ${0.2 + level * 0.3})`;

  // Scale orb with audio
  var scale = 1 + level * 0.2;
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

// ═══════════════════════════════════════════════════════════════
// UI Helpers
// ═══════════════════════════════════════════════════════════════

function setStatus(s) {
  state.currentStatus = s;
  var labels = {
    connecting: "Connecting...",
    listening: "Listening...",
    thinking: "Thinking...",
    speaking: "Speaking...",
    disconnected: "Disconnected",
  };
  statusDot.className = "dot " + s;
  statusTextActive.textContent = labels[s] || s;
}

// ─── Live Caption ───
function setCaption(text, role) {
  role = role || "assistant";
  liveCaption.textContent = text;
  liveCaption.className = "visible role-" + role;

  if (state.captionTimeout) {
    clearTimeout(state.captionTimeout);
    state.captionTimeout = null;
  }

  // Auto-fade user and system captions after a delay
  if (role !== "assistant") {
    state.captionTimeout = setTimeout(function () {
      liveCaption.classList.remove("visible");
    }, 3000);
  }
}

function fadeCaption() {
  if (state.captionTimeout) {
    clearTimeout(state.captionTimeout);
  }
  state.captionTimeout = setTimeout(function () {
    liveCaption.classList.remove("visible");
  }, 2500);
}

// ─── Transcript (hidden panel) ───
function addMessage(role, text) {
  var el = document.createElement("div");
  el.className = "msg " + role;
  el.textContent = text;
  transcript.appendChild(el);

  // Auto-scroll if panel is open
  if (transcriptPanel.classList.contains("expanded")) {
    transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
  }

  // Show toggle button once we have content
  if (!state.hasTranscriptContent) {
    state.hasTranscriptContent = true;
    transcriptToggle.classList.add("visible");
  }

  return el;
}

// ─── Cart ───
function addCartItem(name, qty) {
  cartSection.classList.add("active");
  var li = document.createElement("li");
  var nameSpan = document.createElement("span");
  nameSpan.textContent = name;
  li.appendChild(nameSpan);
  if (qty) {
    var qtySpan = document.createElement("span");
    qtySpan.textContent = qty;
    li.appendChild(qtySpan);
  }
  cartItems.appendChild(li);
}

function showCartLink() {
  if (state.cart_id) {
    var a = document.getElementById("cart-link-a");
    a.href = "https://www.realcanadiansuperstore.ca/en/cartReview?forceCartId=" + state.cart_id;
  }
  cartLink.style.display = "block";
}

// ─── Transcript Toggle ───
function toggleTranscript() {
  var isExpanded = transcriptPanel.classList.toggle("expanded");
  transcriptToggle.classList.toggle("expanded", isExpanded);
  transcriptToggle.querySelector("span").textContent = isExpanded ? "Hide transcript" : "Show transcript";
  if (isExpanded) {
    transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
  }
}

// ═══════════════════════════════════════════════════════════════
// WebRTC Session
// ═══════════════════════════════════════════════════════════════

async function startSession() {
  try {
    startSection.style.display = "none";
    sessionSection.classList.add("active");
    stopBtn.style.display = "block";
    setStatus("connecting");

    // Init WebGL orb
    initOrbGL();
    startOrbLoop();

    // Create AudioContext (user gesture required — we're in a click handler)
    var audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") {
      await audioCtx.resume();
    }
    var analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    state.audioCtx = audioCtx;
    state.analyser = analyser;
    state.analyserData = new Uint8Array(analyser.frequencyBinCount);

    clog("Requesting ephemeral token...");
    var tokenRes = await fetch("/token");
    if (!tokenRes.ok) throw new Error("Token request failed: " + tokenRes.status);
    var tokenData = await tokenRes.json();
    var ephemeralKey = tokenData.client_secret.value;

    var pc = new RTCPeerConnection();
    state.pc = pc;

    var audioEl = document.createElement("audio");
    audioEl.autoplay = true;
    state.audioEl = audioEl;

    // When remote audio track arrives, connect it to the analyser
    pc.ontrack = function (ev) {
      audioEl.srcObject = ev.streams[0];
      // Tap remote audio for visualization (doesn't affect playback)
      try {
        var source = audioCtx.createMediaStreamSource(ev.streams[0]);
        source.connect(analyser);
        // Don't connect to audioCtx.destination — audioEl handles playback
        clog("Remote audio connected to analyser");
      } catch (err) {
        clog("Failed to connect analyser: " + err.message, "error");
      }
    };

    var localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.localStream = localStream;
    localStream.getTracks().forEach(function (track) {
      pc.addTrack(track, localStream);
    });

    var dc = pc.createDataChannel("oai-events");
    state.dc = dc;

    dc.onopen = function () {
      clog("Data channel open, WebRTC connected");
      setStatus("listening");
      addMessage("system", "Connected - start speaking!");
      setCaption("Connected - start speaking!", "system");
    };

    dc.onclose = function () {
      setStatus("disconnected");
    };

    dc.onmessage = function (ev) {
      handleServerEvent(JSON.parse(ev.data));
    };

    var offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    var sdpRes = await fetch(REALTIME_URL, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + ephemeralKey,
        "Content-Type": "application/sdp",
      },
      body: pc.localDescription.sdp,
    });
    if (!sdpRes.ok) throw new Error("SDP exchange failed: " + sdpRes.status);
    clog("SDP exchange complete");

    var answerSdp = await sdpRes.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

    pc.oniceconnectionstatechange = function () {
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
  // Stop mic
  if (state.localStream) {
    state.localStream.getTracks().forEach(function (t) { t.stop(); });
    state.localStream = null;
  }
  // Close WebRTC
  if (state.pc) {
    state.pc.close();
    state.pc = null;
  }
  state.dc = null;
  // Release audio
  if (state.audioEl) {
    state.audioEl.srcObject = null;
    state.audioEl = null;
  }
  // Close AudioContext
  if (state.audioCtx) {
    state.audioCtx.close().catch(function () {});
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

  // Stop orb after a brief delay so disconnected state renders
  setTimeout(function () {
    stopOrbLoop();
  }, 2000);
}

// ═══════════════════════════════════════════════════════════════
// Server Event Handling
// ═══════════════════════════════════════════════════════════════

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
      // Update live caption with streaming text
      setCaption(state.currentAssistantMsg, "assistant");
      break;

    case "response.audio_transcript.done":
      if (currentMsgEl) {
        currentMsgEl.textContent = event.transcript;
      }
      currentMsgEl = null;
      state.currentAssistantMsg = "";
      setStatus("listening");
      // Fade caption after assistant finishes speaking
      fadeCaption();
      break;

    case "conversation.item.input_audio_transcription.completed":
      if (event.transcript) {
        clog('User said: "' + event.transcript + '"');
        // Add to transcript
        var userEl = document.createElement("div");
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
        // Show user text as caption briefly
        setCaption(event.transcript, "user");
      }
      break;

    case "response.function_call_arguments.done":
      setStatus("thinking");
      handleToolCall(event);
      break;

    case "response.done":
      if (event.response && event.response.output) {
        for (var i = 0; i < event.response.output.length; i++) {
          var item = event.response.output[i];
          if (item.type === "function_call" && item.status === "completed") {
            // no-op
          }
        }
      }
      if (!currentMsgEl) {
        setStatus("listening");
      }
      break;

    case "error":
      console.error("Realtime error:", event.error);
      addMessage("system", "Error: " + (event.error && event.error.message ? event.error.message : "Unknown error"));
      setCaption("Error: " + (event.error && event.error.message ? event.error.message : "Unknown"), "system");
      break;
  }
}

// ═══════════════════════════════════════════════════════════════
// Tool Call Handling
// ═══════════════════════════════════════════════════════════════

async function handleToolCall(event) {
  var name = event.name;
  var argsStr = event.arguments;
  var call_id = event.call_id;
  var args;
  try {
    args = JSON.parse(argsStr);
  } catch (e) {
    args = {};
  }

  clog("Tool call: " + name + "(" + argsStr + ")");
  var label = name.replace(/_/g, " ");
  addMessage("system", "Looking up: " + label + "...");
  setCaption("Looking up: " + label + "...", "system");

  var result;
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
    for (var i = 0; i < result.products.length; i++) {
      var p = result.products[i];
      if (p.code && p.name) {
        state.productNames[p.code] = p.brand ? p.brand + " " + p.name : p.name;
      }
    }
  }

  if (name === "add_to_cart" && !result.error && args.items) {
    for (var i = 0; i < args.items.length; i++) {
      var item = args.items[i];
      var displayName = state.productNames[item.product_code] || item.product_code;
      addCartItem(displayName, "x" + item.quantity);
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
      call_id: call_id,
      output: JSON.stringify(result),
    },
  });
  sendDataChannelMessage({
    type: "response.create",
  });
}

// ═══════════════════════════════════════════════════════════════
// Backend API
// ═══════════════════════════════════════════════════════════════

async function callBackend(fnName, args) {
  var endpointMap = {
    find_nearest_stores: "/api/find-stores",
    select_store: "/api/create-cart",
    search_products: "/api/search-products",
    add_to_cart: "/api/add-to-cart",
    finish_shopping: "/api/finish-shopping",
  };
  var endpoint = endpointMap[fnName];
  if (!endpoint) {
    return { error: "Unknown function: " + fnName };
  }
  var body = Object.assign({}, args);
  if (state.cart_id && !body.cart_id) body.cart_id = state.cart_id;
  if (state.store_id && !body.store_id) body.store_id = state.store_id;

  var res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    var text = await res.text();
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
