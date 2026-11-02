import { expect, PEOPLE, signInAs, test, unique } from "./fixtures";

// The Attachments tab (spec 8.1) needs S32, which is not on this branch yet: written now, enabled by the coordinator
// once S32 merges (change `test.fixme` to `test`). Selectors follow the spec text and may need a touch-up then.

test.fixme("Pro: an attachment is uploaded from the tab and listed with a download link", async ({ page, agents }, info) => {
  // FIXME(S32): the attachments API and tab are not merged yet.
  const req = await agents.create({ title: unique("Refund with receipt", info) });
  await signInAs(page, PEOPLE.manager);
  await page.goto(`/decisions/${req.id}?tab=attachments`);
  await page.getByLabel(/upload|attach|file/i).setInputFiles({ name: "receipt.txt", mimeType: "text/plain", buffer: Buffer.from("order 48213, 12.40 EUR") });
  const link = page.getByRole("link", { name: /receipt\.txt/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", new RegExp(`/decision-requests/${req.id}/attachments/[^/]+/download`));
});

// The Free plan gate on the same tab needs a Birch request (no Birch agent token is minted yet): add it with S32.
