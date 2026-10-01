import { expect, test } from "@playwright/test";

// Rewrite or CORS misconfiguration only shows in a real browser.
test("page shows API ready", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.getByText("API ready")).toBeVisible();
  await page.screenshot({ path: `test-results/ready-${info.project.name}.png` });
});
