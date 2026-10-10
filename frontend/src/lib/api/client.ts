import { goTo } from "./navigate";

const BASE = "/api/v1";
const TIMEOUT_MS = 15_000;

/** Every failed call: an HTTP error (the API's envelope), a network failure (status 0, "network") or a timeout (status 0, "timeout"). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string | null = null,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** timeoutMs covers the whole call, body included (default 15 s; the /events long-poll needs longer). */
type Options = { method?: string; body?: unknown; signal?: AbortSignal; timeoutMs?: number };

const timedOut = () => new ApiError(0, "timeout", "The server took too long to answer");

// Rejects as soon as the deadline or the caller's signal fires, even if the wrapped read never settles (a stalled body).
function bounded<R>(p: Promise<R>, deadline: AbortSignal, signal?: AbortSignal): Promise<R> {
  return new Promise<R>((resolve, reject) => {
    const onDeadline = () => reject(timedOut());
    const onAbort = () => reject(signal?.reason);
    if (signal?.aborted) return onAbort();
    if (deadline.aborted) return onDeadline();
    deadline.addEventListener("abort", onDeadline, { once: true });
    signal?.addEventListener("abort", onAbort, { once: true });
    p.then(resolve, reject).finally(() => {
      deadline.removeEventListener("abort", onDeadline);
      signal?.removeEventListener("abort", onAbort);
    });
  });
}

// Retry-After is seconds or an HTTP date.
function seconds(header: string | null): number | null {
  if (!header) return null;
  const n = Number(header);
  if (Number.isFinite(n)) return Math.max(0, Math.ceil(n));
  const at = Date.parse(header);
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - Date.now()) / 1000));
}

async function toError(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => null);
  const e = body?.error ?? {};
  const details = typeof e.details === "object" && e.details && !Array.isArray(e.details) ? e.details : {};
  const requestId = res.headers.get("X-Request-ID") ?? (typeof details.request_id === "string" ? details.request_id : null);
  return new ApiError(
    res.status,
    typeof e.code === "string" ? e.code : `http_${res.status}`,
    typeof e.message === "string" ? e.message : res.statusText || `HTTP ${res.status}`,
    details,
    requestId,
    seconds(res.headers.get("Retry-After")),
  );
}

// Session expired: sign in again and come back here. Never from the login page (that would loop).
function toLogin() {
  const { pathname, search } = window.location;
  if (pathname === "/login" || pathname.startsWith("/login/")) return;
  goTo(`/login?next=${encodeURIComponent(pathname + search)}`);
}

/** Same-origin JSON call to the REST API. Resolves only on 2xx; everything else rejects with ApiError. */
export async function api<T = unknown>(
  path: string,
  { method = "GET", body, signal, timeoutMs = TIMEOUT_MS }: Options = {},
): Promise<T> {
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(), timeoutMs);
  const within = <R,>(p: Promise<R>) => bounded(p, deadline.signal, signal);
  try {
    let res: Response;
    try {
      res = await within(
        fetch(`${BASE}${path}`, {
          method,
          credentials: "same-origin",
          headers: body === undefined ? undefined : { "content-type": "application/json" },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: signal ? AbortSignal.any([signal, deadline.signal]) : deadline.signal,
        }),
      );
    } catch (err) {
      if (signal?.aborted) throw err; // the caller cancelled; not a failure to show
      if (deadline.signal.aborted) throw timedOut();
      throw new ApiError(0, "network", "No response from the server");
    }
    if (res.status === 401) toLogin();
    if (!res.ok) throw await within(toError(res));
    if (res.status === 204) return undefined as T;
    return (await within(res.json())) as T;
  } finally {
    clearTimeout(timer); // only once the body has been read
  }
}
