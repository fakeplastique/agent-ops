"use client";

import { useEffect } from "react";

const LEAVE = "You have unsaved changes. Leave without saving?";

/** Native confirm for in-screen exits (Cancel, opening another editor). */
export const confirmDiscard = () => window.confirm(LEAVE);

/** While dirty: closing the tab asks, and so does a click on a link that leaves this page. */
export function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = ""; // some browsers only ask when this is set
    };
    // Capture phase: runs before the router's own link handler, which a declined confirm must stop.
    const onClick = (e: MouseEvent) => {
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank") return;
      const to = new URL(a.href, window.location.href);
      const here = window.location;
      if (to.origin === here.origin && to.pathname === here.pathname && to.search === here.search) return; // same page, hash only
      if (!confirmDiscard()) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [dirty]);
}
