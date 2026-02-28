# Research: Simpler Orb Effects for Voice App

## Context

The current voice app orb uses a medium-high complexity WebGL fragment shader with an 8-iteration feedback loop and nested cosine interference patterns to create iridescent fractal visuals. This research explores simpler, modern, minimalist alternatives that react to the agent's generated audio.

### Current Implementation Summary

- **Shader**: 8-iteration `for` loop with `cos`/`sin` feedback creating fractal patterns (~14 lines of GLSL)
- **Audio pipeline**: FFT (512 bins) → average → deadzone → exponential smoothing (0.04) → drives `uAmplitude` [0.05-0.19] and `uSpeed` [0.15-0.31]
- **CSS layers**: blur glow (80px), audio-reactive scale [1.0-1.43], box-shadow
- **Known bug**: Line 244-245 of `app.ts` hardcodes `STATE_COLORS.thinking` instead of using `STATE_COLORS[state.currentStatus]`, so state colors never actually change
- **Files**: `voice-app/public/app.ts` (shader + render loop), `voice-app/public/index.html` (DOM + CSS)

---

## Industry Research & Trends (2025-2026)

### Voice Assistant Visual Design

| Product | Approach |
|---------|----------|
| **Apple Siri (iOS 18+)** | Moved FROM central orb TO screen-edge glow. Iridescent colors pulse around device border reacting to voice. Non-intrusive — doesn't block content. |
| **ChatGPT Voice Mode** | Central animated orb/sphere with color shifts and pulsing. Retained the classic "orb" metaphor. |
| **Google Material Expressive** | Dynamic motion + tactile response. Emphasis on sensory feedback. |

### Key Design Trends

1. **Evolved Minimalism** — Purposeful micro-interactions over visual complexity. Clean layouts enhanced with intentional motion.
2. **"The Human Layer"** — Interfaces that respond to voice, gesture, and emotional tone. Sound-reactive UI elements.
3. **Minimalist Feedback** — Subtle visual cues confirming actions without breaking immersion. Less is more.
4. **Kinetic Typography** — Variable fonts that animate in response to audio (complementary technique, not orb-specific).

### Sources

- [UI Design Trends 2026 - Muzli](https://muz.li/blog/web-design-trends-2026/)
- [Minimalist UI Design 2026 - ANC Tech](https://www.anctech.in/blog/explore-how-minimalist-ui-design-in-2026-focuses-on-performance-accessibility-and-content-clarity-learn-how-clean-interfaces-subtle-interactions-and-data-driven-layouts-create-better-user-experie/)
- [iOS 18 Siri Edge Glow - 9to5Mac](https://9to5mac.com/2024/11/03/new-apple-intelligence-siri-looks-different-works-the-same/)
- [UI Trends 2026 - UX Studio](https://www.uxstudioteam.com/ux-blog/ui-trends-2019)

---

## Common Simple Techniques

### Shader-Based

| Technique | How It Works | Complexity |
|-----------|-------------|------------|
| **SDF Glow Circle** | `glow = 0.01 / distance` — inverse distance creates natural falloff | Very Low |
| **Fresnel Rim Light** | `pow(smoothstep(inner, outer, dist), N)` — edges brighter than center | Low |
| **Breathing Pulse** | `sin(time)` modulates radius or intensity | Very Low |
| **FBM Noise** | Hash-based pseudo-noise layered at 2-3 octaves for organic movement | Medium |
| **Concentric Rings** | `fract(dist - time)` creates expanding ripples | Low |

### CSS / Canvas 2D (No WebGL)

| Technique | How It Works | Complexity |
|-----------|-------------|------------|
| **Radial Gradient** | `createRadialGradient()` or CSS `radial-gradient` with offset center | Very Low |
| **Box-Shadow Glow** | Multiple layered `box-shadow` values with blur | Very Low |
| **CSS Scale + Opacity** | `transform: scale()` + `opacity` driven by audio at 60fps | Very Low |
| **Backdrop Filter Blur** | `backdrop-filter: blur()` for frosted glass effect | Very Low |

### Sources

- [Glow Shader Tutorial - Shadertoy](https://inspirnathan.com/posts/65-glow-shader-in-shadertoy/)
- [MDN Web Audio API Visualizations](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Visualizations_with_Web_Audio_API)
- [Shadertoy Glow Tutorial](https://www.shadertoy.com/view/3s3GDn)

---

## Proposed Alternatives

### Option 1: "Breathing Dot" (Pure CSS — No WebGL)

**Visual**: Solid circle with radial gradient (bright offset center at 40%/38% for 3D illusion). Audio drives scale pulsing + glow intensity + inner gradient expansion. Think "Apple Siri pre-2024."

**Approach**: Remove all WebGL code. Replace `<canvas>` with `<div id="orb-fill">`. Use CSS `radial-gradient` updated per frame.

```css
#orb-fill {
  width: 100%; height: 100%;
  border-radius: 50%;
  background: radial-gradient(
    circle at 40% 38%,
    rgba(255,255,255,0.15) 0%,
    var(--orb-color) 35%,
    color-mix(in srgb, var(--orb-color) 60%, black) 100%
  );
}
```

**Audio mapping**: Update inline `radial-gradient` per frame — expand bright center from 30% to 45% with audio, plus existing CSS scale [1.0-1.2] and glow opacity [0.14-1.2].

| Pros | Cons |
|------|------|
| Maximum simplicity — 0 shader lines | Least visually impressive |
| Universal device/browser support | No organic movement or texture |
| Removes ~80 lines of WebGL code | May feel "too simple" for a demo |
| Easy to maintain for non-graphics engineers | Inline style string updates at 60fps (minor cost) |

**Complexity**: Very Low. ~95% less GPU work than current.

---

### Option 2: "Soft Glow Sphere" (SDF + Fresnel) ← Recommended

**Visual**: Perfectly round, softly glowing sphere. Rich semi-transparent fill with dramatic Fresnel edge brightening — like a glass marble. Subtle `sin`-based inner swirl. Audio causes breathing + rim flare. Calm translucent marble at rest; bright rim when speaking.

**Shader** (~12 lines, no loops):

```glsl
precision highp float;
uniform float uTime, uAmplitude, uSpeed;
uniform vec3 uColor, uResolution;
varying vec2 vUv;

void main() {
  float mr = min(uResolution.x, uResolution.y);
  vec2 uv = (vUv * 2.0 - 1.0) * uResolution.xy / mr;
  float dist = length(uv);

  // Soft disc fill
  float fill = smoothstep(1.0, 0.4, dist);

  // Fresnel rim — bright edges, dim center
  float rim = pow(smoothstep(0.3, 1.0, dist), 2.0) * smoothstep(1.2, 0.95, dist);
  float rimStrength = 0.3 + uAmplitude * 3.0;

  // Subtle inner movement
  float swirl = sin(uTime * uSpeed * 0.5 + dist * 4.0) * 0.05;

  vec3 col = uColor * fill * (0.6 + swirl)
           + uColor * rim * rimStrength * (1.2 + sin(uTime * 0.8) * 0.1);

  // Outer glow falloff
  float outerGlow = (1.0 / (dist * dist + 0.01)) * 0.005 * (0.5 + uAmplitude * 2.0);
  col += uColor * outerGlow;

  gl_FragColor = vec4(col, 1.0);
}
```

**Audio mapping**: amplitude = `0.05 + level * 0.25`, speed = `0.1 + level * 0.2`, scale = `1 + level * 0.15`.

| Pros | Cons |
|------|------|
| Extremely simple (no loops, no feedback) | Less visually "interesting" than fractal |
| Fresnel effect reads as premium glass | No multi-color iridescence |
| Clean Apple-style minimalism | May feel static at low audio levels |
| Fast on all devices including low-end mobile | Monochromatic per-state |
| State colors clearly visible | |

**Complexity**: Low. ~80% less GPU work than current.

---

### Option 3: "Pulse Rings" (Concentric Expanding Rings)

**Visual**: Central glowing dot with 3 concentric rings expanding outward like water ripples. At rest, rings pulse slowly. Audio spawns rings faster with greater intensity. Radar/sonar aesthetic — clean, geometric, hypnotic.

**Shader** (~16 lines, 3-iteration loop):

```glsl
precision highp float;
uniform float uTime, uAmplitude, uSpeed;
uniform vec3 uColor, uResolution;
varying vec2 vUv;

void main() {
  float mr = min(uResolution.x, uResolution.y);
  vec2 uv = (vUv * 2.0 - 1.0) * uResolution.xy / mr;
  float dist = length(uv);

  // Central core glow
  float core = exp(-dist * dist * 8.0) * (0.8 + uAmplitude * 0.5);

  // 3 expanding rings, evenly phased
  float ringSum = 0.0;
  float speed = uTime * uSpeed;
  for (float i = 0.0; i < 3.0; i++) {
    float phase = speed + i * 2.094;          // 2π/3 spacing
    float ringPos = fract(phase * 0.15) * 1.5;
    float ring = smoothstep(0.03 + uAmplitude * 0.02, 0.0, abs(dist - ringPos));
    float fade = 1.0 - fract(phase * 0.15);   // fade as ring expands
    ringSum += ring * fade * (0.4 + uAmplitude);
  }

  float bg = smoothstep(1.2, 0.6, dist) * 0.08; // subtle background disc
  vec3 col = uColor * (core + ringSum + bg);
  gl_FragColor = vec4(col, 1.0);
}
```

**Audio mapping**: speed = `0.3 + level * 0.8`, amplitude = `0.05 + level * 0.3`. Disable CSS scale (rings provide the expansion visual).

| Pros | Cons |
|------|------|
| Visually dynamic and rhythmic | More "techy" than organic |
| Strong "responding to sound" metaphor | Concentric rings are a common pattern |
| 3-iteration loop is vastly simpler than 8 | Ring count is a design tradeoff |
| Each ring independently tunable | |

**Complexity**: Low-Medium. ~70% less GPU work than current.

---

### Option 4: "Nebula Bloom" (FBM Noise Cloud)

**Visual**: Slowly morphing color cloud filling the orb. Trig-based pseudo-noise at 3 octaves creates organic movement. Subtle hue shifts within state color range. Fresnel rim for edge definition. Audio drives turbulence — calm drift at rest, churning when speaking. Like a lava lamp in a circle.

**Shader** (~30 lines, FBM with 3 octaves):

```glsl
precision highp float;
uniform float uTime, uAmplitude, uSpeed;
uniform vec3 uColor, uResolution;
varying vec2 vUv;

float noise(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float smoothNoise(vec2 p) {
  vec2 i = floor(p); vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = noise(i), b = noise(i + vec2(1,0));
  float c = noise(i + vec2(0,1)), d = noise(i + vec2(1,1));
  return mix(mix(a,b,f.x), mix(c,d,f.x), f.y);
}

float fbm(vec2 p) {
  return smoothNoise(p*2.0)*0.5 + smoothNoise(p*4.0+1.3)*0.25 + smoothNoise(p*8.0+2.7)*0.125;
}

void main() {
  float mr = min(uResolution.x, uResolution.y);
  vec2 uv = (vUv * 2.0 - 1.0) * uResolution.xy / mr;
  float dist = length(uv);
  float mask = smoothstep(1.0, 0.7, dist);

  float t = uTime * uSpeed * 0.3;
  float n = fbm(uv * (1.5 + uAmplitude * 3.0) + vec2(t, t * 0.7));

  vec3 col = mix(uColor, uColor * vec3(0.7, 1.1, 1.3), n) * (0.5 + n * 0.8 + uAmplitude * 0.5);

  // Fresnel rim
  float rim = pow(smoothstep(0.3, 0.95, dist), 3.0) * smoothstep(1.1, 0.95, dist);
  col += uColor * rim * (0.2 + uAmplitude * 0.6);

  gl_FragColor = vec4(col * mask, 1.0);
}
```

**Audio mapping**: speed = `0.2 + level * 0.4`, amplitude = `0.05 + level * 0.2`, scale = `1 + level * 0.2`.

| Pros | Cons |
|------|------|
| Most visually "alive" of the alternatives | Most complex alternative (~30 shader lines) |
| Endlessly varied, never repetitive | Hash noise can band on low-precision mobile GPU |
| Subtle hue shifts add depth | Requires FBM tuning for small orb size |
| Balances simplicity and richness | Still needs WebGL |

**Complexity**: Medium. ~50% less GPU work than current.

---

## Comparison Matrix

| Aspect | Current (Fractal) | 1. Breathing Dot | 2. Soft Glow ★ | 3. Pulse Rings | 4. Nebula Bloom |
|---|---|---|---|---|---|
| **Shader lines** | 14 (8-iter loop) | 0 (no shader) | 12 (no loop) | 16 (3-iter) | 30 (FBM) |
| **GPU complexity** | High | None | Low | Low-Med | Medium |
| **Visual richness** | ★★★★★ | ★★ | ★★★ | ★★★½ | ★★★★ |
| **Minimalism** | ★★ | ★★★★★ | ★★★★★ | ★★★★ | ★★★½ |
| **State color clarity** | Weak (fractal obscures) | Strong | Strong | Strong | Strong |
| **Audio reactivity feel** | Speed + amplitude | Scale + glow | Rim flare + breathe | Ring spawn rate | Turbulence + brightness |
| **Mobile performance** | Moderate | Excellent | Excellent | Excellent | Good |
| **WebGL required** | Yes | No | Yes | Yes | Yes |
| **Maintenance** | Medium | Very low | Very low | Low | Low-Medium |

---

## Recommendation

**Option 2 "Soft Glow Sphere"** as the new default. It's the best balance of:
- Simplicity (no loops, 12 lines)
- Modern minimalist aesthetic (Fresnel glass-marble look)
- Clear audio reactivity (rim flare + breathing)
- Strong state color visibility
- Universal performance

Optionally implement **Option 1 "Breathing Dot"** as a CSS-only fallback for no-WebGL environments.

---

## Implementation Steps

1. Fix state color bug: change `STATE_COLORS.thinking` → `STATE_COLORS[state.currentStatus]` (line 244-245 of `app.ts`)
2. Replace `FRAG_SRC` constant (lines 127-147) with chosen shader
3. Adjust audio-reactive mapping in `renderOrbFrame` (lines 251-252)
4. Tune CSS overlay params (lines 279-285): glow opacity, box-shadow, scale ranges
5. Build: `cd voice-app && npm run build`
6. Test all 5 states: connecting (orange), listening (green), speaking (red), thinking (blue), disconnected (gray)
