@AGENTS.md

## Voice App

WebRTC voice shopping UI using OpenAI Realtime API with an iridescent WebGL orb.

### Architecture

- **Backend**: `voice-app/modal_app.py` — Modal-deployed FastAPI server (token endpoint, store/cart/product proxy APIs)
- **Frontend**: `voice-app/public/index.html` (markup + CSS) + `voice-app/public/app.ts` (all logic)
- **Build**: `cd voice-app && npm run build` compiles `app.ts` → `app.js` via esbuild

### Key files to edit

| What | File | Lines |
|------|------|-------|
| Orb shader + WebGL setup | `voice-app/public/app.ts` | `initOrbGL`, `renderOrbFrame`, `FRAG_SRC` |
| Audio analysis (agent audio → orb) | `voice-app/public/app.ts` | `startSession` (AudioContext + analyser wiring), `renderOrbFrame` (reads analyser) |
| Orb styling / layout | `voice-app/public/index.html` | `#orb-container`, `#orb-glow`, `#orb-clip`, `#orb-canvas` |
| Status / state colors | `voice-app/public/app.ts` | `STATE_COLORS` constant |
| Server events → status changes | `voice-app/public/app.ts` | `handleServerEvent` |
| Backend APIs + system prompt | `voice-app/modal_app.py` | Full file |

### Audio → Orb pipeline

1. `startSession` creates `AudioContext` + `AnalyserNode` (fftSize=1024)
2. `pc.ontrack` connects remote audio stream to the analyser via `createMediaStreamSource`
3. `renderOrbFrame` reads `getByteFrequencyData`, normalizes to 0-1, smooths, and drives shader uniforms (`uAmplitude`, `uSpeed`) + CSS (`scale`, `glowOpacity`)

### Deploy

```bash
cd voice-app && npm run build   # compile TS
uv run modal deploy voice-app/modal_app.py
```