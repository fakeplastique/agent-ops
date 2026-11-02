import { expect, PEOPLE, signInAs, test, unique } from "./fixtures";


test("Pro: an attachment is uploaded from the tab and listed with a download link", async ({ page, agents }, info) => {
  const req = await agents.create({ title: unique("Refund with receipt", info) });
  await signInAs(page, PEOPLE.manager);
  await page.goto(`/decisions/${req.id}?tab=attachments`);
  await page.getByLabel("Add a file", { exact: true }).setInputFiles({ name: "receipt.txt", mimeType: "text/plain", buffer: Buffer.from("order 48213, 12.40 EUR") });
  const link = page.getByRole("link", { name: /receipt\.txt/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", new RegExp(`/decision-requests/${req.id}/attachments/[^/]+/download`));
});

// The Free plan gate on the same tab needs a Birch request (no Birch agent token is minted yet): add it with S32.
