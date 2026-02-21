// ═══════════════════════════════════════════════════════════════
// Superstore Voice — app.ts
// Voice-reactive iridescent orb + live caption + collapsible transcript
// ═══════════════════════════════════════════════════════════════

// ─── Logging ───
function clog(msg: string, level = "info") {
  console.log(`[client:${level}] ${msg}`);
  fetch("/api/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level, msg }),
  }).catch(() => {});
}

// ─── Constants ───
const REALTIME_MODEL = "gpt-realtime-mini";
const REALTIME_URL = `https://api.openai.com/v1/realtime?model=${REALTIME_MODEL}`;

// Orb color per status — [r, g, b] floats 0-1
const STATE_COLORS: Record<string, number[]> = {
  connecting:   [0.94, 0.63, 0.19],
  listening:    [0.20, 0.82, 0.41],
  speaking:     [0.88, 0.24, 0.24],
  thinking:     [0.29, 0.56, 0.96],
  disconnected: [0.35, 0.38, 0.50],
};

// ─── State ───
interface OrbGL {
  gl: WebGLRenderingContext;
  program: WebGLProgram;
  posBuf: WebGLBuffer;
  uvBuf: WebGLBuffer;
  aPosition: number;
  aUv: number;
  uTime: WebGLUniformLocation | null;
  uColor: WebGLUniformLocation | null;
  uResolution: WebGLUniformLocation | null;
  uAmplitude: WebGLUniformLocation | null;
  uSpeed: WebGLUniformLocation | null;
}

interface AppState {
  pc: RTCPeerConnection | null;
  dc: RTCDataChannel | null;
  audioEl: HTMLAudioElement | null;
  localStream: MediaStream | null;
  cart_id: string | null;
  store_id: string | null;
  currentAssistantMsg: string;
  currentResponseId: string | null;
  productNames: Record<string, string>;
  // Audio analysis
  audioCtx: AudioContext | null;
  analyser: AnalyserNode | null;
  analyserData: Uint8Array | null;
  smoothedAudioLevel: number;
  remoteSource: AudioNode | null;
  // Orb
  currentStatus: string;
  orbColor: number[];
  orbAnimId: number | null;
  orbGL: OrbGL | null;
  // Caption
  captionTimeout: ReturnType<typeof setTimeout> | null;
  // Transcript
  hasTranscriptContent: boolean;
}

const state: AppState = {
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
  orbColor: [0.35, 0.38, 0.50],
  orbAnimId: null,
  orbGL: null,
  // Caption
  captionTimeout: null,
  // Transcript
  hasTranscriptContent: false,
};

// ─── DOM References ───
const startBtn = document.getElementById("start-btn") as HTMLButtonElement;
const startSection = document.getElementById("start-section")!;
const sessionSection = document.getElementById("session-section")!;
const statusTextActive = document.getElementById("status-text-active")!;
const statusDot = document.querySelector("#status-active .dot") as HTMLElement;
const orbGlow = document.getElementById("orb-glow")!;
const orbClip = document.getElementById("orb-clip")!;
const liveCaption = document.getElementById("live-caption")!;
const cartSection = document.getElementById("cart-section")!;
const cartItems = document.getElementById("cart-items")!;
const cartLink = document.getElementById("cart-link")!;
const transcriptToggle = document.getElementById("transcript-toggle")!;
const transcriptPanel = document.getElementById("transcript-panel")!;
const transcript = document.getElementById("transcript")!;
const stopBtn = document.getElementById("stop-btn") as HTMLButtonElement;

// ─── Event Listeners ───
startBtn.addEventListener("click", startSession);
stopBtn.addEventListener("click", endSession);
transcriptToggle.addEventListener("click", toggleTranscript);

// ═══════════════════════════════════════════════════════════════
// WebGL Iridescent Orb
// ═══════════════════════════════════════════════════════════════

const VERT_SRC = `
attribute vec2 position;
attribute vec2 uv;
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position, 0, 1);
}`;

const FRAG_SRC = `
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

function compileShader(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
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

function linkProgram(gl: WebGLRenderingContext, vs: WebGLShader, fs: WebGLShader): WebGLProgram | null {
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
  const canvas = document.getElementById("orb-canvas") as HTMLCanvasElement;
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

  // Full-screen triangle covering clip space
  const posBuf = gl.createBuffer()!;
  gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);

  const uvBuf = gl.createBuffer()!;
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
    uSpeed: gl.getUniformLocation(program, "uSpeed"),
  };

  resizeOrbCanvas();
  window.addEventListener("resize", resizeOrbCanvas);
}

function resizeOrbCanvas() {
  if (!state.orbGL) return;
  const canvas = state.orbGL.gl.canvas as HTMLCanvasElement;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
}

function renderOrbFrame(time: number) {
  const o = state.orbGL;
  if (!o) return;
  const gl = o.gl;

  // Update audio level from analyser
  if (state.analyser && state.analyserData) {
    state.analyser.getByteFrequencyData(state.analyserData);
    let sum = 0;
    for (let i = 0; i < state.analyserData.length; i++) sum += state.analyserData[i];
    const avg = sum / state.analyserData.length;
    const norm = Math.min(1, Math.max(0, (avg - 16) / 90));
    state.smoothedAudioLevel += (norm - state.smoothedAudioLevel) * 0.15;
  } else {
    state.smoothedAudioLevel += (0 - state.smoothedAudioLevel) * 0.08;
  }

  const level = state.smoothedAudioLevel;

  // Lerp orb color toward target state color
  const target = STATE_COLORS[state.currentStatus] || STATE_COLORS.disconnected;
  for (let i = 0; i < 3; i++) {
    state.orbColor[i] += (target[i] - state.orbColor[i]) * 0.04;
  }

  // Audio-reactive uniforms
  const amplitude = 0.18 + level * 1.7;
  const speed = 0.75 + level * 0.5;

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
  const c = state.orbColor;
  const r = Math.round(c[0] * 255);
  const g = Math.round(c[1] * 255);
  const b = Math.round(c[2] * 255);
  const glowOpacity = 0.15 + level * 1.2;
  orbGlow.style.background = `rgba(${r}, ${g}, ${b}, ${glowOpacity})`;
  orbClip.style.boxShadow = `0 0 ${60 + level * 40}px rgba(${r}, ${g}, ${b}, ${0.2 + level * 0.3})`;

  // Scale orb with audio
  const scale = 1 + level * 0.35;
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

function setStatus(s: "connecting" | "listening" | "thinking" | "speaking" | "disconnected") {
  state.currentStatus = s;
  const labels: Record<string, string> = {
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
function setCaption(text: string, role = "assistant") {
  liveCaption.textContent = text;
  liveCaption.className = "visible role-" + role;

  if (state.captionTimeout) {
    clearTimeout(state.captionTimeout);
    state.captionTimeout = null;
  }

  // Auto-fade user and system captions after a delay
  if (role !== "assistant") {
    state.captionTimeout = setTimeout(() => {
      liveCaption.classList.remove("visible");
    }, 3000);
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

// ─── Transcript (hidden panel) ───
function addMessage(role: "assistant" | "user" | "system", text: string): HTMLElement {
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

// ─── Cart ───
function addCartItem(name: string, qty?: string) {
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
    const a = document.getElementById("cart-link-a") as HTMLAnchorElement;
    a.href = `https://www.realcanadiansuperstore.ca/en/cartReview?forceCartId=${state.cart_id}`;
  }
  cartLink.style.display = "block";
}

// ─── Transcript Toggle ───
function toggleTranscript() {
  const isExpanded = transcriptPanel.classList.toggle("expanded");
  transcriptToggle.classList.toggle("expanded", isExpanded);
  transcriptToggle.querySelector("span")!.textContent = isExpanded ? "Hide transcript" : "Show transcript";
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
    const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    if (audioCtx.state === "suspended") {
      await audioCtx.resume();
    }
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    state.audioCtx = audioCtx;
    state.analyser = analyser;
    state.analyserData = new Uint8Array(analyser.frequencyBinCount);

    // Create audio element and add to DOM for cross-browser reliability.
    // Using createMediaElementSource (instead of createMediaStreamSource)
    // because Chrome doesn't reliably feed WebRTC remote stream data to
    // an AnalyserNode via createMediaStreamSource — the DTLS connection
    // isn't established when ontrack fires, so the analyser gets silence.
    const audioEl = document.createElement("audio");
    audioEl.autoplay = true;
    audioEl.style.display = "none";
    document.body.appendChild(audioEl);
    state.audioEl = audioEl;

    // Route: audioEl -> mediaElementSource -> analyser -> destination
    // createMediaElementSource takes over the element's audio output,
    // so we must connect through to destination for audible playback.
    const mediaSource = audioCtx.createMediaElementSource(audioEl);
    mediaSource.connect(analyser);
    analyser.connect(audioCtx.destination);
    state.remoteSource = mediaSource;

    clog("Requesting ephemeral token...");
    const tokenRes = await fetch("/token");
    if (!tokenRes.ok) throw new Error("Token request failed: " + tokenRes.status);
    const tokenData = await tokenRes.json();
    const ephemeralKey = tokenData.client_secret.value;

    const pc = new RTCPeerConnection();
    state.pc = pc;

    // When remote audio track arrives, attach to audio element.
    // The MediaElementSource created above will automatically pick up
    // the audio and route it through the analyser for visualization.
    pc.ontrack = (ev) => {
      audioEl.srcObject = ev.streams[0];
      clog("Remote audio track received, routed through analyser");
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
        "Content-Type": "application/sdp",
      },
      body: pc.localDescription!.sdp,
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
  } catch (err: any) {
    clog("Session start failed: " + err.message, "error");
    addMessage("system", "Error: " + err.message);
    setCaption("Error: " + err.message, "system");
    setStatus("disconnected");
  }
}

function endSession() {
  // Stop mic
  if (state.localStream) {
    state.localStream.getTracks().forEach((t) => t.stop());
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
    state.audioEl.remove();
    state.audioEl = null;
  }
  // Disconnect and close audio graph
  if (state.remoteSource) {
    try { state.remoteSource.disconnect(); } catch (_) {}
    state.remoteSource = null;
  }
  if (state.audioCtx) {
    state.audioCtx.close().catch(() => {});
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
  setTimeout(() => {
    stopOrbLoop();
  }, 2000);
}

// ═══════════════════════════════════════════════════════════════
// Server Event Handling
// ═══════════════════════════════════════════════════════════════

let currentMsgEl: HTMLElement | null = null;

function handleServerEvent(event: any) {
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
            // Already handled by function_call_arguments.done
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

// ═══════════════════════════════════════════════════════════════
// Tool Call Handling
// ═══════════════════════════════════════════════════════════════

async function handleToolCall(event: any) {
  const { name, arguments: argsStr, call_id } = event;
  let args: any;
  try {
    args = JSON.parse(argsStr);
  } catch {
    args = {};
  }

  clog(`Tool call: ${name}(${argsStr})`);
  const label = name.replace(/_/g, " ");
  addMessage("system", `Looking up: ${label}...`);
  setCaption(`Looking up: ${label}...`, "system");

  let result: any;
  try {
    result = await callBackend(name, args);
  } catch (err: any) {
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

async function callBackend(fnName: string, args: any): Promise<any> {
  const endpointMap: Record<string, string> = {
    find_nearest_stores: "/api/find-stores",
    select_store: "/api/create-cart",
    search_products: "/api/search-products",
    add_to_cart: "/api/add-to-cart",
    finish_shopping: "/api/finish-shopping",
  };
  const endpoint = endpointMap[fnName];
  if (!endpoint) {
    return { error: "Unknown function: " + fnName };
  }
  const body: any = { ...args };
  if (state.cart_id && !body.cart_id) body.cart_id = state.cart_id;
  if (state.store_id && !body.store_id) body.store_id = state.store_id;

  const res = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error("API error " + res.status + ": " + text);
  }
  return res.json();
}

function sendDataChannelMessage(msg: any) {
  if (state.dc && state.dc.readyState === "open") {
    state.dc.send(JSON.stringify(msg));
  } else {
    console.warn("Data channel not open, cannot send:", msg);
  }
}
