/* The compact scrub's canvas: the same drawing the host page does, in the same order, with the
 * same numbers.
 *
 * It is a port, not a rewrite. The comments that explain the measurements are the host's,
 * because the measurements are the point: the wave stands on the bar's bottom edge, the bars
 * keep a real gap, and with no envelope decoded yet the surface falls back to a flat progress
 * line so the position is still readable. Changing any of those numbers moves pixels that the
 * baselines in `tests/visual/baseline/` pin.
 */

/** How the host resolves `hl.wave.v1` when nothing is stored. */
export interface WaveConfig {
  bars: number;
  level: number;
  smooth: number;
  mirror: boolean;
  enabled: boolean;
}

export const DEFAULT_WAVE_CONFIG: WaveConfig = {
  bars: Math.round(64 * 1.5),
  level: 30,
  smooth: 25,
  mirror: true,
  enabled: true,
};

export interface WaveState {
  /** Playhead, 0..1. */
  progress: number;
  /** True while a finger or pointer holds the scrub. */
  scrubbing: boolean;
  /** Where the held playhead is, 0..1. Ignored when `scrubbing` is false. */
  scrubAt: number;
  /** The track's envelope, 0..1, or `null` while it is still decoding. */
  envelope: Float32Array | null;
}

/** Per-canvas backing store, resized to the CSS box at a capped device-pixel ratio. */
interface Surface {
  canvas: HTMLCanvasElement;
  g: CanvasRenderingContext2D;
  w: number;
  h: number;
}

function fit(surface: Surface): boolean {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = surface.canvas.clientWidth;
  const h = surface.canvas.clientHeight;
  if (!w || !h) return false;
  const W = Math.round(w * dpr);
  const H = Math.round(h * dpr);
  if (surface.canvas.width !== W || surface.canvas.height !== H) {
    surface.canvas.width = W;
    surface.canvas.height = H;
  }
  surface.g.setTransform(dpr, 0, 0, dpr, 0, 0);
  surface.w = w;
  surface.h = h;
  return true;
}

/** The envelope resampled to `n` bars, with the smooth knob applied as a box blur. */
function barsFor(state: WaveState, config: WaveConfig, n: number): Float32Array {
  const src = state.envelope;
  const out = new Float32Array(n);
  if (!src) return out;
  for (let i = 0; i < n; i++) {
    const a = Math.floor((i * src.length) / n);
    const b = Math.max(a + 1, Math.floor(((i + 1) * src.length) / n));
    let peak = 0;
    for (let j = a; j < b && j < src.length; j++) if (src[j]! > peak) peak = src[j]!;
    out[i] = peak;
  }

  const clamp = (arr: Float32Array) => {
    for (let i = 0; i < arr.length; i++) arr[i] = arr[i]! < 0 ? 0 : arr[i]! > 1 ? 1 : arr[i]!;
    return arr;
  };

  const k = Math.round(Math.max(0, Math.min(1, config.smooth / 100)) * 4);
  if (!k) return clamp(out);

  const blurred = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let peak = 0;
    for (let d = -k; d <= k; d++) {
      const j = i + d;
      if (j < 0 || j >= n) continue;
      if (out[j]! > peak) peak = out[j]!;
    }
    blurred[i] = peak;
  }
  return clamp(blurred);
}

function accentColour(): string {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
    if (value) return value;
  } catch {
    /* A document without a root element is a test harness; the default stands. */
  }
  return "#58d3a8";
}

/**
 * Draw one frame. Returns `false` when the canvas has no box to draw into yet, which is the
 * normal state before layout and is not an error.
 */
export function drawCompactWave(
  canvas: HTMLCanvasElement,
  state: WaveState,
  config: WaveConfig = DEFAULT_WAVE_CONFIG,
  surface?: Surface,
): boolean {
  const current: Surface = surface ?? { canvas, g: canvas.getContext("2d")!, w: 0, h: 0 };
  if (!current.g) return false;
  if (!fit(current)) return false;

  const g = current.g;
  const w = current.w;
  const h = current.h;
  const accent = accentColour();

  g.clearRect(0, 0, w, h);
  if (!config.enabled) return true;

  const n0 = Math.max(12, Math.round(config.bars));
  const p = Math.max(0, Math.min(1, state.progress));

  const scrubbing = state.scrubbing;
  const head = scrubbing ? Math.max(0, Math.min(1, state.scrubAt)) : p;

  const PHONE_W = 182;
  const MIN_GAP = 1;
  const MIN_STEP = 2.5;
  let n = Math.max(12, Math.min(512, Math.round((n0 * w) / PHONE_W)));
  if (w / n < MIN_STEP) n = Math.max(12, Math.floor(w / MIN_STEP));

  const amp = barsFor(state, config, n);
  const step = w / n;
  const bw = Math.min(step * 0.62, Math.max(1, step - MIN_GAP));

  const TRACK_Y = h - 3;

  let lit = false;
  for (let i = 0; i < n; i++) {
    if ((amp[i] ?? 0) > 0.02) {
      lit = true;
      break;
    }
  }

  if (!lit) {
    // No envelope yet: a flat progress line on the base, so the position is still readable.
    g.fillStyle = "#2a2d35";
    g.fillRect(0, TRACK_Y, w, 3);
    g.fillStyle = accent;
    g.fillRect(0, TRACK_Y, Math.max(3, w * head), 3);
    if (scrubbing) {
      const x = Math.round(Math.max(1, Math.min(w - 1, head * w)));
      g.fillStyle = "#eef1f6";
      g.fillRect(x - 1, TRACK_Y - 18, 2, 21);
    }
    g.globalAlpha = 1;
    return true;
  }

  const WAVE_SCALE = 0.575;
  const barH = (i: number) => 1 + (amp[i] ?? 0) * (h - 2) * WAVE_SCALE;

  g.globalAlpha = scrubbing ? 0.82 : 0.55;
  g.fillStyle = "#4a4e58";
  for (let i = 0; i < n; i++) {
    const bh = barH(i);
    g.fillRect(i * step + (step - bw) / 2, h - bh, bw, bh);
  }

  g.globalAlpha = 1;
  g.fillStyle = accent;
  for (let i = 0; i < n; i++) {
    if ((i + 0.5) / n > head) break;
    const bh = barH(i);
    g.fillRect(i * step + (step - bw) / 2, h - bh, bw, bh);
  }

  if (scrubbing) {
    const x = Math.round(Math.max(1, Math.min(w - 1, head * w)));
    const band = (h - 2) * WAVE_SCALE;
    g.fillStyle = "#eef1f6";
    g.fillRect(x - 1, h - band - 2, 2, band + 2);
  }

  return true;
}

/** A canvas and its context, kept across frames so the backing store is only resized when it
 * has to be. */
export function waveSurface(canvas: HTMLCanvasElement): Surface {
  const g = canvas.getContext("2d");
  if (!g) throw new Error("This build has no 2D canvas");
  return { canvas, g, w: 0, h: 0 };
}