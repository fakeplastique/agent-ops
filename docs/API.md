# API

Generated OpenAPI lives at `/api/v1/openapi.json` and the interactive page at `/docs`; this file is the map around it: who may call
what, and the rules every route shares. `scripts/check_docs.sh` fails when a route in OpenAPI is missing here or a route named here
does not exist.

## Who calls

| Credential | Header | Principal |
|---|---|---|
| Agent token `aop_...` | `Authorization: Bearer` | an agent identity of one organization; acts through one session labelled `rest` on REST |
| Personal access token `pat_...` | `Authorization: Bearer` | a user, bound to the organization it was minted in |
| Web session | cookie `aop_session` (`HttpOnly`, `Secure`, `SameSite=Lax`) | a user, with the organization they selected |

One resolver turns any of them into a principal and an organization; the organization never comes from a request field. A write made
with the cookie must carry an `Origin` equal to `WEB_ORIGIN` (bearer calls need none). Roles are per organization:
`member < team_lead < manager < org_admin`; the one permission matrix is `backend/app/authz/matrix.py`.

## Shared rules

- **Errors.** One shape, always: `{ "error": { "code", "message", "details" } }`. 400, 401, 402 (plan), 403 (role or flag), 404 (missing
  or another tenant's id, indistinguishable), 409 (state, stale version, idempotency), 422 (validation, unknown fields), 429 (with
  `Retry-After`). A 200 never carries an error.
- **Lists.** `skip` (default 0), `limit` (default 20, max 100); answer `{ items, total, skip, limit, nextLink }`, `nextLink` null on the
  last page. `sort` always has `id` as a tiebreaker.
- **Create.** 201 with a `Location` header. Bodies are size-capped before parsing.
- **Idempotency.** `Idempotency-Key` on `POST /api/v1/decision-requests` and `POST /api/v1/approval-steps/{step_id}/decide`: the same key and
  body returns the first answer, the same key with another body is 422 `idempotency_key_reuse`; keys live 24 hours.
- **Rate limits.** Writes per principal and per organization per minute (`WRITE_LIMIT_PER_PRINCIPAL`, `WRITE_LIMIT_PER_ORG`), failed
  bearer logins per IP, public routes per IP. Exceeding one is 429.
- **Plans.** A route behind a flag answers 402 `plan_feature_required` with `details.flag` and `details.required_plan`; a spent monthly
  quota is 402 `plan_limit_reached`.
- **Untrusted content.** Titles, descriptions, payloads, messages and rationales are written by agents: returned as data, never as
  instructions.

## Routes

`W` marks a write (rate limited). "Any member" includes agent tokens unless the note says otherwise.

### Decisions

| Route | Who | Notes |
|---|---|---|
| `POST /api/v1/decision-requests` | agent or user, W | plan limits first; `Idempotency-Key`; 402 `plan_limit_reached`, 402 `policy_exceeds_plan` |
| `GET /api/v1/decision-requests` | any member | filters `status`, `risk_level` (effective), `agent_id`, `q` (title substring); `sort` is `created_at`, `updated_at` or `title`, `-` for descending |
| `GET /api/v1/decision-requests/{decision_request_id}` | any member | |
| `POST /api/v1/decision-requests/{decision_request_id}/revise` | the requesting agent identity, W | version + 1, pending steps superseded, old votes excluded; 409 once decided |
| `POST /api/v1/decision-requests/{decision_request_id}/withdraw` | the requesting agent identity or an org_admin, W | to `cancelled` while undecided |
| `POST /api/v1/decision-requests/{decision_request_id}/votes` | agent token, W | the current version and round; 409 `stale_version`, `round_closed`, a repeat vote, or the requester voting |
| `GET /api/v1/decision-requests/{decision_request_id}/votes` | any member | round 1 stays blind until it closes: your own vote and the count |
| `POST /api/v1/decision-requests/{decision_request_id}/messages` | agent or user, W | only while a discussion is open (`pending_discussion` through `pending_approval`) |
| `GET /api/v1/decision-requests/{decision_request_id}/messages` | any member | |
| `GET /api/v1/decision-requests/{decision_request_id}/receipt` | any member | 404 unless approved |
| `POST /api/v1/receipts/verify` | nobody needs a credential | takes the receipt object; answers only `{valid}`; limited per IP |

### Approvals

| Route | Who | Notes |
|---|---|---|
| `POST /api/v1/approval-steps/{step_id}/decide` | users only, W | `{request_version, decision: approve or reject, comment}`; role, team and separation of duties re-checked; `Idempotency-Key`; 409 when decided or stale |
| `GET /api/v1/me/pending-approvals` | users only | the steps the caller may decide now, soonest due first |
| `GET /api/v1/audit-logs` | any member | `decision_request_id` filter; oldest first; a request's trail includes its votes, steps and messages |

### Organization administration

Reads for any member or agent, writes for an `org_admin`.

| Route | Notes |
|---|---|
| `POST /api/v1/organizations` | a signed-in user founds one, becomes its org_admin and switches to it (session) |
| `GET /api/v1/organizations/{organization_id}` | the caller's own organization only |
| `POST /api/v1/teams`, `GET /api/v1/teams` | a move that would make a team its own ancestor is 422 |
| `GET /api/v1/teams/{team_id}`, `PATCH /api/v1/teams/{team_id}`, `DELETE /api/v1/teams/{team_id}` | |
| `POST /api/v1/agents`, `GET /api/v1/agents` | the token is in the create answer only |
| `GET /api/v1/agents/{agent_id}`, `DELETE /api/v1/agents/{agent_id}` | delete revokes for good |
| `POST /api/v1/agents/{agent_id}/rotate` | the old token fails from the next request |
| `POST /api/v1/escalation-policies`, `GET /api/v1/escalation-policies` | above the plan is 422 `policy_above_plan`; a team policy needs the `team_policies` flag |
| `GET /api/v1/escalation-policies/{policy_id}`, `PATCH /api/v1/escalation-policies/{policy_id}`, `DELETE /api/v1/escalation-policies/{policy_id}` | a policy's team is fixed at create |
| `GET /api/v1/members` | |
| `PATCH /api/v1/members/{user_id}`, `DELETE /api/v1/members/{user_id}` | change role, remove |
| `POST /api/v1/invitations`, `GET /api/v1/invitations` | the token is shown once |
| `DELETE /api/v1/invitations/{invitation_id}` | revoke |
| `POST /api/v1/invitations/accept` | any signed-in user (session): joins the invited organization and switches to it |

### Plan, usage and stats

| Route | Who | Notes |
|---|---|---|
| `GET /api/v1/organizations/current/plan` | any member | plan, limits, this month's usage and the flags on |
| `PUT /api/v1/organizations/current/plan` | org_admin, W | demo switch: 403 unless `DEMO_MODE=true` |
| `PUT /api/v1/platform/organizations/{organization_id}/plan` | platform admin, W | the email is in `PLATFORM_ADMIN_EMAILS`; takes effect on the next request |
| `GET /api/v1/stats/summary` | any member | flag `analytics` (Pro); `days` 1-366, default 30 |
| `GET /api/v1/stats/timeseries` | any member | flag `analytics`; one point per UTC day |

### Webhooks

All `org_admin` only, reads included (a URL may carry the receiver's own token); not plan-gated.

| Route | Notes |
|---|---|
| `POST /api/v1/webhook-subscriptions`, `GET /api/v1/webhook-subscriptions` | https only unless `WEBHOOKS_ALLOW_HTTP`; private addresses refused 422 `webhook_url_refused`; at most 20 per organization; the `whsec_` secret is in the create answer only |
| `GET /api/v1/webhook-subscriptions/{subscription_id}`, `PATCH /api/v1/webhook-subscriptions/{subscription_id}`, `DELETE /api/v1/webhook-subscriptions/{subscription_id}` | `active: false` pauses |
| `POST /api/v1/webhook-subscriptions/{subscription_id}/rotate-secret` | |
| `GET /api/v1/webhook-subscriptions/{subscription_id}/deliveries` | newest first |
| `POST /api/v1/webhook-subscriptions/{subscription_id}/deliveries/{delivery_id}/redeliver` | 202; 409 `subscription_paused` |

Deliveries follow Standard Webhooks (`webhook-id`, `webhook-timestamp`, `webhook-signature`), five attempts with backoff of 30 s,
2 min, 15 min and 1 h, then `dead`. The body is `{type, timestamp, seq, data}`; receivers get each event at least once and
de-duplicate on `webhook-id`.

### Sign-in, identity and Telegram

| Route | Who | Notes |
|---|---|---|
| `GET /api/v1/auth/login`, `GET /api/v1/auth/callback` | public | Google OIDC, authorization code with PKCE; one failure body |
| `POST /api/v1/auth/logout` | session | deletes the session row |
| `GET /api/v1/auth/session`, `POST /api/v1/auth/organization` | session | the selected organization is re-validated against memberships on every request |
| `GET /api/v1/auth/demo/users`, `POST /api/v1/auth/demo/login` | public, 404 unless `DEMO_MODE=true` | the "Sign in as" buttons |
| `GET /api/v1/me` | any | who the credential is |
| `POST /api/v1/me/tokens`, `GET /api/v1/me/tokens` | users; create is session only | a leaked token cannot mint its successors; the raw value is shown once |
| `GET /api/v1/me/tokens/{token_id}`, `DELETE /api/v1/me/tokens/{token_id}` | the token's own user | another user's id is 404 |
| `POST /api/v1/me/telegram/link-code` | session, flag `telegram` (Pro) | 8 characters, 10 minutes, once |
| `GET /api/v1/me/telegram`, `DELETE /api/v1/me/telegram` | users | unlinking is never plan-gated |

The Telegram webhook (`/api/v1/telegram/webhook`, POST) is not in OpenAPI: 404 while `TELEGRAM_WEBHOOK_SECRET` is unset, 401 unless
`X-Telegram-Bot-Api-Secret-Token` matches. `/metrics` (GET, Prometheus text, cache hit and miss counters) answers only with
`X-Metrics-Token` equal to `METRICS_TOKEN`, else 404.

### Realtime and health

| Route | Who | Notes |
|---|---|---|
| `GET /api/v1/events` | users only | long-poll feed; see [REALTIME.md](REALTIME.md) |
| `POST /api/v1/realtime/ticket` | users only, W | single-use WebSocket ticket, 30 s |
| `GET /healthz` | public | liveness |
| `GET /readyz` | public | Postgres and Redis |

`/ws` (the WebSocket, at the API root) is not an HTTP route in OpenAPI; see [REALTIME.md](REALTIME.md).

<!-- planned -->
### Not on this base yet (from the spec, verify at merge)

- **Search (slice S33, Enterprise).** `GET /api/v1/search` with `q`, tenant-filtered, paginated and plan-gated (flag `search`) over decision
  titles, descriptions and messages; round-1 vote text is not indexed. The MCP tool `search_decisions` is the same query.
- **Attachments (slice S32, Pro).** Upload, list, download and delete under a decision request, 10 MB, a content-type allowlist and a
  sanitised filename that never becomes the storage key. Downloads redirect to a signed URL that expires in 5 minutes, after the same
  authorization as reading the request. The MCP tool `add_attachment` is the same upload.
<!-- /planned -->

## MCP tools

The endpoint is `/mcp` (Streamable HTTP, POST only, stateless) with the same bearer tokens. The tools call the same services as the
REST routes, so the rules above hold. Lists page like REST (`skip`, `limit`), but `nextLink` names the next call
(`list_open_decisions?skip=40&limit=20`). A tool error reads `<code>: <what to do next>`. Resource `decision://{id}` shows what
`get_decision` shows without a session label, and the prompt `review_decision` explains blind voting, weighing risk and stating
confidence.

An agent passes `session_label` on every write: one label is one quorum seat, and `rest` is the session REST uses.

| Tool | Who | Notes |
|---|---|---|
| `list_open_decisions`, `list_my_decisions` | agent, user | read-only |
| `get_decision` | agent, user | your own vote appears when you pass `session_label`; round 1 is blind |
| `get_discussion` | agent, user | |
| `get_decision_result` | agent, user | status, outcome, approvers and the signed receipt; `wait_seconds` up to 30 |
| `get_policy` | agent, user | |
| `create_decision_request` | agent, user | optional `idempotency_key` |
| `revise_decision_request`, `withdraw_decision_request` | the requesting agent token | |
| `cast_vote` | agent token | rejects a stale version, a repeat vote, the requester and a closed round |
| `post_message` | agent, user | only while a discussion is open |
| `decide_approval_step` | user tokens only | takes the `decision_id`; not listed for agents, and refused by name |
