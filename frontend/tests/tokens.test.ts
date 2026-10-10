// @vitest-environment node
import { readFileSync } from "node:fs";

import { compile } from "tailwindcss";

// Mirrors DESIGN.md (kept at the repo root, untracked): [css variable, value, a Tailwind utility that must resolve to it].
const TOKENS: [string, string, string][] = [
  ["--ground", "#eef2f5", "bg-ground"],
  ["--surface", "#ffffff", "bg-surface"],
  ["--raised", "#f4f6f8", "bg-raised"],
  ["--active", "#e6eaed", "bg-active"],
  ["--border", "#e4e7ea", "border-border"],
  ["--rule", "#eff1f3", "border-rule"],
  ["--control", "#c9cdd1", "border-control"],
  ["--text", "#131416", "text-text"],
  ["--muted", "#5b5e62", "text-muted"],
  ["--accent", "#1b6c80", "text-accent"],
  ["--positive", "#1e7b4f", "text-positive"],
  ["--negative", "#c4242a", "text-negative"],
  ["--warning", "#92400e", "text-warning"],
  ["--chart-1", "#1b6c80", "bg-chart-1"],
  ["--chart-2", "#2f70bb", "bg-chart-2"],
  ["--chart-3", "#a86f0c", "bg-chart-3"],
  ["--pill-positive-bg", "#dcfae5", "bg-pill-positive-bg"],
  ["--pill-positive-ink", "#04250e", "text-pill-positive-ink"],
  ["--pill-negative-bg", "#ffedea", "bg-pill-negative-bg"],
  ["--pill-negative-ink", "#831c1e", "text-pill-negative-ink"],
  ["--pill-warning-bg", "#fffbeb", "bg-pill-warning-bg"],
  ["--pill-warning-ink", "#92400e", "text-pill-warning-ink"],
  ["--pill-neutral-bg", "#e6eaed", "bg-pill-neutral-bg"],
  ["--pill-neutral-ink", "#353736", "text-pill-neutral-ink"],
  ["--text-title", "22px", "text-title"],
  ["--text-section", "16px", "text-section"],
  ["--text-kpi", "28px", "text-kpi"],
  ["--text-body", "14px", "text-body"],
  ["--text-small", "12px", "text-small"],
  ["--spacing", "4px", "p-6"],
  ["--spacing-row", "48px", "min-h-row"],
  ["--spacing-control", "32px", "h-control"],
  ["--spacing-sidebar", "228px", "w-sidebar"],
  ["--spacing-topbar", "48px", "h-topbar"],
  ["--spacing-inset", "8px", "p-inset"],
  ["--radius-sheet", "16px", "rounded-sheet"],
  ["--radius-card", "8px", "rounded-card"],
  ["--radius-control", "6px", "rounded-control"],
  ["--radius-pill", "9999px", "rounded-pill"],
  ["--shadow-float", "0 8px 24px rgb(19 20 22 / 0.12)", "shadow-float"],
];

const css = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");
// The real build loads Tailwind's default theme first; tokens.css must override it.
const defaults = readFileSync(new URL("../node_modules/tailwindcss/theme.css", import.meta.url), "utf8");

async function utilities(candidates: string[]) {
  const compiler = await compile(`${defaults}\n${css}\n@tailwind utilities;`);
  return compiler.build(candidates);
}

describe("DESIGN.md tokens", () => {
  it.each(TOKENS)("%s is declared once in tokens.css as %s", (name, value) => {
    const decls = [...css.matchAll(new RegExp(`^\\s*${name}:\\s*([^;]+);`, "gm"))].map((m) => m[1].trim());
    expect(decls).toEqual([value]);
  });

  // A utility that compiles to nothing means the doc and the theme drifted apart.
  it.each(TOKENS.map(([n, , u]) => [n, u]))("%s is reachable as the utility %s", async (_name, utility) => {
    const out = await utilities([utility]);
    expect(out).toContain(`.${utility}`);
  });

  // Tailwind's own palette would let a component bypass the tokens with bg-white or text-red-500.
  it("exposes no default palette, sizes or radii", async () => {
    const out = await utilities(["bg-white", "text-red-500", "text-lg", "rounded-xl", "shadow-md", "font-serif"]);
    expect(out).not.toMatch(/\.(bg-white|text-red-500|text-lg|rounded-xl|shadow-md|font-serif)\b/);
  });

  it("keeps the three loaded Plex weights", async () => {
    const out = await utilities(["font-normal", "font-medium", "font-semibold", "font-bold"]);
    expect(out).toMatch(/\.font-medium/);
    expect(out).toMatch(/\.font-semibold/);
    expect(out).not.toMatch(/\.font-bold/);
  });

  it("has Plex Sans for text and Plex Mono for ids only", async () => {
    const out = await utilities(["font-sans", "font-mono"]);
    expect(out).toMatch(/IBM Plex Sans/);
    expect(out).toMatch(/IBM Plex Mono/);
  });
});
