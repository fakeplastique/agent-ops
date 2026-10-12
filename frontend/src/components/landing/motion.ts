export const prefersReducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// Runs tick only while `el` is on screen; minGapMs caps the frame rate. Returns a stop function.
export function runWhileVisible(el: Element, tick: (now: number, dt: number) => void, minGapMs = 0): () => void {
  let raf = 0;
  let last = 0;
  const loop = (now: number) => {
    raf = requestAnimationFrame(loop);
    if (now - last < minGapMs) return;
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    tick(now, dt);
  };
  const start = () => {
    last = performance.now();
    raf = requestAnimationFrame(loop);
  };
  const stop = () => cancelAnimationFrame(raf);
  if (typeof IntersectionObserver === "undefined") {
    start();
    return stop;
  }
  const io = new IntersectionObserver((entries) => {
    stop();
    if (entries[entries.length - 1].isIntersecting) start();
  });
  io.observe(el);
  return () => {
    io.disconnect();
    stop();
  };
}

// Resolves a CSS colour expression (tokens, color-mix) to the browser's computed colour string.
export function colorOf(host: Element, expr: string, fallback: string): string {
  const probe = document.createElement("span");
  probe.style.color = expr;
  host.appendChild(probe);
  const value = getComputedStyle(probe).color;
  probe.remove();
  return value || fallback;
}

// Computed colours come back as `color(srgb r g b)` (0..1) or as comma-separated 0..255 channels.
export function channels(css: string, fallback: number[]): number[] {
  const n = css.match(/[\d.]+/g)?.slice(0, 3).map(Number);
  if (!n || n.length < 3 || n.some(Number.isNaN)) return fallback;
  return css.startsWith("color(") ? n : n.map((v) => v / 255);
}

export const mix = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);
