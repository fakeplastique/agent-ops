import { expect, test } from "@playwright/test";

// Layout at the phone width only shows in a real browser: the orb moves under the slip card as a full circle.
test("the orb is a full circle under the slip card and nothing scrolls horizontally", async ({ page }, info) => {
  await page.goto("/");
  const width = page.viewportSize()!.width;
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);

  const orb = (await page.locator(".ln-orb").boundingBox())!;
  const slip = (await page.locator(".ln-frag").boundingBox())!;
  expect(Math.abs(orb.width - orb.height)).toBeLessThan(1);
  if (width <= 820) {
    expect(orb.y).toBeGreaterThanOrEqual(slip.y + slip.height - 1);
    expect(orb.x).toBeGreaterThanOrEqual(0);
    expect(orb.x + orb.width).toBeLessThanOrEqual(width);
  }
  await page.screenshot({ path: `test-results/landing-${info.project.name}.png`, fullPage: true });
});

test("the earth band draws a simulated feed", async ({ page }) => {
  await page.goto("/");
  const earth = page.locator("canvas.ln-globe");
  await expect(earth).toHaveAttribute("data-earth", /turning|still/);
  await expect(page.getByText("simulated feed")).toBeVisible();
  const box = (await earth.boundingBox())!;
  expect(Math.abs(box.width - box.height)).toBeLessThan(1);
});
