"""Realtime benchmark: WebSocket vs long-poll vs adaptive polling against a running API (docs/REALTIME.md).

    uv run --project backend python scripts/bench_realtime.py --base http://localhost:8000 --pid <api pid>

Needs the API with DEMO_MODE=true on seeded data (scripts/seed.py) and the arq worker (its relay wakes the
sockets). N clients sign in as the seeded users of one organization (a user holds at most 5 streams), a
producer flips that organization's plan (demo switch, one `plan.changed` event each) and every client times
every event. Wire bytes are estimated from HTTP header and body sizes plus WebSocket frame sizes: no TCP, IP
or TLS overhead."""

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx2
import websockets

COOKIE = "aop_session"
CAP = 5  # concurrent streams per user and organization (spec 5.3)
ADAPTIVE_MIN, ADAPTIVE_MAX = (
    1.0,
    10.0,
)  # seconds between polls: reset on an event, doubled when idle
TRANSPORTS = ("ws", "longpoll", "adaptive")
CLK = os.sysconf("SC_CLK_TCK")


@dataclass
class Wire:
    requests: int = 0
    bytes: int = 0  # up + down
    frames: int = 0


@dataclass
class Client:
    http: httpx2.AsyncClient
    cookie: str
    wire: Wire
    seen: dict[int, float] = field(
        default_factory=dict
    )  # seq -> first receipt (monotonic)


def _header_bytes(headers: Any) -> int:
    return sum(len(k) + len(v) + 4 for k, v in headers.raw)  # "k: v\r\n"


async def call(c: Client, method: str, path: str, **kw: Any) -> httpx2.Response:
    """One HTTP exchange, its estimated wire size added to the client's tally."""
    r = await c.http.request(method, path, **kw)
    req = r.request
    c.wire.requests += 1
    c.wire.bytes += (
        len(method)
        + len(req.url.raw_path)
        + 12
        + _header_bytes(req.headers)
        + 2
        + len(req.content)
    )
    c.wire.bytes += 15 + _header_bytes(r.headers) + 2 + r.num_bytes_downloaded
    return r


def record(c: Client, events: list[dict[str, Any]], now: float) -> bool:
    got = False
    for e in events:
        if e["type"] == "plan.changed":
            c.seen.setdefault(e["seq"], now)
            got = True
    return got


async def run_ws(c: Client, origin: str, base: str, after: int) -> None:
    t = (
        await call(c, "POST", "/api/v1/realtime/ticket", headers=_hdr(c, origin))
    ).json()["ticket"]
    url = base.replace("http", "ws", 1) + f"/ws?ticket={t}&after={after}"
    async with websockets.connect(url, additional_headers={"Origin": origin}) as ws:
        async for msg in ws:
            raw = msg if isinstance(msg, bytes) else msg.encode()
            c.wire.frames += 1
            c.wire.bytes += len(raw) + (
                4 if len(raw) > 125 else 2
            )  # server frames are unmasked
            record(c, json.loads(raw)["events"], time.perf_counter())


async def run_longpoll(c: Client, after: int) -> None:
    while True:
        r = await call(
            c, "GET", f"/api/v1/events?after={after}&wait=25", headers=_hdr(c)
        )
        feed = r.json()
        record(c, feed["events"], time.perf_counter())
        after = feed["after"]


async def run_adaptive(c: Client, after: int) -> None:
    gap = ADAPTIVE_MIN
    while True:
        r = await call(
            c, "GET", f"/api/v1/events?after={after}&wait=0", headers=_hdr(c)
        )
        feed = r.json()
        busy = bool(feed["events"])
        record(c, feed["events"], time.perf_counter())
        after = feed["after"]
        gap = ADAPTIVE_MIN if busy else min(gap * 2, ADAPTIVE_MAX)
        await asyncio.sleep(gap)


def _hdr(c: Client, origin: str | None = None) -> dict[str, str]:
    h = {"Cookie": f"{COOKIE}={c.cookie}"}
    return h | ({"Origin": origin} if origin else {})


class Proc:
    """CPU seconds and memory of the server process, read from /proc (None without --pid)."""

    def __init__(self, pid: int | None) -> None:
        self.pid = pid

    def cpu(self) -> float | None:
        if self.pid is None:
            return None
        with open(f"/proc/{self.pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / CLK  # utime + stime

    def rss_mb(self) -> float | None:
        if self.pid is None:
            return None
        with open(f"/proc/{self.pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
        return None


async def sign_in(base: str, origin: str, email: str) -> tuple[str, str, str]:
    """(session cookie value, organization id, role) for a seeded demo user."""
    async with httpx2.AsyncClient(base_url=base) as h:
        r = await h.post(
            "/api/v1/auth/demo/login", json={"email": email}, headers={"Origin": origin}
        )
        r.raise_for_status()
        cookie = next(
            v.split(";")[0].split("=", 1)[1]
            for v in r.headers.get_list("set-cookie")
            if v.startswith(COOKIE + "=")
        )
        return cookie, r.json()["organization_id"], r.json()["role"]


async def head(c: Client) -> int:
    """The organization's current newest seq: every client starts after it, so history is not replayed."""
    after = 0
    while True:
        feed = (
            await c.http.get(f"/api/v1/events?after={after}&wait=0", headers=_hdr(c))
        ).json()
        if not feed["events"] and feed["after"] == after:
            return after
        after = feed["after"]


async def trial(
    transport: str, clients: list[Client], producer: Client, origin: str, base: str,
    n_events: int, interval: float, proc: Proc, rng: random.Random,
) -> dict[str, Any]:  # fmt: skip
    for c in clients:
        c.seen.clear()
    after = await head(producer)
    plan = (
        await producer.http.get(
            "/api/v1/organizations/current/plan", headers=_hdr(producer)
        )
    ).json()["plan"]
    other = {"free": "pro", "pro": "free"}[
        plan
    ]  # the demo organizations are Free or Pro
    runner = {
        "ws": lambda c: run_ws(c, origin, base, after),
        "longpoll": lambda c: run_longpoll(c, after),
        "adaptive": lambda c: run_adaptive(c, after),
    }[transport]
    tasks = [asyncio.create_task(runner(c)) for c in clients]
    await asyncio.sleep(2)  # sockets open, first polls in flight
    for c in clients:
        c.wire = Wire()
    cpu0, load0, t0 = proc.cpu(), os.getloadavg()[0], time.perf_counter()

    sent: list[float] = []
    for i in range(n_events):
        await asyncio.sleep(interval * rng.uniform(0.5, 1.5))
        sent.append(time.perf_counter())
        r = await producer.http.put(
            "/api/v1/organizations/current/plan", json={"plan": other if i % 2 == 0 else plan},
            headers=_hdr(producer, origin),
        )  # fmt: skip
        r.raise_for_status()
    deadline = (
        time.perf_counter() + ADAPTIVE_MAX + 15
    )  # one slowest poll gap plus slack
    while time.perf_counter() < deadline and any(
        len(c.seen) < n_events for c in clients
    ):
        await asyncio.sleep(0.2)
    t1, cpu1 = time.perf_counter(), proc.cpu()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if n_events % 2:  # leave the plan as found
        await producer.http.put(
            "/api/v1/organizations/current/plan",
            json={"plan": plan},
            headers=_hdr(producer, origin),
        )

    # The k-th plan.changed (by seq) is the k-th flip.
    seqs = sorted({s for c in clients for s in c.seen})[:n_events]
    lat = sorted(
        (c.seen[s] - sent[k]) * 1000
        for c in clients
        for k, s in enumerate(seqs)
        if s in c.seen
    )
    wall = t1 - t0
    wire = Wire(
        *(
            sum(getattr(c.wire, f) for c in clients)
            for f in ("requests", "bytes", "frames")
        )
    )
    return {
        "transport": transport,
        "events": n_events,
        "delivered": len(lat),
        "latency_ms": {
            "p50": round(statistics.median(lat), 1) if lat else 0,
            "p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else 0,
            "max": round(lat[-1], 1) if lat else 0,
        },
        "window_s": round(wall, 1),
        "requests_per_min": round(wire.requests / wall * 60, 1),
        "frames_per_min": round(wire.frames / wall * 60, 1),
        "bytes_per_min": round(wire.bytes / wall * 60),
        "server_cpu_pct": None
        if cpu0 is None or cpu1 is None
        else round((cpu1 - cpu0) / wall * 100, 2),
        "server_rss_mb": None
        if proc.rss_mb() is None
        else round(proc.rss_mb() or 0, 1),
        "load_avg_1m": [round(load0, 2), round(os.getloadavg()[0], 2)],
    }


async def main(a: argparse.Namespace) -> dict[str, Any]:
    if a.smoke:
        a.clients, a.events, a.interval = 10, 3, 1.0
    async with httpx2.AsyncClient(base_url=a.base) as h:
        users = (await h.get("/api/v1/auth/demo/users")).json()["users"]
    admin = next(
        u for u in users if u["role"] == "org_admin"
    )  # the producer; its org is the one measured
    mates = [u["email"] for u in users if u["organization"] == admin["organization"]]
    if a.clients > CAP * len(mates):
        sys.exit(
            f"{a.clients} clients need {-(-a.clients // CAP)} users; {admin['organization']} has {len(mates)}"
        )
    sessions = {e: await sign_in(a.base, a.origin, e) for e in mates}

    def new_client(email: str) -> Client:
        return Client(
            httpx2.AsyncClient(base_url=a.base, timeout=60), sessions[email][0], Wire()
        )

    producer = new_client(admin["email"])
    clients = [new_client(mates[i % len(mates)]) for i in range(a.clients)]
    proc, rng, results = Proc(a.pid), random.Random(a.seed), []
    try:
        for t in a.transports.split(","):
            results.append(
                await trial(
                    t,
                    clients,
                    producer,
                    a.origin,
                    a.base,
                    a.events,
                    a.interval,
                    proc,
                    rng,
                )
            )
            await asyncio.sleep(3)  # long-poll slots of the last trial free themselves
    finally:
        for c in [producer, *clients]:
            await c.http.aclose()
        async with httpx2.AsyncClient(base_url=a.base) as h:
            for cookie, _, _ in sessions.values():
                await h.post(
                    "/api/v1/auth/logout",
                    headers={"Origin": a.origin, "Cookie": f"{COOKIE}={cookie}"},
                )
    return {"clients": a.clients, "organization": admin["organization"], "interval_s": a.interval,
            "cpus": os.cpu_count(), "results": results}  # fmt: skip


def table(report: dict[str, Any]) -> str:
    head = f"{'transport':<9} {'events':>6} {'got':>5} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8} {'req/min':>9} {'frames/min':>10} {'bytes/min':>10} {'cpu %':>6} {'rss MB':>7}"
    rows = [head]
    for r in report["results"]:
        lat = r["latency_ms"]
        rows.append(
            f"{r['transport']:<9} {r['events']:>6} {r['delivered']:>5} {lat['p50']:>8} {lat['p95']:>8} {lat['max']:>8}"
            f" {r['requests_per_min']:>9} {r['frames_per_min']:>10} {r['bytes_per_min']:>10}"
            f" {r['server_cpu_pct'] if r['server_cpu_pct'] is not None else 'n/a':>6} {r['server_rss_mb'] or 'n/a':>7}"
        )
    return (
        f"{report['clients']} clients of {report['organization']}, an event every ~{report['interval_s']}s\n"
        + "\n".join(rows)
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument(
        "--origin", default="http://localhost:3000", help="the API's WEB_ORIGIN"
    )
    p.add_argument("--clients", type=int, default=20)
    p.add_argument("--events", type=int, default=30)
    p.add_argument(
        "--interval", type=float, default=2.0, help="mean seconds between events"
    )
    p.add_argument("--transports", default=",".join(TRANSPORTS))
    p.add_argument("--pid", type=int, help="API process id, for CPU and memory")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument(
        "--smoke", action="store_true", help="10 clients, 3 events, 1 s apart"
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    out = asyncio.run(main(args))
    print(json.dumps(out, indent=1) if args.json else table(out))
