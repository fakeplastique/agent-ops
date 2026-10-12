import { expect, test, type Page } from "@playwright/test";

import { fakeApp, TITLE_A, TITLE_B } from "./app-fake";

// FM31: an identity or organization change leaves nothing of the previous one in the DOM, in memory or in storage.
// Google is not reachable here: the API is faked at the browser edge, one signed-in user at a time.

const USER_A = "user-a-7f3e";
const USER_B = "user-b-91c2";

async function fakeApi(page: Page) {
  const state: { user: string | null; org: string | null; sessionReads: string[] } = { user: USER_A, org: null, sessionReads: [] };
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    const method = route.request().method();
    if (pathname === "/api/v1/auth/session") {
      if (!state.user) return route.fulfill({ status: 401, json: { error: { code: "unauthenticated", message: "x", details: {} } } });
      state.sessionReads.push(state.user);
      return route.fulfill({ json: { user_id: state.user, organization_id: state.org, role: state.org ? "org_admin" : null } });
    }
    if (pathname === "/api/v1/auth/logout" && method === "POST") {
      state.user = null;
      return route.fulfill({ status: 204 });
    }
    // The real callback lands on the web origin after Google; here sign-in hands the session straight to user B.
    if (pathname === "/api/v1/auth/login") {
      state.user = USER_B;
      return route.fulfill({ status: 302, headers: { location: "/onboarding" } });
    }
    if (pathname === "/api/v1/organizations" && method === "POST") {
      state.org = "org-new";
      return route.fulfill({ status: 201, json: { id: "org-new", name: "Contoso", slug: "contoso", plan_key: "free" } });
    }
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "x", details: {} } } });
  });
  return state;
}

// Stand-ins for what a screen keeps for its user: a draft in storage, a selection, and an in-memory cache.
async function leaveTraces(page: Page, who: string) {
  await page.evaluate((w) => {
    localStorage.setItem("draft", `reject comment by ${w}`);
    sessionStorage.setItem("selected", `dr_of_${w}`);
    (window as unknown as { __cache: string }).__cache = `decisions of ${w}`;
  }, who);
}

async function traces(page: Page) {
  return page.evaluate(() => ({
    local: { ...localStorage },
    session: { ...sessionStorage },
    cache: (window as unknown as { __cache?: string }).__cache ?? null,
    text: document.body.innerText,
  }));
}

function expectClean(t: Awaited<ReturnType<typeof traces>>) {
  expect(t.local).toEqual({});
  expect(t.session).toEqual({});
  expect(t.cache).toBeNull();
}

test("user A signs out, user B signs in: no A data in DOM, memory or storage", async ({ page }, info) => {
  const api = await fakeApi(page);
  await page.goto("/onboarding");
  await page.getByLabel("Name").fill("Northwind of user A");
  await leaveTraces(page, USER_A);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expectClean(await traces(page));

  await page.getByRole("link", { name: "Continue with Google" }).click();
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByLabel("Name")).toBeVisible();
  const after = await traces(page);
  expectClean(after);
  expect(after.text).not.toContain("Northwind of user A");
  expect(await page.content()).not.toContain(USER_A);
  expect(api.sessionReads.at(-1)).toBe(USER_B);
  await page.screenshot({ path: `test-results/onboarding-${info.project.name}.png`, fullPage: true });
});

test("an organization change reloads into the app with the open form and selection gone", async ({ page }) => {
  await fakeApi(page);
  await page.goto("/onboarding");
  await page.getByLabel("Name").fill("Contoso");
  await leaveTraces(page, "org-a");

  // Creating switches the session to the new organization (API), so the client must start over.
  await page.getByRole("button", { name: "Create organization" }).click();
  await expect(page).toHaveURL(/\/inbox$/);
  const after = await traces(page);
  expectClean(after);
  expect(after.text).not.toContain("Contoso");
});

test("another tab's sign-out reloads this tab", async ({ context }) => {
  const first = await context.newPage();
  const second = await context.newPage();
  await fakeApi(first);
  await fakeApi(second);
  await first.goto("/onboarding");
  await second.goto("/onboarding");
  await second.getByLabel("Name").fill("typed in the second tab");
  await second.evaluate(() => ((window as unknown as { __cache: string }).__cache = "second tab"));

  const reloaded = second.waitForEvent("load");
  await first.getByRole("button", { name: "Sign out" }).click();
  await reloaded;
  expect(await second.evaluate(() => (window as unknown as { __cache?: string }).__cache ?? null)).toBeNull();
  await expect(second.getByLabel("Name")).toHaveValue("");
});

test("login shows the callback error and keeps only a same-origin next", async ({ page }, info) => {
  await fakeApi(page);
  await page.goto("/login?error=access_denied&next=%2F%2Fevil.com");
  await expect(page.getByText("Sign-in was cancelled")).toBeVisible();
  await page.screenshot({ path: `test-results/login-error-${info.project.name}.png`, fullPage: true });
  await page.getByRole("link", { name: "Continue with Google" }).click();
  await expect(page).toHaveURL(/\/onboarding$/);
  expect(await page.evaluate(() => sessionStorage.getItem("aop.next"))).toBeNull();
});

test("an invitation for another account names it", async ({ page }, info) => {
  await page.route("**/api/v1/invitations/accept", (route) =>
    route.fulfill({
      status: 403,
      json: { error: { code: "invitation_email_mismatch", message: "x", details: { invited_email: "a@x.com" } } },
    }),
  );
  await page.goto(`/invite/inv_${"a".repeat(43)}`);
  await page.getByRole("button", { name: "Accept invitation" }).click();
  await expect(page.getByText("This invitation is for a@x.com")).toBeVisible();
  await page.screenshot({ path: `test-results/invite-mismatch-${info.project.name}.png`, fullPage: true });
});

// With a row selected and a rejection comment half-typed: the screen a stale cache would hurt most.
async function openRowWithDraft(page: Page) {
  await page.goto("/inbox");
  await page.getByRole("button", { name: new RegExp(TITLE_A) }).click();
  await page.getByLabel(/Comment/).fill("draft reason typed by user A");
  await leaveTraces(page, "user-a");
  await expect(page.getByRole("heading", { level: 2, name: TITLE_A })).toBeVisible();
}

// The sidebar folds into a menu sheet at 375 px; its copy of the controls is the only one on screen.
async function sidebar(page: Page, narrow: boolean) {
  if (narrow) await page.getByRole("button", { name: "Menu" }).click();
}

test("switching organization with a row selected and a comment typed leaves nothing of the first organization", async ({ page }, info) => {
  await fakeApp(page);
  await openRowWithDraft(page);
  await sidebar(page, info.project.name === "narrow-375");
  const reloaded = page.waitForEvent("load");
  await page.getByRole("combobox", { name: "Organization" }).selectOption("org-b");
  await reloaded;
  await expect(page).toHaveURL(/\/inbox$/);
  await expect(page.getByRole("button", { name: new RegExp(TITLE_B) })).toBeVisible();
  const after = await traces(page);
  expectClean(after);
  expect(after.text).not.toContain(TITLE_A);
  expect(after.text).not.toContain("draft reason");
  expect(await page.getByLabel(/Comment/).inputValue()).toBe("");
  expect(await page.content()).not.toContain(TITLE_A);
});

test("user A signs out with a row and a draft open, user B signs in: nothing of A is left", async ({ page }, info) => {
  const state = await fakeApp(page);
  await page.route("**/api/v1/auth/login", (route) => {
    state.user = "user-b";
    state.org = "org-b";
    return route.fulfill({ status: 302, headers: { location: "/inbox" } });
  });
  await openRowWithDraft(page);
  await sidebar(page, info.project.name === "narrow-375");
  await page.getByRole("button", { name: "Sign out" }).first().click();
  await expect(page).toHaveURL(/\/login$/);
  expectClean(await traces(page));
  await page.getByRole("link", { name: "Continue with Google" }).click();
  await expect(page.getByRole("button", { name: new RegExp(TITLE_B) })).toBeVisible();
  const after = await traces(page);
  expectClean(after);
  expect(after.text).not.toContain(TITLE_A);
  expect(await page.content()).not.toContain("draft reason");
});
