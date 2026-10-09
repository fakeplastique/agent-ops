import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { Shell } from "@/components/shell/Shell";

vi.mock("next/navigation", () => ({ usePathname: () => "/decisions/42" }));

const NAV = ["Inbox", "Decisions", "Agents", "Policies", "Teams", "Webhooks", "Analytics", "Plan and usage", "Settings"];

describe("Shell", () => {
  it("has the main nav in spec order, the current section marked, and a main landmark", () => {
    render(<Shell>body</Shell>);
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(NAV.map((n) => expect.stringContaining(n)));
    expect(within(nav).getByRole("link", { current: "page" })).toHaveTextContent("Decisions");
    expect(screen.getByRole("main")).toHaveTextContent("body");
  });

  it("shows the inbox count only when something waits", () => {
    const { rerender } = render(<Shell inboxCount={0}>x</Shell>);
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    rerender(<Shell inboxCount={3}>x</Shell>);
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    expect(within(nav).getByRole("link", { name: /Inbox, 3 waiting/ })).toBeInTheDocument();
  });

  // At 375 px the sidebar is hidden; the menu sheet is the only way to the other screens.
  it("narrow: the menu opens a sheet with the nav, traps focus, and Escape returns focus to Menu", async () => {
    const user = userEvent.setup();
    render(
      <Shell org={<button type="button">Northwind</button>} user={<span>Maya Okafor</span>}>
        <button type="button">Behind the sheet</button>
      </Shell>,
    );
    const menu = screen.getByRole("button", { name: "Menu" });
    await user.click(menu);
    const sheet = screen.getByRole("dialog", { name: "Menu" });
    expect(within(sheet).getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Northwind" })).toBeInTheDocument();
    expect(within(sheet).getByText("Maya Okafor")).toBeInTheDocument();
    // Modal: the page behind is hidden from assistive tech while the sheet is open.
    expect(screen.queryByRole("button", { name: "Behind the sheet" })).not.toBeInTheDocument();

    for (let i = 0; i < 15; i++) {
      await user.tab();
      expect(sheet).toContainElement(document.activeElement as HTMLElement);
    }

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(menu).toHaveFocus();
  });

  it("the first Tab reaches a skip link to the main content", async () => {
    render(<Shell>x</Shell>);
    await userEvent.tab();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
  });

  it("the sidebar has the user row (spec 8)", () => {
    render(<Shell user={<span>Maya Okafor</span>}>x</Shell>);
    expect(within(screen.getByRole("complementary", { name: "Sidebar" })).getByText("Maya Okafor")).toBeInTheDocument();
  });

  // Rotating a tablet past lg hides the sheet's content; a modal left open would keep the page inert.
  it("closes the sheet when the viewport reaches the sidebar breakpoint", async () => {
    const listeners = new Set<(e: { matches: boolean }) => void>();
    vi.stubGlobal("matchMedia", (query: string) => ({
      media: query,
      matches: false,
      addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => listeners.add(fn),
      removeEventListener: (_: string, fn: (e: { matches: boolean }) => void) => listeners.delete(fn),
    }));
    render(
      <Shell>
        <button type="button">Behind the sheet</button>
      </Shell>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Menu" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    act(() => listeners.forEach((fn) => fn({ matches: true })));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Behind the sheet" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("narrow: choosing a link closes the sheet", async () => {
    const user = userEvent.setup();
    render(<Shell>x</Shell>);
    await user.click(screen.getByRole("button", { name: "Menu" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("link", { name: /Agents/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
