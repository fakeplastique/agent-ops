import { test as base, expect, type Page } from "@playwright/test";

import { agentsFor } from "./real";

export { API, asUser, EMAILS, PEOPLE, sessionCookie, WEB } from "./real";

export const test = base.extend<{ agents: ReturnType<typeof agentsFor> }>({
  agents: async ({}, provide) => {
    const opened: string[] = [];
    const a = agentsFor(opened);
    await provide(a);
    // Open requests pile up in the shared inbox across runs; one that already ended refuses the withdraw, which is fine.
    await Promise.all(opened.map((id) => a.withdraw(id).catch(() => undefined)));
  },
});
export { expect };

/** A title no other test or run shares. */
export const unique = (label: string, info: { project: { name: string } }) => `${label} ${info.project.name} ${Math.random().toString(36).slice(2, 8)}`;

/** The login screen's "Sign in as" block: the real path a demo visitor takes. Lands on the inbox. */
export async function signInAs(page: Page, name: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(name) }).click();
  await expect(page).toHaveURL(/\/inbox$/);
}

/** The sidebar folds into a menu sheet at 375 px; its copy of the controls is the only one on screen. */
export async function openMenu(page: Page, narrow: boolean) {
  if (narrow) await page.getByRole("button", { name: "Menu" }).click();
}

export const noSidewaysScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
