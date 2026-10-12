"use client";

import { useEffect, useRef, useState } from "react";

import { colorOf, prefersReducedMotion, runWhileVisible } from "../motion";
import { createGlobe } from "./globe";

// One light colour per agent, all mixed from tokens and lighter than the band's ground.
const AGENT_COLORS = [
  "var(--ln-pale)",
  "color-mix(in srgb, var(--accent) 55%, var(--surface))",
  "var(--control)",
  "var(--surface)",
  "color-mix(in srgb, var(--positive) 40%, var(--surface))",
  "color-mix(in srgb, var(--muted) 30%, var(--surface))",
  "color-mix(in srgb, var(--accent) 30%, var(--surface))",
];

const loadIcon = () =>
  new Promise<HTMLImageElement | null>((done) => {
    const img = new Image();
    img.onload = () => done(img);
    img.onerror = () => done(null); // the brand mark is the fallback
    img.src = "/landing/agent.png";
  });

// The live band: heading, receipt counter and the earth. Everything drawn here is simulated and labelled so.
export function Earth() {
  const ref = useRef<HTMLCanvasElement>(null);
  const [count, setCount] = useState(0);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    let stop = () => {};
    let dead = false;
    const still = prefersReducedMotion();
    const col = (expr: string) => colorOf(cv, expr, "white");
    Promise.all([
      fetch("/landing/land-mask.bin").then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error("mask")))),
      loadIcon(),
    ])
      .then(([mask, icon]) => {
        if (dead) return;
        const g = createGlobe(cv, {
          mask: new Uint8Array(mask),
          still,
          icon,
          colors: { paper: col("var(--surface)"), pale: col("var(--ln-pale)"), deep: col("var(--ln-deep)"), agents: AGENT_COLORS.map(col) },
          onReceipt: setCount,
        });
        if (!g) return;
        cv.dataset.earth = still ? "still" : "turning";
        const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => (g.resize(), g.draw()));
        resize?.observe(cv);
        // Reduced motion keeps the earth fixed, so only the receipt flashes need frames.
        const halt = runWhileVisible(cv, (_n, dt) => g.step(dt));
        stop = () => (halt(), resize?.disconnect());
      })
      .catch(() => {}); // no mask: the band stays text only
    return () => {
      dead = true;
      stop();
    };
  }, []);

  return (
    <section className="ln-live" id="live" aria-labelledby="live-h">
      <div className="ln-in">
        <h2 id="live-h">Receipts, one after another.</h2>
        <p className="ln-count" aria-hidden="true">
          {count.toLocaleString("en-US")}
        </p>
        <p className="ln-cap">
          Receipts signed in this simulated feed. The page makes up the requests to show the rhythm; your real numbers come from
          your own approvals.
        </p>
      </div>
      <canvas ref={ref} className="ln-globe" aria-hidden="true" />
    </section>
  );
}
