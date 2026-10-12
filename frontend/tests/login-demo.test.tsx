import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginView } from "@/app/(auth)/login/LoginView";
import { goTo } from "@/lib/api/navigate";

import { failure, serve } from "./helpers";

vi.mock("@/lib/api/navigate", () => ({ goTo: vi.fn() }));

const USERS = {
  users: [
    { email: "maya@northwind.test", name: "Maya Okafor", role: "manager", organization: "Northwind Logistics" },
    { email: "dmitri@northwind.test", name: "Dmitri Volkov", role: "team_lead", organization: "Northwind Logistics" },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(goTo).mockReset();
  localStorage.clear();
});

describe("Login demo block", () => {
  // catches: demo sign-in offered where DEMO_MODE is off (the API answers 404 there)
  it("is hidden when the roster is not served (404) or empty", async () => {
    const s = serve({ "GET /auth/demo/users": failure(404, "not_found") });
    const { unmount } = render(<LoginView error={null} next={null} />);
    await waitFor(() => expect(s.count("GET /auth/demo/users")).toBe(1));
    expect(screen.queryByText(/Sign in as/)).not.toBeInTheDocument();
    unmount();
    serve({ "GET /auth/demo/users": { users: [] } });
    render(<LoginView error={null} next={null} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/Sign in as/)).not.toBeInTheDocument();
  });

  it("a network failure hides the block and leaves Google sign-in alone", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    render(<LoginView error={null} next={null} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/Sign in as/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue with Google" })).toBeInTheDocument();
  });

  it("lists one button per seeded user with role and organization", async () => {
    serve({ "GET /auth/demo/users": USERS });
    render(<LoginView error={null} next={null} />);
    const maya = await screen.findByRole("button", { name: /Maya Okafor/ });
    expect(maya).toHaveTextContent("Manager");
    expect(maya).toHaveTextContent("Northwind Logistics");
    expect(screen.getByRole("button", { name: /Dmitri Volkov/ })).toHaveTextContent("Team lead");
  });

  it("signing in clears storage, posts the email and loads the inbox", async () => {
    localStorage.setItem("draft", "previous user");
    const s = serve({ "GET /auth/demo/users": USERS, "POST /auth/demo/login": { user_id: "u", organization_id: "o", role: "manager" } });
    render(<LoginView error={null} next={null} />);
    await userEvent.click(await screen.findByRole("button", { name: /Maya Okafor/ }));
    const [, , init] = s.calls.find(([m]) => m === "POST")!;
    expect(JSON.parse(String(init.body))).toEqual({ email: "maya@northwind.test" });
    await waitFor(() => expect(goTo).toHaveBeenCalledWith("/inbox"));
    expect(localStorage.length).toBe(0);
  });

  it("a refused sign-in says so, stays on the page and can be tried again", async () => {
    serve({ "GET /auth/demo/users": USERS, "POST /auth/demo/login": failure(404, "not_found") });
    render(<LoginView error={null} next={null} />);
    await userEvent.click(await screen.findByRole("button", { name: /Maya Okafor/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign in as Maya Okafor");
    expect(goTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Maya Okafor/ })).toBeEnabled();
  });
});
