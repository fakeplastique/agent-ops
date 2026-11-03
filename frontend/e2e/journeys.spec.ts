import { API, asUser, EMAILS, expect, PEOPLE, openMenu, sessionCookie, signInAs, test, unique, WEB } from "./fixtures";

// SC27, SC28 and I22 against the REAL backend (demo mode): nothing is faked at the browser edge. Seeded agents act over
// REST, seeded people sign in through the login screen, and the browser reaches the API through the web origin's rewrite.

type Page = Parameters<typeof signInAs>[0];

test("an agent asks, reviewers vote blind, the manager approves from the inbox and the receipt verifies", async ({ page, agents }, info) => {
  const req = await agents.create({ title: unique("Refund order", info), description: "Customer reports a damaged parcel" });
  await agents.vote(req, 0);
  await agents.vote(req, 1);

  await signInAs(page, PEOPLE.manager);
  await page.goto(`/decisions/${req.id}?tab=votes`);
  // Blind round: how many voted, never what. The rationale an agent wrote must not be anywhere in the page.
  await expect(page.getByText("Reviewer 1 has voted. Vote hidden until round closes")).toBeVisible();
  await expect(page.getByText("Reviewer 3 has not voted yet")).toBeVisible();
  expect(await page.content()).not.toContain("per policy");

  await agents.vote(req, 2);
  await page.goto("/inbox");
  await page.getByRole("button", { name: new RegExp(req.title) }).click();
  await expect(page.getByRole("heading", { level: 2, name: req.title })).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/You approved this/)).toBeVisible();

  await page.goto(`/decisions/${req.id}?tab=votes`);
  await expect(page.getByText(/per policy/).first()).toBeVisible(); // the round closed: the reasons are revealed
  await page.goto(`/decisions/${req.id}?tab=receipt`);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText(/^Valid\. It matches/)).toBeVisible();
  await page.screenshot({ path: `test-results/journey-receipt-${info.project.name}.png`, fullPage: true });
});

test("Reject stays disabled until a comment is written, then rejects", async ({ page, agents }, info) => {
  const req = await agents.create({ title: unique("Refund order", info) });
  await agents.voteAll(req);

  await signInAs(page, PEOPLE.manager);
  await page.getByRole("button", { name: new RegExp(req.title) }).click();
  const reject = page.getByRole("button", { name: "Reject" });
  await expect(reject).toBeDisabled();
  await page.getByLabel(/Comment/).fill("   ");
  await expect(reject).toBeDisabled(); // blank is not a reason
  await page.getByLabel(/Comment/).fill("Above the refund limit for this customer");
  await expect(reject).toBeEnabled();
  await reject.click();
  await expect(page.getByText(/You rejected this/)).toBeVisible();

  await page.goto(`/decisions/${req.id}`);
  await expect(page.getByText("Rejected", { exact: true }).first()).toBeVisible();
});

// S29/S30 in a real browser: a second approver decides, the first inbox follows by itself.
test("a second browser context decides and the first inbox updates without a reload", async ({ page, browser, agents }, info) => {
  await signInAs(page, PEOPLE.manager);
  await page.evaluate(() => ((window as unknown as { __loaded: boolean }).__loaded = true)); // gone after any reload
  const reviewer = await browser.newContext({ baseURL: WEB, viewport: page.viewportSize() });
  try {
    const other = await reviewer.newPage();
    await signInAs(other, PEOPLE.lead);

    const req = await agents.create({ title: unique("Refund order", info) });
    await agents.voteAll(req);
    const row = (p: Page) => p.getByRole("button", { name: new RegExp(req.title) });
    // The step arrives in both inboxes by itself.
    await expect(row(page)).toBeVisible({ timeout: 30_000 });
    await expect(row(other)).toBeVisible({ timeout: 30_000 });

    await row(page).click();
    await row(other).click();
    await other.getByLabel(/Comment/).fill("Rejected by the team lead");
    await other.getByRole("button", { name: "Reject" }).click();
    await expect(other.getByText(/You rejected this/)).toBeVisible();

    await expect(page.getByText(/no longer waiting|already decided/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("list", { name: "Steps waiting on me" }).getByText(req.title)).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __loaded?: boolean }).__loaded)).toBe(true);
    await page.screenshot({ path: `test-results/journey-realtime-${info.project.name}.png`, fullPage: true });
  } finally {
    await reviewer.close();
  }
});

// Spec 8.1 Offline: losing the network pauses live updates visibly, and the banner clears when it returns.
test("going offline shows the paused banner and coming back clears it", async ({ page, context }, info) => {
  await signInAs(page, PEOPLE.manager);
  await expect(page.getByRole("heading", { level: 1, name: "Inbox" })).toBeVisible();
  await context.setOffline(true);
  await expect(page.getByText("Live updates paused, reconnecting")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: `test-results/state-offline-${info.project.name}.png`, fullPage: true });
  await context.setOffline(false);
  await expect(page.getByText("Live updates paused, reconnecting")).toBeHidden({ timeout: 40_000 });
});

// I22 / FM31: nothing of the previous identity or organization survives in the DOM, memory or storage.
async function leaveTraces(page: Page, who: string) {
  await page.evaluate((w) => {
    localStorage.setItem("draft", `note by ${w}`);
    sessionStorage.setItem("selected", `selected by ${w}`);
    (window as unknown as { __cache: string }).__cache = `cache of ${w}`;
  }, who);
}
const traces = (page: Page) =>
  page.evaluate(() => ({
    local: { ...localStorage },
    session: { ...sessionStorage },
    cache: (window as unknown as { __cache?: string }).__cache ?? null,
    text: document.body.innerText,
  }));

test("signing out and in as someone from another organization leaves nothing of the first user", async ({ page, agents }, info) => {
  const req = await agents.create({ title: unique("Refund order", info) });
  await agents.voteAll(req);
  await signInAs(page, PEOPLE.manager);
  await page.getByRole("button", { name: new RegExp(req.title) }).click();
  await page.getByLabel(/Comment/).fill("draft typed by the Fernwood manager");
  await leaveTraces(page, "fernwood");

  await openMenu(page, info.project.name === "narrow-375");
  await page.getByRole("button", { name: "Sign out" }).first().click();
  await expect(page).toHaveURL(/\/login$/);
  expect(await traces(page)).toMatchObject({ local: {}, session: {}, cache: null });

  await page.getByRole("button", { name: new RegExp(PEOPLE.birchManager) }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("heading", { level: 1, name: "Inbox" })).toBeVisible();
  const after = await traces(page);
  expect(after).toMatchObject({ local: {}, session: {}, cache: null });
  expect(after.text).not.toContain(req.title);
  expect(after.text).not.toContain("draft typed");
  expect(await page.content()).not.toContain(req.title);
  expect(await page.getByLabel(/Comment/).count()).toBe(0);
});

test("switching organization reloads into the other one with the open request and draft gone", async ({ page, agents }, info) => {
  const req = await agents.create({ title: unique("Refund order", info) });
  await agents.voteAll(req);
  await signInAs(page, PEOPLE.admin);
  // The demo roster has one organization per person: give this admin a second one, which the API also selects.
  const slug = `e2e-${Math.random().toString(36).slice(2, 10)}`;
  const made = await page.request.post("/api/v1/organizations", { headers: { Origin: WEB }, data: { name: `E2E ${slug}`, slug } });
  expect(made.status()).toBe(201);
  await page.goto("/inbox");
  await expect(page.getByText("Nothing is waiting on you", { exact: true })).toBeVisible(); // the new, empty organization
  await leaveTraces(page, "the new organization");

  await openMenu(page, info.project.name === "narrow-375");
  const reloaded = page.waitForEvent("load");
  await page.getByRole("combobox", { name: "Organization" }).selectOption({ label: "Fernwood Retail" });
  await reloaded;
  await expect(page.getByRole("button", { name: new RegExp(req.title) })).toBeVisible();
  expect(await traces(page)).toMatchObject({ local: {}, session: {}, cache: null });
});

// Spec 8.1: Forbidden, Not found (a missing id and another tenant's id read the same) and Plan-gated.
test("a member opening an admin screen sees the 403 screen with the role it needs", async ({ page }, info) => {
  await signInAs(page, PEOPLE.member);
  await page.goto("/members");
  await expect(page.getByRole("heading", { name: "You do not have access to this" })).toBeVisible();
  await expect(page.getByText(/org admin role/)).toBeVisible();
  await page.screenshot({ path: `test-results/state-403-${info.project.name}.png`, fullPage: true });
});

test("a missing request and another organization's request both say Not found", async ({ page }, info) => {
  const birch = await sessionCookie(EMAILS.birchAdmin);
  const theirs = (await asUser<{ items: { id: string }[] }>(birch, "/decision-requests?limit=1")).items[0].id;
  await signInAs(page, PEOPLE.manager);

  await page.goto("/decisions/00000000-0000-4000-8000-000000000000");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  const missing = await page.getByRole("main").innerText();
  await page.goto(`/decisions/${theirs}`);
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  expect(await page.getByRole("main").innerText()).toBe(missing); // same words: the answer must not tell them apart
  await page.screenshot({ path: `test-results/state-404-${info.project.name}.png`, fullPage: true });
});

test("a Free organization sees the plan gate on Telegram and nothing breaks", async ({ page }, info) => {
  await signInAs(page, PEOPLE.birchAdmin);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Telegram approvals is on the Pro plan" })).toBeVisible();
  await page.screenshot({ path: `test-results/state-402-${info.project.name}.png`, fullPage: true });
});

// SC28: the Free frame says what the page does and what plan it needs, and asks the API for no data.
test("Free organization opens Analytics and sees the upgrade prompt without a stats request", async ({ page }, info) => {
  const stats: string[] = [];
  page.on("request", (r) => /\/stats\//.test(r.url()) && stats.push(r.url()));
  await signInAs(page, PEOPLE.birchAdmin);
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { level: 1, name: "Analytics" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Analytics is on the Pro plan" })).toBeVisible();
  await expect(page.getByText(/Requests, how many were approved without a human/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Switch to Pro" })).toBeVisible(); // DEMO_MODE: the upgrade path is one press
  expect(stats).toEqual([]);
  await page.screenshot({ path: `test-results/analytics-free-${info.project.name}.png`, fullPage: true });
});

test("a Pro organization sees Analytics built from /stats", async ({ page }, info) => {
  const stats: string[] = [];
  page.on("response", (r) => /\/stats\/summary/.test(r.url()) && stats.push(`${r.status()}`));
  await signInAs(page, PEOPLE.admin);
  await page.goto("/analytics");
  await expect(page.getByText("Auto-approval rate")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Requests per day" })).toBeVisible();
  expect(stats).toContain("200");
  await page.screenshot({ path: `test-results/analytics-pro-${info.project.name}.png`, fullPage: true });
});
