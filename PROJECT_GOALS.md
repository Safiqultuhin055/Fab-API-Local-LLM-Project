# Local Ollama AI Gateway — Project Goals & Execution Plan

> Source analysis of `ApiPrompt.txt`, `AI_Gateway_Prompt_Library.md`, and `api prototype1.png`,
> distilled into professional goals and a concrete, step-by-step build plan.
> Owner: ai@dbl-digital.com · Started: 2026-06-24

---

## 1. Analysis (what the inputs actually ask for)

The three inputs describe one product at **two conflicting scopes**:

| Track | Source | Tables | Auth | Frontend |
|---|---|---|---|---|
| **Prototype** | diagram (`api prototype1.png`) | `api_keys`, `request_logs`, `settings` | static admin API key | optional |
| **Enterprise** | `ApiPrompt.txt` spec | 15 tables, full RBAC | JWT access+refresh + RBAC | React/Jinja admin |

The Prompt Library (`AI_Gateway_Prompt_Library.md`) already resolves this: **deliver a runnable
prototype first, then layer enterprise features.** This plan adopts that resolution.

### Key correctness/security requirements pulled from the inputs
- API keys **never** stored raw. Store HMAC-SHA256 hash + short non-secret display prefix
  (`AI-7f3e…`). Full key shown **once** at creation. (Corrects the diagram's raw `api_key` column.)
- Passwords hashed with Argon2id (enterprise track).
- Async-first everywhere I/O happens (`httpx.AsyncClient`, async SQLAlchemy 2.0, SSE streaming).
- Rate limiting per-key and per-IP with tiers (Free 100/day, Pro 10k/day, Enterprise unlimited).
- Request/response logging is privacy-aware (truncatable, redactable, retention policy).
- Consistent error envelope, correlation IDs, structured JSON logs, `/health` vs `/ready`.
- One source of truth for schema (ORM models == migrations).

---

## 2. Professional goals (definition of success)

1. **Runnable in one command.** `docker compose up` (or `uvicorn` for dev) brings up a working
   gateway in front of a local Ollama server, with zero external AI provider.
2. **Secure by default.** No raw secrets stored or logged; hashed API keys; CORS allow-list;
   security headers; input validation on every endpoint.
3. **Production-shaped.** Clean architecture (routers → services → repositories → models),
   fully typed, async, observable, consistent error contract.
4. **Drop-in for clients.** REST + SSE endpoints consumable from Oracle APEX, PHP/Laravel,
   ASP.NET, Python, React/Vue, mobile — exactly the request/response shapes from the diagram.
5. **Extensible to enterprise.** Schema and layering leave room for users/RBAC/JWT without rework.

---

## 3. Phased execution plan (this repo)

**Track A — Prototype (build now, runnable):**
- P1 — Repo structure, pinned deps, `.env.example`, README.
- P2 — Data layer: async SQLAlchemy models (`api_keys` hashed, `request_logs`, `settings`) + DB engine + auto-create.
- P3 — Pydantic v2 schemas + single error envelope.
- P4 — Core: config (pydantic-settings), JSON logging w/ correlation IDs, HMAC key + password utils.
- P5 — API-key auth dependency (hash lookup) + rate limiter (DB/in-memory now, Redis-ready).
- P6 — Ollama service (httpx async): completion, token streaming, model list/status, timeouts, error mapping.
- P7 — Public routers: `POST /api/v1/chat`, `POST /api/v1/chat/stream` (SSE), `GET /api/v1/models`,
  `GET /api/v1/models/status`, `GET /api/v1/health`, `GET /api/v1/ready`.
- P8 — Admin routers: `POST/GET/DELETE /admin/api-keys`, `GET /admin/logs` (paginated/filtered), `GET /admin/stats`.
- P9 — Request-logging middleware (privacy-aware) + security headers + CORS + correlation middleware.
- P10 — App wiring, lifespan, OpenAPI metadata.
- P11 — Docker, docker-compose (gateway + Redis + optional SQL Server + Nginx note), smoke test.

**Track B — Enterprise (later, planned not built now):**
- Users/roles/permissions tables, Argon2id passwords, JWT access+refresh w/ rotation+reuse detection,
  RBAC dependencies, audit/error logs, full analytics, 90%+ test suite, CI/CD, Mermaid ER/arch docs.

---

## 4. Tech decisions (defaults chosen so it runs immediately)
- **DB:** async SQLAlchemy 2.0 on Microsoft SQL Server only (`aioodbc`, ODBC Driver 18),
  configured via `MSSQL_*`. No SQLite fallback — the app refuses to start without a reachable SQL Server.
- **Admin auth:** static admin API key (`ADMIN_API_KEY`) per diagram. JWT users deferred to Track B.
- **Streaming:** SSE for `/chat/stream`.
- **Rate limiting:** in-memory/DB sliding window now, Redis backend pluggable via `REDIS_URL`.
- **Key format:** `AI-` + 32 url-safe chars. Store HMAC-SHA256(hash) + 8-char prefix only.

---

## 5. Definition of Done (prototype)
- [ ] `uvicorn app.main:app` starts clean; `/api/v1/health` returns 200.
- [ ] Admin creates a key (full key returned once); DB stores only hash+prefix.
- [ ] `POST /api/v1/chat` with `X-API-KEY` proxies to Ollama and logs the request.
- [ ] `POST /api/v1/chat/stream` streams tokens via SSE.
- [ ] Rate limit returns 429 + `Retry-After` past tier limit.
- [ ] No raw key/password in DB or logs; error envelope consistent across endpoints.
