# 12 - Implementation Plan

> **Version:** 1.0
> **Purpose:** Turn the roadmap (`09`) into an ordered, task-level plan you can execute (alone or with a coding assistant): what to do, in what order, which tests first, and when a step is truly done.
> **Location:** `docs/12-implementation-plan.md`
> **Builds on:** `09-roadmap.md` (phases), `11-decisions.md` (decisions), `05-architecture.md` (structure), `06`/`07` (rules and tests)
> **Does not replace** `09`. If they disagree, `06` wins for rules, `11` for decisions, `09` for scope.

---

## 1. How this plan works

| Principle | What it means in practice |
|---|---|
| **Vertical slices** | Every phase ends with something that runs end-to-end and is tested |
| **Tests first** | For each task, write the listed `T-*` tests (from `07`) before the code |
| **One phase per session** | Don't start phase N+1 until phase N is closed (all its `critical` tests green) |
| **Docs before code** | Any rule ambiguity is fixed in `06`/`07` first |
| **Small commits** | One task = one branch = one PR into `main` after `make check` |
| **Stop on doubt** | Ambiguity in consent, permissions, emergency, or purge → ask, don't guess |

**Task ID format:** `P<phase>.<n>` (backend) and `W<n>.<m>` (web layer). Each task lists: **what**, **where** (files), **tests first**, **done when**.

**Estimates** are working days for one person (`11` §9): backend 41–54, web layer 18–24, total **59–78 days (≈ 12–16 weeks)**. Re-estimate after P2 and P3 using your real speed.

---

## 2. Before you write any code (gates)

| # | Gate | Owner | Needed by |
|---|---|---|---|
| G1 | **Campaign schedule baseline set** (decides 16-week vs 12-week plan, `D-59`) | You + club | Before P0 | ✅ **PASSED** (16-week baseline plan confirmed per D-59, tracked in `docs/13-external-tracks.md`) |
| G2 | **Timezone confirmed** (`D-36`, proposed `Africa/Algiers`) | You | Before P1 | ✅ **PASSED** (`Africa/Algiers` confirmed and set in `.env.example`) |
| G3 | **Managed / Local PostgreSQL account & roles** (`D-09`) | Developer / You | Before P2 | ✅ **PASSED** (PostgreSQL 18 instance bootstrapped on port 5432 with databases `bloodday`, `bloodday_test`, `bloodday_staging`; roles `app_migrator` & `app_user` created with verified privilege isolation; migrations 001–008 applied; verified via `verify-database` [GATE-G3]) |
| G4 | **SMTP provider + verified domain** (`D-50`) | Developer / You | Before P2 | ✅ **PASSED** (Transactional SMTP configured via Resend on port 465 SSL; authenticated and verified with live test message delivery) |
| G5 | **Second security reviewer named** (`D-58`) | You | Before P3 | ⏳ Due before P3 |
| G6 | **Blood center contacted**: texts, consent wording, rest-days value (`D-37`), emergency page approval | You + Medical Club | Start now; needed before P7/P11 | ⏳ In progress |
| G7 | **Legal review booked** (`D-67`) + data controller confirmed in writing (`D-66`) | You + club | Before P10 | ⏳ In progress |
| G8 | **VPS ordered** (`D-49`), payment method verified | Developer / You | Before P10 | ⏳ Due before P10 |

> G1, G2, G3, and G4 are resolved. All external tracks, owners, deadlines, and deliverables are tracked in `docs/13-external-tracks.md`.

---

## 3. Phase P0: Preparation (3 days)

**Goal:** a repository where `make check` is green on an empty skeleton, and docs synchronized with decisions.

| ID | Task | Where | Done when |
|---|---|---|---|
| **P0.1** | **Update `06` to v1.1** (9 notes from `07` §8 + `BR-EMERG-11` param + Markdown sanitizing rule) | `docs/06` | ✅ **DONE** (v1.1 updated, 9 notes resolved, D-37 param, D-70 Markdown rule, changelog updated) |
| **P0.2** | **Update `07` to v1.1** (new web tests `T-WEB-*`, fixes from `06` v1.1) | `docs/07` | ✅ **DONE** (v1.1 updated, section 5.16 `T-WEB-*` added, notes resolved, changelog updated) |
| **P0.3** | **Update `02` to v2.1** (`totp_last_used_step`, final-stats ranking, rest-days filter, optional `rate_limit_events`) | `docs/02` | ✅ **DONE** (v2.1 updated, `users.totp_last_used_step`, ended campaign ranking query, D-37 rest-days param, `rate_limit_events` table, migrations and changelog updated) |
| **P0.4** | **Update `04` to v2.1** (`invalid_state_transition`, `503`, `open` sets current, behaviors from `06` §17, HTML routes note) | `docs/04` | ✅ **DONE** (v2.1 updated, `invalid_state_transition` 409, `service_unavailable` 503, ranking from `final_stats`, photo consent withdrawal, relocate photos update, web routes appendix D-73) |
| **P0.5** | **Update `05`, `08`, `09`, `01`, `10`** (`web/` layer, dependency rules, `purge --dry-run`, template rules, env vars, 16 weeks, staging rehearsal, tech stack) | `docs/*` | ✅ **DONE** (No known drift left across any specification document) |
| **P0.6** | Create repo (private), add `docs/` and root `CLAUDE.md` | repo root | ✅ **DONE** (Repository initialized on `main`, docs organized in `docs/`, instructions retained at root, `.gitignore` configured) |
| **P0.7** | `pyproject.toml` (uv), lock file; ruff, mypy configs; `.gitignore`, `.dockerignore`, `.env.example` | root | ✅ **DONE** (`pyproject.toml` created, `uv.lock` generated, `uv sync` verified, ruff/mypy configured, `.dockerignore` & `.env.example` complete) |
| **P0.8** | `docker-compose.yml` (`db`, `db_test` on tmpfs, `mailpit`) + `scripts/db/init-roles.sql` | root, `scripts/db/` | ✅ **DONE** (`docker compose` up and healthy for `db`, `db_test`, `mailpit`; `app_migrator` and `app_user` roles verified) |
| **P0.9** | `Makefile` (`setup`, `db-up`, `migrate`, `run`, `test`, `test-critical`, `lint`, `audit`, `check`) | root | ✅ **DONE** (Root `Makefile`, `make.cmd`, and `scripts/make.ps1` created and verified for all targets) |
| **P0.10** | `pre-commit` (ruff, mypy, secret scan, block `.env`) | root | ✅ **DONE** (`.pre-commit-config.yaml` active, `.secrets.baseline` generated, secret detection and .env block verified) |
| **P0.11** | **CI** pipeline: lint + types + tests + `pip-audit` + secret scan on every PR | CI config | ✅ **DONE** (`.github/workflows/ci.yml` created with quality, security, and test jobs; verified green on skeleton) |
| **P0.12** | Create empty package skeleton per `05` §4 (all folders with `__init__.py`) | `app/`, `tests/` | ✅ **DONE** (Complete package skeleton created for all `app/` and `tests/` packages, architecture import test passes, mypy sees package) |
| **P0.13** | Start parallel tracks (center, legal, content, accounts) | `docs/13` | ✅ **DONE** (Tracked in `docs/13-external-tracks.md`: G1–G8 gates, owners, deadlines, and checklists documented) |

**Exit gate:** `make check` green in CI; docs consistent; decisions file referenced from `CLAUDE.md`.

---

## 4. Phase P1: Foundation (3 days) → Milestone M1

**Goal:** an app that boots, answers `/health` and `/ready`, returns unified errors, and has the whole test harness.

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P1.1** | Settings with validation; **fail fast in `prod`** on missing or weak secrets | `core/config.py` | `T-STRUCT-08` (config part) | ✅ **DONE** (`app/core/config.py` implemented with Settings, validation, fail-fast production rules, check_config helper; 13 unit tests passing) |
| **P1.2** | DB engine, session-per-request dependency, **naming convention**, pool settings | `core/database.py` | `/ready` test | ✅ **DONE** (`app/core/database.py` & `app/models/base.py` implemented with naming convention, pool settings, `get_db()` lifecycle, `check_database_connection()`; 10 unit tests passing) |
| **P1.3** | Domain errors base + code registry (start with generic codes) | `core/errors.py` | `T-STRUCT-03` (skeleton) | ✅ **DONE** (`app/core/errors.py` implemented with 32 AppError subclasses, ErrorCode enum, ErrorSpec registry, to_dict envelope; verified by T-STRUCT-03 and 8 unit tests) |
| **P1.4** | Error handlers: domain errors, validation (`unknown_field` vs `validation_error`), unexpected → 500 with `request_id` | `api/error_handlers.py` | `T-GEN-06` mechanism | ✅ **DONE** (`app/api/error_handlers.py` implemented with handlers for AppError, RequestValidationError, StarletteHTTPException, and 500 unhandled; verified by 9 contract and unit tests) |
| **P1.5** | Logging (JSON in prod) + **central redaction** (phone, password, token, code, email…) | `core/logging.py` | `T-STRUCT-10` (mechanism), sentinel test | ✅ **DONE** (`app/core/logging.py` implemented with RedactionFilter, JSON/Console formatters, request_id propagation, and anonymize_ip; verified by T-STRUCT-10 and 11 unit tests) |
| **P1.6** | `Clock`, `RateLimiter` skeleton (in-memory), trusted-proxy IP resolver | `core/clock.py`, `core/ratelimit.py` | `T-RATE-10-a/b` | ✅ **DONE** (`app/core/clock.py` with Clock/SystemClock/FakeClock, `app/core/ratelimit.py` with InMemoryRateLimiter and trusted-proxy resolver; verified by T-RATE-10-a/b/c and 14 unit tests) |
| **P1.7** | Middleware: request-id, security headers, Origin check | `api/middleware.py` | headers test | ✅ **DONE** (`app/api/middleware.py` implemented with RequestIDMiddleware, ClientIPMiddleware, SecurityHeadersMiddleware, and OriginCheckMiddleware; verified by 18 unit tests covering header enforcement, HSTS, and 403 csrf_failed origin rejection) |
| **P1.8** | `main.py` app factory; `/health`, `/ready`; `/docs` only if `DOCS_ENABLED` | `main.py`, `api/routers/system.py` | `T-STRUCT-08` | ✅ **DONE** (`app/main.py` app factory and `app/api/routers/system.py` implemented with `/health` and `/ready`; verified by 6 tests confirming 200/503 health/ready states and 404 docs gating per T-STRUCT-08) |
| **P1.9** | Shared schemas: `extra="forbid"` base, phone type (BR-GEN-01), blood type, pagination (limit ≤ 100) | `schemas/common.py` | `T-GEN-01`, `T-GEN-02`, `T-GEN-05` | ✅ **DONE** (`app/schemas/common.py` implemented with AppBaseModel [extra='forbid'], PhoneNumber normalizer [BR-GEN-01], NormalizedName [BR-GEN-02], BloodType enum [8 types], PaginationParams [limit<=100], and HeroesPaginationParams [limit<=200]; verified by 38 unit tests) |
| **P1.10** | Test harness: `conftest`, transaction-per-test, role fixtures (skeleton), fakes for Clock/Storage/Mailer, pytest markers (`critical`, `br`, layers) | `tests/conftest.py` | — | ✅ **DONE** (`tests/conftest.py` implemented with `db_session` transaction-per-test isolation, fakes for Clock/Storage/Mailer/RateLimiter, role skeletons per `07` §3.1, leak sentinels, markers `critical`/`br`/layers; verified by 6 tests in `tests/unit/test_harness_fakes.py`) |
| **P1.11** | **Architecture test** (dependency rules from `05` §5.2) | `tests/architecture/` | `T-STRUCT-04` | ✅ **DONE** (`tests/architecture/test_dependency_rules.py` implemented with AST analysis enforcing layer dependencies, no circular imports, no direct commits, and verifying deliberately bad imports fail per T-STRUCT-04) |

**Exit gate (M1):** ✅ **PASSED** (/health and /ready work [P1.8]; prod-config checks pass [P1.1]; redaction test passes [P1.5]; architecture test enforced in CI [P1.11]).

---

## 5. Phase P2: Models and migrations (3 days)

**Goal:** the full schema from `02`, with constraints proven by tests.

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P2.1** | Models: campaigns, sites, campaign_sites | `models/campaign.py` | `T-CAMP-02-c`, `T-CAMP-04-a`, `T-CSITE-01-a`, `T-CSITE-03-a` | ✅ **DONE** (`Campaign`, `Site`, and `CampaignSite` implemented in `app/models/campaign.py` with all constraints: `ck_campaigns_slug_format`, `ck_campaigns_status`, `uq_campaigns_single_current`, `ck_campaign_sites_time_order`, composite PK `(campaign_id, site_id)`, and composite FKs; verified by 17 tests in `tests/db/test_campaign_models.py`) |
| **P2.2** | Models: users, user_tokens, mfa_recovery_codes, auth_sessions (+ `totp_last_used_step`) | `models/user.py` | `T-USER-01-a` (DB) | ✅ **DONE** (`User`, `UserToken`, `MfaRecoveryCode`, `AuthSession` implemented in `app/models/user.py` per `02` §5.4-§5.7 & `06` §5; constraints `ck_users_role`, `ck_users_supervisor_site`, functional lower(email) unique index `uq_users_email_lower`, `ck_user_tokens_purpose`, cascades, and `totp_last_used_step`; verified by 17 tests in `tests/db/test_user_models.py`) |
| **P2.3** | Models: pledges (partial unique index), donors (all CHECKs + composite FK) | `models/pledge.py`, `donor.py` | `T-PLEDGE-06-b`, `T-DONOR-04-b`, `T-DONOR-07-c`, `T-CONSENT-02/03` (DB), `T-CSITE-06-a` | ✅ **DONE** (`Pledge` implemented in `app/models/pledge.py` with `first_name_len`, `phone_format`, composite FK `(campaign_id, site_id)` ON DELETE CASCADE, partial unique index `uq_pledges_campaign_site_phone`; `Donor` implemented in `app/models/donor.py` with 8 CHECK constraints, 4 independent consents, `donated_at`, composite FK ON DELETE RESTRICT, user FK ON DELETE SET NULL; verified by 48 tests in `tests/db/test_pledge_models.py` and `tests/db/test_donor_models.py`) |
| **P2.4** | Models: photos, file_deletion_queue, audit_log, phone_reveal_log | `models/photo.py`, `audit.py` | `T-PHOTO-02-a` | ✅ **DONE** (`Photo` and `FileDeletionQueue` implemented in `app/models/photo.py` with `individual_donor` CHECK `((kind = 'individual') = (donor_id IS NOT NULL))`, mime/size/caption checks, composite FK ON DELETE RESTRICT, donor FK ON DELETE CASCADE; `AuditLog`, `PhoneRevealLog` [no donor FK], and `RateLimitEvent` implemented in `app/models/audit.py`; verified by 32 tests in `tests/db/test_photo_models.py` and `tests/db/test_audit_models.py`) |
| **P2.5** | Alembic setup; migrations 001–006 (one concern each) | `migrations/` | `T-STRUCT-06` | ✅ **DONE** (Alembic configured via `alembic.ini` and `migrations/env.py`; migrations 001–006 implemented covering all 14 tables, constraints, partial indexes, and cascades; reversible up/down SQL verified; verified by 4 tests in `tests/db/test_migrations.py` covering T-STRUCT-06) |
| **P2.6** | Migration 008: DB roles/grants, make `audit_log` append-only | `migrations/` | `T-AUDIT-02-a`, `T-STRUCT-11` | ✅ **DONE** (Migration `008_db_roles_and_grants.py` implemented granting `app_user` DML privileges while strictly revoking `UPDATE`, `DELETE`, and `TRUNCATE` on `audit_log` per BR-AUDIT-02 & `02` §7; reversible downgrade restores privileges; verified by `tests/db/test_roles_and_grants.py` and `tests/db/test_migrations.py`) |
| **P2.7** | Factories + base `seed` data per `07` §3.2 (refuses in `prod`) | `tests/factories/`, `app/cli/seed.py` | `T-STRUCT-09` | ✅ **DONE** (Comprehensive model factories implemented in `tests/factories/` with valid constraint defaults; base `seed` CLI command in `app/cli/seed.py` and `app/cli/__main__.py` with strict production refusal `T-STRUCT-09`; builds Sites A/B/C/Z, Campaigns C1–C5, staff users, reference donors dA1–dB1 and 50 total donors across all blood types and consents, 100 pledges, and photos; verified by `tests/cli/test_seed.py` and `tests/unit/test_factories.py`) |
| **P2.8** | Set up **staging** DB (`D-57`) and run migrations there | infra, `app/cli/migrate.py`, `.env.staging.example`, `docker-compose.yml` | `T-STRUCT-06` | ✅ **DONE** (Staging configuration `.env.staging.example` implemented conforming strictly to `08` §13; `docker-compose.yml` augmented with isolated `db_staging` service; Alembic `migrations/env.py` updated to prioritize `migration_database_url`; CLI migration runner `check-migrations` implemented in `app/cli/migrate.py` supporting schema inspection and `--upgrade` to head; verified by 7 tests in `tests/cli/test_staging_infra.py`; live managed DB provisioning ready under GATE-G3) |

**Exit gate:** every constraint in `02` has a rejection test; migrations reversible; roles verified.

---

## 6. Phase P3: Authentication and permissions (8 days) → Milestone M2 (highest risk)

**Goal:** secure staff login and the permission framework everything else relies on.
**Rule:** the second reviewer reads `core/security.py`, `services/auth.py`, `services/sessions.py`, `api/deps.py` line by line.

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P3.1** | Password hashing (argon2id), policy (10–128 chars, common-password list), rehash on login | `core/security.py` | `T-AUTH-03`, `T-AUTH-04` | ✅ **DONE** (argon2id verified, zero password in logs, 12 unit tests passing) |
| **P3.2** | Session model/service: random ID, **hashed storage**, idle/absolute timeouts, revocation, new session per login | `services/sessions.py` | `T-AUTH-11`, `T-AUTH-12` | ✅ **DONE** (SHA-256 hashed storage, fixation test passes, timeouts verified, 11 tests green) |
| **P3.3** | Login flow: uniform error, dummy verify when user missing, lockout (5 → 15 min, doubling, cap 1 h), unlock | `services/auth.py` | `T-AUTH-05`, `T-AUTH-06` | ✅ **DONE** (uniform error, dummy verify on wire, progressive lockout 15/30/60m, unlock, 10 tests green) |
| **P3.4** | Cookie handling (`HttpOnly`, `Secure`, `SameSite=Lax`, `__Host-`) + CSRF (token + Origin) | `api/deps.py` | `T-AUTH-18`, `T-AUTH-19` | ✅ **DONE** (`set_session_cookie` / `clear_session_cookie` with RFC 6265bis `__Host-`, `HttpOnly`, `Secure`, `SameSite=Lax`; HMAC-SHA256 CSRF protection `generate_csrf_token` / `verify_csrf_token` / `verify_csrf` returning `403 csrf_failed`; session/auth dependency injection wired in `api/deps.py` and `app/main.py`; 8 tests in `tests/api/test_cookie_csrf.py` passing) |
| **P3.5** | Invitations and password reset: hashed tokens, TTLs, single use, resend invalidates, `Mailer` abstraction (+ console/SMTP) | `services/auth.py`, `mail/` | `T-AUTH-02`, `T-AUTH-15` | Uniform `204` on forgot; no mail for unknown email |
| **P3.6** | `create-admin` CLI (interactive password) | `cli/create_admin.py` | `T-AUTH-01-b` | First admin created; MFA forced on first login |
| **P3.7** | MFA: TOTP, ±1 window, **no code reuse** (`totp_last_used_step`), encrypted secret, 8 recovery codes (hashed, single use) | `services/mfa.py` | `T-AUTH-08`, `T-AUTH-09`, `T-AUTH-10` | Reuse rejected; interim session limited to MFA endpoints |
| **P3.8** | **Policy module**: permissions, role matrix, `can()`, conditions S/L/M/R; `require(...)` dependency | `policy/*`, `api/deps.py` | `T-STRUCT-02`, `T-AUTH-14` | Step-up (R) enforced with 10-min window |
| **P3.9** | `/auth/*` and `/me` endpoints | `api/routers/auth.py`, `me.py` | `T-AUTH-13`, `T-AUTH-16/17` | Revocation on all six triggers |
| **P3.10** | Rate limits for login/MFA/forgot/accept-invite | `core/ratelimit.py` | `T-RATE-01/02/06/07/09` | `429` + `Retry-After` |
| **P3.11** | **Route-coverage test** and **matrix test harness** (run on existing routes) | `tests/api/` | `T-STRUCT-01/02` | Unprotected route breaks the build |
| **P3.12** | Auth audit events (no PII) | `services/audit.py` | `T-AUDIT-01/03` | Events recorded in the same transaction |
| **P3.13** | **Manual end-to-end:** `create-admin` → MFA → invite supervisor → accept → login → deactivate → session dies instantly | — | — | Walkthrough recorded |
| **P3.14** | **Second-reviewer review** of the four files above | — | — | Findings fixed |

**Exit gate (M2):** every AUTH `critical` test green; manual walkthrough done; reviewer findings closed.
**Overrun rule:** if P3 exceeds 150% of estimate → apply `09` §8.3 (defer supervisor MFA; consider a vetted library for part; never weaken lockout/revocation/step-up).

---

## 7. Phase P4: Core administration (5 days)

**Goal:** an admin can prepare a full campaign through the API.

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P4.1** | Sites CRUD (deactivate, conditional delete) | `services/sites.py`, `api/routers/admin/sites.py` | `T-SITE-*` | Rules BR-SITE-02/03 pass |
| **P4.2** | Campaign CRUD + publish; slug rules; content size limit | `services/campaigns.py` | `T-CAMP-01/02/13/14/15` | |
| **P4.3** | Campaign-sites: add/update/remove with `in_use` rules | `services/campaigns.py` | `T-CSITE-*` | |
| **P4.4** | Lifecycle: `set-current`, **`open`**, `revert` (single transaction, one live campaign) | `services/campaigns.py` | `T-CAMP-05…08`, `T-CONC-02/03`, `T-GEN-12-b` | Concurrent opens → one wins |
| **P4.5** | User admin: invite, update, deactivate/activate, unlock, reset-MFA, resend, **bulk deactivate supervisors**; last-admin protection | `services/users.py` | `T-USER-*`, `T-CONC-05` | Sessions drop on deactivate |
| **P4.6** | Audit service + admin audit endpoint | `services/audit.py` | `T-AUDIT-*` | |
| **P4.7** | Admin image upload for covers/logos (shared image pipeline: validate, strip EXIF, resize) | `storage/images.py` | `T-PHOTO-14-a` | EXIF removed |
| **P4.8** | Matrix rows for admin endpoints; export OpenAPI snapshot | `tests/api/` | `T-STRUCT-02/07` | Snapshot committed |

**Exit gate:** scripted scenario (httpie/pytest) prepares a full campaign: sites → campaign → participation → publish → set-current → open → invite supervisors.

---

## 8. Phase P5: Supervisor field flow (8 days) → Milestone M3 (the heart)

**Goal:** a supervisor can run an entire event day. **No rule changes mid-build.**

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P5.1** | Supervisor context/summary; donors list (no phone) and detail (phone); name search | `services/donors.py`, `api/routers/supervisor/` | `T-DONOR-14`, `T-DONOR-17`, `T-COUNT-07` | |
| **P5.2** | **Create donor:** derive scope from user/context, reject reserved fields, **client-supplied UUID idempotency** (201/200/409) | `services/donors.py` | `T-DONOR-01/02/03/05`, `T-GEN-07-a`, `T-CONC-04` | Replay returns 200, one row |
| **P5.3** | **Consent rules** (4 flags): truth table in schema + service + DB; final-state evaluation; no silent phone clearing | `schemas/donor.py`, `services/donors.py` | `T-CONSENT-01…06` | All six truth-table rows pass in 3 layers |
| **P5.4** | Status change (both directions, `donated_at` set/clear), delete, `check-phone` | `services/donors.py` | `T-DONOR-06/07/11/13`, `T-CONC-06` | |
| **P5.5** | **Isolation:** other-site access → `404` across list/get/patch/status/delete | `policy/rules.py` | `T-DONOR-09`, matrix rows | No `403` leaks existence |
| **P5.6** | Storage abstraction + local implementation; photo upload (validate real content, re-encode, strip EXIF, resize, server-side key) | `storage/`, `services/photos.py` | `T-PHOTO-03…09` | GPS EXIF gone; failure removes file |
| **P5.7** | Individual-photo consent gate; delete photo; **withdrawing `consent_photo` deletes photos** (single transaction) | `services/photos.py`, `services/donors.py` | `T-PHOTO-03/10`, `T-CONSENT-07` | Atomic with queue rows |
| **P5.8** | `file_deletion_queue` on every deletion path | `services/file_queue.py` | `T-FILE-01…03` | Four paths covered |
| **P5.9** | Audit for every donor/photo action (IDs only) | `services/audit.py` | `T-DONOR-18`, `T-AUDIT-01` | Sentinels absent |
| **P5.10** | Property tests: consent truth table and counter invariants (`D-43`) | `tests/unit/` | `T-COUNT-02-a` | Hypothesis finds no counterexample |
| **P5.11** | **"Supervisor's day" automated scenario:** login → context → add 15 donors (all consent combos) → statuses → undo → photos (one with GPS) → replay after "network drop" → other-site supervisor gets 404 | `tests/api/` | — | Scenario green |
| **P5.12** | **Line-by-line review** of `DonorService`, `PhotoService`, consent schemas | — | — | Findings fixed |

**Exit gate (M3):** all DONOR/CONSENT/PHOTO critical tests green; the scenario is automated; EXIF verified.
**If time is short:** defer individual photos (keep general photos, EXIF stripping, and content validation).

---

## 9. Phase P6: Public API (4 days)

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P6.1** | Public campaign content endpoints (current, list, by slug, sites, photos, sites list); unpublished → `404` everywhere | `api/routers/public/` | `T-CAMP-03`, `T-PHOTO-11` | Draft invisible on all routes |
| **P6.2** | **Pledge**: honeypot, identical response for duplicates, rate limit | `services/pledges.py` | `T-PLEDGE-*`, `T-CONC-07` | Duplicate = byte-identical `204` |
| **P6.3** | Counters (live), heroes, ranking, site interest; cache headers | `services/stats.py` | `T-HERO-*`, `T-RANK-01…04`, `T-COUNT-01/02/06` | Heroes are exactly `dA2`, `dA3`, `dB1` |
| **P6.4** | Contract tests: exact response keys; no pledge data in any public response | `tests/security/` | `T-GEN-09/10`, `T-PLEDGE-08/09` | |
| **P6.5** | Light load test on pledge and public pages | scripts | — | No pool exhaustion |

## 10. Phase P7: Emergency (3 days) → Milestone M4

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P7.1** | Emergency list (no phone, ≤50, oldest donation first, optional min-days filter `EMERGENCY_MIN_DAYS_SINCE_DONATION`) | `services/emergency.py` | `T-EMERG-01…04` | Exact key set verified |
| **P7.2** | Reveal (`POST`), per-IP limit 10/h, identical `404` for all ineligible cases, reveal log (hash only), audit by ID | `services/emergency.py` | `T-EMERG-05…09`, `T-RATE-05/10` | Spoofed `X-Forwarded-For` doesn't bypass |
| **P7.3** | Kill switches `EMERGENCY_ENABLED`, `PLEDGES_ENABLED` → `503` | `core/config.py` | new tests | Toggle works after restart |
| **P7.4** | Spike detection event | `services/emergency.py` | `T-EMERG-13` | |
| **P7.5** | **Scraping attempt** with a script; **second-reviewer security review** | — | — | Throttled after 10; findings closed |

**Can be deferred** (`09` §6.2): ship a static page with the center's phone only. Consent data is already captured for later activation.

---

## 11. Phase P8: End of campaign and retention (5 days) → Milestone M5

| ID | Task | Where | Tests first | Done when |
|---|---|---|---|---|
| **P8.1** | **`end`** (atomic: stats freeze, status, audit), re-`end` refresh, purged guard | `services/campaigns.py` | `T-CAMP-09/10/17/18`, `T-GEN-12-a`, `T-CONC-01` | Injected failure leaves no half state |
| **P8.2** | Frozen counters/ranking for ended campaigns | `services/stats.py` | `T-COUNT-03/04`, `T-RANK-05` | Post-end edits don't change numbers |
| **P8.3** | `RetentionService` + **`purge`** CLI (`--dry-run`), idempotent, per-campaign transaction, cleanup of logs/tokens/sessions | `services/retention.py`, `cli/purge.py` | `T-PRIV-01…08/11`, `T-CONC-08` | Day-boundary cases pass |
| **P8.4** | Single-donor anonymize (on request), delete pledge/phone on request | `services/donors.py` | `T-PRIV-09/10` | Also removes individual photos |
| **P8.5** | Relocate donor (moves photos too), anonymized donors read-only | `services/donors.py` | `T-DONOR-12/15` | Atomic |
| **P8.6** | Admin file-queue endpoints + deletion job | `services/file_queue.py` | `T-FILE-02` | |
| **P8.7** | **Lifecycle scenario:** open → donors → end → frozen → fake clock +366 days → purge → public data intact, sensitive data gone | `tests/api/` | `T-PRIV-12` | Scenario green; `purge` twice = no change |
| **P8.8** | Dry-run `purge` on a copy of staging data | — | — | Output reviewed |

---

## 12. Phase P9: Free posts (2 days, optional)

CRUD for admin, public read for published only, `published_at` on first publish, 100 KB limit. Tests: `T-POST-*`. Place after P8 if time allows.

---

## 13. Web layer (Jinja2, decision D-34): tasks W1–W5

> Follows `11` §5.1: server-rendered pages, light JavaScript with no build step, same repo, same origin, no external assets, strict CSP.

| ID | Task | Starts after | Tests first | Done when |
|---|---|---|---|---|
| **W1.1** | `app/web/` skeleton: base template (RTL, `dir="rtl"`, `lang="ar"`), self-hosted CSS and Arabic font, static route, error pages | P1 | `T-WEB-*` basics | Page renders with CSP and no external requests |
| **W1.2** | **Open Graph** helper: title, description, image (absolute URL), canonical | P1 | OG test | Tags present on every public page |
| **W1.3** | Shared JS modules: API client (CSRF header, error handling), **error-code → Arabic map** (from `04` §3.3), idempotent retry (in-memory) | P3 | JS-level manual tests | Unknown codes fall back gracefully |
| **W2.1** | Login page, forced-password-set (invite), reset pages | P3 | HTML smoke | End-to-end login in a browser |
| **W2.2** | **Supervisor panel:** summary, search, list, add-donor form (4 consents), status toggle with undo, photo upload | P5 | HTML smoke + manual on a real phone | "Supervisor's day" works on a phone |
| **W3.1** | Home, campaigns list/detail (SSR Markdown with sanitizer), sites | P6 | OG + sanitizer tests (`D-70`) | Link preview correct in WhatsApp/Facebook |
| **W3.2** | Pledge form + thank-you page (honeypot field) | P6 | HTML smoke | Duplicate shows same page |
| **W3.3** | Heroes, ranking, info page, contact (static, center-approved texts) | P6 | no-phone-in-HTML test | |
| **W3.4** | **Emergency page**: markup contains no numbers; reveal on click; mandatory warnings; `noindex`, `no-store` | P7 | `T-EMERG-02`, HTML test | View-source shows no phone |
| **W4.1** | **Admin panel tier 1**: campaigns, sites, participation, users, end campaign, bulk deactivate, step-up (re-auth) dialog | P8 | HTML smoke | A whole campaign set up from the UI |
| **W4.2** | Admin tier 2 (optional): audit view, pledges list, file queue, posts | P9 | | |
| **W5.1** | Device pass (Android low-end, iOS Safari, in-app browsers), accessibility, Arabic copy review | P10 | manual checklist (`10` §6) | Checklist signed |

**Web rules to enforce in review:** autoescape on; no inline scripts; no third-party resources; panels and emergency `noindex`; panels `no-store`; JavaScript never re-implements business rules.

---

## 14. Phase P10: Hardening and release candidate (5 days) → Milestone M6

| ID | Task | Done when |
|---|---|---|
| **P10.1** | All `critical` tests green; `T-STRUCT-05` (every `BR-*` has a test) green | CI gate on |
| **P10.2** | Run all structural tests `T-STRUCT-01…11` | Green |
| **P10.3** | **Manual security tests** (`10` §17, 35 items) on staging, with a written record | No open high finding |
| **P10.4** | Sentinel sweep across logs, audit, responses after full flows | Clean |
| **P10.5** | Load test (pledge, public pages, 10–20 concurrent supervisors, concurrent image uploads, login burst) | No pool exhaustion; argon2 cost acceptable |
| **P10.6** | Config/headers check, `/docs` off, cookies, `check-config`, `pip-audit`, secret scan incl. git history | Clean |
| **P10.7** | **Staging = production behavior**; **restore a backup** onto a separate environment | Restore verified |
| **P10.8** | Final doc sync (02/03/04/06/07), freeze OpenAPI | Docs match code |
| **P10.9** | Provision production (VPS, Caddy, managed DB, SMTP, monitoring); run `08` §14 checklist **without real data** | Checklist complete |

---

## 15. Phase P11: Integration, rehearsal, acceptance (7 days) → Milestone M7

| ID | Task | Done when |
|---|---|---|
| **P11.1** | **Training** supervisors (hands-on, each adds a dummy donor) | Every supervisor succeeds unaided |
| **P11.2** | **Dress rehearsal on staging** (`10` §8) incl. real invites, WhatsApp link sharing, network cut, `end`, `purge`, kill switches | Numbers match; no P0/P1 |
| **P11.3** | Field simulation (`10` §7) on real phones and networks | Pass |
| **P11.4** | **UAT** with the center, club, supervisors | Written approval |
| **P11.5** | Smoke test on production (no real data created) | Pass |
| **P11.6** | **Freeze** one week before the campaign; Go/No-Go three days before | Signed checklist |

## 16. Phase P12: Launch, operation, closure

Follow `10` §25 (T-1, event day, T+1) and §28 (closure). Key moves: backup → `open` → monitor → paper fallback → `end` → deactivate supervisors → backup → publish results → week-1 review → `purge` scheduled for one year later.

---

## 17. Calendar (16-week baseline)

| Week | Backend | Web | Notes |
|---|---|---|---|
| 1 | P0, P1 | W1.1–W1.2 (start) | Start center/legal tracks |
| 2 | P2 | W1 | Staging DB up |
| 3–5 | P3 (incl. reviewer pass) | W1.3, W2.1 after P3.5 | **M2** |
| 6 | P4 | — | OpenAPI snapshot |
| 7–9 | P5 | W2.2 | **M3** |
| 10 | P6, P7 | W3.1–W3.4 | **M4**, emergency review |
| 11 | P8 | W4.1 | **M5** |
| 12 | P9 (if time) | W4.2, buffer | Catch-up week |
| 13 | P10 | W5.1 | **M6** |
| 14–15 | P11 | — | Rehearsal, training, UAT, **M7** |
| 16 | Freeze, P0-only fixes | — | Go/No-Go; campaign after |

**12-week compressed variant:** drop P9 and ranking, keep emergency static if late, admin tier 1 minimal (other admin actions via API), individual photos deferred (`09` §6.2).

---

## 18. The first 10 working days (detailed)

| Day | Work | Output |
|---|---|---|
| 1 | G1–G2 decided; P0.1 (`06` v1.1) | Updated `06` |
| 2 | P0.2–P0.5 (docs sync) | All docs consistent |
| 3 | P0.6–P0.12; P0.13 kickoff of external tracks | `make check` green in CI |
| 4 | P1.1–P1.4 (config, DB, errors, handlers) | Boots; unified errors |
| 5 | P1.5–P1.8 (logging, clock, middleware, health) | `/health`, `/ready` |
| 6 | P1.9–P1.11 (schemas, harness, architecture test) | **M1** |
| 7 | P2.1–P2.2 (campaign/user models + tests) | Constraint tests |
| 8 | P2.3–P2.4 (pledges, donors, photos, audit) | Truth-table DB tests |
| 9 | P2.5–P2.6 (migrations, roles) | up/down/up green |
| 10 | P2.7–P2.8 (factories, seed, staging) | **P2 closed**; re-estimate |

---

## 19. Working with the coding assistant (per task)

1. **Start the session** with the template in `CLAUDE.md` §14, naming the phase and the task ID.
2. **Give only the relevant docs** (e.g., for P5.3: `06` consent section, `07` `T-CONSENT-*`, `04` donors endpoints).
3. **Ask for tests first**, review them, then let it implement the minimum.
4. **Review the diff** against the forbidden patterns (`CLAUDE.md` §9) before merging.
5. **Never merge** auth, consent, emergency, or purge changes on green tests alone: read them line by line.
6. **Update docs in the same PR** when a rule or error code changes.
7. End each session by running `make check` and updating the "Current phase" line when a phase closes.

---

## 20. Quality gates (summary)

| Gate | When | Condition |
|---|---|---|
| **PR merge** | every task | `make check` green; no forbidden patterns; docs updated |
| **Phase close** | end of each phase | All its `critical` tests green; exit criteria met; Git tag `phase-N-done` |
| **Staging deploy** | from P2 on | All test stages green |
| **Release candidate (M6)** | end of P10 | All `critical` + `T-STRUCT-05` + manual security record + restore verified |
| **Go/No-Go** | 3 days before | `10` §11 checklist signed |

---

## 21. Tracking and re-planning

| What | How |
|---|---|
| **Velocity** | After each phase, record planned vs. actual days |
| **Re-estimate** | After P2 and P3; adjust calendar and apply the cut line if needed |
| **Trigger: >20% over** | Absorb inside the phase |
| **Trigger: 20–50% over** | Apply cut order (`09` §6.2) |
| **Trigger: >50% over in P3/P5** | Stop and re-plan (consider vetted library, or move the date) |
| **Weekly ritual (30 min)** | Review phase progress, open decisions (`11`), risks (`09` §10), and external tracks (center, legal) |
| **Never cut** | Auth hardening, consent rules, site isolation, purge, critical tests, tested backup |

---

## 22. Definition of done (any task)

- [ ] Rule exists in `06` with its `BR-*` ID.
- [ ] Tests written first, named with the rule ID, marked `br` (and `critical` if ★).
- [ ] Migration reversible (if schema changed).
- [ ] Schemas `In`/`Update`/`Out`; no leaked fields.
- [ ] Permission declared in `permissions.py`, `matrix.py`, and `03`.
- [ ] Service enforces scope and campaign state through `policy`.
- [ ] Errors from the unified catalog.
- [ ] Audit event if sensitive (IDs only).
- [ ] `04` and OpenAPI updated.
- [ ] `ruff`, `mypy`, tests pass; no personal data in logs.

---

## 23. Next action

1. **Decide G1 (campaign date)** and confirm G2 (timezone).
2. Execute **P0.1 → P0.13**, starting with `06` v1.1.
3. Kick off **G6 (blood center)** and **G7 (legal)** this week.
