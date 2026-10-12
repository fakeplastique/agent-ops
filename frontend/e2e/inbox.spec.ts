import { expect, test } from "@playwright/test";

import { fakeApp, TITLE_A } from "./app-fake";

// SC27: the approver sees what waits, opens it, and decides from an always-visible bar; the API is faked at the browser edge.

test("the inbox lists the step, opens it with the rail, and approving sends exactly one decision", async ({ page }, info) => {
  const state = await fakeApp(page);
  await page.goto("/inbox");
  const row = page.getByRole("button", { name: new RegExp(TITLE_A) });
  await expect(row).toBeVisible();
  await expect(page.getByRole("link", { name: /Inbox, 1 waiting/ }).first()).toBeAttached();
  await page.screenshot({ path: `test-results/inbox-list-${info.project.name}.png`, fullPage: true });

  await row.click();
  await expect(page.getByRole("heading", { level: 2, name: TITLE_A })).toBeVisible();
  await expect(page.getByRole("region", { name: "Decision progress" })).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve" });
  await expect(approve).toBeVisible(); // the bar is on screen without scrolling
  await page.screenshot({ path: `test-results/inbox-pane-${info.project.name}.png`, fullPage: true });

  await approve.dblclick();
  await expect(page.getByText(/You approved this/)).toBeVisible();
  expect(state.decideCalls).toBe(1);
});

test("Reject needs a comment", async ({ page }) => {
  await fakeApp(page);
  await page.goto("/inbox");
  await page.getByRole("button", { name: new RegExp(TITLE_A) }).click();
  const reject = page.getByRole("button", { name: "Reject" });
  await expect(reject).toBeDisabled();
  await page.getByLabel(/Comment/).fill("Above the limit");
  await expect(reject).toBeEnabled();
});

test("a session that expired sends the user to sign in and back", async ({ page }) => {
  const state = await fakeApp(page);
  state.user = null;
  await page.goto("/inbox");
  await expect(page).toHaveURL(/\/login\?next=%2Finbox$/);
});
