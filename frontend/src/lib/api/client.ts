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

type Options = { method?: string; body?: unknown; signal?: AbortSignal };

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
export async function api<T = unknown>(path: string, { method = "GET", body, signal }: Options = {}): Promise<T> {
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), TIMEOUT_MS);
  const signals = signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signals,
    });
  } catch (err) {
    if (signal?.aborted) throw err; // the caller cancelled; not a failure to show
    if (timeout.signal.aborted) throw new ApiError(0, "timeout", "The server took too long to answer");
    throw new ApiError(0, "network", "No response from the server");
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 401) toLogin();
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
