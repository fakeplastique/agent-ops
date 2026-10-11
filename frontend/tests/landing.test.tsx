import { createHmac, createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { vi } from "vitest";

import Page from "@/app/page";
import { ReceiptDemo } from "@/components/landing/ReceiptDemo";
import { DEMO_KEY, canonical, issueReceipt, verifyReceipt } from "@/components/landing/receipt";

const SRC = join(__dirname, "../src");
const landingFiles = () => [
  join(SRC, "app/page.tsx"),
  ...readdirSync(join(SRC, "components/landing"), { recursive: true, withFileTypes: true })
    .filter((f) => f.isFile() && /\.(tsx?|css)$/.test(f.name))
    .map((f) => join(f.parentPath, f.name)),
];

describe("Landing", () => {
  // Anything rendered only after hydration is invisible without scripts.
  it("all sections and the credits are in the server-rendered HTML", async () => {
    const html = renderToString(await Page());
    const headings = [...html.matchAll(/<h[12][^>]*>(.*?)<\/h[12]>/g)].map((m) => m[1].replace(/<[^>]+>/g, ""));
    for (const h of [
      "Agents ask.",
      "Receipts, one after another.",
      "Votes, levels, receipt.",
      "Signed.",
      "Rules, not promises.",
      "Free",
      "Pro",
      "Fair questions.",
      "Let agents ask first.",
    ]) {
      expect(headings.join(" | ")).toContain(h);
    }
    expect(html).toContain("Do you run my agents?");
    expect(html).toContain("Refund order #48213");
    for (const credit of ["Cleveland Museum of Art", "Wellcome Collection", "Noun Project", "Natural Earth"]) {
      expect(html).toContain(credit);
    }
    // The receipt is signed on the server, so the signature is readable without scripts.
    expect(html).toMatch(/[0-9a-f]{64}/);
    expect(html.match(/<h1/g)).toHaveLength(1);
  });

  it("uses the four credited pictures that exist on disk", async () => {
    const html = renderToString(await Page());
    const srcs = [...html.matchAll(/<img[^>]+src="(\/landing\/[^"]+)"/g)].map((m) => m[1]);
    expect(srcs).toHaveLength(4);
    for (const s of srcs) expect(() => readFileSync(join(SRC, "../public", s))).not.toThrow();
  });

  // The page shares DESIGN.md's single sans face and its colour tokens.
  it("no monospace on this page and no literal colours", () => {
    for (const file of landingFiles()) {
      const code = readFileSync(file, "utf8");
      expect(code, file).not.toMatch(/monospace|font-mono|Plex Mono/i);
      // Prose like "order #48213" is not a colour: in TS only a whole quoted hex counts.
      const hex = file.endsWith(".css") ? /#[0-9a-fA-F]{3,8}\b/ : /["'`]#[0-9a-fA-F]{3,8}["'`]/;
      expect(code, file).not.toMatch(hex);
      expect(code, file).not.toMatch(/\brgba?\(|\bhsla?\(/);
    }
  });
});

describe("Receipt", () => {
  // Same bytes the backend signs: HMAC-SHA256 over canonical({kid, payload}), sorted keys, no whitespace.
  it("signs with the backend's receipt format and verifies only the unchanged request", async () => {
    const r = await issueReceipt();
    const expected = createHmac("sha256", DEMO_KEY)
      .update(canonical({ kid: r.kid, payload: r.payload }))
      .digest("hex");
    expect(r.signature).toBe(expected);
    expect(r.alg).toBe("HS256");
    expect(Object.keys(r).sort()).toEqual(["alg", "kid", "payload", "signature"]);
    expect(r.payload.content_hash).toBe(
      createHash("sha256")
        .update(
          canonical({
            title: "Refund order #48213",
            action_type: "refund.issue",
            payload: { amount: 1240, currency: "USD" },
            risk_level: "high",
          }),
        )
        .digest("hex"),
    );
    expect(await verifyReceipt(r, "1240.00")).toBe(true);
    expect(await verifyReceipt(r, "1240.01")).toBe(false);
    expect(await verifyReceipt(r, "")).toBe(false);
    expect(await verifyReceipt({ ...r, signature: "0".repeat(64) }, "1240")).toBe(false);
    expect(await verifyReceipt({ ...r, kid: "other" }, "1240")).toBe(false);
  });

  it("editing the amount flips 'Signed' to 'Broken' using WebCrypto HMAC with the labelled demo key", async () => {
    const user = userEvent.setup();
    const sign = vi.spyOn(crypto.subtle, "sign");
    render(<ReceiptDemo receipt={await issueReceipt()} />);
    expect(screen.getByRole("heading", { name: "Signed." })).toBeInTheDocument();
    expect(screen.getByText(/demo key/i)).toBeInTheDocument();
    const amount = await screen.findByLabelText("Amount (USD)");
    await waitFor(() => expect(amount).toBeEnabled());

    await user.clear(amount);
    await user.type(amount, "12400.00");
    expect(await screen.findByRole("heading", { name: "Broken." })).toBeInTheDocument();
    expect(sign).toHaveBeenCalledWith("HMAC", expect.anything(), expect.anything());
    expect(screen.getByRole("status")).toHaveTextContent(/not valid/i);

    await user.click(screen.getByRole("button", { name: /Restore 1,240\.00/ }));
    expect(await screen.findByRole("heading", { name: "Signed." })).toBeInTheDocument();
    expect(amount).toHaveValue(1240);
  });

  // An insecure context has no crypto.subtle; the page must still say something true and stay usable.
  it("without crypto.subtle shows static 'Signed.' and a locked amount", async () => {
    const receipt = await issueReceipt();
    vi.stubGlobal("crypto", {});
    render(<ReceiptDemo receipt={receipt} />);
    expect(screen.getByRole("heading", { name: "Signed." })).toBeInTheDocument();
    expect(screen.getByLabelText("Amount (USD)")).toBeDisabled();
    expect(screen.getByText(/secure connection/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
