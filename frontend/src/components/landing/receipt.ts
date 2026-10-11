// Demo of the backend receipt (backend/app/domain/receipt.py): HS256 over canonical({kid, payload}); valid only if
// the receipt is intact AND its payload equals the live request's payload. The key below is public on purpose.
export const DEMO_KEY = "agent-ops-public-demo-key-not-a-secret";
export const DEMO_KID = "demo-1";
const ORDER = 48213;
export const ORIGINAL_AMOUNT = "1240.00";

export type Receipt = {
  alg: "HS256";
  kid: string;
  payload: {
    request_id: string;
    version: number;
    content_hash: string;
    approved_at: string;
    approvers: string[];
  };
  signature: string;
};

// Sorted keys, no whitespace: the exact bytes the backend hashes and signs.
export function canonical(value: unknown): string {
  const sort = (v: unknown): unknown =>
    Array.isArray(v)
      ? v.map(sort)
      : v && typeof v === "object"
        ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : 1)).map(([k, x]) => [k, sort(x)]))
        : v;
  return JSON.stringify(sort(value));
}

const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const enc = (s: string) => new TextEncoder().encode(s);

async function sha256(text: string) {
  return hex(await crypto.subtle.digest("SHA-256", enc(text)));
}

async function mac(kid: string, payload: Receipt["payload"]) {
  const key = await crypto.subtle.importKey("raw", enc(DEMO_KEY), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", key, enc(canonical({ kid, payload }))));
}

// Same fields as the backend's content_hash: title, action type, payload and the declared risk.
export const contentHash = (amount: number) =>
  sha256(
    canonical({
      title: `Refund order #${ORDER}`, // built, not typed: the repo's hex-colour lint rule would read the order number as a colour
      action_type: "refund.issue",
      payload: { amount, currency: "USD" },
      risk_level: "high",
    }),
  );

const payloadFor = (content_hash: string): Receipt["payload"] => ({
  request_id: "3f6c2a1e-8b47-4d0a-9c15-72e0b6a1d948",
  version: 1,
  content_hash,
  approved_at: "2026-03-12T14:33:07.000000Z",
  approvers: ["7a1d4c90-2b3e-4f56-8a71-0c9d5e2f1b34", "c85e0f12-6d49-4a3b-b7c8-19e4a2d60f75"],
});

export async function issueReceipt(): Promise<Receipt> {
  const payload = payloadFor(await contentHash(Number(ORIGINAL_AMOUNT)));
  return { alg: "HS256", kid: DEMO_KID, payload, signature: await mac(DEMO_KID, payload) };
}

// Never throws: anything unparseable or unsigned is simply not valid.
export async function verifyReceipt(receipt: Receipt, liveAmount: string): Promise<boolean> {
  try {
    return await check(receipt, liveAmount);
  } catch {
    return false; // WebCrypto itself failed: nothing was proven
  }
}

async function check(receipt: Receipt, liveAmount: string): Promise<boolean> {
  const amount = liveAmount.trim() === "" ? NaN : Number(liveAmount);
  if (!Number.isFinite(amount) || receipt.kid !== DEMO_KID || receipt.alg !== "HS256") return false;
  if ((await mac(receipt.kid, receipt.payload)) !== receipt.signature) return false;
  return canonical(receipt.payload) === canonical(payloadFor(await contentHash(amount)));
}
