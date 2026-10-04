import { expect, test } from "@playwright/test";

// Rewrite or CORS misconfiguration only shows in a real browser.
test("page shows API ready", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.getByText("API ready")).toBeVisible();
  await page.screenshot({ path: `test-results/ready-${info.project.name}.png` });
});

// The login flow runs on the web origin (cookie, Origin check, OAuth redirect URI): the API tree must be reachable
// through Next's rewrite, not only on the API's own host. Unauthenticated, the API answers 401 with its error body.
test("the API is reachable through the web origin", async ({ request }) => {
  const res = await request.get("/api/v1/auth/session");
  expect(res.status()).toBe(401);
  expect((await res.json()).error.code).toBe("unauthenticated");
});
