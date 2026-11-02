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

test("the photographs load, the compositions are decoration, the credits link to the photo pages", async ({ page }) => {
  await page.goto("/");
  const photos = page.locator(".ln img[src^='/landing/']");
  await expect(photos).toHaveCount(2);
  for (const img of await photos.all()) {
    // The questions photo is hidden below 1000px by design: a hidden lazy image never loads.
    if (!(await img.isVisible())) continue;
    await img.scrollIntoViewIfNeeded();
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0)).toBe(true);
  }
  const pics = page.locator("svg[data-pic]");
  await expect(pics).toHaveCount(2);
  for (const svg of await pics.all()) await expect(svg).toHaveAttribute("aria-hidden", "true");
  const credits = page.locator(".ln-credits");
  await expect(credits.getByRole("link", { name: /Jakub/ })).toHaveAttribute("href", /unsplash\.com\/photos\/man-signing/);
  await expect(credits.getByRole("link", { name: /Michael Walter/ })).toHaveAttribute("href", /unsplash\.com\/photos\/official-receipt/);
});
