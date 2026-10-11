"use client";

import { useRef, useState, useSyncExternalStore } from "react";

import { ORIGINAL_AMOUNT, type Receipt, contentHash, verifyReceipt } from "./receipt";

const noop = () => () => {};
const short = (h: string) => `${h.slice(0, 16)}…${h.slice(-4)}`;

// Server markup is the signed, static state; it becomes live only where WebCrypto exists (secure contexts).
export function ReceiptDemo({ receipt }: { receipt: Receipt }) {
  const live = useSyncExternalStore(noop, () => Boolean(globalThis.crypto?.subtle), () => false);
  const [amount, setAmount] = useState(ORIGINAL_AMOUNT);
  const [check, setCheck] = useState({ ok: true, hash: receipt.payload.content_hash });
  const latest = useRef(0);
  const input = useRef<HTMLInputElement>(null);

  const edit = (value: string) => {
    setAmount(value);
    const id = ++latest.current;
    const n = value.trim() === "" ? NaN : Number(value);
    Promise.all([verifyReceipt(receipt, value), Number.isFinite(n) ? contentHash(n) : Promise.resolve("")]).then(
      ([ok, hash]) => {
        if (id === latest.current) setCheck({ ok, hash });
      },
    );
  };

  const { ok, hash } = check;
  return (
    <section className="ln-show" id="receipt" aria-labelledby="show-h" data-broken={!ok}>
      <div className="ln-in ln-show-grid">
        <div>
          <h2 id="show-h">{ok ? "Signed." : "Broken."}</h2>
          <p className="ln-sentence">
            {ok
              ? "Edit the amount on the receipt. The check stops passing the moment a number changes."
              : "One changed number and the receipt no longer proves the approval. Your system would refuse to act."}
          </p>
        </div>
        <div className="ln-slip ln-receipt" data-ui>
          <div className="ln-slip-t">Approval receipt</div>
          <div className="ln-slip-m">
            <span className="ln-id">kid {receipt.kid}</span> · {receipt.alg} (HMAC-SHA256)
          </div>
          <div className="ln-field">
            <span className="ln-k">Request</span>
            <span className="ln-v">Refund order #48213</span>
          </div>
          <div className="ln-field">
            <label htmlFor="amt">Amount (USD)</label>
            <input
              id="amt"
              ref={input}
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              autoComplete="off"
              spellCheck={false}
              value={amount}
              disabled={!live}
              aria-describedby="verdict"
              onChange={(e) => edit(e.target.value)}
            />
          </div>
          <div className="ln-field">
            <span className="ln-k">Version</span>
            <span className="ln-v">1</span>
          </div>
          <div className="ln-field">
            <span className="ln-k">Approved</span>
            <span className="ln-v">14:33 UTC by 2 people</span>
          </div>
          <div className="ln-field">
            <span className="ln-k">Hash in receipt</span>
            <span className="ln-v ln-sig">{short(receipt.payload.content_hash)}</span>
          </div>
          <div className="ln-field">
            <span className="ln-k">Hash of the request now</span>
            <span className="ln-v ln-sig" data-changed={hash !== receipt.payload.content_hash}>
              {hash ? short(hash) : "no valid amount"}
            </span>
          </div>
          <div className="ln-field">
            <span className="ln-k">Signature</span>
            <span className="ln-v ln-sig">{receipt.signature}</span>
          </div>
          <div className="ln-verdict" id="verdict" role="status" data-bad={!ok}>
            <span>{ok ? "Verification: valid" : "Verification: not valid. The request changed after approval."}</span>
            {!ok && (
              <button className="ln-reset" type="button" onClick={() => {
                  edit(ORIGINAL_AMOUNT);
                  input.current?.focus(); // the button unmounts; keep keyboard focus
                }}>
                Restore 1,240.00
              </button>
            )}
          </div>
          <p className="ln-small">
            Checked in your browser with a public demo key, using the same rule as the API: the signature must match and
            the receipt must still describe the live request. Real receipts are signed with your server key and checked
            through the API.
          </p>
          {!live && (
            <p className="ln-small">
              Editing needs a script and a secure connection (https), so this shows the receipt as signed.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
