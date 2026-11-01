import { expect, test } from "@playwright/test";

import { fakeApp, TITLE_A } from "./app-fake";

// SC27: the approver sees what waits, opens it, and decides from an always-visible bar; the API is faked at the browser edge.

test("the inbox lists the step, opens it with the rail, and approving sends exactly one decision", async ({ page }, info) => {
  const state = await fakeApp(page);
  await page.goto("/inbox");
  const row = page.getByRole("button", { name: new RegExp(TITLE_A) });
  await expect(row).toBeVisible();
  // The nav lives in the menu sheet at 375 px (not rendered until opened); the count is checked where the sidebar shows.
  if (info.project.name === "wide-1280") await expect(page.getByRole("link", { name: /Inbox, 1 waiting/ })).toBeVisible();
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

test("the decisions list opens a detail whose tabs are reachable by keyboard", async ({ page }, info) => {
  await fakeApp(page);
  await page.goto("/decisions");
  await expect(page.getByText("1-1 of 1")).toBeVisible();
  await page.screenshot({ path: `test-results/decisions-list-${info.project.name}.png`, fullPage: true });
  await page.getByRole("link", { name: TITLE_A }).click();
  await expect(page.getByRole("heading", { level: 1, name: TITLE_A })).toBeVisible();
  await page.screenshot({ path: `test-results/decision-detail-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Overview" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page).toHaveURL(/\?tab=votes$/);
  await expect(page.getByRole("tabpanel")).toContainText("rationale reject");
  await page.screenshot({ path: `test-results/decision-votes-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Discussion" }).click();
  await expect(page.getByText(/No messages yet/)).toBeVisible();
});

// Review A2: at 375 px the list and the pane never show together; a key press must not decide what nobody opened.
test("on the phone, A and R with the list showing decide nothing; after opening the request A decides it", async ({ page }, info) => {
  test.skip(info.project.name !== "narrow-375", "list and pane share the screen only below 1024 px");
  const state = await fakeApp(page);
  await page.goto("/inbox");
  const row = page.getByRole("button", { name: new RegExp(TITLE_A) });
  await row.focus();
  await page.keyboard.press("a");
  await page.keyboard.press("r");
  await expect(page.getByRole("button", { name: "Approve" })).toBeHidden(); // pane is not on screen
  expect(state.decideCalls).toBe(0);
  await row.click();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await page.getByRole("button", { name: /← Inbox/ }).focus();
  await page.keyboard.press("a");
  await expect(page.getByText(/You approved this/)).toBeVisible();
  expect(state.decideCalls).toBe(1);
});
