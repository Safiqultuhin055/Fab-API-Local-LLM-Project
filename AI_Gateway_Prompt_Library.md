# AI Gateway Platform — Professional Prompt Library

A re-engineered prompt set for building the **Local Ollama AI Gateway** described in your
diagram and `ApiPrompt.txt`. It replaces the single "do everything at once" prompt with a
reusable **master context** plus **phased execution prompts**, each with a clear objective,
dependencies, deliverables, and a definition of done.

---

## 1. Diagnosis — why the original prompt underperforms

Your original prompt is thorough on *scope* but has the failure modes typical of a
"kitchen-sink" prompt:

| Problem | Effect on output | Fix applied here |
|---|---|---|
| Asks for 17 large deliverables in one response | The model runs out of room, emits stubs, `# TODO`, and "rest is similar" placeholders | Split into 13 focused phases, one per response |
| Two conflicting scopes | The diagram shows 3 tables + admin-key auth; the text asks for 15 tables + RBAC + refresh tokens. The model guesses | Phase 0 reconciles scope explicitly (prototype track vs enterprise track) |
| No interaction protocol | Model assumes instead of asking; you can't course-correct | Master prompt mandates clarifying questions first, one phase per turn |
| No definition of done / acceptance criteria | "Done" is undefined, quality drifts | Every phase ends with a checklist the output must satisfy |
| Security stated as keywords, not requirements | API keys stored raw, passwords unhashed, logs leak PII | Phase prompts spell out the *correct* implementation (hashing, key prefixes, retention) |
| No output-format contract | Files without paths, missing dependency notes | Master prompt fixes a file/output format |

---

## 2. Professional upgrades baked into the prompts

These are deliberately specified so the AI builds them correctly the first time:

- **API keys are never stored in plaintext.** Store an HMAC-SHA-256 hash + a short
  non-secret display prefix (e.g. `AI-7f3e…`). The full key is shown **once** at creation.
  (Your diagram's `api_keys.api_key NVARCHAR(255) UNIQUE` is corrected to a hashed column.)
- **Passwords hashed with Argon2id** (or bcrypt) — never reversible.
- **Secrets via environment / secret store**, committed only as `.env.example`.
- **Prompt/response logging is privacy-aware** — configurable, truncatable, redactable, with a
  retention policy, because `request_logs.prompt/response` can hold PII.
- **Async-first throughout** — `httpx.AsyncClient` to Ollama, async SQLAlchemy, SSE streaming.
- **Rate limiting in Redis** with an atomic sliding-window/token-bucket (Lua), per-key and per-IP,
  with a DB fallback.
- **One source of truth for the schema** — Alembic migrations and SQLAlchemy models must agree;
  the raw `init_db.sql` is generated *from* them or kept in lockstep.
- **Observability** — correlation/request IDs, structured JSON logs, `/health` vs `/ready`.
- **Pinned versions, consistent error envelope, pagination, CORS allow-list (not `*`),
  security headers.**

---

## 3. How to use this library

1. Paste **the Master System Prompt (§4)** once at the start of the conversation.
2. The model asks clarifying questions (Phase 0). Answer them.
3. Send **one phase prompt at a time** (§5). Review the output, then send the next.
4. Use the **Review prompt (§6)** whenever you want a quality gate, and the **Continue prompt**
   if a response gets cut off.

> Works with Claude, GPT-class models, or coding agents (Cursor/Windsurf/Claude Code). For
> agents that edit a repo directly, tell it to **write files to disk** rather than print them.

---

## 4. Master System Prompt (paste once)

```text
ROLE
You are a principal software architect and senior Python engineer building a production
Local AI Gateway. You also wear the hats of SQL Server DBA, DevOps engineer, and application
security engineer. You write enterprise-grade, fully typed, async-first, documented code.

PRODUCT
A self-hosted REST gateway in front of a local Ollama server (default http://localhost:11434,
models: llama3.1, mistral, deepseek-r1, gemma). It is consumed by Oracle APEX, PHP/Laravel,
ASP.NET, Python, React/Vue, and mobile clients. No external AI provider is ever required.

TECH STACK (pin these)
- Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, Alembic, Uvicorn/Gunicorn
- Microsoft SQL Server 2019+ (ODBC Driver 18; async via aioodbc/pyodbc+greenlet)
- Ollama via httpx.AsyncClient
- Redis for rate limiting, caching, token blacklist
- Docker, Docker Compose, Nginx reverse proxy; runs on Linux and Windows Server

ARCHITECTURE & STANDARDS
Clean architecture with clear layers: routers/api -> services -> repositories -> models.
Apply SOLID, dependency injection (FastAPI Depends), and DDD where it earns its keep.
Async everywhere I/O happens. Consistent error envelope. Typed end to end (mypy-clean intent).

SECURITY (non-negotiable)
- API keys: generate "AI-" + 32 url-safe chars; store ONLY an HMAC-SHA256 hash plus a short
  display prefix; reveal the full key once at creation. Never log or store raw keys.
- Passwords: Argon2id (fallback bcrypt). Never store or log plaintext.
- JWT access + refresh tokens; refresh-token rotation + reuse detection; logout via blacklist.
- RBAC with roles: Super Admin, Admin, Developer, User, API Consumer.
- Secrets from env/secret store; commit only .env.example. Validate all input with Pydantic.
- CORS allow-list (no wildcard with credentials), security headers, trusted hosts, audit log.
- Treat request prompts/responses as potential PII: logging is configurable, truncatable,
  redactable, and governed by a retention policy.

INTERACTION PROTOCOL  (important)
1. Before writing any code, run PHASE 0: ask me your clarifying questions and propose the
   key architecture decisions (as short ADRs). Wait for my answers.
2. Then deliver work ONE PHASE PER RESPONSE, in the order I send phases. Do not jump ahead.
3. Produce COMPLETE, runnable files — no pseudocode, no "TODO", no "the rest is similar".
4. If a phase is too large for one response, stop at a file boundary and end with
   "CONTINUE?" so I can ask you to resume. Never truncate mid-file.
5. End every phase response with:
   - "Files in this phase:" (each with its full path relative to repo root)
   - "New dependencies:" (exact versions)
   - "Assumptions / decisions:" (anything you chose)
   - "Definition of Done check:" (tick the acceptance criteria I gave for that phase)
   - "Phase N complete — ready for Phase N+1?"
6. Proactively flag any security, privacy, performance, or correctness risk you notice.

OUTPUT FORMAT
For each file use a fenced code block preceded by its path, e.g.:

  `app/services/ollama_service.py`
  ```python
  ...full file...
  ```

Acknowledge this brief, then begin PHASE 0.
```

---

## 5. Phase prompts (send one at a time)

### Phase 0 — Discovery & architecture decisions
```text
PHASE 0 — DISCOVERY.
Reconcile the two scopes I have:
(A) PROTOTYPE (from the diagram): tables api_keys, request_logs, settings; admin endpoints
    protected by an admin API key; single "ai_gateway" package; runnable quickly.
(B) ENTERPRISE (from the spec): users, roles, permissions, user_roles, api_keys,
    api_key_usage, models, request_logs, audit_logs, rate_limits, system_settings,
    notifications, sessions, refresh_tokens, error_logs; full JWT+RBAC; repositories/DDD.

Recommend a path that delivers a runnable prototype first, then layers in enterprise
features, and tell me which phases build which.

Then ask me the decisions you need, including at minimum:
- Admin auth: admin JWT user vs static admin API key (diagram shows "API Key (Admin)")?
- Key ownership model: free-form owner_name (prototype) vs user_id FK (enterprise)?
- Admin frontend: React Admin or FastAPI+Jinja? (it's optional)
- Streaming transport for /v1/chat/stream: SSE or chunked transfer?
- SQL Server: containerized or external? OS target (Linux/Windows)? async driver preference?
- Rate-limit windows and tiers (Free 100/day, Pro 10k/day, Enterprise unlimited) — confirm.
- Log retention period and whether full prompt/response bodies may be stored (privacy).
- Ollama: which models must be preloaded; host/port; timeout/streaming behavior.

Output: a one-page solution overview, a numbered phase plan mapped to my 17 deliverables,
and the open questions above. Do NOT write implementation code yet.
```

### Phase 1 — Architecture & project structure
```text
PHASE 1 — SOLUTION ARCHITECTURE + STRUCTURE.
Deliver: (1) a component/architecture description and request lifecycle for both /v1/chat
and an admin call; (2) 3–6 short ADRs for the decisions we settled in Phase 0; (3) the
complete repo folder structure (clean architecture: api/core/database/models/schemas/
repositories/services/middlewares/security/utils/admin/tests/migrations/docker/docs) with a
one-line purpose per folder; (4) pyproject/requirements with pinned versions and a README skeleton.
DoD: structure compiles conceptually, every folder has a purpose, versions pinned, no code stubs needed yet.
```

### Phase 2 — Data layer (SQL Server schema, Alembic, SQLAlchemy 2.0 models)
```text
PHASE 2 — DATA LAYER.
Deliver SQL Server 2019+ DDL AND the matching async SQLAlchemy 2.0 ORM models (Mapped[],
mapped_column) for the agreed table set, plus the Alembic baseline migration.
Requirements for every table: PK, FKs, unique + check constraints, indexes, soft delete
(is_deleted/deleted_at), created_by/created_date/updated_by/updated_date.
Correct the api_keys table: store key_hash (HMAC) + key_prefix + status + rate_limit +
last_used; NEVER a raw key column. request_logs: include token counts, response_time_ms,
ip_address, user_agent, model, and make body storage retention-aware.
The DDL and ORM models must be the same schema. Provide the Alembic env.py wiring for async.
DoD: migration runs clean on SQL Server; ORM == DDL; constraints/indexes present; keys hashed.
```

### Phase 3 — Pydantic v2 schemas + core config + secret management
```text
PHASE 3 — SCHEMAS + CORE.
Deliver Pydantic v2 models for every request/response (auth, users, api-keys, chat, models,
logs, analytics) with validation, examples, and a single consistent error envelope. Deliver
core config via pydantic-settings reading from env (.env.example included), structured
logging setup (JSON, correlation IDs), and a secrets/HMAC utility.
DoD: schemas validate sample payloads from the diagram; settings load from env only; no secrets in code.
```

### Phase 4 — Security foundation
```text
PHASE 4 — SECURITY MODULE.
Deliver: password hashing (Argon2id), JWT access+refresh issuance/validation with refresh
rotation and reuse detection, RBAC dependencies/decorators for the five roles, API-key
verification dependency (hash lookup), request-validation + security-headers + trusted-host +
CORS middleware, and audit-logging hooks. Include SQL-injection and XSS defenses as
testable, explicit code paths.
DoD: a protected route rejects bad/expired/blacklisted tokens and unknown keys; roles enforced; unit-testable.
```

### Phase 5 — Authentication module (endpoints)
```text
PHASE 5 — AUTH ENDPOINTS.
Deliver routers + services + repositories for: login, logout (blacklist), refresh, password
reset, change password, and user profile. Wire to Phase 4 primitives and Phase 2 models.
DoD: full happy-path + failure-path behavior; refresh rotation works; logout invalidates tokens.
```

### Phase 6 — User & API-key management + rate limiting
```text
PHASE 6 — USERS, API KEYS, RATE LIMITING.
Deliver user CRUD + activate/deactivate + role assignment; API-key create/regenerate/disable/
expire (returning the raw key only on create), per-key usage tracking, and the Redis rate
limiter (atomic sliding-window via Lua) enforcing Free/Pro/Enterprise tiers per key and per IP,
with a DB fallback and correct 429 + Retry-After responses.
DoD: limits enforced under concurrency; usage recorded; disabled/expired keys rejected.
```

### Phase 7 — Ollama integration service
```text
PHASE 7 — OLLAMA SERVICE.
Deliver an async Ollama client service (httpx.AsyncClient) supporting completion and token
streaming, model listing/status, timeouts, retries, error mapping, and token/latency capture.
Make host/port/timeouts configurable. No web framework logic in this layer.
DoD: handles Ollama down/slow/model-missing gracefully; streams tokens; returns timing+tokens.
```

### Phase 8 — REST APIs (chat, stream, models, health)
```text
PHASE 8 — PUBLIC REST APIS.
Deliver: POST /api/v1/chat, POST /api/v1/chat/stream (using the agreed transport),
GET /api/v1/models, GET /api/v1/models/status, GET /api/v1/health (no auth) and a separate
/ready that checks DB+Redis+Ollama. All chat routes require API-key auth + rate limiting and
emit the standard success/response envelope from the diagram.
DoD: matches the documented request/response shapes; streaming works; health vs ready separated.
```

### Phase 9 — Logging, analytics & admin backend
```text
PHASE 9 — LOGGING + ANALYTICS + ADMIN.
Deliver request-logging middleware persisting the agreed fields (privacy-aware), the
audit/error log writers, admin endpoints (POST/GET/DELETE /admin/api-keys, GET /admin/logs
with pagination+filters, GET /admin/stats), and analytics aggregations powering the dashboard
cards (total/active users, active keys, total/daily/monthly requests, errors, avg response
time) and charts (request trends, model usage, user activity, error analysis).
DoD: logs written without leaking secrets; admin routes authorized; stats queries indexed/paginated.
```

### Phase 10 — DevOps (Docker, Compose, Nginx, CI/CD, backup)
```text
PHASE 10 — DEVOPS.
Deliver a multi-stage Dockerfile, docker-compose for gateway + SQL Server + Redis + Nginx
(+ a note on running Ollama on host vs container), an Nginx reverse-proxy config (TLS-ready,
streaming-friendly buffering, security headers), .env.example, a SQL Server backup strategy,
and a CI/CD pipeline (lint, type-check, test, coverage gate, build, push).
DoD: `docker compose up` brings the stack up; Nginx proxies and supports streaming; CI gates on coverage.
```

### Phase 11 — Tests (target 90%+)
```text
PHASE 11 — TESTS.
Deliver pytest suites: unit (services/security/rate-limit), integration (DB + Redis),
API tests (httpx AsyncClient against the app), and a load-test script. Provide fixtures/
factories and the coverage configuration targeting 90%+. Mock or containerize Ollama.
DoD: tests run green locally and in CI; coverage >=90%; auth, rate-limit, and streaming covered.
```

### Phase 12 — Documentation & deployment guide
```text
PHASE 12 — DOCS.
Deliver: an ER diagram (Mermaid), an architecture diagram (Mermaid), API documentation +
Swagger/OpenAPI config, a database design document, a user manual, and a production
deployment guide for both Linux and Windows Server (incl. ODBC driver, Ollama setup,
TLS, backups, scaling, and a security checklist).
DoD: a new engineer can stand up prod from the guide alone; diagrams match the built schema/code.
```

---

## 6. Utility prompts

### Quality-gate / review prompt
```text
REVIEW the code from Phase N as a senior reviewer and security engineer. Check: no raw API
keys/passwords stored or logged; async correctness (no blocking calls in async paths); input
validation; consistent error envelope; proper status codes; injection/XSS defenses; missing
indexes; and any "TODO"/stub. List findings as Critical/High/Medium with the exact file+line
and a concrete fix. Do not rewrite everything — give targeted patches.
```

### Continue prompt (when a response is cut off)
```text
You stopped mid-phase. Resume EXACTLY where you left off at the next file boundary. Do not
repeat files already delivered; just list their paths as "already delivered". Continue until
the phase's Definition of Done is met, then give the phase-completion summary.
```

### Scope-change prompt
```text
We are changing one decision: <state the change>. Tell me which delivered files this affects,
give the minimal patches to each, and update the affected ADR. Don't regenerate unaffected files.
```

---

## 7. Prompt-engineering principles applied (so you can adapt these)

1. **Separate context from tasks.** One durable system prompt; small task prompts per phase.
2. **Make scope explicit; never let the model guess** between two conflicting specs.
3. **Define "done" before asking for work** — acceptance criteria beat adjectives like
   "enterprise-grade."
4. **Budget the context window.** Big builds must be phased; demand file-boundary stops, not
   truncation.
5. **Specify the *correct* implementation for risky areas** (key hashing, password hashing,
   PII logging) rather than naming the buzzword.
6. **Fix an output contract** (file paths, dependency list, assumptions) so results are
   reviewable and pasteable.
7. **Force a clarification step** up front to surface hidden decisions cheaply.
8. **Give review and resume prompts** so quality and continuity are first-class, not afterthoughts.
```
