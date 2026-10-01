import { ESLint } from "eslint";
import stylelint from "stylelint";

const eslint = new ESLint();
const lint = async (code: string) =>
  (await eslint.lintText(code, { filePath: "src/components/Probe.tsx" }))[0].messages;

describe("token lint rule", () => {
  // Literal colours and sizes in a component bypass DESIGN.md tokens.
  it("rejects a hex colour", async () => {
    const m = await lint(`export const A = () => <p style={{ color: "#fff" }}>x</p>;`);
    expect(m.map((x) => x.ruleId)).toContain("no-restricted-syntax");
  });

  it("rejects a px font size", async () => {
    const m = await lint(`export const A = () => <p style={{ fontSize: "13px" }}>x</p>;`);
    expect(m.map((x) => x.ruleId)).toContain("no-restricted-syntax");
  });

  it("rejects a numeric fontSize", async () => {
    const m = await lint(`export const A = () => <p style={{ fontSize: 13 }}>x</p>;`);
    expect(m.map((x) => x.ruleId)).toContain("no-restricted-syntax");
  });

  it("rejects font-size: 13px in a string", async () => {
    const m = await lint("export const css = `font-size: 13px`;");
    expect(m.map((x) => x.ruleId)).toContain("no-restricted-syntax");
  });

  it("accepts token references", async () => {
    const m = await lint(`export const A = () => <p style={{ color: "var(--text)" }}>x</p>;`);
    expect(m).toEqual([]);
  });
});

describe("css token rule", () => {
  const css = async (code: string, codeFilename = "src/styles/globals.css") =>
    (await stylelint.lint({ code, codeFilename })).results[0].warnings;

  // CSS is where most literals live; the eslint rule never sees it.
  it("rejects a px size and a hex colour", async () => {
    expect(await css(".a { width: 480px; }")).toHaveLength(1);
    expect(await css(".a { color: #fff; }")).toHaveLength(1);
  });

  it("allows 1px hairlines, tokens and unitless values", async () => {
    expect(await css(".a { border: 1px solid var(--border); padding: var(--space-2); opacity: 0.6; }")).toEqual([]);
  });

  it("lets tokens.css declare the literals", async () => {
    expect(await css(":root { --measure: 480px; }", "src/styles/tokens.css")).toEqual([]);
  });
});
