// Marks use --chart-* tokens; every label is --text or --muted on the sheet (contrast is pinned in tests/chart-contrast.test.ts).

/** Horizontal bars, one hue. A zero is a bar with no length, never a hidden row: the absence is the data. */
export function BarList({ rows, label }: { rows: { label: string; value: number }[]; label: string }) {
  const max = Math.max(0, ...rows.map((r) => r.value));
  return (
    <ul aria-label={label} className="m-0 flex list-none flex-col gap-2 p-0">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(6rem,10rem)_1fr_3rem] items-center gap-3 max-sm:grid-cols-[minmax(5rem,8rem)_1fr_2rem]">
          <span title={r.label} className="min-w-0 wrap-anywhere">
            {r.label}
          </span>
          <span className="h-2 overflow-hidden rounded-pill bg-raised" aria-hidden="true">
            <span data-bar data-value={r.value} className="block h-full rounded-pill bg-chart-1" style={{ width: max ? `${(100 * r.value) / max}%` : "0%" }} />
          </span>
          <span className="text-right tabular-nums">{r.value}</span>
        </li>
      ))}
    </ul>
  );
}

const dayFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
export const dayLabel = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00Z`));

/** One column per point the API returned, nothing added between them. The chart is an image with a text summary and a table for assistive tech. */
export function Columns({ points, noun }: { points: { date: string; value: number }[]; noun: string }) {
  const peak = points.reduce((a, p) => (p.value > a.value ? p : a), points[0]);
  const summary = `${noun} per day, ${dayLabel(points[0].date)} to ${dayLabel(points[points.length - 1].date)}, peak ${peak.value} on ${dayLabel(peak.date)}`;
  const n = points.length;
  return (
    <figure className="m-0 flex flex-col gap-1">
      <svg viewBox={`0 0 ${n * 10} 100`} preserveAspectRatio="none" role="img" aria-label={summary} className="h-32 w-full">
        <line x1="0" x2={n * 10} y1="99.5" y2="99.5" className="stroke-control" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => {
          const h = peak.value ? (96 * p.value) / peak.value : 0;
          return <rect key={p.date} x={i * 10 + 1} y={98 - h} width="8" height={h} className="fill-chart-1" />;
        })}
      </svg>
      <figcaption className="flex justify-between text-small text-muted tabular-nums">
        <span>{dayLabel(points[0].date)}</span>
        <span>
          peak {peak.value} on {dayLabel(peak.date)}
        </span>
        <span>{dayLabel(points[n - 1].date)}</span>
      </figcaption>
      <table className="sr-only">
        <caption>{noun} per day</caption>
        <tbody>
          {points.map((p) => (
            <tr key={p.date}>
              <th scope="row">{dayLabel(p.date)}</th>
              <td>{p.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
