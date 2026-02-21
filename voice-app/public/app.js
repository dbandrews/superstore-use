// ─── Logging ───
function clog(msg, level = "info") {
  console.log(`[client:${level}] ${msg}`);
  fetch("/api/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level, msg }),
  }).catch(() => {});
}

// ─── Constants ───
var REALTIME_MODEL = "gpt-realtime-mini";
var REALTIME_URL = `https://api.openai.com/v1/realtime?model=${REALTIME_MODEL}`;

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
  // Orb
  orb: null,
  animationFrameId: null,
  smoothedLevel: 0,
};

// ─── DOM Refs ───
var startBtn = document.getElementById("start-btn");
var startSection = document.getElementById("start-section");
var sessionSection = document.getElementById("session-section");
var statusText = document.getElementById("status-text");
var statusTextActive = document.getElementById("status-text-active");
var statusDot = document.querySelector("#status-active .dot");
var caption = document.getElementById("caption");
var cartSection = document.getElementById("cart-section");
var cartItems = document.getElementById("cart-items");
var cartLink = document.getElementById("cart-link");
var stopBtn = document.getElementById("stop-btn");
var transcriptToggle = document.getElementById("transcript-toggle");
var transcriptPanel = document.getElementById("transcript-panel");
var orbWrapper = document.getElementById("orb-wrapper");
var orbGlow = document.getElementById("orb-glow");
var orbCanvas = document.getElementById("orb-canvas");

// ─── Event Listeners ───
startBtn.addEventListener("click", startSession);
stopBtn.addEventListener("click", endSession);
transcriptToggle.addEventListener("click", toggleTranscript);

// ═══════════════════════════════════════════════
// WebGL Iridescent Orb
// ═══════════════════════════════════════════════

var VERT_SRC = `
  attribute vec2 position;
  varying vec2 vUv;
  void main() {
    vUv = position * 0.5 + 0.5;
    gl_Position = vec4(position, 0.0, 1.0);
  }
`;

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

    vec3 col = vec3(
      cos(uv * vec2(d, a)) * 0.6 + 0.4,
      cos(a + d) * 0.5 + 0.5
    );
    col = cos(col * cos(vec3(d, a, 2.5)) * 0.5 + 0.5) * uColor;

    // Amplitude-driven brightness
    col *= (0.7 + uAmplitude * 0.5);

    // Spherical vignette
    float dist = length(vUv - 0.5) * 2.0;
    col *= smoothstep(1.0, 0.4, dist);

    gl_FragColor = vec4(col, 1.0);
  }
`;

class IridescentOrb {
  constructor(canvas) {
    this.canvas = canvas;
    this.startTime = performance.now();
    this.level = 0;
    this.targetLevel = 0;
    this.program = null;
    this.uniforms = {};
    this.gl = null;
    this._init();
  }

  _init() {
    // Size canvas for retina
    var dpr = window.devicePixelRatio || 1;
    var rect = this.canvas.getBoundingClientRect();
    var displaySize = Math.round(rect.width) || 220;
    this.canvas.width = displaySize * dpr;
    this.canvas.height = displaySize * dpr;

    var gl = this.canvas.getContext("webgl", { alpha: false, antialias: false });
    if (!gl) {
      clog("WebGL not available", "error");
      return;
    }
    this.gl = gl;

    // Compile shaders
    var vs = this._compileShader(gl.VERTEX_SHADER, VERT_SRC);
    var fs = this._compileShader(gl.FRAGMENT_SHADER, FRAG_SRC);
    if (!vs || !fs) return;

    // Link program
    var prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      clog("Shader link error: " + gl.getProgramInfoLog(prog), "error");
      return;
    }
    gl.useProgram(prog);
    this.program = prog;

    // Fullscreen quad (triangle strip)
    var verts = new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);

    var posLoc = gl.getAttribLocation(prog, "position");
    gl.enableVertexAttribArray(posLoc);
    gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

    // Uniform locations
    this.uniforms = {
      uTime: gl.getUniformLocation(prog, "uTime"),
      uColor: gl.getUniformLocation(prog, "uColor"),
      uResolution: gl.getUniformLocation(prog, "uResolution"),
      uAmplitude: gl.getUniformLocation(prog, "uAmplitude"),
      uSpeed: gl.getUniformLocation(prog, "uSpeed"),
    };

    // Initial uniform values
    gl.uniform3f(this.uniforms.uColor, 0.3, 0.55, 1.0);
    gl.uniform3f(
      this.uniforms.uResolution,
      this.canvas.width,
      this.canvas.height,
      this.canvas.width / this.canvas.height
    );
    gl.uniform1f(this.uniforms.uAmplitude, 0.18);
    gl.uniform1f(this.uniforms.uSpeed, 0.75);

    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
  }

  _compileShader(type, src) {
    var gl = this.gl;
    var shader = gl.createShader(type);
    gl.shaderSource(shader, src);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      clog("Shader compile error: " + gl.getShaderInfoLog(shader), "error");
      gl.deleteShader(shader);
      return null;
    }
    return shader;
  }

  setLevel(target) {
    this.targetLevel = target;
  }

  render() {
    if (!this.gl) return;

    // Smooth the level
    this.level += (this.targetLevel - this.level) * 0.15;

    var gl = this.gl;
    var t = (performance.now() - this.startTime) * 0.001;

    var amplitude = 0.18 + this.level * 1.7;
    var speed = 0.75 + this.level * 0.5;

    gl.uniform1f(this.uniforms.uTime, t);
    gl.uniform1f(this.uniforms.uAmplitude, amplitude);
    gl.uniform1f(this.uniforms.uSpeed, speed);

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  destroy() {
    if (this.gl) {
      var ext = this.gl.getExtension("WEBGL_lose_context");
      if (ext) ext.loseContext();
    }
  }
}

// ═══════════════════════════════════════════════
// Audio Analysis (remote stream only)
// ═══════════════════════════════════════════════

function setupAudioAnalysis(stream) {
  try {
    if (!state.audioCtx) {
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (state.audioCtx.state === "suspended") {
      state.audioCtx.resume();
    }

    var analyser = state.audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.8;

    var source = state.audioCtx.createMediaStreamSource(stream);
    source.connect(analyser);

    state.analyser = analyser;
    state.analyserData = new Uint8Array(analyser.frequencyBinCount);
    clog("Audio analysis connected to remote stream");
  } catch (err) {
    clog("Audio analysis setup failed: " + err.message, "error");
  }
}

// ═══════════════════════════════════════════════
// Animation Loop
// ═══════════════════════════════════════════════

function startAnimation() {
  function animate() {
    var targetLevel = 0;
    if (state.analyser && state.analyserData) {
      state.analyser.getByteFrequencyData(state.analyserData);
      var sum = 0;
      for (var i = 0; i < state.analyserData.length; i++) {
        sum += state.analyserData[i];
      }
      var avg = sum / state.analyserData.length;
      targetLevel = Math.min(1, Math.max(0, (avg - 16) / 90));
    }

    // Smooth the level for glow/scale (orb smooths internally too)
    state.smoothedLevel += (targetLevel - state.smoothedLevel) * 0.15;

    // Update orb shader
    if (state.orb) {
      state.orb.setLevel(targetLevel);
      state.orb.render();
    }

    // Update glow intensity
    if (orbGlow) {
      orbGlow.style.opacity = 0.15 + state.smoothedLevel * 0.55;
    }

    // Update scale
    if (orbWrapper) {
      var scale = 1 + state.smoothedLevel * 0.08;
      orbWrapper.style.transform = "scale(" + scale + ")";
    }

    // Update canvas box-shadow dynamically
    if (orbCanvas) {
      var glowStr = Math.round(40 + state.smoothedLevel * 40);
      var glowAlpha = (0.12 + state.smoothedLevel * 0.25).toFixed(2);
      orbCanvas.style.boxShadow =
        "0 0 0 1px rgba(60, 100, 255, 0.08), 0 0 " +
        glowStr +
        "px rgba(60, 100, 255, " +
        glowAlpha +
        ")";
    }

    state.animationFrameId = requestAnimationFrame(animate);
  }
  animate();
}

function stopAnimation() {
  if (state.animationFrameId) {
    cancelAnimationFrame(state.animationFrameId);
    state.animationFrameId = null;
  }
}

// ═══════════════════════════════════════════════
// UI Functions
// ═══════════════════════════════════════════════

function setStatus(s) {
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

function setCaption(text, isSystem) {
  caption.textContent = text;
  if (isSystem) {
    caption.classList.add("system-caption");
  } else {
    caption.classList.remove("system-caption");
  }
}

function addTranscriptEntry(role, text) {
  var el = document.createElement("div");
  el.className = "t-msg " + role;
  el.textContent = text;
  transcriptPanel.appendChild(el);
  transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
}

function addCartItem(name, qty) {
  cartSection.classList.add("active");
  var li = document.createElement("li");
  var nameSpan = document.createElement("span");
  nameSpan.textContent = name;
  li.appendChild(nameSpan);
  if (qty) {
    var qtySpan = document.createElement("span");
    qtySpan.textContent = qty;
    qtySpan.style.color = "#8888a0";
    li.appendChild(qtySpan);
  }
  cartItems.appendChild(li);
}

function showCartLink() {
  if (state.cart_id) {
    var a = document.getElementById("cart-link-a");
    a.href =
      "https://www.realcanadiansuperstore.ca/en/cartReview?forceCartId=" +
      state.cart_id;
  }
  cartLink.style.display = "block";
}

function toggleTranscript() {
  var isOpen = transcriptPanel.classList.toggle("open");
  transcriptToggle.classList.toggle("open", isOpen);
  transcriptToggle.innerHTML = isOpen
    ? '<span class="toggle-arrow">&#9660;</span> Hide transcript'
    : '<span class="toggle-arrow">&#9660;</span> Show transcript';
  if (isOpen) {
    transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
  }
}

// ═══════════════════════════════════════════════
// Session Management
// ═══════════════════════════════════════════════

async function startSession() {
  try {
    startSection.style.display = "none";
    sessionSection.classList.add("active");
    stopBtn.style.display = "block";
    setStatus("connecting");
    addTranscriptEntry("system", "Connecting...");

    // Init orb
    state.orb = new IridescentOrb(orbCanvas);
    startAnimation();

    // Create AudioContext early (within user gesture)
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();

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

    pc.ontrack = function (ev) {
      audioEl.srcObject = ev.streams[0];
      // Wire audio analysis to the remote (assistant) stream
      setupAudioAnalysis(ev.streams[0]);
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
      setCaption("");
      addTranscriptEntry("system", "Connected - start speaking!");
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
      if (
        pc.iceConnectionState === "disconnected" ||
        pc.iceConnectionState === "failed"
      ) {
        setStatus("disconnected");
      }
    };
  } catch (err) {
    clog("Session start failed: " + err.message, "error");
    setCaption("Error: " + err.message, true);
    addTranscriptEntry("system", "Error: " + err.message);
    setStatus("disconnected");
  }
}

function endSession() {
  stopAnimation();

  if (state.orb) {
    state.orb.destroy();
    state.orb = null;
  }
  if (state.localStream) {
    state.localStream.getTracks().forEach(function (track) {
      track.stop();
    });
    state.localStream = null;
  }
  if (state.pc) {
    state.pc.close();
    state.pc = null;
  }
  if (state.dc) {
    state.dc = null;
  }
  if (state.audioEl) {
    state.audioEl.srcObject = null;
    state.audioEl = null;
  }
  if (state.audioCtx) {
    state.audioCtx.close().catch(function () {});
    state.audioCtx = null;
  }
  state.analyser = null;
  state.analyserData = null;
  state.smoothedLevel = 0;

  setStatus("disconnected");
  setCaption("Session ended", true);
  stopBtn.style.display = "none";
  addTranscriptEntry("system", "Session ended.");

  if (state.cart_id) {
    showCartLink();
  }
}

// ═══════════════════════════════════════════════
// Server Event Handling
// ═══════════════════════════════════════════════

var currentMsgEl = null;

function handleServerEvent(event) {
  switch (event.type) {
    case "response.audio_transcript.delta":
      setStatus("speaking");
      state.currentAssistantMsg += event.delta;
      // Update live caption with streaming text
      setCaption(state.currentAssistantMsg);
      // Also update the streaming transcript entry
      if (!currentMsgEl) {
        currentMsgEl = document.createElement("div");
        currentMsgEl.className = "t-msg assistant";
        currentMsgEl.textContent = "";
        transcriptPanel.appendChild(currentMsgEl);
      }
      currentMsgEl.textContent = state.currentAssistantMsg;
      transcriptPanel.scrollTop = transcriptPanel.scrollHeight;
      break;

    case "response.audio_transcript.done":
      if (currentMsgEl) {
        currentMsgEl.textContent = event.transcript;
      }
      // Set final caption
      setCaption(event.transcript);
      currentMsgEl = null;
      state.currentAssistantMsg = "";
      setStatus("listening");
      break;

    case "conversation.item.input_audio_transcription.completed":
      if (event.transcript) {
        clog('User said: "' + event.transcript + '"');
        addTranscriptEntry("user", event.transcript);
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
            // function call completed
          }
        }
      }
      if (!currentMsgEl) {
        setStatus("listening");
      }
      break;

    case "error":
      console.error("Realtime error:", event.error);
      setCaption("Error: " + (event.error && event.error.message ? event.error.message : "Unknown error"), true);
      addTranscriptEntry("system", "Error: " + (event.error && event.error.message ? event.error.message : "Unknown error"));
      break;
  }
}

// ═══════════════════════════════════════════════
// Tool Calls & Backend
// ═══════════════════════════════════════════════

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
  setCaption("Looking up: " + label + "...", true);
  addTranscriptEntry("system", "Looking up: " + label + "...");

  var result;
  try {
    result = await callBackend(name, args);
  } catch (err) {
    result = { error: err.message };
  }

  if (name === "select_store" && result.cart_id) {
    state.cart_id = result.cart_id;
    state.store_id = args.store_id || result.store_id;
    addTranscriptEntry("system", "Store selected, cart created.");
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
    for (var j = 0; j < args.items.length; j++) {
      var item = args.items[j];
      var displayName =
        state.productNames[item.product_code] || item.product_code;
      addCartItem(displayName, "x" + item.quantity);
    }
  }

  if (name === "finish_shopping") {
    showCartLink();
    addTranscriptEntry("system", "Shopping complete! Review your cart on Superstore.");
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
