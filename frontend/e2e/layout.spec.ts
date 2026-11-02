import { expect, noSidewaysScroll, PEOPLE, signInAs, test, unique } from "./fixtures";

// FM33 (spec 8.1 Long content, Narrow) against the real backend: the content that breaks layouts is created through
// the API, then every screen that shows it is opened at 375 and 1280 px, screenshotted and checked for sideways scroll.
// "10,000 rows" is not covered: lists are paged (20 a page), so a page never holds that many.

const WORDS = "refund-for-the-customer-whose-parcel-arrived-damaged-and-who-was-already-charged-twice-for-shipping";
const LONG_TITLE = `${WORDS} ${"x".repeat(88)}`.slice(0, 200); // 200 characters, one unbroken token at the end
const RTL_TITLE = "طلب استرداد المبلغ للطلب رقم ٤٨٢١٣ — מראה בקשה דחופה לאישור";
const BIG_PAYLOAD = { note: "y".repeat(60_000), order: "o".repeat(300), lines: Array.from({ length: 40 }, (_, i) => ({ sku: `SKU-${i}`, qty: i })) };
const REVISIONS = 13; // 14 versions of 3 votes each: 42 votes

async function settle(page: Parameters<typeof signInAs>[0], what: string, info: { project: { name: string } }) {
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0);
  expect(await noSidewaysScroll(page), `${what} scrolls sideways`).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `test-results/layout-${what}-${info.project.name}.png`, fullPage: true });
}

test("a 200-character title, a 60 KB payload and right-to-left text hold at 375 and 1280", async ({ page, agents }, info) => {
  const long = await agents.create({ title: LONG_TITLE, description: "d ".repeat(1500), payload: BIG_PAYLOAD });
  const rtl = await agents.create({ title: `${RTL_TITLE} ${unique("", info)}`, description: RTL_TITLE });
  await agents.voteAll(long);
  await agents.voteAll(rtl);

  await signInAs(page, PEOPLE.manager);
  await expect(page.getByRole("list", { name: "Steps waiting on me" })).toBeVisible();
  await settle(page, "inbox-list", info);

  // The unique suffix, not the shared RTL prefix: the narrow and wide projects use the same manager inbox.
  await page.getByRole("button", { name: new RegExp(rtl.title.split(" ").slice(-2).join(" ")) }).click();
  await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible();
  await settle(page, "inbox-rtl", info);

  for (const tab of ["overview", "votes", "timeline", "audit"]) {
    await page.goto(`/decisions/${long.id}?tab=${tab}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(LONG_TITLE.slice(0, 30));
    await settle(page, `detail-${tab}`, info);
  }
  await page.goto("/decisions");
  await expect(page.getByRole("link", { name: new RegExp(LONG_TITLE.slice(0, 30)) }).first()).toBeVisible();
  await settle(page, "decisions-list", info);
  await page.goto(`/decisions/${rtl.id}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("طلب استرداد");
  await settle(page, "detail-rtl", info);
});

test("a request revised 13 times lists 40 votes without overflow", async ({ page, agents }, info) => {
  test.setTimeout(120_000);
  const req = await agents.create({ title: unique("Revised refund", info) });
  await agents.voteAll(req);
  for (let i = 1; i <= REVISIONS; i++) {
    await agents.revise(req, `Revision ${i}: amount confirmed with the customer`);
    await agents.voteAll(req);
  }

  await signInAs(page, PEOPLE.manager);
  await page.goto(`/decisions/${req.id}?tab=votes`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Revised refund");
  await expect(page.getByText(/Session [0-9a-f]{8}/).first()).toBeVisible();
  expect(await page.getByText(/Session [0-9a-f]{8}/).count()).toBeGreaterThanOrEqual(40);
  await settle(page, "votes-40", info);
});
