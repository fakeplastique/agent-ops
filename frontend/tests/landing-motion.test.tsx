import { act, render } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Earth } from "@/components/landing/Earth/Earth";
import { createGlobe } from "@/components/landing/Earth/globe";
import { Orb } from "@/components/landing/Orb/Orb";

// jsdom has no WebGL, rAF timing or IntersectionObserver: these fakes let a test drive frames by hand.
const fakeGl = () => {
  const gl: Record<string, unknown> = {
    getShaderParameter: () => true,
    getProgramParameter: () => true,
    drawArrays: vi.fn(),
  };
  return new Proxy(gl, { get: (t, k: string) => (k in t ? t[k] : (t[k] = vi.fn(() => ({})))) });
};

const reducedMotion = (on: boolean) =>
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: on && q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));

let frames: Map<number, FrameRequestCallback>;
let nextId: number;
const fakeRaf = () => {
  frames = new Map();
  nextId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => (frames.set(nextId, cb), nextId++));
  vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => frames.delete(id)));
};
// Runs every queued frame callback at time `t`.
const tickAt = (t: number) => {
  const due = [...frames.entries()];
  frames.clear();
  act(() => due.forEach(([, cb]) => cb(t)));
};

let visible: (on: boolean) => void;
const fakeObserver = () =>
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(cb: (e: { isIntersecting: boolean }[]) => void) {
        visible = (on) => act(() => cb([{ isIntersecting: on }]));
      }
      observe() {}
      disconnect() {}
    },
  );

beforeEach(() => {
  fakeRaf();
  fakeObserver();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Orb", () => {
  it("no WebGL context leaves a plain teal orb", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    reducedMotion(false);
    const { container } = render(<Orb />);
    expect(container.querySelector("canvas")?.dataset.silk).toBeUndefined();
    expect(frames.size).toBe(0);
  });

  it("reduced motion draws one still frame and runs no animation loop", () => {
    const gl = fakeGl();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(gl as never);
    reducedMotion(true);
    render(<Orb />);
    expect(gl.drawArrays).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
  });

  it("loop pauses offscreen and caps at 30 fps (fake rAF)", () => {
    const gl = fakeGl();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(gl as never);
    reducedMotion(false);
    render(<Orb />);
    const drawn = () => (gl.drawArrays as ReturnType<typeof vi.fn>).mock.calls.length;
    const first = drawn(); // the initial frame
    visible(true);
    const t0 = performance.now();
    for (let i = 1; i <= 60; i++) tickAt(t0 + i * (1000 / 60)); // one second at 60 Hz
    expect(drawn() - first).toBeGreaterThanOrEqual(25);
    expect(drawn() - first).toBeLessThanOrEqual(31);

    visible(false);
    expect(cancelAnimationFrame).toHaveBeenCalled();
    const before = drawn();
    for (let i = 61; i <= 90; i++) tickAt(t0 + i * (1000 / 60));
    expect(drawn()).toBe(before);
  });
});

const ctx2d = () =>
  new Proxy({}, { get: (t: Record<string, unknown>, k: string) => (k in t ? t[k] : (t[k] = vi.fn())), set: (t: Record<string, unknown>, k: string, v) => ((t[k] = v), true) });
const colors = { paper: "white", pale: "white", deep: "black", agents: ["white", "gray"] };
const globe = (still: boolean, fixed?: number) => {
  const cv = document.createElement("canvas");
  const ctx = ctx2d();
  vi.spyOn(cv, "getContext").mockReturnValue(ctx as never);
  let seed = 7;
  const random = () => fixed ?? ((seed = (seed * 16807) % 2147483647) / 2147483647); // deterministic
  const g = createGlobe(cv, { mask: new Uint8Array(2025), still, colors, random })!;
  return Object.assign(g, { draws: () => (ctx as { clearRect: ReturnType<typeof vi.fn> }).clearRect.mock.calls.length });
};

describe("Orb context loss", () => {
  // A lost context draws nothing; the loop must stop and the orb falls back to plain teal.
  it("webglcontextlost is handled and stops the loop", () => {
    const gl = fakeGl();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(gl as never);
    reducedMotion(false);
    const { container } = render(<Orb />);
    const cv = container.querySelector("canvas")!;
    visible(true);
    const drawn = () => (gl.drawArrays as ReturnType<typeof vi.fn>).mock.calls.length;
    const t0 = performance.now();
    tickAt(t0 + 40);
    const lost = new Event("webglcontextlost", { cancelable: true });
    act(() => void cv.dispatchEvent(lost));
    expect(lost.defaultPrevented).toBe(true);
    const before = drawn();
    for (let i = 2; i < 10; i++) tickAt(t0 + i * 40);
    expect(drawn()).toBe(before);
    expect(frames.size).toBe(0);
  });
});

describe("Earth", () => {
  // The count is made up by the page, so the band must say so and start from zero.
  it("labelled 'simulated feed', no invented statistic", () => {
    const html = renderToString(<Earth />);
    expect(html).toContain("simulated feed");
    expect(html).toMatch(/ln-count[^>]*>0</);
    const text = html.replace(/<[^>]+>/g, " ").replace(/ln-[a-z-]+/g, "");
    expect(text.replace(/\b0\b/, "")).not.toMatch(/\d/);
  });

  it("reduced motion holds earth and agents still, only receipts flash", () => {
    const g = globe(true);
    const yaw = g.state().yaw;
    for (let i = 0; i < 200; i++) g.step(0.1); // 20 simulated seconds
    expect(g.state().yaw).toBe(yaw);
    expect(g.state().flights).toBe(0);
    expect(g.state().receipts).toBeGreaterThan(5);
  });

  // Still mode only has receipt flashes to show: redraw at most ~30 fps, and not at all while nothing flashes.
  it("reduced motion redraws at most 30 fps and not at all between flashes", () => {
    const g = globe(true, 0.99); // a receipt at ~1 s, flashing 1.6 s, the next one ~2 s later
    const start = g.draws();
    for (let i = 0; i < 600; i++) g.step(1 / 60); // 10 s at 60 Hz
    expect(g.draws() - start).toBeLessThanOrEqual(10 * 30 + 5);
    const h = globe(true, 0.99);
    for (let i = 0; i < 280; i++) h.step(0.01); // to 2.8 s: the first flash is over
    const idle = h.draws();
    for (let i = 0; i < 25; i++) h.step(0.01); // 2.8 s to 3.05 s: nothing flashes
    expect(h.draws()).toBe(idle);
  });

  it("when motion is allowed the earth turns and agents fly, and each landing counts one receipt", () => {
    const g = globe(false);
    const yaw = g.state().yaw;
    let maxFlights = 0;
    for (let i = 0; i < 200; i++) {
      g.step(0.1);
      maxFlights = Math.max(maxFlights, g.state().flights);
    }
    expect(g.state().yaw).not.toBe(yaw);
    expect(maxFlights).toBeGreaterThan(0);
    expect(g.state().receipts).toBeGreaterThan(5);
  });

  it("the counter follows the simulated receipts", () => {
    const seen: number[] = [];
    const cv = document.createElement("canvas");
    vi.spyOn(cv, "getContext").mockReturnValue(ctx2d() as never);
    const h = createGlobe(cv, { mask: new Uint8Array(2025), still: true, colors, onReceipt: (n) => seen.push(n) })!;
    for (let i = 0; i < 100; i++) h.step(0.1);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen).toEqual(seen.map((_, i) => i + 1));
  });
});
