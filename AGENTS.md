# AGENTS.md — Foundry Engineering & Agent Instructions

This file defines the mandatory workflow, architectural invariants, and coding standards for every AI coding agent working on the **Foundry** repository. Read this file and reference [foundry_design_document.md](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md) (v1.4) before proposing or writing any code.

---

## 1. Mandatory Plan-First Workflow (Rule #1 — Non-Negotiable)

**NEVER jump directly into writing or modifying project code.** For every module, phase, feature, or non-trivial change, you MUST follow this exact cycle:

1. **Scope & Inspect:** Read the relevant sections of [foundry_design_document.md](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md) and inspect existing workspace files.
2. **Present the Implementation Plan:** Present a concise, structured plan to the user containing:
   - **Goal:** Which Module (`M1`–`M4`) and Phase (`P0`–`P10`) or sub-feature is being built.
   - **Files to Create / Modify:** Exact file paths and their single responsibility.
   - **Public Interfaces & Schemas:** Protocols, Pydantic models, SQL tables, or CLI commands introduced.
   - **Verification & Test Plan:** How we will test and prove the slice works (at \$0 API cost using cassettes/mocks where applicable) and what "Done" looks like.
3. **STOP and Wait for User Approval:** Explicitly ask the user to review and approve (or adjust) the plan before executing.
4. **Implement & Verify:** Only after the user approves the plan, write the code, run `ruff`, `mypy`, and `pytest`, and report the verified results.

---

## 2. Modular Architecture & Progressive Build Principles

Foundry is built as a **polished, daily-driver software product** across **4 lean MIV modules** ([Section 25 & 26](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L1852-L1960)):
- **Module 1 (P0–P2):** Core Kernel + Zero-LiteLLM First-Party SDK Gateway + `.git`-Free Hardened Sandbox/Git Guard + Single-Agent Full-Stack Builder (`v0.1`, also serves as Baseline `B1`).
- **Module 2 (P3):** Postgres-Only Blackboard + `LISTEN/NOTIFY` Outbox (`publish_seq`) + CLI Decision Inbox + Staged Contract Hierarchy (`Data → Backend → Frontend/Infra`) + Two-Wave Build (`Wave 1 Foundation → Wave 2 Features`).
- **Module 3 (P4–P5):** Lean Concurrency Control (static `write_reservations`, symlink + diff enforcement, 2-slot heavy-exec semaphore, serial merge queue) + Cross-Family Black-Box Verification (`G3` + post-test `Test-Validator` + Dispute Protocol) + Dashboard `v0.5`.
- **Module 4 (P6–P7, MIV Complete):** `LocalComposeTarget` Blue/Green CI/CD + `squawk` expand-only migrations + Held-Out Pilot Benchmark (`foundry-bench` with paired bootstrap CIs).

All deferred features (Stage A Cloud Deploy, Dashboard v1.0, Core Ablations, Mergiraf, mid-task write-set expansion, 5-min pause preemption, `Notify+Undo`, Redis Streams, opt-in single-shot CLI adapter, Stage B Terraform, brownfield repos) live in the **Deferred / Post-MIV Expansion Backlog** ([Section 26.2](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L1943-L1960)) and plug cleanly into existing `Protocol` boundaries.

### Modularity Rules
1. **Strict Protocol Boundaries:** Subsystems communicate through explicit `typing.Protocol` interfaces (`ModelTransport`, `SandboxProvider`, `DeployTarget`, `StackPack`) so later modules and Post-MIV features plug in without rewriting earlier modules.
2. **No Upward Imports:** Lower-level packages (`config`, `security`, `gateway`, `sandbox`) must **never** import from higher-level orchestration packages (`supervisor`, `agents`, `api`).
3. **Every Slice Must Run Cleanly:** Never leave half-wired stubs that break the CLI or test suite. At the end of every approved task, `foundry --help` and `pytest` must pass 100%.

---

## 3. Non-Negotiable Architectural & Supply-Chain Guardrails

When implementing each subsystem, strictly enforce these invariants from [foundry_design_document.md](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md) (v1.4):

### 3.1 Zero-LiteLLM First-Party SDK Gateway & Supply-Chain Safety ([Section 16](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L1247-L1464) & [Section 19.1](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L1591-L1608))
- **NEVER add `litellm` as a dependency** (`A7-1` — removed after 2026 PyPI/Docker supply-chain compromises `v1.82.7`/`v1.82.8` and `v1.107.0`).
- **Never call LLM SDKs directly** inside agent graphs or services. Every LLM call must go through `foundry.gateway`.
- **Three Thin First-Party SDK Transports Only (`ModelTransport`):**
  1. `openai_compat` (`openai` SDK with configurable `base_url`): OpenAI, xAI Grok, Groq, DeepSeek, Mistral, Together, Fireworks, GitHub Models, Ollama, LM Studio, vLLM, and OpenRouter (strictly an optional `base_url` preset).
  2. `google_genai` (`google-genai` SDK): native Gemini support with exact `cached_content_token_count` telemetry.
  3. `anthropic` (`anthropic` SDK): native Claude support with direct `cache_control` breakpoints and tool-use blocks.
- **Strict Account & ToS Safety (`A7-2`):** NEVER extract, read, proxy, or spoof internal IDE/CLI OAuth tokens (`~/.gemini`, `~/.codex`, `~/.claude`, Cursor, Antigravity, etc.), and NEVER run multi-step ReAct loops through consumer CLI wrappers.
- **Daemon Supply-Chain Hardening (`A7-1`):** Commit hash-verified `uv.lock`, enforce `uv sync --frozen` (and `--require-hashes` in CI/builds), apply a 3–7 day minimum PyPI release-age cooldown (`exclude-newer` in `uv`), and **pin every Docker image by immutable `sha256` digest** (never `:latest`).
- **Secret Scanning, Budgets & Cassettes:** Run the secret scanner on every outgoing prompt before calling any transport, enforce reconciled partitioned budgets + hard per-run wall-clock (90 min) & call-count (600) kill-switches (`A7-11`), disable cross-provider fallbacks when `benchmark_pin_models: true` (`A7-13`), and pass prompts through the **Prompt Normalizer** for deterministic \$0 Record/Replay testing.

### 3.2 LangGraph Threading & Idempotent Interrupts ([Section 7](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L339-L470) & [Section 17](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L1468-L1515))
- **No Cross-Domain `Send()` Fan-Out:** Never run concurrent Domain Leads or Implementers inside a single shared LangGraph `thread_id`. Spawn independent threads (`{run}:project`, `{run}:lead:{domain}`, `{run}:task:{id}:{attempt}`, `{run}:verify:{feature}:{round}`).
- **Re-Run-Safe Nodes & Lean Decisions (`A7-11`):** Because LangGraph re-runs a node from the top upon `Command(resume=...)`, always place LLM calls in dedicated `draft_*` nodes and keep `interrupt()` inside separate `approve_*` nodes guarded by idempotent `decisions.get_or_create(idempotency_key)`. In v1, keep decisions binary (`Auto` vs `Approve`/`Human`) with a **CLI Decision Inbox** in Module 2.

### 3.3 Hard / Fuzzy Memory Split, Postgres-Only Outbox & Contract Freeze Caps ([Section 8](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L472-L812) & [Section 9](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L817-L920))
- **Exact Facts in Postgres Only:** Contracts (including per-operation `x-foundry-rules`), schemas, task specs, decisions, and write reservations are injected into prompts as exact JSON rows from Postgres—never retrieved via `pgvector`, and never mixed with raw chat histories.
- **Postgres-Only Outbox with Monotonic `publish_seq` (`A7-5`):** Do NOT add Redis in v1. Write `events` rows in the same DB transaction as state mutations (`version = expected_version` optimistic locking), have the single-process relay acquire `pg_advisory_xact_lock` to assign a strictly monotonic `publish_seq` to committed rows, fan out to in-process `asyncio` consumers deduped by `processed_events(consumer_group, publish_seq)`, and cursor WebSockets on `?after_seq=`.
- **Two-Wave Execution & Linear Schema Barriers (`A7-9`):** Wave 1 (Foundation: deps, centralized SQLAlchemy models in `backend/app/models/` + `0001_initial`, Auth + `conftest.py` with `seed_user(role=...)`, Frontend `AppLayout` reading `feature.ts` nav registries) must merge to `main` before Wave 2 parallel feature tasks start. Wave 2 `kind='schema'` tasks run as serialized barriers chained with `blocks` dependencies until `MERGED`; on mandatory boundary-rebase conflict, cleanly discard the WIP branch, increment `attempt`, and restart from the new `main` SHA.
- **Contract Change Caps:** Enforce `pinned_pointers` checks, 3-minute breaking-change batching, max 2 contract reopens per task (`FROZEN` → single joint Lead co-design), and **never reopen `MERGED` tasks** (spawn a fresh `delta` task from `main` instead).

### 3.4 Concurrency, Host-Escape Prevention & 16 GB RAM Discipline ([Section 5](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L219-L307) & [Section 10](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L922-L1012))
- **Prefix-Aware Write Reservations (`A7-12`):** Guard `write_reservations` acquisition with `pg_advisory_xact_lock(hashtext('reserve:' || :run_id))` and check both file equality and trailing-`/` directory prefix overlap. Renew task-scoped heartbeats across `IN_PROGRESS`, `IN_REVIEW`, `VERIFIED`, and `QUEUED`.
- **`.git`-Free Workspaces & Symlink/Host-Escape Prevention (`A7-3`, `A7-4`, `A7-10`):**
  - Agents inside sandboxes never run `git` and `/workspace/.git` **must not exist** inside Docker (sync to/from a `.git`-free container workspace dir rather than bind-mounting `/dev/null`).
  - Before *any* host daemon tool (`git-mcp`, `Semgrep`, `gitleaks`, `oasdiff`) reads a workspace or worktree, run a deterministic recursive walk rejecting **any symlink (`os.path.islink` / git mode `120000`), FIFO/device node, or oversized YAML bomb**.
  - Export `openapi.json` **strictly inside Docker** as a static JSON artifact (never import agent code on the host).
  - Run daemon-side git with `-c core.hooksPath=/dev/null -c core.fsmonitor=false` and reject any commit/branch touching paths outside `task_write_sets` or containing mode `120000`.
  - Disable external DNS on the internal sandbox Docker network; isolate the blackboard DB (`REVOKE CONNECT ON DATABASE foundry_blackboard FROM PUBLIC`).
  - Gate unplanned `install_package` strictly by **curated allowlist OR hard human `Approve`** (never `Notify`/`Auto`), enforce `uv pip install --only-binary :all:` and `pnpm add --ignore-scripts`, and inject `HTTP(S)_PROXY` strictly into that single `docker exec` call.
- **Heavy-Execution Semaphore & Crash-Safe `app_template` (`A7-6`, `A7-9`):** Decouple lightweight LLM-waiting containers (`max_concurrent_tasks: 4`) from a **2-slot `HeavyExecSemaphore`** (`× 1.5 GB`, with G5 merge-queue priority) for `pytest`/`mypy`/`tsc`/`vitest`/`Schemathesis`/G5. Never run Postgres inside sandboxes; clone logical DBs (`CREATE DATABASE sbx_<task> TEMPLATE app_template`) under a Postgres advisory lock, and run `pg_terminate_backend` + daemon startup cleanup before refreshing `app_template`. Use `fastembed` (ONNX CPU, <200 MB RAM) for local embeddings.

### 3.5 Verification Gates & Safe Migrations ([Section 11](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L1014-L1050) & [Section 13](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md#L1120-L1196))
- **Sequential Checks, Bounded Schemathesis & Static Frontend Fixtures (`A7-7`, `A7-8`):** Run in-sandbox checks sequentially (`ruff` → `mypy` → `pytest` → export `openapi.json` → bounded `Schemathesis` (`--hypothesis-seed=42 --max-examples=10`, status/schema conformance only) / `tsc --noEmit` → `vitest` with static OpenAPI-validated fixtures). Run `Semgrep` (`--max-memory 512 --jobs 1`), `gitleaks`, and `oasdiff` daemon-side **only after the symlink/FIFO pre-walk**. Push stateful multi-step UI behavior to `kind='integration'` and G6 Playwright tests.
- **Black-Box G3 + Post-Test `Test-Validator` (`A7-7`):** The G3 Test-Writer sees only the task spec, locked contracts, and read-only `conftest.py` (including `seed_user(role=...)`). Record outbound OpenAPI request-validation results per call during test execution and classify `invalid_test_request` **post-test** (only if the test *failed* and received `400`/`422` without `expect_invalid=True`) so broken test code routes back to the Test-Writer, never the Implementer. Route behavioral contract ambiguities to the owning Lead via `x-foundry-rules`.
- **Expand/Contract Migrations:** Enforce backward-compatible (`expand`) migrations via `squawk` in Gate G5 so code-only rollbacks never break against the database schema.

---

## 4. Coding, Tooling & Testing Standards

- **Python Environment:** Python 3.12+, managed exclusively with `uv` (`uv.lock` committed and hash-verified via `uv sync --frozen`, 3–7 day `exclude-newer` cooldown). Use `async`/`await` end-to-end (`AsyncSession` with `asyncpg`, `httpx.AsyncClient`).
- **Type Safety & Linting:** Every Python file must pass `ruff check`, `ruff format --check`, and strict `mypy`. Use Pydantic v2 models for all config, API payloads, and structured LLM outputs.
- **Frontend (Dashboard & Stack Pack):** TypeScript strict mode, `pnpm` (`--ignore-scripts`), React + Vite + Tailwind + `shadcn/ui`.
- **Zero-Cost Test Suite:** Unit and integration tests under `tests/` must run offline at **\$0 API cost** using normalized record/replay cassettes (`miss_policy="strict"`). Live provider smoke tests must be marked with `@pytest.mark.live_llm` and skipped by default in CI.
- **Secrets Safety:** Never hardcode or log API keys, OAuth tokens, or `.env` secrets. Store official API keys via `keyring` or the `scrypt` + `Fernet` encrypted local vault (`~/foundry/vault/credentials.enc`).
