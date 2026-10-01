import { ESLint } from "eslint";

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

  it("rejects font-size: 13px in a string", async () => {
    const m = await lint("export const css = `font-size: 13px`;");
    expect(m.map((x) => x.ruleId)).toContain("no-restricted-syntax");
  });

  it("accepts token references", async () => {
    const m = await lint(`export const A = () => <p style={{ color: "var(--text)" }}>x</p>;`);
    expect(m).toEqual([]);
  });
});
