import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InviteView } from "@/app/(auth)/invite/[token]/InviteView";
import { goTo } from "@/lib/api/navigate";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const TOKEN = `inv_${"a".repeat(43)}`;
const reply = (status: number, body?: unknown) =>
  vi.fn(() =>
    Promise.resolve(
      new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
const envelope = (code: string, details = {}) => ({ error: { code, message: code, details } });

afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(goTo).mockReset();
  localStorage.clear();
});

async function accept(fetchMock: ReturnType<typeof reply>) {
  vi.stubGlobal("fetch", fetchMock);
  render(<InviteView token={TOKEN} />);
  await userEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
}

describe("Invitation", () => {
  // Opening a link must not join anyone: a link prefetched by a mail scanner would burn it.
  it("asks before joining and sends nothing on open", () => {
    const fetchMock = reply(200, {});
    vi.stubGlobal("fetch", fetchMock);
    render(<InviteView token={TOKEN} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Join an organization");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // Unknown, expired, used and revoked are one 404 from the API; a malformed token is a 422. All read the same.
  it.each([
    ["expired or used", 404, envelope("invitation_unusable")],
    ["unknown", 404, envelope("not_found")],
    ["malformed", 422, envelope("validation_error")],
  ])("%s shows one message and a way to get a new link", async (_label, status, body) => {
    await accept(reply(status, body));
    expect(await screen.findByRole("alert")).toHaveTextContent("This invitation can't be used");
    expect(screen.getByRole("alert")).toHaveTextContent("Ask the person who invited you for a new link.");
    expect(screen.queryByRole("button", { name: "Accept invitation" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveFocus(); // Accept unmounted under the keyboard; focus lands on the reason
    expect(goTo).not.toHaveBeenCalled();
  });

  it("an email mismatch names the account to switch to and offers sign out", async () => {
    await accept(reply(403, envelope("invitation_email_mismatch", { invited_email: "a@x.com" })));
    expect(await screen.findByRole("alert")).toHaveTextContent("This invitation is for a@x.com");
    expect(screen.getByRole("alert")).toHaveTextContent("sign in with a@x.com");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("a server failure keeps Accept and does not blame the connection", async () => {
    await accept(reply(503, envelope("unavailable")));
    expect(await screen.findByRole("alert")).toHaveTextContent("The invitation was not accepted");
    expect(screen.getByRole("alert")).not.toHaveTextContent(/connection/);
    expect(screen.getByRole("button", { name: "Accept invitation" })).toBeInTheDocument();
  });

  it("no answer from the server points at the connection", async () => {
    await accept(vi.fn(() => Promise.reject(new TypeError("offline"))));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Check your connection/);
    expect(screen.getByRole("button", { name: "Accept invitation" })).toBeInTheDocument();
  });

  // After switching accounts the user should land back on this invitation.
  it("Sign out from the mismatch notice comes back to the invitation", async () => {
    window.history.replaceState(null, "", `/invite/${TOKEN}`);
    await accept(reply(403, envelope("invitation_email_mismatch", { invited_email: "a@x.com" })));
    vi.stubGlobal("fetch", reply(204));
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(goTo).toHaveBeenCalledWith(`/login?next=${encodeURIComponent(`/invite/${TOKEN}`)}`));
  });

  it("joining clears the client and does a full load of the inbox", async () => {
    localStorage.setItem("draft", "from the previous organization");
    const fetchMock = reply(200, { organization_id: "org-b", role: "reviewer" });
    await accept(fetchMock);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/invitations/accept", expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toEqual({ token: TOKEN });
    expect(localStorage.length).toBe(0);
    expect(goTo).toHaveBeenCalledWith("/inbox");
  });
});
