"use client";

import { useEffect, useRef } from "react";

import { channels, colorOf, mix, prefersReducedMotion } from "../motion";
import { startSilk } from "./silk";

// Fallbacks only matter where the browser cannot resolve a colour; they are teal-ish channel values.
const DEEP = [0.06, 0.29, 0.35];
const INK = [0.07, 0.08, 0.08];
const TEAL = [0.11, 0.42, 0.5];
const PALE = [0.84, 0.92, 0.94];

// Draws the silk shader into the hero orb; with no WebGL the orb behind it stays plain teal.
export function Orb() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const ch = (expr: string, fb: number[]) => channels(colorOf(cv, expr, ""), fb);
    const accent = ch("var(--accent)", TEAL);
    // The ramp follows the stage bar: folds lean to ink, highlights stop short of the page ground so the orb keeps its edge.
    const ramp = [mix(ch("var(--ln-deep)", DEEP), ch("var(--text)", INK), 0.3), accent, mix(ch("var(--ln-pale)", PALE), accent, 0.3)] as [
      number[],
      number[],
      number[],
    ];
    const stop = startSilk(cv, cv.closest("section") ?? cv, { still: prefersReducedMotion(), ramp });
    if (stop) cv.dataset.silk = "on";
    return () => {
      stop?.();
      delete cv.dataset.silk;
    };
  }, []);
  return <canvas ref={ref} className="ln-orb-canvas" />;
}
