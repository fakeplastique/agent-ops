const CAP = 64 * 1024;

/** The agent's payload as data: scrollable monospace text, capped at 64 KB with the cut said out loud. */
export function PayloadBlock({ payload, label = "Payload" }: { payload: unknown; label?: string }) {
  const full = JSON.stringify(payload, null, 2) ?? "";
  const cut = full.length > CAP;
  return (
    <div className="flex flex-col gap-1.5">
      {/* tabIndex: a keyboard user must be able to scroll a long block. */}
      <pre
        tabIndex={0}
        aria-label={label}
        className="m-0 max-h-80 overflow-auto rounded-card bg-raised px-3.5 py-3 font-mono text-small leading-relaxed text-text"
      >
        {cut ? full.slice(0, CAP) : full}
      </pre>
      {cut && <p className="m-0 text-small text-muted">Truncated: showing the first 64 KB of {Math.round(full.length / 1024)} KB.</p>}
    </div>
  );
}
