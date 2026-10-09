"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Icon paths from the approved prototype (docs/design-options/inbox-a.html), 16px grid, 1.5 stroke.
const ITEMS = [
  { href: "/inbox", label: "Inbox", icon: "M2.5 9.5l2-5h7l2 5v4h-11zM2.5 9.5h3.5l1 1.5h2l1-1.5h3.5" },
  { href: "/decisions", label: "Decisions", icon: "M3 4.5h10M3 8h10M3 11.5h6" },
  { href: "/agents", label: "Agents", icon: "M3 4.5h10v8H3zM8 4.5v-2M6 8.5h.01M10 8.5h.01" },
  { href: "/policies", label: "Policies", icon: "M8 2l5 2v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4z" },
  {
    href: "/teams",
    label: "Teams",
    icon: "M6 8a2 2 0 100-4 2 2 0 000 4zM2.5 13c0-2 1.5-3.5 3.5-3.5s3.5 1.5 3.5 3.5M11.5 8a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM11 9.6c1.7 0 2.9 1.2 2.9 2.9",
  },
  { href: "/webhooks", label: "Webhooks", icon: "M6.5 10a3 3 0 110-6H8M9.5 6a3 3 0 110 6H8M6 8h4" },
  { href: "/analytics", label: "Analytics", icon: "M3 13V8M7 13V4M11 13V9M2 13h12", plan: "Pro" },
  { href: "/plan", label: "Plan and usage", icon: "M2.5 4.5h11v7h-11zM2.5 7h11" },
  {
    href: "/settings",
    label: "Settings",
    icon: "M8 10a2 2 0 100-4 2 2 0 000 4zM8 2.5V4M8 12v1.5M2.5 8H4M12 8h1.5M4.1 4.1l1 1M10.9 10.9l1 1M11.9 4.1l-1 1M5.1 10.9l-1 1",
  },
];

export function Nav({ inboxCount = 0, onNavigate }: { inboxCount?: number; onNavigate?: () => void }) {
  const path = usePathname() ?? "";
  return (
    <nav aria-label="Main">
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
        {ITEMS.map((item) => {
          const current = path === item.href || path.startsWith(`${item.href}/`);
          const count = item.href === "/inbox" && inboxCount > 0 ? inboxCount : 0;
          return (
            <li key={item.href} className={item.href === "/inbox" ? "mb-3" : undefined}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={current ? "page" : undefined}
                aria-label={count ? `${item.label}, ${count} waiting` : undefined}
                className="flex h-control items-center gap-2.5 rounded-control px-2.5 text-muted no-underline transition-colors hover:bg-surface hover:text-text aria-[current=page]:bg-active aria-[current=page]:font-medium aria-[current=page]:text-text max-lg:h-touch"
              >
                <svg
                  viewBox="0 0 16 16"
                  aria-hidden="true"
                  className="size-4 flex-none fill-none stroke-current stroke-[1.5] [stroke-linecap:round] [stroke-linejoin:round]"
                >
                  <path d={item.icon} />
                </svg>
                <span>{item.label}</span>
                {item.plan && <span className="ml-auto text-small text-muted">{item.plan}</span>}
                {count > 0 && <span className="ml-auto text-small font-semibold text-text tabular-nums">{count}</span>}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
