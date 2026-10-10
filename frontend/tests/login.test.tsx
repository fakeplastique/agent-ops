import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginView } from "@/app/(auth)/login/LoginView";
import { takeNext } from "@/lib/session";

// An account-existence oracle: copy that says whether an email is known here.
const ORACLE = /no account|not registered|not found|does not exist|doesn't exist|unknown (user|email)|already (has|have) an account|sign up/i;

// jsdom cannot navigate; stop the link's default so the click handler is what is under test.
const stopNavigation = (e: Event) => e.preventDefault();
beforeEach(() => document.addEventListener("click", stopNavigation));
afterEach(() => {
  document.removeEventListener("click", stopNavigation);
  localStorage.clear();
  sessionStorage.clear();
});

describe("Login", () => {
  it("offers one way in: Continue with Google, to the API's login route", () => {
    render(<LoginView error={null} next={null} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Sign in to Agent Ops");
    expect(screen.getByRole("link", { name: "Continue with Google" })).toHaveAttribute("href", "/api/v1/auth/login");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("access_denied says the Google sign-in was cancelled", () => {
    render(<LoginView error="access_denied" next={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in was cancelled");
  });

  // The API reports an unverified Google email as login_failed (one body for every login failure, S15).
  it.each(["login_failed", "email_unverified"])("%s names the verified-email requirement", (code) => {
    render(<LoginView error={code} next={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/verified email address/);
  });

  it.each(["access_denied", "login_failed", "email_unverified", "login_unavailable", "user_not_found"])(
    "%s carries no account-existence hint",
    (code) => {
      render(<LoginView error={code} next={null} />);
      expect(document.body.textContent).not.toMatch(ORACLE);
    },
  );

  // ?error= is attacker-controlled: its text must never reach the page.
  it("an unknown error code shows the generic text, not the parameter", () => {
    render(<LoginView error="Your account is locked. Call +1 555 0100" next={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in did not complete");
    expect(document.body.textContent).not.toContain("555");
  });

  it("Continue drops the previous user's local state and keeps a safe next for after the round trip", async () => {
    localStorage.setItem("draft", "user A");
    render(<LoginView error={null} next="/decisions?status=pending_votes" />);
    await userEvent.click(screen.getByRole("link", { name: "Continue with Google" }));
    expect(localStorage.getItem("draft")).toBeNull();
    expect(takeNext()).toBe("/decisions?status=pending_votes");
  });

  it("an off-site next is not kept", async () => {
    render(<LoginView error={null} next="//evil.com/inbox" />);
    await userEvent.click(screen.getByRole("link", { name: "Continue with Google" }));
    expect(takeNext()).toBeNull();
  });
});
