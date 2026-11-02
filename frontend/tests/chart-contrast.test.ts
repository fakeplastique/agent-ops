// @vitest-environment node
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");
const token = (name: string) => {
  const m = css.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`${name} not found`);
  return m[1];
};

// WCAG 2.x relative luminance and contrast ratio.
const lum = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

describe("chart contrast (spec 8.2: marks 3:1, text 4.5:1)", () => {
  // Charts sit on the sheet; the bar track is --raised, so a mark must also stand out from its own track.
  const surfaces = ["--surface", "--raised"];
  it.each(["--chart-1", "--chart-2", "--chart-3"])("%s marks reach 3:1 on the sheet and on the track", (mark) => {
    for (const s of surfaces) expect(ratio(token(mark), token(s))).toBeGreaterThanOrEqual(3);
  });
  it.each(["--text", "--muted", "--negative", "--positive", "--warning"])("%s text reaches 4.5:1 on the sheet", (ink) => {
    expect(ratio(token(ink), token("--surface"))).toBeGreaterThanOrEqual(4.5);
  });
  it("chart labels stay in the text and muted inks on the sheet, never a mark colour", () => {
    const src = readFileSync(new URL("../src/components/charts/Charts.tsx", import.meta.url), "utf8");
    expect(src).not.toMatch(/text-chart-/);
  });
});
