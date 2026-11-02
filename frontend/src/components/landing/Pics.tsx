// Decorative compositions for the circular masks: the rail node at picture scale. Meaning lives in the text beside them.

// Three requests converge on one signed node (the brand mark, enlarged).
export function Converge({ className }: { className?: string }) {
  return (
    <svg className={className} data-pic="converge" viewBox="0 0 200 200" aria-hidden="true" focusable="false">
      <rect className="ln-p-bg" width="200" height="200" />
      <circle className="ln-p-halo" cx="132" cy="100" r="76" />
      <circle className="ln-p-halo" cx="132" cy="100" r="54" />
      <path className="ln-p-line" d="M42 44L132 100M42 100H132M42 156L132 100" />
      <g className="ln-p-on">
        <circle cx="42" cy="44" r="9" />
        <circle cx="42" cy="100" r="9" />
        <circle cx="42" cy="156" r="9" />
        <circle cx="132" cy="100" r="30" />
      </g>
    </svg>
  );
}

// The decision rail drawn large: two done levels, the current ring, the unsigned receipt.
export function Rail({ className }: { className?: string }) {
  return (
    <svg className={className} data-pic="rail" viewBox="0 0 360 360" aria-hidden="true" focusable="false">
      <rect className="ln-r-bg" width="360" height="360" />
      <g className="ln-r-line">
        <rect x="56" y="172" width="188" height="16" />
        <rect x="244" y="172" width="36" height="16" />
        <rect x="280" y="172" width="58" height="16" opacity="0.3" />
      </g>
      <g className="ln-r-on">
        <circle cx="56" cy="180" r="40" />
        <circle cx="150" cy="180" r="40" />
      </g>
      <circle className="ln-r-ring" cx="244" cy="180" r="34" />
      <circle className="ln-r-dash" cx="338" cy="180" r="36" />
    </svg>
  );
}
