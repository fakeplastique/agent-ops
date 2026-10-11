import { expect, test } from "@playwright/test";

// What the browser client depends on, through the web origin: an unauthenticated feed read is a clean 401 (the client
// stops and the session redirect takes over) and a ticket request is refused, never served. The two-browser journey
// (a second approver and a live inbox) is S48's, once the screens exist.
test("the event feed and the ticket endpoint refuse an anonymous browser", async ({ request }) => {
  const feed = await request.get("/api/v1/events?after=0&wait=0&limit=1");
  expect(feed.status()).toBe(401);
  expect((await feed.json()).error.code).toBe("unauthenticated");

  const ticket = await request.post("/api/v1/realtime/ticket");
  expect(ticket.ok()).toBe(false); // 404 until S30 lands on main, then 401
});
