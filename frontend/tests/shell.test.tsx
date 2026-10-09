import { render, screen, within } from "@testing-library/react";
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
      <Shell org={<button type="button">Northwind</button>}>
        <button type="button">Behind the sheet</button>
      </Shell>,
    );
    const menu = screen.getByRole("button", { name: "Menu" });
    await user.click(menu);
    const sheet = screen.getByRole("dialog", { name: "Agent Ops" });
    expect(within(sheet).getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Northwind" })).toBeInTheDocument();
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

  it("narrow: choosing a link closes the sheet", async () => {
    const user = userEvent.setup();
    render(<Shell>x</Shell>);
    await user.click(screen.getByRole("button", { name: "Menu" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("link", { name: /Agents/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
