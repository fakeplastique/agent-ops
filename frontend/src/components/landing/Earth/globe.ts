// A turning dot-matrix earth. Agents sit at cities, fly dotted arcs to a sign-off city and land as signed-receipt
// squares. Everything is simulated: the page makes up the requests (the band says so).
export type GlobeColors = { paper: string; pale: string; deep: string; agents: string[] };
export type GlobeOpts = {
  mask: Uint8Array; // 2-degree land mask, 180 x 90 bits, row 0 = 90N
  still: boolean; // reduced motion: earth and agents hold, only receipts flash
  colors: GlobeColors;
  icon?: HTMLImageElement | null; // agent icon; the brand mark is the fallback
  random?: () => number;
  onReceipt?: (total: number) => void;
};

const N = 3600;
const TILT = 0.38;
const HUBS = [
  [37.8, -122.4], [40.7, -74], [19.4, -99.1], [-23.5, -46.6], [51.5, -0.1], [50.1, 8.7], [6.5, 3.4], [-1.3, 36.8],
  [25.2, 55.3], [19.1, 72.9], [1.35, 103.8], [37.6, 127], [35.7, 139.7], [-33.9, 151.2], [43.7, -79.4],
];
const AGENTS = [0, 1, 3, 4, 7, 9, 12]; // hub indexes where an agent lives
const TAU = Math.PI * 2;
const STILL_GAP = 0.03; // seconds between redraws in reduced motion

const xyz = (lat: number, lon: number) => {
  const a = (lat * Math.PI) / 180;
  const b = (lon * Math.PI) / 180;
  return [Math.cos(a) * Math.sin(b), Math.sin(a), Math.cos(a) * Math.cos(b)];
};

type Flight = { a: number; b: number; t: number; d: number; col: string };
type Flash = { h: number; t: number; col: string; go?: boolean };

export function createGlobe(cv: HTMLCanvasElement, o: GlobeOpts) {
  const c = cv.getContext("2d");
  if (!c) return null;
  const rnd = o.random ?? Math.random;
  const { paper, pale, deep, agents: COL } = o.colors;
  const X = new Float32Array(N);
  const Y = new Float32Array(N);
  const Z = new Float32Array(N);
  const L = new Uint8Array(N);
  const land = (lat: number, lon: number) => {
    const i = Math.min(89, Math.floor((90 - lat) / 2)) * 180 + Math.min(179, Math.floor((lon + 180) / 2));
    return (o.mask[i >> 3] >> (7 - (i & 7))) & 1;
  };
  for (let i = 0; i < N; i++) {
    // Fibonacci sphere: evenly spread dots.
    const lat = (Math.asin(1 - (2 * (i + 0.5)) / N) * 180) / Math.PI;
    const lon = ((i * 137.508) % 360) - 180;
    [X[i], Y[i], Z[i]] = xyz(lat, lon);
    L[i] = land(lat, lon);
  }
  const hubs = HUBS.map(([la, lo]) => xyz(la, lo));
  const buf = [0, 1, 2, 3].map(() => new Float32Array(N * 2));
  const cnt = [0, 0, 0, 0];
  const ct = Math.cos(TILT);
  const st = Math.sin(TILT);

  let D = 0;
  let clock = 0;
  let next = 1;
  let receipts = 0;
  let yaw = o.still ? -0.35 : 0;
  let cy = 1;
  let sy = 0;
  let fl: Flight[] = [];
  let fx: Flash[] = [];
  let sinceDraw = 0;
  let wasFlashing = false;

  // Writes into one shared triple (callers read it before the next call): no allocation per dot per frame.
  const P = [0, 0, 0];
  const proj = (x: number, y: number, z: number) => {
    const x1 = x * cy + z * sy;
    const z1 = -x * sy + z * cy;
    P[0] = x1;
    P[1] = y * ct - z1 * st;
    P[2] = y * st + z1 * ct;
    return P;
  };
  // Point at fraction t along the great circle a->b, lifted off the surface so the flight reads as an arc.
  const arc = (a: number, b: number, t: number) => {
    const A = hubs[a];
    const B = hubs[b];
    const w = Math.acos(Math.max(-1, Math.min(1, A[0] * B[0] + A[1] * B[1] + A[2] * B[2])));
    const s = Math.sin(w) || 1;
    const k = 1 + 0.16 * Math.sin(Math.PI * t) * Math.min(1, w / 1.4);
    const p = Math.sin((1 - t) * w) / s;
    const q = Math.sin(t * w) / s;
    return [(p * A[0] + q * B[0]) * k, (p * A[1] + q * B[1]) * k, (p * A[2] + q * B[2]) * k];
  };

  // The icon is tinted once and thickened so it holds up at badge size.
  let sprite: HTMLCanvasElement | null = null;
  if (o.icon) {
    const S = 96;
    const t = document.createElement("canvas");
    t.width = t.height = S;
    const g = t.getContext("2d");
    if (g) {
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) g.drawImage(o.icon, dx * 1.3, dy * 1.3, S, S);
      g.globalCompositeOperation = "source-in";
      g.fillStyle = deep;
      g.fillRect(0, 0, S, S);
      sprite = t;
    }
  }
  // Fallback badge: the brand mark (three agents converging on one decision) in a disc.
  const agent = (x: number, y: number, r: number, alpha: number, col: string) => {
    c.globalAlpha = alpha;
    c.fillStyle = col;
    c.beginPath();
    c.arc(x, y, r, 0, TAU);
    c.fill();
    if (sprite) {
      c.drawImage(sprite, x - r * 0.84, y - r * 0.84, r * 1.68, r * 1.68);
      return;
    }
    const k = (r * 1.3) / 15;
    const ox = x - 11.5 * k;
    const oy = y - 12 * k;
    c.fillStyle = c.strokeStyle = deep;
    c.lineWidth = Math.max(1, 1.4 * k);
    c.beginPath();
    for (const y0 of [5, 12, 19]) {
      c.moveTo(ox + 6 * k, oy + y0 * k);
      c.lineTo(ox + 16 * k, oy + 12 * k);
    }
    c.stroke();
    for (const [dx, dy, dr] of [[4, 5, 2.2], [4, 12, 2.2], [4, 19, 2.2], [19, 12, 3.2]]) {
      c.beginPath();
      c.arc(ox + dx * k, oy + dy * k, dr * k, 0, TAU);
      c.fill();
    }
  };

  const resize = () => {
    const d = Math.min(devicePixelRatio || 1, 2);
    D = cv.clientWidth;
    cv.width = cv.height = D * d;
    c.setTransform(d, 0, 0, d, 0, 0);
  };

  const draw = () => {
    const G = D / 2;
    const R = G * 0.94;
    const sc = D / 760;
    const ar = Math.max(16, 22 * sc);
    cy = Math.cos(yaw);
    sy = Math.sin(yaw);
    c.clearRect(0, 0, D, D);
    cnt.fill(0);
    for (let i = 0; i < N; i++) {
      const p = proj(X[i], Y[i], Z[i]);
      const z = p[2];
      if (z <= 0.02) continue;
      const b = L[i] ? (z > 0.5 ? 0 : z > 0.2 ? 1 : 2) : 3;
      const k = cnt[b]++;
      buf[b][k * 2] = G + p[0] * R;
      buf[b][k * 2 + 1] = G - p[1] * R;
    }
    const rad = [2.8, 2.2, 1.5, 1.2];
    const alp = [1, 0.65, 0.35, 0.16];
    for (let b = 0; b < 4; b++) {
      c.globalAlpha = alp[b];
      c.fillStyle = b < 3 ? paper : pale;
      c.beginPath();
      for (let k = 0; k < cnt[b]; k++) {
        const x = buf[b][k * 2];
        const y = buf[b][k * 2 + 1];
        c.moveTo(x + rad[b] * sc, y);
        c.arc(x, y, rad[b] * sc, 0, TAU);
      }
      c.fill();
    }
    for (const f of fx) {
      const h = hubs[f.h];
      const p = proj(h[0], h[1], h[2]);
      if (p[2] <= 0.1) continue;
      const a = (clock - f.t) / 1.6;
      const x = G + p[0] * R;
      const y = G - p[1] * R;
      const q = (3 + 4 * Math.min(1, a * 6)) * sc;
      c.globalAlpha = 1 - a;
      c.fillStyle = f.col;
      if (!f.go) c.fillRect(x - q, y - q, q * 2, q * 2); // the signed receipt
      if (!o.still) {
        c.globalAlpha = 0.6 * (1 - a);
        c.strokeStyle = f.col;
        c.lineWidth = 2;
        c.beginPath();
        c.arc(x, y, q + a * 34 * sc, 0, TAU);
        c.stroke();
      }
    }
    AGENTS.forEach((h, i) => {
      const p = proj(hubs[h][0], hubs[h][1], hubs[h][2]);
      if (p[2] > 0.12) agent(G + p[0] * R, G - p[1] * R, ar * 0.8, 0.55 + 0.45 * Math.min(1, p[2] * 2), COL[i % COL.length]);
    });
    for (const f of fl) {
      const t = (clock - f.t) / f.d;
      const e = t * t * (3 - 2 * t);
      for (let k = 9; k >= 1; k--) {
        // dotted trail behind the agent
        const te = Math.max(0, t - k * 0.022);
        const p = proj(...(arc(f.a, f.b, te * te * (3 - 2 * te)) as [number, number, number]));
        if (p[2] > 0.05) {
          c.globalAlpha = (1 - k / 10) * 0.85;
          c.fillStyle = f.col;
          c.beginPath();
          c.arc(G + p[0] * R, G - p[1] * R, (3 - k * 0.25) * sc, 0, TAU);
          c.fill();
        }
      }
      const q = proj(...(arc(f.a, f.b, e) as [number, number, number]));
      if (q[2] > 0.05) agent(G + q[0] * R, G - q[1] * R, ar, Math.min(1, q[2] * 3), f.col);
    }
    c.globalAlpha = 1;
  };

  const pick = <T>(a: T[]) => a[Math.floor(rnd() * a.length)];
  const land1 = (h: number, col: string, go = false) => fx.push({ h, t: clock, col, go });
  const receipt = (h: number, col: string) => {
    land1(h, col);
    receipts++;
    o.onReceipt?.(receipts);
  };
  // dt in seconds. Reduced motion: no yaw, no flights; receipts still flash and count.
  const step = (dt: number) => {
    clock += dt;
    if (!o.still) yaw = clock * 0.06;
    if (clock >= next) {
      next = clock + 0.7 + rnd() * 1.4;
      if (o.still) receipt(Math.floor(rnd() * hubs.length), pick(COL));
      else {
        const ai = Math.floor(rnd() * AGENTS.length);
        const a = AGENTS[ai];
        let b = a;
        while (b === a) b = Math.floor(rnd() * hubs.length);
        fl.push({ a, b, t: clock, d: 2.2 + rnd() * 1.2, col: COL[ai % COL.length] });
        land1(a, COL[ai % COL.length], true);
      }
    }
    fl = fl.filter((f) => {
      if (clock - f.t < f.d) return true;
      receipt(f.b, f.col);
      return false;
    });
    fx = fx.filter((f) => clock - f.t < 1.6);
    sinceDraw += dt;
    if (!o.still) return draw();
    // Reduced motion: only receipt flashes change the picture, so draw at ~30 fps while one shows (and once more to clear it).
    if (sinceDraw < STILL_GAP) return;
    sinceDraw = 0;
    const flashing = fx.length > 0;
    if (flashing || wasFlashing) draw();
    wasFlashing = flashing;
  };

  resize();
  draw();
  return { resize, draw, step, state: () => ({ yaw, flights: fl.length, receipts }) };
}
