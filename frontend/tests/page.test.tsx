import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import Page from "@/app/status/page";

const ready = { status: "ok", checks: { postgres: true, redis: true } };
const down = { status: "unavailable", checks: { postgres: true, redis: false } };
const reply = (body: object, status: number) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

describe("health page", () => {
  it("shows loading before the first answer", () => {
    vi.stubGlobal("fetch", () => new Promise(() => {}));
    render(<Page />);
    expect(screen.getByRole("status")).toHaveTextContent("Checking the API");
  });

  it("shows ready on 200", async () => {
    vi.stubGlobal("fetch", () => reply(ready, 200));
    render(<Page />);
    expect(await screen.findByText("API ready")).toBeInTheDocument();
  });

  // A failed read must never look healthy (I22).
  it("renders the error state with Retry on 503, naming the failed dependency", async () => {
    vi.stubGlobal("fetch", () => reply(down, 503));
    render(<Page />);
    expect(await screen.findByText("API unavailable")).toBeInTheDocument();
    expect(screen.queryByText("API ready")).not.toBeInTheDocument();
    expect(screen.getByText(/Redis/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("renders a network error with Retry, and Retry recovers", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("failed"))
      .mockImplementationOnce(() => reply(ready, 200));
    vi.stubGlobal("fetch", fetchMock);
    render(<Page />);
    expect(await screen.findByText("Cannot reach the API")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("API ready")).toBeInTheDocument();
  });

  // Retry unmounting would drop keyboard focus to <body>.
  it("keeps focus on the Retry button while retrying, and ignores a second click", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("failed"))
      .mockImplementationOnce(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    render(<Page />);
    const retry = await screen.findByRole("button", { name: "Retry" });
    retry.focus();
    await userEvent.click(retry);
    const busy = screen.getByRole("button", { name: "Retrying…" });
    expect(busy).toHaveFocus();
    await userEvent.click(busy);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  // The Retry button unmounts on success; focus must land on the status region, not <body>.
  it("moves focus to the status region when a retry succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValueOnce(new TypeError("failed")).mockImplementationOnce(() => reply(ready, 200)),
    );
    render(<Page />);
    await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await screen.findByText("API ready");
    expect(screen.getByRole("status")).toHaveFocus();
  });
});
