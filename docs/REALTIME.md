# Realtime

The web app shows decisions and approval steps as they change. Three transports carry one event stream: a **WebSocket**, a
**long-poll** and **adaptive polling** (the fallback). They differ in cost and latency, not in content: a frame and a poll answer have
the same shape, and a client moves between them without losing its place.

## The stream

Every state change writes an event to `outbox_events` in its own transaction. Each organization has its own `seq`, assigned under a
counter-row lock, so `seq` order is commit order and `seq > cursor` never skips an event. A client keeps the highest `seq` it has seen
and asks for what follows.

- **Delivery is at-least-once and ordered by `seq`.** Clients de-duplicate by `seq`.
- **The organization comes from the credential**, never from a query parameter. `organization_id` is in every answer; a client resets
  its cursor when it changes (an organization switch).
- **Users only.** An agent token gets 403 on every realtime route.
- **Only what the role may read.** Each event type has an allow-list of fields (`backend/app/realtime/projection.py`); an unknown type
  or field never leaves. Round-1 blindness has no exemption: a `vote.cast` carries verdict and confidence only from round 2.
  `after` moves past events the caller may not see, so a hidden event never stalls the cursor.
- **The credential is re-verified** before a batch leaves and, on a socket, on every idle tick: a revoked token, a removed membership
  or an organization switch ends the stream with nothing more sent.

Answer shape (`GET /api/v1/events` and every WebSocket frame):

```json
{ "organization_id": "…", "events": [{ "id": "…", "seq": 42, "type": "step.created", "data": { }, "created_at": "…" }], "after": 42 }
```

Event types: `decision.created`, `decision.revised`, `decision.status_changed`, `vote.cast`, `round.revealed`, `message.posted`,
`step.created`, `step.decided`, `step.timed_out`, `plan.changed` and `attachment.added` (id, request, type and size; never the
filename). (`member.joined` is defined but has no emitter yet, so it is not on the feed.)

## WebSocket

1. `POST /api/v1/realtime/ticket` (cookie with the web `Origin`, or a personal access token) answers 201 `{ticket, expires_in: 30}`. The
   ticket is 32 random bytes, kept in Redis only as a hash, bound to the user, the organization and the credential.
2. Open `wss://<api>/ws?ticket=<ticket>&after=<seq>` with the web `Origin` header. Browsers cannot set headers on a WebSocket, so the
   ticket is in the URL; it is spent at the handshake, so a logged URL holds a dead ticket.
3. The server refuses before the upgrade (HTTP 403) when `Origin` is not exactly `WEB_ORIGIN`, `after` is not plain digits, the ticket is
   unknown, used or expired, the user is no longer a member, or the user's stream cap is spent.
4. It then subscribes to the organization's Redis channel, reads from the cursor in pages of 100 and sends each page as one text frame.
   Redis pub/sub carries wake-ups only: the outbox relay publishes the organization after its batch commits, and without a wake-up the
   socket re-reads every 5 s, so a lost message or a Redis outage delays events and never loses them.
5. A frame not taken within 5 s drops the socket. A socket lives 5 minutes, then closes 1000 and the client reconnects with a new ticket
   and its cursor. A revoked credential closes it with 1008. Client frames are ignored.

## Long-poll

`GET /api/v1/events?after=<seq>&wait=<0..25>&limit=<1..100>` returns as soon as there is an event after the cursor, or empty after
`wait` seconds (default 25). It re-queries every 0.5 s in a fresh short transaction, so no transaction is held while it waits, and it
ends early when the client disconnects. A user holds at most 5 streams per organization across both transports (long-polls and
sockets share the cap, kept in Redis so it holds across instances); one more is 429 with `Retry-After`, and a limiter outage is 503.

## Adaptive polling

The same endpoint with `wait=0`, driven by the client: poll, and wait longer while nothing happens. The benchmark client waits 1 s
after a poll that returned events and doubles the gap up to 10 s after each empty one. It needs no open connection and survives any
proxy, which is why it is the last rung of the fallback ladder (WebSocket, then long-poll, then adaptive polling).

> **Client side (slice S45, from the spec; verify at merge).** The web app's realtime client follows that ladder, de-duplicates by
> `seq`, resumes from the last `seq` after a switch, and shows "Live updates paused, reconnecting" while it is offline. It is built on
> another branch; this file describes the server and the benchmark client, which exist on this base.

## Measured comparison

Measured on 2026-11-03 with `scripts/bench_realtime.py` against one API process (`uvicorn`, one worker) and the arq worker on a
12-core, 31 GB Linux box that other builds were using at the same time: the 1-minute load average was 6.5 to 7.5 throughout, so
treat the absolute numbers as indicative and the ratios as the result. One run per row, seed 1, nothing averaged. Clients are
20 seeded users' sessions of one organization (a user holds at most 5 streams and the demo organization has 4 users, so 20 is the
largest N the seed can host); a producer flips the organization's plan, which emits one `plan.changed` event each, and every client
times every event from just before the producer's request. Latency is therefore end to end: commit, relay, wake-up, read, frame
(or poll), client. "Got" is events delivered to clients out of events x clients: nothing was lost on any transport.

**Busy: 20 clients, an event about every 2 s (30 events)**

| Transport | p50 latency | p95 | max | HTTP requests/min | frames/min | bytes/min on the wire | API CPU (of one core) | API RSS |
|---|---|---|---|---|---|---|---|---|
| WebSocket | 556 ms | 1128 ms | 1409 ms | 0 | 608 | 181 kB | 10.8 % | 137 MB |
| Long-poll (`wait=25`) | 298 ms | 533 ms | 648 ms | 575 | 0 | 407 kB | 15.3 % | 137 MB |
| Adaptive polling (1 s to 10 s) | 1035 ms | 2013 ms | 3132 ms | 829 | 0 | 529 kB | 12.4 % | 137 MB |

**Quiet: 20 clients, an event about every 20 s (6 events, 2 minutes)**

| Transport | p50 latency | p95 | max | HTTP requests/min | frames/min | bytes/min on the wire | API CPU (of one core) | API RSS |
|---|---|---|---|---|---|---|---|---|
| WebSocket | 852 ms | 1163 ms | 1191 ms | 0 | 60 | 17.8 kB | 2.9 % | 137 MB |
| Long-poll (`wait=25`) | 313 ms | 1165 ms | 1358 ms | 83 | 0 | 54 kB | 9.7 % | 137 MB |
| Adaptive polling (1 s to 10 s) | 6868 ms | 7664 ms | 8054 ms | 257 | 0 | 139 kB | 3.7 % | 137 MB |

**Busy with 10 clients** (an event about every 2 s, 30 events): WebSocket 760 ms p50 / 5.2 % CPU / 92 kB/min; long-poll 301 ms /
8.3 % / 204 kB/min and 289 requests/min; adaptive 1053 ms / 7.4 % / 260 kB/min and 406 requests/min.

Wire bytes are estimated from HTTP header and body sizes and WebSocket frame sizes: no TCP, IP or TLS overhead, and no WebSocket
pings. A WebSocket also costs a ticket request and a handshake once per 5-minute socket life (about 8 requests a minute across 20
clients), which the table leaves out. CPU is the API process's user plus system time over the window, divided by wall time; the
worker is not included. Memory is the API's resident size at the end of each trial and did not move.

**What it shows**

- **WebSocket is the cheapest in requests and bytes, and the cost of an idle client is near zero:** 18 kB/min for 20 idle-ish clients
  against 54 kB (long-poll) and 139 kB (adaptive), and no request at all. Per-event cost is a page read and a credential re-check
  per client, so API CPU rises with event rate (2.9 % quiet, 10.8 % busy).
- **WebSocket latency is not the lowest, and the reason is the relay.** A socket is woken by Redis pub/sub after the outbox relay
  publishes, and the relay is an arq cron that runs once a second, so an event waits 0 to 1 s (0.5 s on average) before the wake-up.
  Long-poll reads the outbox directly every 0.5 s and skips the relay, so it sees an event after 0 to 0.5 s. p50 of 556 and 760 ms
  for the socket against about 300 ms for long-poll fits those two cadences plus the read. Waking sockets at commit instead of at
  the relay would remove most of the gap; it is not done, because the relay is the one place that publishes after a batch commits.
- **Long-poll is the lowest latency and a middling cost:** one request per client per event or per 25 s, but each waiting poll
  re-queries the database every 0.5 s, which is why its CPU is the highest when quiet (9.7 %).
- **Adaptive polling is cheap to hold and slow to see:** after an idle stretch the gap has grown to 10 s, so in the quiet run an
  event waited 6.9 s at p50; in the busy run the 1 s floor keeps it near 1 s. It costs the most bytes, since every empty poll is a
  full request with headers (about 540 bytes each).

That is why the ladder is WebSocket first (cheapest, one open connection), long-poll when sockets are blocked (proxies, no
`Upgrade`), and adaptive polling last (works anywhere, slowest to notice). All three read the same stream by `seq`, so a client can
switch rung without a gap.

**Load check (API alone, no realtime).** 10 and then 50 clients each read `GET /api/v1/decision-requests?limit=20` back to back for
30 s against the same single process, with the 9 seeded decisions of Acme Retail:

| Clients | Requests/s | p50 | p95 | p99 | max | Errors | API CPU (of one core) |
|---|---|---|---|---|---|---|---|
| 10 | 144 | 66 ms | 97 ms | 124 ms | 186 ms | 0 | 97 % |
| 50 | 132 | 325 ms | 717 ms | 920 ms | 1867 ms | 0 | 92 % |

One uvicorn worker saturates one core at about 130 to 145 list reads a second and then queues: throughput stays flat and latency
grows with the number of clients. The first run of this check, made while the machine's disk was full, was discarded and repeated.
Scaling out means more API processes behind the load balancer; nothing in the realtime path pins a client to an instance (the stream
cap and tickets live in Redis).


## Reproduce

Needs the API with `DEMO_MODE=true` on seeded data and the worker running, both set up as in the README quick start (the worker
needs `APP_WORKER_PASSWORD` at migrate time; the wake-ups come from its relay). The bench refuses a `--base` that is not this
machine unless `--allow-remote` is given, because it signs in as demo users and flips a plan. Then:

```bash
uv run --project backend python scripts/bench_realtime.py --base http://localhost:8000 --pid <api pid> --clients 20 --events 30 --interval 2
uv run --project backend python scripts/bench_realtime.py --base http://localhost:8000 --pid <api pid> --clients 50 --load 30
```

`--smoke` runs 10 clients and 3 events; `backend/tests/scripts/test_bench_realtime.py` runs it against a throwaway database in the
test suite. `--transports ws,longpoll` picks transports, `--json` prints the raw numbers.
