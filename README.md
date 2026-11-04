<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Agent Ops: agents ask, humans sign off. A refund request waits at the team lead level after the agents' quorum approved it.">
</p>

<p align="center">
  <b>The audit-ready decision system for AI agents.</b><br />
  Agents ask, a quorum votes, humans sign off, and the approval is cryptographically signed.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#connect-an-mcp-client">Connect an agent</a> ·
  <a href="docs/API.md">API</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL_16-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL 16">
  <img src="https://img.shields.io/badge/MCP-000000?style=flat-square&logo=modelcontextprotocol&logoColor=white" alt="Model Context Protocol">
  <img src="https://img.shields.io/badge/Railway-0B0D0E?style=flat-square&logo=railway&logoColor=white" alt="Railway">
</p>

**Agent Ops is an audit-ready decision system for AI agents, with cryptographic approval.** Before an agent spends money, sends email or
changes data, it asks. Other agents and the right people weigh in, every step lands in an audit trail, and an approval ends in an
**HMAC-signed decision** that anyone can verify and that stops verifying if the request is edited afterwards. It works with the agents you
already use (Claude, ChatGPT, Copilot, your own); we don't build agents, we make them accountable.

## See it work

<p align="center">
  <img src="./assets/readme/inbox.png" width="100%" alt="The approval inbox: two requests wait on the signed-in org admin, with a countdown, the request's progress and Approve and Reject buttons.">
</p>

<table>
  <tr>
    <td width="50%"><img src="./assets/readme/decision.png" alt="A request's votes: three agent sessions approved blind, now waiting for the team lead."></td>
    <td width="50%"><img src="./assets/readme/receipt.png" alt="An auto-approved low-risk request with its signed decision and a Verify button."></td>
  </tr>
  <tr>
    <td><sub><b>Votes, then people.</b> Agents vote blind; anything that is not low-risk and unanimous goes to humans.</sub></td>
    <td><sub><b>A decision that proves itself.</b> Signed, verifiable by anyone, and broken by any later edit.</sub></td>
  </tr>
</table>

## How it works

1. **An agent asks.** It describes what it wants to do and why, through an MCP client or the REST API.
2. **Other agents review.** Independent agent sessions vote blind, then see each other's reasoning and discuss.
3. **Safe things go through.** If the risk is low and everyone agrees, it is approved automatically, with no one interrupted.
4. **Everything else goes to people.** A team lead, then a manager, then an org admin, each with a deadline. If someone is away, it
   moves up. Approvers can answer from the web app, the API or Telegram.
5. **The agent gets a signed decision.** Cryptographic proof of approval that the system carrying out the action can verify. Change
   anything after the fact and the signature stops verifying.

## What you get

- **Fewer interruptions.** Routine, low-risk requests clear themselves; people only see what needs judgment.
- **Audit-ready by design.** Roles, separation of duties, escalation rules and an append-only audit trail for every request, plus a
  cryptographically signed decision for every approval.
- **Live visibility.** An approval inbox, request details with votes and discussion, attachments, search and analytics, updated in real time.
- **Fits your stack.** An MCP server for agents, a REST API and webhooks for integrations, Telegram for approvals on the go.
- **Safe for many companies at once.** Each organization's data is fully isolated; another company's request simply doesn't exist for you.
- **Free and Pro plans** with usage limits and feature flags per organization.

<p align="center">
  <img src="./assets/readme/analytics.png" width="100%" alt="Analytics for the last 30 days: requests, auto-approval rate, and breakdowns by status and risk.">
</p>

## Try it

The fastest way is the demo: two sample companies (Fernwood Retail on Pro, Birch Studio on Free), a user for every role, agents, and
requests in every state. Follow [Quick start](#quick-start), open <http://localhost:3000> and choose "Sign in as".

| Want to know | Read |
|---|---|
| How it is built | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): layers, state machine, event flow |
| How to integrate | [`docs/API.md`](docs/API.md): every REST route and MCP tool |
| How live updates work | [`docs/REALTIME.md`](docs/REALTIME.md): WebSocket, long-poll and polling, measured |

---

*Technical setup follows: running it locally, connecting an agent, tests and configuration.*

## Quick start

Needs Docker, [uv](https://docs.astral.sh/uv/) and [pnpm](https://pnpm.io/) with Node 22. Run from the repo root; the defaults of
`app/config.py` match these commands, so no `.env` file is needed (the app reads the process environment, see
[Configuration](#configuration)).

```bash
# 1. Postgres 16 and Redis
docker compose up -d --wait postgres redis

# 2. Install, then migrate as the owner role (this also creates the runtime role app_rt and, with the password, lets app_worker log in)
uv sync --project backend
pnpm --dir frontend install
MIGRATE_DATABASE_URL=postgresql://app:app@localhost:5432/agent_ops APP_WORKER_PASSWORD=app_worker scripts/migrate.sh

# 3. Demo data: two organizations (Fernwood Retail on Pro, Birch Studio on Free), one user per role, agents, requests in every status.
#    The agent tokens and webhook secrets it prints are shown once.
uv run --project backend python scripts/seed.py

# 4. API (demo mode adds the "Sign in as" buttons and lets an org admin switch plan), worker, web
DEMO_MODE=true uv run --project backend uvicorn app.main:app --app-dir backend --port 8000
(cd backend && WORKER_DATABASE_URL=postgresql://app_worker:app_worker@localhost:5432/agent_ops \
  uv run arq app.worker.settings.WorkerSettings)                 # outbox relay, timeouts, webhooks, Telegram, analytics
pnpm --dir frontend dev                                          # http://localhost:3000
```

Open <http://localhost:3000>, choose a demo user. The API docs are at <http://localhost:8000/docs>, the OpenAPI document at
`/api/v1/openapi.json`, liveness at `/healthz` and readiness (Postgres, Redis) at `/readyz`.

The worker is what moves events from the outbox to webhooks, Telegram, analytics and the WebSocket wake-ups, and what times out
overdue approval steps. Without it the API still answers; live updates arrive on a 5 s fallback and nothing escalates.

`docker compose --profile prod up --build` builds and runs the production images (`api`, `worker`, `web`) on top of the two services. It
does not migrate: run step 2 first, with `APP_WORKER_PASSWORD=app_worker`, or the worker cannot log in (the `app_worker` role is
`NOLOGIN` until a password is set).

## Connect an MCP client

The endpoint is Streamable HTTP at `/mcp` (POST only, stateless). Authenticate with an **agent token** (`aop_...`, printed once by
`scripts/seed.py` or returned by `POST /api/v1/agents`) or a **personal access token** (`pat_...`, from `POST /api/v1/me/tokens`).
Copy-paste config for a client that reads `mcpServers`:

```json
{
  "mcpServers": {
    "agent-ops": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer aop_REPLACE_WITH_YOUR_AGENT_TOKEN" }
    }
  }
}
```

Claude Code: `claude mcp add --transport http agent-ops http://localhost:8000/mcp --header "Authorization: Bearer aop_..."`.

An agent passes its own `session_label` on every write tool: one label is one seat in the quorum. Behind a public hostname, set
`MCP_ALLOWED_HOSTS` (Railway's `RAILWAY_PUBLIC_DOMAIN` is added by itself), or the transport answers 421. The tools are listed in
[`docs/API.md`](docs/API.md#mcp-tools).

## Test

| What | Command |
|---|---|
| Backend lint, format, types | `uv run --project backend ruff check backend && uv run --project backend ruff format --check backend && uv run --project backend pyright --project backend/pyproject.toml` |
| Backend tests (need Postgres and Redis up) | `uv run --project backend pytest backend/tests` |
| Frontend lint, types, unit tests | `pnpm --dir frontend lint && pnpm --dir frontend typecheck && pnpm --dir frontend test` |
| Docs drift check | `scripts/check_docs.sh` |
| Realtime benchmark | see [`docs/REALTIME.md`](docs/REALTIME.md#reproduce) |
| Everything CI runs | `./scripts/gate.sh` |

`GATE_LANE=<n> ./scripts/gate.sh` gives a second checkout its own ports, databases and Redis db so two gates can run at once.
Backend tests wipe their database: point a parallel run elsewhere with `TEST_DB_NAME` and `TEST_REDIS_URL`.

## Configuration

Environment variables only; `.env.example` lists the dev defaults. Hosted runs (`RAILWAY_ENVIRONMENT` set) refuse the public dev keys.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `app_rt` on localhost | the API and worker's runtime role; non-owner, so RLS applies. The API refuses to start as an owner or `BYPASSRLS` role |
| `MIGRATE_DATABASE_URL` | `app` on localhost | owner role, used only by `scripts/migrate.sh` |
| `WORKER_DATABASE_URL` | `app_worker` on localhost | the worker's scan identity (ids of due work only); the role is `NOLOGIN` until `scripts/migrate.sh` runs with `APP_WORKER_PASSWORD` |
| `REDIS_URL` | `redis://localhost:6379/0` | queue, cache, rate limits, realtime tickets and wake-ups |
| `WEB_ORIGIN` | `http://localhost:3000` | the web app's origin: OAuth redirect, cookie, and the exact `Origin` writes and WebSockets must carry |
| `OIDC_ISSUER` | `https://accounts.google.com` | login provider |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | empty | Google OAuth client; empty means login is unavailable (demo mode still works) |
| `RECEIPT_KEYS` | dev key | `kid:secret[,kid:secret]`, the last signs and all verify; required on Railway |
| `WEBHOOK_SECRET_KEYS` | dev key | `kid:base64(32 bytes)[,...]`, AES-256-GCM for stored webhook secrets; required on Railway |
| `WEBHOOKS_ALLOW_HTTP` | `false` | allow plain-http webhook targets (local dev only; refused on Railway) |
| `MCP_ALLOWED_HOSTS` | empty | comma-separated public hostnames the MCP endpoint answers on |
| `DEMO_MODE` | `false` | demo sign-in routes, and org admins may switch their own plan |
| `PLATFORM_ADMIN_EMAILS` | empty | comma-separated; may change any organization's plan |
| `METRICS_TOKEN` | empty | sent as `X-Metrics-Token` to read `GET /metrics`; empty means the route answers 404 |
| `WRITE_LIMIT_PER_PRINCIPAL`, `WRITE_LIMIT_PER_ORG` | `60`, `300` | writes per minute |
| `AUTH_FAILURES_PER_MINUTE` | `20` | failed bearer logins per IP per minute |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_BOT_USERNAME` | empty | no token means nothing is sent; no secret means the webhook answers 404 |
| `API_ORIGIN` | `http://localhost:8000` | read at `next build`: where the web app's rewrites send `/api` |
| `APP_RT_PASSWORD` | dev password | replaces the `app_rt` password in `scripts/migrate.sh`; required on Railway |
| `APP_WORKER_PASSWORD` | unset | when set, `scripts/migrate.sh` lets `app_worker` log in with it; the worker service needs it in `WORKER_DATABASE_URL` |
| `FILE_STORAGE` | `local` | `local` or `s3` attachment storage |
| `FILE_LOCAL_ROOT` | `<system temp>/agent-ops-files` | where the local adapter keeps files; set a persistent path outside dev |
| `FILE_URL_SECRET` | dev key | HMAC key for local signed download URLs; on Railway with `local`, 32+ characters and not the dev value |
| `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL` | empty | S3-compatible endpoint (internal) and the host presigned URLs use |
| `S3_BUCKET`, `S3_REGION` | empty | bucket and region for the `s3` adapter |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | empty | credentials for the `s3` adapter (optional dev store: `docker compose --profile storage up -d garage`, then `scripts/garage-init.sh`) |
