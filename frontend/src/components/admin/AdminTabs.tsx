import Link from "next/link";

/** Teams and Members are one spec screen on two routes; this is the switch between them. */
export function AdminTabs({ current }: { current: "teams" | "members" }) {
  return (
    <nav aria-label="Teams and members" className="flex gap-1">
      {(["teams", "members"] as const).map((t) => (
        <Link
          key={t}
          href={`/${t}`}
          aria-current={t === current ? "page" : undefined}
          className="inline-flex h-control items-center rounded-control px-3 text-muted no-underline hover:bg-raised hover:text-text aria-[current=page]:bg-active aria-[current=page]:font-medium aria-[current=page]:text-text max-lg:h-touch coarse:h-touch"
        >
          {t === "teams" ? "Teams" : "Members and invitations"}
        </Link>
      ))}
    </nav>
  );
}
