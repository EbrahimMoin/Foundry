# FOUNDRY: Design Document v1.4

**An open-source, local-first, agent-first development environment.**
You open Foundry on your computer, describe the app you want, and a hierarchy of AI agents designs it, builds it in parallel on one codebase under formal conflict control, verifies it, and deploys it through a gated CI/CD pipeline. You supervise and make decisions from a CLI inbox and web dashboard.

> **v1.4** upgrades v1.3 with major **supply-chain, ToS, security, runtime-correctness, and MIV-simplification fixes (`A7-1` to `A7-13`)**:
> 1. **Zero LiteLLM — First-Party SDK Transports + Supply-Chain Hardening (`A7-1`):** Removed `litellm` completely following its 2026 PyPI/Docker supply-chain compromises (`v1.82.7`/`v1.82.8`, `v1.107.0`). Replaced it with 3 thin first-party SDK adapters (`openai_compat` via `openai`, `google_genai` via `google-genai`, `anthropic` via `anthropic`) + hash-verified `uv.lock` (`--frozen`/`--require-hashes`), 3–7 day PyPI release-age cooldown (`exclude-newer`), and `sha256`-pinned Docker images.
> 2. **Honest API-First Model Strategy (`A7-2`):** Dropped multi-step ReAct `cli_bridge` loops and removed `"100% ToS-compliant CLI bridge"` claims (vendors actively restrict automated third-party harnesses over consumer OAuth subscriptions). Official API keys + local Ollama/vLLM are the default; `ModelTransport` remains pluggable for any future opt-in single-shot adapter.
> 3. **Host Escape, Symlink, Network & `.git` Isolation Fixes (`A7-3`, `A7-4`):** Export `openapi.json` strictly inside Docker; reject symlinks (git mode `120000`), FIFOs, and YAML bombs before any host tool reads a worktree; isolate the sandbox workspace without `/dev/null` `.git` bind-mounts; block sandbox access to the blackboard DB (`REVOKE CONNECT … FROM PUBLIC`) and external DNS.
> 4. **Lean Local Daemon Architecture (`A7-5`, `A7-6`, `A7-11`, `A7-12`):** Dropped Redis in v1 in favor of Postgres `events` + `LISTEN/NOTIFY` + serialized monotonic `publish_seq`; switched from idle container slots to a **2-slot heavy-execution semaphore**; reconciled budget caps + added per-run kill-switches; simplified v1 decisions to `Auto` vs `Approve` + **CLI Decision Inbox**; deferred Redis Streams, `Notify+Undo`, Mergiraf/dynamic write-set expansion, and Stage A Cloud Deploy to the **Post-MIV Expansion Backlog** (Section 26.2).
> 5. **Verification & Benchmark Rigor (`A7-7`, `A7-8`, `A7-9`, `A7-10`, `A7-13`):** Post-test `Test-Validator` classification, bounded Schemathesis, honest static frontend fixtures (stateful UI tested in `integration`/G6), `pg_terminate_backend` crash-safe `app_template` refresh, hard `Approve`/allowlist for unplanned `install_package`, and held-out vs. dev benchmark splits with paired bootstrap CIs.

---

## Table of Contents
1. Vision and summary
2. Goals, non-goals, and the modular minimum impressive version
3. Key decisions
4. System architecture overview
5. How Foundry runs (local-first runtime)
6. Agent hierarchy and roles
7. LangGraph design
8. The shared blackboard (schema + event loop)
9. Coordination protocol
10. Concurrency control layer
11. Verification and quality gates
12. Stack packs
13. CI/CD and deployment
14. Infrastructure as code (Terraform + infra-mcp)
15. MCP tool layer
16. Model gateway: first-party SDK transports & supply-chain safety
17. Human-in-the-loop decision system
18. Budget and cost model
19. Security, safety, and privacy
20. Observability
21. Dashboard and daemon API
22. Configuration files
23. Evaluation and benchmark
24. Tech stack
25. Modular repository structure
26. Modular roadmap (MIV Modules 1–4 + Deferred Post-MIV Backlog)
27. Risks and mitigations
28. Resume bullets and interview talking points
29. Distribution, system requirements, and future work
30. Glossary
- Appendix A: Review resolutions (v1.0 → v1.4)

---

## 1. Vision and summary

### Vision
Foundry is an **agent-first development environment**. Traditional IDEs are built for humans who write code with some AI help. Foundry flips this: **AI agents write the code, and the human acts as architect, decision-maker, and supervisor.** It runs on your machine, uses your own official API keys or local models (Ollama/vLLM) through a minimal first-party SDK gateway, and deploys to local blue/green or your own cloud accounts.

### What it does
1. You describe an app: *"A team task manager with auth, projects, and real-time updates."*
2. The **Orchestrator** turns it into requirements, an architecture, and a feature list.
3. **Domain Leads** design **contracts** in a fixed order: DB schema → API (OpenAPI) → UI components and infrastructure.
4. **Wave 1 (Foundation):** shared foundations (models + initial migration, auth, app shell, dependencies, CI) are built and merged first.
5. **Wave 2 (Features):** **Implementers** build features **in parallel on the same codebase**, under formal conflict control.
6. **Verifiers** test every feature black-box against the contracts and review the risky changes.
7. A **gated pipeline** merges, runs migration-safety checks, deploys to a local blue/green target (or optional Stage A/B cloud targets), runs Playwright E2E smoke tests, and auto-rolls back on failure.
8. You make the decisions you choose to make (configurable), via the CLI Decision Inbox or live Dashboard, and preview the app as it grows.

### The problem it solves
Multi-agent coding systems usually fail in three ways:
1. **Lost shared context:** split work makes conflicting assumptions, so the parts don't fit together.
2. **Errors compound:** small mistakes spread through every layer below them.
3. **Concurrent edits collide:** agents overwrite each other or break code that others depend on.

| Problem | Foundry's solution |
|---|---|
| Lost shared context | Blackboard: **exact facts in Postgres** (contracts, decisions) + code and lessons search + Postgres outbox events |
| Parts don't fit | **Contract-first** design, **checked automatically** (oasdiff conformance, bounded Schemathesis conformance, test-ID checks) |
| Concurrent edits | **Concurrency control layer**: write-set reservations, diff enforcement, single-writer shared files, serial merge queue |
| Errors compound | Gates after every task, escalation ladder, black-box verifiers from a different model family, a test-dispute protocol |
| Endless revision loops | Contract change **classification, batching, and revision caps**, plus freeze-and-escalate |
| Unsafe autonomy | Configurable human-in-the-loop + hardcoded approval for releases, infrastructure, and unplanned packages |
| High multi-agent API costs & supply-chain risk | **Zero-LiteLLM First-Party SDK Gateway** (`openai`, `google-genai`, `anthropic`) supporting low-cost APIs (Gemini Flash-Lite, DeepSeek, Groq, xAI, OpenRouter preset) + local Ollama/vLLM + native prefix caching + normalized \$0 record/replay |
| "Does it work?" | A benchmark with **pinned interfaces**, held-out specs, and hidden tests, compared at **strictly equal API cost** against a single agent |

### Positioning (honest)
Role-based agent teams, shared state, and auto-deployed apps all exist already:
- **MetaGPT** and **ChatDev** use role hierarchies with shared state.
- **Replit Agent, Lovable, and Bolt** build and deploy apps.
- **Devin, OpenHands, and OpenCode** run autonomous coding sessions.

Foundry doesn't claim to have invented those. **What Foundry adds:**

| Area | Existing tools (typical) | What's new in Foundry |
|---|---|---|
| Concurrent editing | One writer at a time, or editing until merge conflicts appear | **Many agents edit one codebase concurrently under formal conflict control** (reservations, diff enforcement, single-writer shared files, merge queue), with conflict metrics reported |
| Contracts | Implied in prompts or docs | Machine-checked contracts: **oasdiff conformance + bounded Schemathesis + test-ID checks**; locked regions; change classification |
| Verification | Same model writes and checks | Black-box tests from a **different model family**, a deterministic post-test validator, a dispute protocol |
| Human control | Approve-everything or approve-nothing | **4 categories × binary `Auto`/`Approve`/`Human` modes + risk-based escalation**; only dependent work waits; CLI inbox + Web UI |
| Model & supply-chain safety | Vendor-locked credits or bloated third-party proxy aggregators | **Minimal First-Party SDK Gateway** (`openai`, `google-genai`, `anthropic` — zero LiteLLM): covers OpenAI, Gemini, Anthropic, xAI, Groq, DeepSeek, Mistral, OpenRouter preset, and local Ollama/vLLM with exact prefix-caching telemetry |
| Evidence | Demos | **Measured ablations at strictly equal API cost** on held-out specs, with paired bootstrap confidence intervals and honest parallelism-speedup numbers |
| Deployment | Hosted by the vendor | **Local blue/green target in MIV** + optional Stage A/B cloud deploy to your own accounts with migration-safe rollback |
| Openness | Closed SaaS | **Open source, local-first, modular architecture** |

---

## 2. Goals, non-goals, and the modular minimum impressive version

### Goals
- Build **small-to-medium full-stack web apps** from a written spec, end to end.
- Let **multiple agents work concurrently** on one codebase without conflicts, and measure honestly whether parallelism beats a single agent.
- Give the human **configurable control** over decisions (CLI Decision Inbox in Module 2, Dashboard in Module 3).
- **Deploy reliably:** a deterministic **local blue/green target** in MIV, with optional Vercel + Render + Neon (Stage A) and AWS/Azure Terraform (Stage B) in Post-MIV.
- Support **every major LLM provider via 3 minimal, first-party official SDKs** (`openai`, `google-genai`, `anthropic`) + local Ollama/vLLM — **zero LiteLLM**, zero unauthorized OAuth token scraping, and strict supply-chain lockfile/digest pinning.
- Run on a **16 GB RAM Windows laptop (WSL2 with native Docker Engine)** within **\$0–25/month** (using normalized \$0 record/replay for engine dev and low-cost/cached API tiers or local models for live runs).
- **Develop modularly** so every stage is a working, polished software tool you can actually use immediately.
- **Prove it works** with an honest, equal-API-cost benchmark on held-out specs.
- Open source (Apache-2.0), well documented.

### Non-goals (v1 / MIV)
- Arbitrary software (v1 builds one stack: FastAPI + React/Vite + Postgres).
- Hosted multi-user SaaS.
- Kubernetes.
- Adopting existing brownfield codebases (future expansion after MIV).
- Mobile, games, embedded.
- *(Note on naming: `"Foundry"` is the working codename; before public PyPI/GitHub release we may pick a distinct name to avoid collision with Palantir Foundry, Azure AI Foundry, and Ethereum Foundry.)*

### Modular Architecture & Minimum Impressive Version (MIV)
Foundry is architected into **4 lean, plug-and-play modules** (spanning roadmap phases P0–P7 for the MIV), where each module is a complete, runnable software release:
- **Module 1 — Core Kernel, First-Party SDK Gateway & Single-Agent Builder (P0–P2):** CLI (`foundry`), First-Party SDK Model Gateway (`openai_compat`, `google_genai`, `anthropic`) + secret scanner + normalized record/replay, hardened Docker/worktree sandbox runner (`.git`-free workspace, Postgres logical template DBs), and a working Single-Agent full-stack builder with G1/G2 checks. *(Usable on Day 1, and serves as Baseline `B1`.)*
- **Module 2 — Postgres Blackboard, Staged Contracts & Two-Wave Hierarchy (P3):** Postgres Blackboard + `LISTEN/NOTIFY` outbox with monotonic `publish_seq` (no Redis), **CLI Decision Inbox**, Orchestrator + Domain Leads (`Data → Backend → Frontend/Infra`), locked contracts, and two build waves (`Wave 1 Foundation → Wave 2 Features`).
- **Module 3 — Parallel Concurrency, Black-Box Verification & Dashboard v0.5 (P4–P5):** Static write-set reservations (`pg_advisory_xact_lock`), daemon `git diff` + symlink enforcement, serial rebase-and-test merge queue, 2-slot heavy-execution semaphore, contract-change protocol, cross-family Black-Box Test-Writer (G3), deterministic post-test Validator, Dispute Protocol, G4 Reviewer, and Dashboard v0.5.
- **Module 4 — Local Blue/Green CI/CD & Held-Out Benchmark Release (P6–P7, completes MIV):** `LocalComposeTarget` (Caddy blue/green + 60s probe + Playwright smoke + automatic code-only rollback), `squawk` expand-only migration gate, plus the **MIV Pilot Benchmark** (`B1` vs `F0` at strictly equal API cost on held-out specs with paired bootstrap CIs).

Everything deferred to keep v1 lean (Stage A Vercel/Render/Neon deploy, Stage B Terraform AWS/Azure, Redis Streams scaling, `Notify + Undo`, Mergiraf AST merge, mid-task write-set expansion, 5-min pause preemption, opt-in single-shot CLI adapters, and Dashboard v1.0) is catalogued cleanly in the **Deferred / Post-MIV Expansion Backlog** ([Section 26.2](file:///c:/Users/ebrah/OneDrive/Desktop/Foundry/foundry_design_document.md)) so every piece can be plugged in cleanly later without rewrites.

---

## 3. Key decisions

| # | Decision | Choice | Reason |
|---|---|---|---|
| D1 | Product form | Local-first: a Python daemon **running inside WSL2** (Linux/macOS natively) + CLI inbox + web UI at `localhost:4200`; optional desktop wrapper (Tauri) later | Fast filesystem, native Docker Engine, simple setup |
| D2 | Agent framework | **LangGraph** for agent logic, with **one thread per lead, per task, and per verification**; custom deterministic services for scheduling, reservations, the merge queue, and Postgres events | Avoids `Send` + `interrupt()` blocking sibling branches |
| D3 | First stack pack | FastAPI + React (Vite) + TS + Postgres | Clean frontend/backend boundary, deploys anywhere |
| D4 | Next.js | Later, as Stack Pack #2 | Blurs the frontend/backend domain boundary |
| D5 | Dashboard | React + Vite + TS + Tailwind + shadcn/ui (CLI Decision Inbox ships first in M2) | Clean incremental UX without blocking M2 |
| D6 | Deploy path | **MIV:** `LocalComposeTarget` (Docker Compose blue/green + Caddy + Playwright smoke + auto-rollback). **Post-MIV:** Stage A (Vercel + Render + Neon) → Stage B (AWS/Azure Terraform) | Keeps MIV self-contained, deterministic, and immune to volatile free-tier changes |
| D7 | Event delivery & Redis | **Postgres-only in v1:** transactional `events` outbox + `LISTEN/NOTIFY` + serialized monotonic `publish_seq` + in-process `asyncio` dispatcher + `processed_events` (no Redis container) | Single local daemon needs no distributed broker; fixes `bigserial` out-of-order commit gaps; saves RAM and failure modes |
| D8 | Models & supply chain | **First-Party SDK Gateway (`openai_compat`, `google_genai`, `anthropic`) — zero LiteLLM** + strict supply-chain lockfile (`uv.lock --frozen`, `exclude-newer` cooldown, `sha256` Docker digests). Official API keys + local Ollama/vLLM by default; no consumer-OAuth scraping or ReAct CLI loops | Eliminates 2026 LiteLLM supply-chain blast radius, saves ~200 MB RAM, guarantees native tool calling + prefix caching + zero ToS ban risk |
| D9 | Sandboxes & RAM slots | **Lightweight Docker containers** (`.git`-free workspace, no Postgres inside, 1.5 GB cap) + **shared Postgres with one logical DB per task** + **2-slot heavy-execution semaphore** (separating idle LLM thinking from heavy `pytest`/`mypy`/`tsc`/G5 runs) | Security (network off, path jail, symlink checks) at low RAM cost without stalling idle LLM waits |
| D10 | Local embeddings | **fastembed (ONNX Runtime, CPU)** | No PyTorch/CUDA RAM overhead (<200 MB) |
| D11 | Secrets | `keyring` where a working backend exists; otherwise an **encrypted local vault** (scrypt + Fernet), unlocked at `foundry up`, storing API keys | `keyring` fails on WSL2 without D-Bus |
| D12 | Tracing | **Local by default** (own `llm_calls` + `events` tables); optional exporters: Phoenix (local), LangSmith (cloud, opt-in, redacted) | Privacy + RAM |
| D13 | Benchmark interface | **Pinned API contract & test-IDs** in every benchmark spec, **strictly split into Dev vs. Held-Out specs**, run on first-party APIs with pinned models and exact token telemetry, scored with paired bootstrap CIs | Eliminates overfitting, shadow-cost estimation error, and scaffolding bias |
| D14 | Migrations in production | **Expand/contract only** (`squawk` enforced in G5); destructive changes need a separate, human-approved release; backup before migrating | Code-only rollback is always safe |
| D15 | Architecture style | **Modular Kernel + Pluggable Subsystems** (Protocols for `ModelTransport`, `SandboxProvider`, `DeployTarget`, `StackPack`) | Build and use incrementally without rewrites |
| D16 | License & Git | Apache-2.0; GitHub for repos and CI | Business-friendly, standard ecosystem |

---

## 4. System architecture overview

```
 Windows browser ──► http://localhost:4200 (WSL2 forwards localhost)
┌───────────────────────────────────────────────────────────────────────────┐
│                FOUNDRY DASHBOARD (React) / CLI INBOX                      │
└───────────────────────────────┬───────────────────────────────────────────┘
                      REST + WebSocket (?after_seq=)
┌───────────────────────────────▼───────── WSL2 (Ubuntu) ───────────────────┐
│                        FOUNDRY DAEMON (Python)                              │
│ ┌──────────────┐ ┌───────────────────┐ ┌───────────────┐ ┌──────────────┐ │
│ │ API Server   │ │ Run Supervisor    │ │ Decision Svc  │ │ Event Relay  │ │
│ │ FastAPI + WS │ │ scheduler, waves, │ │ Auto/Approve, │ │ PG outbox +  │ │
│ └──────────────┘ │ exec semaphore(2),│ │ CLI/UI inbox, │ │ publish_seq  │ │
│                  │ budgets, threads  │ │ idemp. resume │ │ (in-process) │ │
│                  └─────────┬─────────┘ └───────────────┘ └──────────────┘ │
│  ┌─────────────────────────▼──────────────────────────────────────────┐    │
│  │ AGENT LAYER: independent LangGraph threads                          │    │
│  │  {run}:project │ {run}:lead:{domain} │ {run}:task:{id}:{attempt}    │    │
│  │  {run}:verify:{feature}   (AsyncPostgresSaver checkpoints)          │    │
│  └─────────────────────────┬──────────────────────────────────────────┘    │
│                            │ MCP tools                                      │
│ ┌──────────────┐ ┌─────────▼─────────┐ ┌──────────────┐ ┌───────────────┐  │
│ │ Concurrency  │ │ MCP servers       │ │ Verification │ │ Pipeline Svc  │  │
│ │ reservations │ │ blackboard, git*, │ │ G1–G7, post- │ │ DeployTargets:│  │
│ │ diff+symlink │ │ sandbox, ci,      │ │ test validator│ │ LocalCompose  │  │
│ │ merge queue  │ │ deploy, docs      │ │ disputes     │ │ (+ Post-MIV)  │  │
│ └──────────────┘ └───────────────────┘ └──────────────┘ └───────────────┘  │
│ ┌────────────────────────────────────────────────────────────────────────┐ │
│ │ MODEL GATEWAY (Zero-LiteLLM First-Party Official SDK Transports):      │ │
│ │ Transports: [openai_compat] [google_genai] [anthropic] (pluggable)     │ │
│ │ Profiles, assignment, rate-limit router, budgets, normalized replay    │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│   * git & static scanners run daemon-side only AFTER symlink/FIFO checks   │
└──────┬──────────────────┬─────────────────────┬─────────────────┬──────────┘
┌──────▼───────┐ ┌────────▼─────────┐ ┌─────────▼───────┐ ┌───────▼─────────┐
│ Postgres 16  │ │ Sandboxes        │ │ Repo + worktrees│ │ External: GitHub│
│ + pgvector   │ │ internal network │ │ on WSL ext4     │ │ Official APIs:  │
│ blackboard,  │ │ no DNS/egress;   │ │ (.git kept out  │ │ OpenAI, Gemini, │
│ checkpoints, │ │ scoped proxy for │ │  of container)  │ │ Anthropic, xAI, │
│ events,      │ │ install_package  │ └─────────────────┘ │ Groq, DeepSeek, │
│ sandbox DBs  │ ├──────────────────┤                     │ Ollama/vLLM     │
└──────────────┘ │ Egress proxy     │                     └─────────────────┘
                 └──────────────────┘
```

| Component | Responsibility |
|---|---|
| Dashboard / CLI Inbox | Visualization and human decisions (CLI Decision Inbox in M2, React Dashboard v0.5 in M3) |
| API Server | REST + WebSocket (`?after_seq=` replay using monotonic `publish_seq`) |
| Run Supervisor | Waves, task scheduling, **2-slot heavy-execution semaphore**, budgets & run kill-switch, spawning/resuming LangGraph threads, deadlock detection |
| Agent Layer | Independent LangGraph threads (reasoning and workflows) |
| Decision Service | Autonomy policy (`Auto` vs `Approve`/`Human`), idempotent `get_or_create`, resuming paused threads |
| Event Relay | Single-process Postgres outbox relay (`LISTEN/NOTIFY` → assigns monotonic `publish_seq` → dispatches to in-process consumers deduped by `processed_events`) |
| Concurrency Control | Write-set reservations, daemon diff + symlink enforcement, single-writer shared files, serial merge queue |
| Verification | Gates G1–G7, post-test `Test-Validator`, Dispute Protocol |
| Pipeline Service | CI, `DeployTarget` (`LocalComposeTarget` in MIV; Stage A/B in Post-MIV), expand-only migrations, rollback, incidents |
| Model Gateway | All LLM calls via 3 first-party SDKs (`openai`, `google-genai`, `anthropic`): routing, rate-limit backoff, exact token/cache ledger, secret scanning, normalized record/replay |

---

## 5. How Foundry runs (local-first runtime)

### 5.1 User experience
```bash
# inside WSL2 (Ubuntu) on Windows (with native Docker Engine), or natively on Linux/macOS
uv tool install foundry-dev
foundry init                      # checks Docker, creates the vault, adds official API keys or local Ollama
foundry up                        # starts Postgres + egress proxy + daemon; open http://localhost:4200
foundry new "Team task manager with auth and real-time updates"
foundry decisions list            # CLI Decision Inbox (usable before the Dashboard exists)
```

### 5.2 Where things run
| Process | Where | Notes |
|---|---|---|
| Foundry daemon | **Native Python inside WSL2** | Repos and worktrees on the WSL ext4 filesystem (never `/mnt/c`) |
| Dashboard | Static files served by the daemon | Viewed in the Windows browser via localhost forwarding |
| Postgres 16 + pgvector | Docker (**pinned by `sha256` digest**) | Blackboard, checkpoints, **outbox events (`publish_seq`)**, and per-task sandbox DBs (no Redis in v1) |
| Egress proxy | Docker (tinyproxy, **pinned by `sha256` digest**) | Allowlists package registries only; injected strictly into approved `install_package` calls |
| Sandboxes | Docker, temporary, **2-slot heavy-execution semaphore** | Up to 3–4 lightweight task containers may wait on LLM API I/O (~20 MB idle), while at most **2 heavy tool runs** (`pytest`/`mypy`/`tsc`/`vitest`/`Schemathesis`/G5) execute concurrently (G5 holds priority over G3/G2/G1) |
| Release environment | Docker Compose (on demand) | App containers + Playwright Chromium for G6; takes both heavy-exec slots while running |
| Local deploy target | Docker Compose blue/green + Caddy | Used for development, MIV deploys, and the benchmark |

### 5.3 Memory budget (16 GB host, native Docker Engine inside WSL2, `.wslconfig memory=10GB`)
> **Important:** These numbers assume **Docker Engine installed directly inside WSL2** (not Docker Desktop, which adds a separate VM overhead).

| Item | Typical | Peak |
|---|---|---|
| **Outside WSL:** Windows + browser + editor | 5 GB | 6 GB |
| WSL kernel + page cache baseline | 0.6 GB | 1.0 GB |
| Docker Engine (native inside WSL2) | 0.3 GB | 0.3 GB |
| Postgres (incl. outbox + sandbox DBs) | 0.4 GB | 0.7 GB |
| Egress proxy | 0.05 GB | 0.05 GB |
| Daemon (Python, LangGraph, 3 first-party SDKs, fastembed ONNX, static scanners — **zero LiteLLM saves ~200 MB**) | 0.35 GB | 0.65 GB |
| Active heavy tool executions: 2 semaphore slots × (cap 1.5 GB, sequential checks) | 1.6 GB | 3.0 GB |
| App preview (Vite dev + FastAPI) | 0.6 GB | 0.9 GB |
| Release env (G6: app + Chromium) | – | 1.5 GB (*only while heavy-exec slots are paused*) |
| **WSL total** | **~3.9 GB** | **~7.4 GB of 10 GB** |

`.wslconfig`:
```ini
[wsl2]
memory=10GB
processors=6
swap=4GB

[experimental]
autoMemoryReclaim=gradual
```

### 5.4 Sandbox specification
| Property | Value |
|---|---|
| Image | `ghcr.io/<you>/foundry-sandbox-fastapi-react@sha256:<digest>` (**digest-pinned**, never `:latest`) with Python 3.12, Node 22, `uv`, `pnpm`, and the stack pack's standard dependencies **pre-installed**. No Playwright browsers (those live only in the release env image). Boots in about 1 s. |
| Mounts & `.git` isolation (`A7-4`) | The container mounts a **`.git`-free workspace directory** synced from/to the daemon's git worktree (so `/workspace/.git` **does not exist** inside Docker — avoiding broken `hatchling`/`pytest`/`ruff`/`vite` tooling caused by `/dev/null` bind-mounts). The repo's `.git` dir is **never mounted**. `uv` cache and `pnpm` store mounted **read-only**. |
| Database isolation (`A7-3`, `A7-9`) | `DATABASE_URL` for a logical DB `sbx_<task>` created via `CREATE DATABASE sbx_<task> TEMPLATE app_template` under a Postgres advisory lock (with startup cleanup + `pg_terminate_backend` before template refreshes). `REVOKE CONNECT ON DATABASE foundry_blackboard FROM PUBLIC` ensures sandbox DB credentials can connect **only** to `sbx_<task>` and can never reach the blackboard or checkpoint tables. Dropped on cleanup. |
| Limits | `--memory=1.5g --cpus=1.5 --pids-limit=512`, `NODE_OPTIONS=--max-old-space-size=768` |
| Hardening | Non-root user, `--cap-drop=ALL`, `no-new-privileges`, read-only root filesystem + tmpfs `/tmp`, default seccomp, **never the Docker socket** |
| Network (`A7-3`, `A7-10`) | Dedicated internal Docker network (`internal: true`, **no external egress and no external DNS resolution**). The host daemon and blackboard DB are unreachable from the sandbox network. `HTTP(S)_PROXY` → egress proxy (allowlist: `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`) is injected **strictly into the single approved `install_package` `docker exec` call**, never in the container's default environment. |
| Secrets | **None.** No LLM API keys or deploy tokens ever enter a sandbox. All LLM calls happen in the daemon. |

> **Why containers and not just host venvs?** Container overhead itself is small (tens of MB). The memory goes to the processes inside (tsc, pytest). Containers give us the network jail, path jail, and resource caps that host venvs can't. Separating idle containers from the **2-slot heavy-execution semaphore** prevents LLM network latency from wasting RAM slots.

### 5.5 Sandbox providers (pluggable)
```python
class SandboxProvider(Protocol):
    async def create(self, task_id: str, image: str, workspace: Path, env: dict) -> Sandbox
    async def exec(self, sandbox: Sandbox, cmd: list[str], *, network: bool = False,
                   heavy: bool = False, timeout_s: int = 600) -> ExecResult
    async def destroy(self, sandbox: Sandbox) -> None
```
v1: `LocalDockerProvider`. Later: `RemoteDockerProvider` (SSH to your own VM) and managed sandboxes (E2B/Daytona), for users with weaker machines (section 29).

### 5.6 Workspace on disk (inside WSL)
```
~/foundry/
├── config/ foundry.yaml, models.yaml
├── vault/credentials.enc            # stores API keys when keyring is unavailable
└── projects/<app>/
    ├── repo/                        # main git repo
    ├── worktrees/<task_id>/         # daemon-owned git worktree (never mounted directly with .git)
    ├── workspaces/<task_id>/        # .git-free container workspace synced with worktrees/<task_id>/
    ├── .foundry/ project.yaml, cassettes/
    └── logs/
```

### 5.7 Secrets Storage (No Token Extraction)
- **Backend order:** OS keychain via `keyring` (macOS Keychain, Windows Credential Manager via WSL interop, Linux Secret Service), **if a working backend is detected** → otherwise an **encrypted vault** (`vault/credentials.enc`: Fernet, key derived with scrypt from a passphrase entered at `foundry up` and kept only in daemon memory).
- **Strict Account Safety:** Foundry stores only official developer API keys in its vault and **never** scrapes, copies, or spoofs internal IDE/CLI OAuth tokens (`~/.gemini`, `~/.codex`, `~/.claude`, Cursor, Antigravity, etc.).
- Keys are **never** written to the repo, prompts, traces, or sandboxes.

---

## 6. Agent hierarchy and roles

```
                       Orchestrator
     ┌──────────────┬───────┴──────┬──────────────┐
 Data Lead      Backend Lead   Frontend Lead    Infra Lead
     │              │              │               │
 Data impl.     BE impl. ×N    FE impl. ×N     Infra impl.
     └────── Verifiers: Black-box Test-Writer, Reviewer ──────┘
     Deterministic: Test-Validator, Merge Queue, Contract Checkers
             Integration Agent          Release Agent
```

| Agent | Responsibilities | Default profile (First-Party API / Local) | Key tools |
|---|---|---|---|
| **Orchestrator** | Requirements, architecture, feature list, wave plan, budget split, cross-domain re-planning, arbitration of frozen contracts, human chat | `@planner` (Pro/Frontier tier, **only for key calls**) | blackboard, decisions |
| **Data Lead** | **Schema contract** (entities, relations, indexes); approves schema changes. Does **not** write migrations itself | `@lead` (Mid/Flash/Sonnet tier) | blackboard, docs |
| **Backend Lead** | **OpenAPI contract** built on the schema contract, using stack-pack conventions (auth scheme, pagination envelope, error schema, per-op `x-foundry-rules`); backend task breakdown + write sets; settles API test disputes | `@lead` | blackboard, docs |
| **Frontend Lead** | **Component and route contracts**, including the required `data-testid`s and each component's export path; frontend tasks; settles UI test disputes | `@lead` | blackboard, docs |
| **Infra Lead** | Dependency list (declared in planning), CI config, deploy config, Terraform (Stage B); approves unplanned dependency requests | `@lead` | blackboard, ci, infra (plan only) |
| **Implementer** | One task in its own worktree + `.git`-free sandbox. Domain-specific variants: **Data implementer** (models + migrations, the only writer of `models/` and `migrations/`), Backend, Frontend, Infra | `@coder` (Fast/Lite API or Local tier), upgraded to `@coder_plus` on attempt 2+ | sandbox, git (via daemon), blackboard (task scope), docs |
| **Black-box Test-Writer** | **Per feature:** writes **only** API tests (against OpenAPI, through the `client` fixture) and component tests (against contract export paths + `data-testid`s). Reads `conftest.py` (read-only), never the implementation | `@verifier` (Different model family than `@coder`, e.g. Grok/OpenAI/Claude) | sandbox (tests only), blackboard (spec + contracts) |
| **Reviewer** | **Risk-based:** reviews diffs touching auth/security, shared code, migrations, or more than 300 changed lines; a random 25% sample of the rest | `@verifier` | git (read), blackboard |
| **Test-Validator** *(deterministic, not an LLM)* | Classifies test failures: errors in the tests themselves vs real assertion failures | – | sandbox |
| **Integration Agent** | Resolves merge conflicts the structural merge can't handle | `@coder_plus` | git (via daemon), sandbox |
| **Release Agent** | Runs the pipeline, warm-ups, probes, rollbacks, incidents | `@fast` | ci, deploy, infra (apply with approval token) |

**Agent lifetimes:**
- Leads are **long-lived threads** that wake on events.
- Implementers and verifiers are **started per task or feature** and released afterwards.

---

## 7. LangGraph design

### 7.1 Split of responsibilities
| LangGraph | Deterministic services |
|---|---|
| Agent loops (plan → act → check) | Scheduling, waves, sandbox slots |
| Project and lead workflows | Write-set reservations, diff enforcement |
| Human pauses (`interrupt()`) | Merge queue, dependency application |
| Checkpointing / crash resume | Event outbox + relay |
| Tool calling via `langchain-mcp-adapters` | Contract checkers, test validator, deploy targets |

### 7.2 Independent threads (no `Send` fan-out across domains)
In LangGraph, an `interrupt()` inside one branch of a `Send` fan-out pauses the **whole thread's superstep**. So Foundry **never runs independent work inside one thread**. The Run Supervisor spawns separate threads:

| Thread ID | Graph | Lifetime |
|---|---|---|
| `{run}:project` | Project graph (Orchestrator) | Whole run |
| `{run}:lead:{domain}` | Lead graph | Whole run (sleeps between events) |
| `{run}:task:{task_id}:{attempt}` | Implementer graph | One attempt |
| `{run}:verify:{feature}:{round}` | Test-Writer graph | One verification round |

A pause in one thread blocks only that thread and the tasks that depend on it (via the task graph).

### 7.3 Project graph (phases, with waits)
```
START
 → draft_requirements → approve_requirements      (decision: Strategic)
 → draft_architecture → approve_architecture      (decision: Strategic)
 → plan_contracts      (supervisor runs leads in order: Data → Backend → {Frontend ∥ Infra};
                        waits for event `contracts.locked`)
 → wave1_foundation    (supervisor schedules foundation tasks; waits for `wave.completed`)
 → wave2_features      (supervisor schedules feature tasks; handles escalations;
                        waits for `wave.completed`)
 → release             (Release Agent thread; waits for `release.completed`)
 → monitor             (incidents → fix tasks → back to wave2_features)
END
```
**Waiting pattern:** a phase node asks the supervisor to start work (an *idempotent* call), then calls `interrupt({"wait_for": ...})`. The supervisor resumes the thread with `Command(resume=event)` when the condition is met.

### 7.4 Re-run-safe nodes (code sketch)
When a paused LangGraph node resumes, it **runs again from the top**. So:
1. LLM calls happen in **separate "draft" nodes**, whose results are checkpointed and never repeated.
2. Side effects before `interrupt()` must be **idempotent**, keyed by `thread_id + node + revision`.

```python
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

class ProjectState(TypedDict, total=False):
    run_id: str
    spec: str
    req_revision: int
    requirements_draft: dict | None
    requirements: dict | None
    feedback: str | None
    contracts_version: int | None

async def draft_requirements(state: ProjectState) -> dict:
    # LLM call lives alone in this node; its output is checkpointed when the node completes.
    draft = await orchestrator.draft_requirements(state["spec"], feedback=state.get("feedback"))
    return {"requirements_draft": draft}

async def approve_requirements(state: ProjectState, config) -> dict:
    tid = config["configurable"]["thread_id"]
    key = f"{tid}:approve_requirements:{state.get('req_revision', 0)}"
    # get_or_create is idempotent: re-running this node after resume returns the SAME decision.
    decision = await decisions.get_or_create(
        idempotency_key=key, category="strategic",
        title="Approve requirements", payload=state["requirements_draft"], thread_id=tid)
    if decision.resolved_without_human:                 # Auto / Notify modes
        return {"requirements": state["requirements_draft"]}
    answer = interrupt({"decision_id": decision.id})    # pauses ONLY this thread
    if answer["action"] == "approve":
        return {"requirements": state["requirements_draft"]}
    return {"requirements": None, "requirements_draft": None,
            "feedback": answer.get("feedback"),
            "req_revision": state.get("req_revision", 0) + 1}

def after_approval(state: ProjectState) -> str:
    return "draft_architecture" if state.get("requirements") else "draft_requirements"

async def plan_contracts(state: ProjectState, config) -> dict:
    # Idempotent: starts lead threads only if not already started for this run.
    await supervisor.ensure_contract_planning(state["run_id"],
                                              order=[["data"], ["backend"], ["frontend", "infra"]])
    event = interrupt({"wait_for": "contracts.locked", "run_id": state["run_id"]})
    return {"contracts_version": event["version"]}

builder = StateGraph(ProjectState)
builder.add_node("draft_requirements", draft_requirements)
builder.add_node("approve_requirements", approve_requirements)
builder.add_node("draft_architecture", draft_architecture)
builder.add_node("approve_architecture", approve_architecture)
builder.add_node("plan_contracts", plan_contracts)
builder.add_edge(START, "draft_requirements")
builder.add_edge("draft_requirements", "approve_requirements")
builder.add_conditional_edges("approve_requirements", after_approval,
                              ["draft_architecture", "draft_requirements"])
# ... architecture, plan_contracts, wave1_foundation, wave2_features, release, monitor

async with AsyncPostgresSaver.from_conn_string(DB_URL) as saver:
    graph = builder.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": f"{run_id}:project"}}
    await graph.ainvoke({"run_id": run_id, "spec": spec, "req_revision": 0}, cfg)
    # Later, when the human answers or a waited-for event fires:
    # await graph.ainvoke(Command(resume=answer_or_event), cfg)
```

### 7.5 Implementer graph
```
load_context   → task row + subscribed contract rows (exact JSON) + relevant decisions
                 + code search (signatures) + lessons. Never raw chat logs.
plan           → short step plan; confirm the planned write set
act            → bounded ReAct loop (max 25 steps) over MCP tools using native SDK tool calling
                 (compact older tool outputs >6 steps back to keep prefix cache hot and prompts bounded)
boundary       → loop boundary: apply buffered contract diffs; if an upstream schema barrier
                 merged, rebase onto main + re-clone sbx_<task> (on conflict: discard WIP branch,
                 increment attempt, restart cleanly from new main SHA — A7-9)
self_check     → G1 inside sandbox (acquires 1 of 2 heavy-exec semaphore slots; tools run sequentially
                 to stay under 1.5 GB RAM; exports openapi.json inside Docker as static artifact)
  ├ fail, attempts left  → act (with error feedback)
  └ fail, none left      → escalate (release reservations, report to lead)
commit         → daemon-side symlink/FIFO walk + sync clean workspace → git commit (write-set diff enforced)
report         → task report → status IN_REVIEW (supervisor continues reservation heartbeat)
```

---

## 8. The shared blackboard (schema + event loop)

Three layers (all in **Postgres 16 + pgvector** in v1 — zero Redis overhead):
- **Hard memory** (Postgres tables): exact facts
- **Fuzzy memory** (pgvector): code search, lessons, docs
- **Event delivery** (Postgres transactional outbox + `LISTEN/NOTIFY` + serialized monotonic `publish_seq` + in-process `asyncio` dispatcher)

### 8.1 Hard/fuzzy memory split (strict)
| Information | Stored in | How implementers get it |
|---|---|---|
| Contracts, schemas, task specs, decisions, ownership, reservations | **Postgres rows only** | Injected verbatim (exact JSON) into the prompt |
| Existing code signatures | pgvector `code_chunks` (tree-sitter) | Code-search tool |
| Lessons from past failures (across projects) | pgvector `lessons` | Top-k injected, labelled "advisory" |
| Library docs | pgvector `docs` (**pre-indexed locally, curated, versioned**) | `docs-mcp` search; **no live web fetching** |
| Agent notes / messages | Postgres (`notes`, `threads`, `messages`), linked to tasks and features | Only notes linked to the task's feature; **never full chat logs** |

**Rule:** anything an agent must get exactly right comes from Postgres, never from vector search.

### 8.2 Core schema (DDL)
```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------- runs & agents ----------
CREATE TABLE runs (
  id            text PRIMARY KEY,                -- 'run_<ulid>'
  project_id    text NOT NULL,
  phase         text NOT NULL DEFAULT 'planning', -- planning|contracts|wave1|wave2|release|monitor|done
  budget_usd    numeric(8,2) NOT NULL,
  spent_usd     numeric(8,4) NOT NULL DEFAULT 0,
  data_policy   jsonb NOT NULL DEFAULT '{"allow_free_tier": false, "allow_trace_export": false}',
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz
);

CREATE TABLE agents (
  id              text PRIMARY KEY,
  run_id          text NOT NULL REFERENCES runs(id),
  role            text NOT NULL,     -- orchestrator|lead|implementer|test_writer|reviewer|integration|release
  domain          text,              -- data|backend|frontend|infra
  parent_agent_id text REFERENCES agents(id),
  model_profile   text NOT NULL,
  status          text NOT NULL DEFAULT 'idle',
  graph_thread_id text
);

-- ---------- tasks ----------
CREATE TYPE task_status AS ENUM (
  'PENDING','BLOCKED','READY','IN_PROGRESS','IN_REVIEW','VERIFIED',
  'QUEUED','MERGED','DEPLOYED','FAILED','ESCALATED','FROZEN','CANCELLED');

CREATE TABLE tasks (
  id                   text PRIMARY KEY,               -- 't_<ulid>'
  run_id               text NOT NULL REFERENCES runs(id),
  wave                 smallint NOT NULL,              -- 1 foundation, 2 features, 3 release/fix
  domain               text NOT NULL,
  feature              text,
  kind                 text NOT NULL DEFAULT 'build',  -- build|schema|delta|fix|integration|deps
  title                text NOT NULL,
  description          text NOT NULL,
  acceptance           jsonb NOT NULL DEFAULT '[]',
  status               task_status NOT NULL DEFAULT 'PENDING',
  owner_agent_id       text REFERENCES agents(id),
  parent_task_id       text REFERENCES tasks(id),      -- for delta/fix tasks
  attempt              smallint NOT NULL DEFAULT 0,
  max_attempts         smallint NOT NULL DEFAULT 3,
  contract_reopens     smallint NOT NULL DEFAULT 0,    -- capped at 2 (section 9.4)
  base_sha             text,
  branch               text,
  worktree_path        text,
  graph_thread_id      text,
  pending_deps         jsonb NOT NULL DEFAULT '[]',    -- unplanned dependency requests
  budget_implement_usd numeric(8,4) NOT NULL,
  budget_review_usd    numeric(8,4) NOT NULL,
  spent_implement_usd  numeric(8,4) NOT NULL DEFAULT 0,
  spent_review_usd     numeric(8,4) NOT NULL DEFAULT 0,
  version              integer NOT NULL DEFAULT 1,     -- optimistic locking
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tasks_run_status ON tasks (run_id, status);

CREATE TABLE task_dependencies (
  task_id            text REFERENCES tasks(id) ON DELETE CASCADE,
  depends_on_task_id text REFERENCES tasks(id) ON DELETE CASCADE,
  kind               text NOT NULL CHECK (kind IN ('blocks','join')),
  PRIMARY KEY (task_id, depends_on_task_id)
);

-- Write sets: only two path forms are allowed:
--   exact file  'backend/app/features/tasks/router.py'
--   directory   'backend/app/features/tasks/'   (trailing slash = whole subtree)
CREATE TABLE task_write_sets (
  task_id text REFERENCES tasks(id) ON DELETE CASCADE,
  path    text NOT NULL,
  mode    text NOT NULL CHECK (mode IN ('write','read')),
  source  text NOT NULL DEFAULT 'planned' CHECK (source IN ('planned','expansion')),
  PRIMARY KEY (task_id, path, mode)
);

-- Active reservations (replaces v1.0 "leases"; see section 10)
CREATE TABLE write_reservations (
  id           bigserial PRIMARY KEY,
  run_id       text NOT NULL REFERENCES runs(id),
  task_id      text NOT NULL REFERENCES tasks(id),
  path         text NOT NULL,
  expires_at   timestamptz NOT NULL,
  heartbeat_at timestamptz NOT NULL DEFAULT now(),
  released_at  timestamptz
);
CREATE INDEX write_res_active ON write_reservations (run_id) WHERE released_at IS NULL;

-- ---------- contracts ----------
CREATE TABLE contracts (
  id                  text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(id),
  name                text NOT NULL,                 -- 'api', 'db_schema', 'ui_components', 'deps'
  domain              text NOT NULL,
  kind                text NOT NULL CHECK (kind IN ('db_schema','openapi','component','deps','event')),
  owner_agent_id      text REFERENCES agents(id),
  current_version     integer NOT NULL DEFAULT 0,
  status              text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','locked','frozen')),
  breaking_in_wave    smallint NOT NULL DEFAULT 0,   -- capped at 3 per wave (section 9.4)
  version             integer NOT NULL DEFAULT 1,    -- optimistic locking
  UNIQUE (run_id, name)
);

CREATE TABLE contract_versions (
  contract_id     text REFERENCES contracts(id),
  version         integer NOT NULL,
  body            jsonb NOT NULL,
  pinned_pointers text[] NOT NULL DEFAULT '{}',      -- JSON pointers that may never change
  change_kind     text NOT NULL CHECK (change_kind IN ('initial','additive','breaking')),
  changed_elements text[] NOT NULL DEFAULT '{}',     -- e.g. operationIds / components / tables
  change_summary  text,
  author_agent_id text,
  approved_by     text,                              -- agent id or 'human'
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (contract_id, version)
);

CREATE TABLE contract_subscriptions (
  contract_id text REFERENCES contracts(id),
  task_id     text REFERENCES tasks(id) ON DELETE CASCADE,
  scope       text[] NOT NULL DEFAULT '{}',          -- elements this task actually uses
  PRIMARY KEY (contract_id, task_id)
);

CREATE TABLE contract_change_requests (
  id              text PRIMARY KEY,
  contract_id     text NOT NULL REFERENCES contracts(id),
  requested_by    text NOT NULL REFERENCES tasks(id),
  proposed_body   jsonb NOT NULL,
  reason          text NOT NULL,
  classified_as   text CHECK (classified_as IN ('additive','breaking','pinned_violation')),
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','batched','approved','rejected','escalated')),
  batch_id        text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------- decisions ----------
CREATE TABLE decisions (
  id              text PRIMARY KEY,
  run_id          text NOT NULL REFERENCES runs(id),
  idempotency_key text NOT NULL UNIQUE,              -- thread_id:node:revision
  category        text NOT NULL CHECK (category IN ('strategic','tactical','operational','release_infra')),
  title           text NOT NULL,
  context         text NOT NULL,
  options         jsonb NOT NULL,
  recommendation  jsonb,
  confidence      real,
  reversibility   text CHECK (reversibility IN ('reversible','hard','irreversible')),
  impact          jsonb NOT NULL DEFAULT '{}',
  mode            text NOT NULL CHECK (mode IN ('auto','notify','approve','human')),
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','auto_decided','resolved','undone','expired')),
  answer          jsonb,
  decided_by      text,
  graph_thread_id text,
  blocks_task_ids text[] NOT NULL DEFAULT '{}',
  version         integer NOT NULL DEFAULT 1,
  created_at      timestamptz NOT NULL DEFAULT now(),
  decided_at      timestamptz
);

-- ---------- communication ----------
CREATE TABLE threads (
  id         text PRIMARY KEY,
  run_id     text NOT NULL REFERENCES runs(id),
  kind       text NOT NULL CHECK (kind IN ('agent_dm','task','contract','incident','human_chat','dispute')),
  subject_id text,                                    -- task/contract/incident id
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE messages (
  id         text PRIMARY KEY,
  thread_id  text NOT NULL REFERENCES threads(id),
  from_id    text NOT NULL,
  to_id      text,
  body       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE notes (
  id        text PRIMARY KEY,
  run_id    text NOT NULL REFERENCES runs(id),
  task_id   text REFERENCES tasks(id),
  feature   text,
  kind      text NOT NULL CHECK (kind IN ('note','gotcha')),
  body      text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- release & operations ----------
CREATE TABLE releases (
  id                  text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(id),
  env                 text NOT NULL CHECK (env IN ('local','staging','production')),
  target              text NOT NULL,                  -- local_compose|vercel+render|aws|azure
  git_sha             text NOT NULL,
  image_ref           text,
  migration_head      text,                           -- alembic revision after deploy
  migration_kind      text CHECK (migration_kind IN ('none','expand','contract')),
  db_backup_ref       text,                           -- Neon branch name / snapshot id
  status              text NOT NULL CHECK (status IN ('deploying','warming','probing','live','rolled_back','failed')),
  previous_release_id text REFERENCES releases(id),
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE incidents (
  id            text PRIMARY KEY,
  run_id        text NOT NULL REFERENCES runs(id),
  release_id    text REFERENCES releases(id),
  severity      text NOT NULL CHECK (severity IN ('sev1','sev2','sev3')),
  source        text NOT NULL CHECK (source IN ('probe','e2e','health','user','ci')),
  summary       text NOT NULL,
  evidence      jsonb NOT NULL DEFAULT '{}',
  owning_domain text,
  fix_task_id   text REFERENCES tasks(id),
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open','mitigated','resolved')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at   timestamptz
);

-- ---------- LLM usage (exact first-party SDK token/cache telemetry + local tracing) ----------
CREATE TABLE llm_calls (
  id             bigserial PRIMARY KEY,
  run_id         text, agent_id text, task_id text, graph_thread_id text, node text,
  provider       text NOT NULL, model text NOT NULL, profile text NOT NULL,
  transport      text NOT NULL DEFAULT 'openai_compat'
                 CHECK (transport IN ('openai_compat','google_genai','anthropic')),
  billing_mode   text NOT NULL DEFAULT 'paid_api'
                 CHECK (billing_mode IN ('paid_api','free_tier','local')),
  input_tokens   integer NOT NULL, cached_tokens integer NOT NULL DEFAULT 0,
  output_tokens  integer NOT NULL,
  cost_usd       numeric(10,6) NOT NULL,             -- exact USD from provider token telemetry ($0.00 for local/free)
  est_value_usd  numeric(10,6) NOT NULL DEFAULT 0,   -- equivalent paid API value for equal-cost comparisons
  latency_ms     integer, replayed boolean NOT NULL DEFAULT false,
  prompt_hash    text, created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- events (Postgres transactional outbox with monotonic publish_seq — A7-5) ----------
CREATE SEQUENCE events_publish_seq;

CREATE TABLE events (
  id           bigserial PRIMARY KEY,
  publish_seq  bigint UNIQUE,                        -- assigned strictly in commit-visible relay order
  run_id       text NOT NULL,
  type         text NOT NULL,
  subject_id   text,
  payload      jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE INDEX events_unpublished ON events (id) WHERE publish_seq IS NULL;
CREATE INDEX events_run_seq ON events (run_id, publish_seq) WHERE publish_seq IS NOT NULL;

CREATE TABLE processed_events (
  consumer_group text NOT NULL,
  publish_seq    bigint NOT NULL,
  PRIMARY KEY (consumer_group, publish_seq)
);
```
Fuzzy-memory tables (`code_chunks`, `lessons`, `docs`) each have `id, source_ref, content, content_hash, embedding vector(384), tsv tsvector`, with HNSW and GIN indexes.

### 8.3 Event loop: Postgres-only outbox with monotonic `publish_seq` (`A7-5`)
**Principle:** **Postgres is the sole source of truth and event bus in v1 (no Redis).** Every state change writes its event row **in the same transaction**, so events are never lost or published for rolled-back transactions.

> **Why `publish_seq` instead of raw `bigserial id`?** Concurrent Postgres transactions can allocate `id=10` and `id=11`, where `id=11` commits first. If a WebSocket or consumer cursors on raw `id`, seeing `11` permanently skips `10`. Having the single-process relay acquire an advisory lock and assign a monotonic `publish_seq` strictly to **committed** rows guarantees gap-free, strictly ordered replay via `?after_seq=`.

```
 state change ─┐  (one transaction)
 INSERT events ┘ ──commit──► NOTIFY events_new
                                   │
                       In-Process Event Relay (daemon)
          pg_advisory_xact_lock → UPDATE unpublished SET publish_seq = nextval(...)
                                   │
         ┌──────────────┬──────────┼──────────────┬───────────────┐
   supervisor      decisions     leads         indexer        ws_bridge
   (in-process asyncio consumers: handle → INSERT processed_events(group, publish_seq))
```

```python
# 1) Emit inside the business transaction
async def set_task_status(s, task, new_status, expected_version):
    res = await s.execute(
        update(Task).where(Task.id == task.id, Task.version == expected_version)
        .values(status=new_status, version=Task.version + 1, updated_at=func.now()))
    if res.rowcount == 0:
        raise StaleWrite(task.id)                     # optimistic locking
    s.add(Event(run_id=task.run_id, type=f"task.{new_status.lower()}",
                subject_id=task.id, payload={"task_id": task.id}))
    # commit by caller; an AFTER INSERT trigger issues NOTIFY events_new

# 2) Relay: assign strictly monotonic publish_seq to committed rows, then fan out in-process
async def relay_loop(dispatcher):
    async for _ in listen_or_poll("events_new", poll_s=0.5):
        async with db.begin() as s:
            await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('events:publish'))"))
            rows = await s.execute(text("""
                UPDATE events
                SET publish_seq = nextval('events_publish_seq'), published_at = now()
                WHERE id IN (SELECT id FROM events WHERE publish_seq IS NULL ORDER BY id LIMIT 500)
                RETURNING publish_seq, run_id, type, subject_id, payload
            """))
            published = rows.fetchall()
        for event in published:
            await dispatcher.dispatch(event)          # fans out to supervisor/leads/ws_bridge

# 3) Consumers: crash-safe replay on daemon startup + idempotent handling via processed_events
async def handle_event(group: str, event, handler):
    async with db.begin() as s:
        if not await s.get(ProcessedEvent, (group, event.publish_seq)):
            await handler(s, event)
            s.add(ProcessedEvent(consumer_group=group, publish_seq=event.publish_seq))
```
**Recovery:** on daemon restart, each consumer group replays `events WHERE publish_seq > last_processed_seq ORDER BY publish_seq` directly from Postgres. (Redis Streams is deferred to Post-MIV if multi-process distributed daemon scaling is ever needed.)

### 8.4 Event catalogue
| Event | Emitted when | Main consumers |
|---|---|---|
| `task.*` (`ready`, `in_progress`, `in_review`, `verified`, `queued`, `merged`, `failed`, `escalated`, `frozen`) | Task lifecycle | Supervisor, Leads, Dashboard |
| `reservation.granted/denied/expired/released` | Write-set reservations | Supervisor, Dashboard |
| `writeset.expansion_requested/granted/denied` | Mid-task expansion | Supervisor, metrics |
| `contract.change_requested/batched/versioned/frozen/locked` | Contract protocol | Leads, subscribed tasks, Project graph |
| `wave.completed` | All of a wave's tasks are MERGED | Project graph (resume) |
| `main.updated` | Merge queue merge | Indexer, running threads (buffered) |
| `decision.created/resolved/undone` | Decisions | Dashboard, Decision Service |
| `test.dispute_opened/resolved` | G3 disputes | Leads, Dashboard |
| `ci.*`, `release.*`, `incident.*` | Pipeline | Release Agent, owning domain, Dashboard |
| `budget.warning/exceeded` | Cost thresholds | Supervisor, Dashboard |
| `ratelimit.backoff` / `quota.cooldown_fallback` | Provider throttling or subscription rolling-window cooldown | Gateway, Dashboard |

---

## 9. Coordination protocol

### 9.1 Staged contract planning
Leads depend on each other, so they run in a fixed order:
```
1. Data Lead      → schema contract (entities, relations, indexes)
2. Backend Lead   → OpenAPI contract (built on the schema contract + stack-pack conventions)
3. Frontend Lead ∥ Infra Lead
                  → component/route contracts (with data-testids + export paths)
                  → dependency list, CI + deploy config
4. Contract check (deterministic):
     Spectral lint (stack-pack ruleset), schema↔OpenAPI consistency check,
     data-testid uniqueness, pinned-region check (benchmark/user-pinned)
5. One Orchestrator consistency review (single call)
6. Contracts v1 → status 'locked'  → event contract.locked → Project graph resumes
```
A downstream lead can file a **change request** against an upstream contract during step 3 (e.g., the Frontend Lead needs a field in a response). Requests are batched and applied before step 6.

### 9.2 Build waves
| Wave | What | Parallelism |
|---|---|---|
| **Wave 1: Foundation** | **F1** deps + CI + config (Infra impl.) → **F2** SQLAlchemy models in `backend/app/models/` + initial migration `0001_initial` from the schema contract (Data impl.) → **F3** auth: `User`, `get_current_user`, auth routes, shared `conftest.py` fixtures (including contract-defined `seed_user(role=...)` helper for black-box G3 role seeding) (Backend impl.) ∥ **F4** frontend shell: `AppLayout`, `AuthProvider`, route/nav registry, generated TS client + static OpenAPI fixture helpers (Frontend impl.) | Mostly sequential (F3 ∥ F4). All merged before Wave 2. After F2, the supervisor builds the **`app_template`** DB used for sandbox DBs. |
| **Wave 2: Features** | One task per feature per domain (backend feature, frontend feature), plus schema/delta tasks as needed. **Any Wave 2 `kind='schema'` tasks run as serialized mini-wave barriers** (chained sequentially with `blocks` dependencies until `MERGED`, preventing Alembic two-head conflicts) | Parallel (up to 3–4 thinking threads sharing a **2-slot heavy-execution semaphore**), scheduled by write sets |
| **Wave 3: Release** | Integration fixes, E2E, deploy | Sequential |

### 9.3 Contract-first parallelism and the join barrier (`A7-8`)
1. The backend and frontend tasks for a feature run in parallel. For G1–G2 component tests, the frontend tests isolated component rendering, form validation, and error states against **static contract fixtures** backing the generated TypeScript API client (`frontend/src/api/generated/`), validating **both** outbound requests and mock fixture responses against the locked OpenAPI contract. *(OpenAPI defines shapes, not RBAC/conflict state machines, so multi-step stateful UI flows are NOT mocked.)*
2. When both reach VERIFIED, a **join barrier** fires: the supervisor creates a `kind='integration'` task that reserves **both** `backend/app/features/<f>/` and `frontend/src/features/<f>/`, connects the frontend to the **live FastAPI backend + `sbx_<task>` database**, and verifies full multi-step stateful UI + API behavior end to end.
3. Agents can ask each other questions through `threads` (`kind='agent_dm'`).

### 9.4 Contract change protocol (classification, batching, caps)
```
implementer → propose_contract_change(contract, proposed_body, reason)
   │
   ▼  deterministic classification
   ├─ touches pinned pointer  → auto-REJECT (pinned_violation)
   ├─ additive (oasdiff / schema diff / component diff: no breaking changes)
   │     → new version, NOTIFY subscribers, nobody is reopened
   └─ breaking
         → BATCH: collect requests for this contract for 3 min (or until the owner lead's next turn)
         → owner lead approves/rejects the batch (Tactical decision per autonomy policy)
         → ONE new version; reopen only subscribed tasks whose `scope` overlaps changed_elements
```
**Caps (stop livelocks):**
| Cap | Limit | On breach |
|---|---|---|
| Contract-driven reopens per task | **2** | The 3rd triggers **freeze**: all tasks subscribed to the changed elements go `FROZEN`; the owning lead(s) run **one joint co-design call** with both sides' context, producing a version that is locked for the rest of the wave; tasks unfreeze to READY |
| Breaking versions per contract per wave | **3** | Contract `frozen`; escalate to the Orchestrator (and the human if Tactical ≥ Approve) |
| Pending change requests per task | 1 | Further requests wait |

**Handling depends on the subscribed task's state:**
| Subscribed task state | Action | Counts as a reopen? |
|---|---|---|
| PENDING / BLOCKED / READY | Context updated in place (contract diff attached) | No |
| IN_PROGRESS | Diff buffered and applied at the thread's **next loop boundary**; the agent continues | Yes |
| IN_REVIEW / VERIFIED / QUEUED | Pulled back to READY with the diff; its review results are discarded | Yes |
| MERGED / DEPLOYED | **Never reopened.** The supervisor creates a **delta task** `Adapt <feature> to <contract> v<N>` that branches fresh from `main` | No (new task) |

### 9.5 Task lifecycle
```
PENDING ─deps unmet─► BLOCKED ─deps met─► READY ─reserved+slot─► IN_PROGRESS
IN_PROGRESS ─G1 ok + commit─► IN_REVIEW ─G2,G3,G4 ok─► VERIFIED
VERIFIED ─enqueue─► QUEUED ─G5 ok─► MERGED ─release─► DEPLOYED
FAILED ─attempts left─► READY            FAILED ─none left─► ESCALATED
ESCALATED ─lead re-plans─► CANCELLED (+ replacement tasks) | READY (clarified)
any active ─contract cap─► FROZEN ─co-design locked─► READY
```

### 9.6 Escalation ladder
1. **Implementer** retries with error feedback (attempt 2 switches to `@coder_plus`; max 3 attempts)
2. **Domain Lead:** split the task, clarify the contract, add context, or reassign
3. **Orchestrator:** cross-domain re-plan or architecture change
4. **Human:** a decision card with full context

### 9.7 Test dispute protocol (G3) (`A7-7`)
When a black-box test fails with a **real assertion failure** (after the post-test `Test-Validator` has already filtered out `invalid_test_request` and test-code errors, see 11.2):
1. **Deterministic contract check first:** check the locked OpenAPI schema **and** per-operation behavioral rules (`x-foundry-rules` / explicit error-trigger conditions written by the Backend Lead):
   - **Asserted field/status is not in the contract** → the test is wrong → back to the Test-Writer.
   - **Unambiguous schema/status violation** (e.g., wrong response shape, missing required field, or violated explicit `x-foundry-rules` precondition on a valid request) → the code is wrong → back to the Implementer.
   - **Status code exists in the contract (e.g., `409`), but the trigger condition is behavioral or ambiguous** → route to Step 2 (Lead ruling) rather than auto-blaming the Implementer.
2. **Contract ambiguity ruling:** the **owning Domain Lead** rules and **clarifies the contract's `x-foundry-rules`** (counts as a contract change, with the usual caps).
3. **Max 2 dispute rounds per feature**, then escalate to the Orchestrator.

### 9.8 Safeguards
- **Deadlock detection:** cycle check every 30 s on the wait-for graph (task dependencies + reservation waits + frozen tasks). A cycle escalates to the lead.
- **Timeouts & Run Kill-Switch (`A7-11`):** wall-clock limit per task attempt (default 20 min) **plus a hard per-run wall-clock timeout (default 60–90 min) and max total LLM calls cap (default 600)** so no run can loop indefinitely across escalations, delta tasks, or disputes.
- **Optimistic locking:** `version` columns on `tasks`, `contracts`, `decisions` (`UPDATE … WHERE version = :v`).
- **Budgets:** section 18.

---

## 10. Concurrency control layer

**Principle:** treat the codebase like a database with many concurrent writers.
- **Declared write sets** = transactions declaring what they'll touch
- **Reservations** = locks
- **Diff enforcement** = constraint checks
- **Merge queue** = a guaranteed order of commits

### Layer 1: Write sets from conventions (accurate by construction)
- **Leads, not implementers, assign static write sets**, using stack-pack conventions:
  - backend feature → `backend/app/features/<f>/`
  - frontend feature → `frontend/src/features/<f>/`
  - join-barrier integration task (`kind='integration'`) → `backend/app/features/<f>/` + `frontend/src/features/<f>/`
  - schema task (`kind='schema'`) → `backend/app/models/` + `backend/migrations/` (chained sequentially until `MERGED`)
- **Shared code** (`backend/app/core/`, `frontend/src/components/ui/`, `frontend/src/app/`) is never in a feature's write set. Changing it requires a request, which becomes its own task.
- **Read sets** are recorded too (for rebase warnings and context).
- *(v1 lean scope — `A7-12`: Static feature-directory write sets are enforced in MIV. If a task genuinely needs files outside its static write set, it escalates cleanly to its Lead to split/adjust the task. Mid-task dynamic `request_write_set_expansion` is deferred to the Post-MIV Backlog.)*

### Layer 2: Isolation + reservations
- **One daemon worktree + branch per task** (`task/<id>-<slug>`), synced to a **`.git`-free container workspace** (`workspaces/<task_id>`), **one sandbox container per task**, **one logical DB per task**.
- **Reservations (task-scoped heartbeats):**
  - When a task is scheduled, its whole write set is **reserved atomically** with a 10-minute TTL.
  - **Task-Scoped Heartbeats:** The Run Supervisor renews reservation heartbeats every 60 s for any task in active pipeline states (`IN_PROGRESS`, `IN_REVIEW`, `VERIFIED`, `QUEUED`), independent of whether an LLM thread is currently executing, and releases them at `MERGED`, `CANCELLED`, or `ESCALATED`. *(5-minute pause preemption for long `FROZEN`/`Approve` waits is deferred to the Post-MIV Backlog unless multi-feature lock contention appears in practice.)*
- **Why keep reservations** when worktrees already isolate edits?
  - They make the "no overlapping writers" rule **durable across daemon restarts**.
  - They enforce **single writers** for `models/`, `migrations/`, and dependency manifests.
  - They power the dashboard's lock view.
- **Overlap rule:** two paths conflict if they are equal, or one is a directory (trailing `/`) that is a prefix of the other. Paths are normalized (POSIX, relative, no `..`, no symlinks) before storage.

**Atomic reservation (advisory lock + prefix-overlap check):**
```sql
BEGIN;
-- serialize all reservation changes within this run
SELECT pg_advisory_xact_lock(hashtext('reserve:' || :run_id));

-- conflicts with any other active reservation?
SELECT r.task_id, r.path, p.path AS requested
FROM write_reservations r
JOIN unnest(CAST(:paths AS text[])) AS p(path)
  ON  r.path = p.path
  OR (right(r.path, 1) = '/' AND starts_with(p.path, r.path))
  OR (right(p.path, 1) = '/' AND starts_with(r.path, p.path))
WHERE r.run_id = :run_id
  AND r.task_id <> :task_id
  AND r.released_at IS NULL
  AND r.expires_at > now();

-- application code: if any row returned → ROLLBACK and report conflicts; else:
INSERT INTO write_reservations (run_id, task_id, path, expires_at)
SELECT :run_id, :task_id, p, now() + interval '10 minutes'
FROM unnest(CAST(:paths AS text[])) AS p;
COMMIT;
```

### Layer 3: Shared files many tasks need
| Shared file | Strategy |
|---|---|
| `backend/app/main.py` | **Auto-discovery** of `features/*/router.py`; never edited after Wave 1 |
| Frontend routing + navigation | Each feature exports `feature.ts` (`routes`, `navItems`, `guards`). The **`AppLayout`** (from F4) loads all of them via `import.meta.glob('../features/*/feature.ts')` |
| SQLAlchemy models (`backend/app/models/`) + migrations | **Single writer + strict `MERGED` chaining:** only `kind='schema'` tasks (Data implementer, cheap model) hold these paths, and the supervisor runs them as serialized barriers (`blocks` dependency until `MERGED`, preventing Alembic two-head conflicts). Whenever a schema task merges in G5, the daemon refreshes `app_template` safely (`pg_terminate_backend` + `alembic upgrade head` under a Postgres advisory lock — `A7-9`). |
| `pyproject.toml`, `package.json`, lockfiles | **Planned** dependencies are installed in Wave 1 (F1). **Unplanned (`install_package`, `A7-10`):** gated strictly by a **curated allowlist OR hard human `Approve`** (never `Notify`/`Auto`, regardless of preset). Enforces `--only-binary :all:` (`uv`) and `--ignore-scripts` (`pnpm`) to block malicious `setup.py` / `postinstall` execution, injects `HTTP(S)_PROXY` strictly into that single `docker exec` call, and records approved packages in `tasks.pending_deps` for deterministic lockfile application in the Merge Queue. |
| Generated TS client (`frontend/src/api/generated/`) | Regenerated from the contract by the pipeline; never hand-edited |
| `conftest.py` test fixtures | Created in F3 (Backend Lead's domain, including contract-defined `seed_user(role=...)` helper); readable by everyone; changes only by request |
| `.env.example`, CI workflows, `.git*` files | Infra domain only; `.gitattributes`/`.gitmodules` and symlink entries (`mode 120000`) are always rejected for non-infra tasks |

### Layer 4: Enforcement + serial merge queue
**Diff & symlink enforcement (`A7-3`, closes the `run_command` and symlink-exfiltration loopholes):**
- `write_file` checks the reservation immediately (fast feedback).
- **Pre-sync / pre-scan safety walk:** before syncing `workspaces/<task_id>` back into `worktrees/<task_id>` or running *any* host daemon tool (`git-mcp`, `Semgrep`, `gitleaks`, `oasdiff`), the daemon runs a deterministic recursive walk that **rejects any symlink (`os.path.islink` / git mode `120000`), FIFO/device node, or oversized YAML bomb**.
- **At commit** (`git-mcp`, daemon-side): every path in `git status --porcelain` must fall within the task's reserved write set and contain zero mode `120000` entries. Otherwise the commit is rejected with the list of offending paths.
- **At G2 and G5:** `git diff --name-only <base_sha>...HEAD` is re-checked.
- Formatters and linters with `--fix` are run by `sandbox-mcp` **scoped to the write-set paths only**.

**Serial merge queue** (one branch at a time, run by the daemon with hardened git):
1. Pop the next VERIFIED → QUEUED branch and acquire 1 of the 2 **heavy-execution semaphore slots** (with priority over G3/G2/G1).
2. Apply `pending_deps` (if approved) on a temporary integration branch.
3. Rebase onto `main`. Conflict resolution, step by step:
   - normal git merge/rebase
   - **Integration Agent** (with both tasks' intents)
   - **re-run the task** from a fresh copy of `main`
   *(Note: AST-aware **Mergiraf** is deferred to the Post-MIV Backlog — `A7-12` — and plugged in before the Integration Agent only if empirical merge conflict telemetry warrants it.)*
4. Symlink/write-set diff check again; `squawk` migration lint (if migrations changed).
5. **Full test suite on the combined code** (inside Docker, using the acquired heavy-exec slot). This catches semantic conflicts: changes that each work alone but break together.
6. Fast-forward `main` → if `kind='schema'`, refresh `app_template` safely (`pg_advisory_lock` → `pg_terminate_backend` on lingering sessions → `ALLOW_CONNECTIONS = true` → `alembic upgrade head` → `ALLOW_CONNECTIONS = false`, plus matching cleanup on daemon startup — `A7-9`) → emit `main.updated` → incremental re-index.

**No live rebasing (with clean recovery on schema-barrier merges — `A7-9`).** `main.updated` is **buffered** in the thread's state; regular feature merges rebase in the merge queue. When a `kind='schema'` barrier task merges, any dependent feature task rebases onto the new `main` SHA at its next clean loop boundary and re-clones `sbx_<task>` from `app_template`. If that boundary rebase hits a merge conflict or broken imports, the supervisor cleanly **discards the attempt's WIP branch, increments `attempt`, and restarts the task cleanly from the new `main` SHA** rather than leaving the thread in an undefined state.

### Layer 5: Contract boundaries with automatic checks
Domains interact only through contracts and generated clients. Conformance is **checked, not assumed**:
- **Backend (`A7-3`):** `openapi.json` is exported **strictly inside the Docker sandbox** as a static JSON artifact (never by importing agent code on the host), then compared against the locked contract using **oasdiff**. Any breaking or undocumented difference fails the check.
- **Backend (`A7-7`):** **Bounded Schemathesis** validates the task's operations against the contract — restricted to **status-code and response-schema conformance checks**, with a **fixed seed (`--hypothesis-seed=42`) and capped examples (`--max-examples=10`)** so property fuzzing does not thrash LLM implementers.
- **Frontend (`A7-8`):** Required `data-testid`s and component export paths from the component contract must exist (static check + component render tests using static OpenAPI-validated fixtures).

---

## 11. Verification and quality gates

### 11.1 Gates
| Gate | When | Checks | Blocking? | Cost |
|---|---|---|---|---|
| **G1: Self-check** | Inside the implementer loop | Acquires 1 heavy-exec slot; **sequential execution** inside Docker (`ruff` → `mypy` → `pytest` + export `openapi.json` / `tsc` → `vitest`) **scoped to the write set**; quick `oasdiff` on static `openapi.json`; bounded Schemathesis (advisory here) | Loops back to the agent | Free |
| **G2: Task gate** | → IN_REVIEW | **Symlink/FIFO walk + write-set diff check**; **sequential sandbox checks** (`ruff` → `mypy` → `pytest` + coverage → export `openapi.json` → **bounded Schemathesis** (`--hypothesis-seed=42 --max-examples=10`) / `tsc` → `vitest` + test-ID check); **daemon-side static checks** on the symlink-verified, read-only worktree (**`oasdiff` on static `openapi.json` (blocking)**, `Semgrep --max-memory 512 --jobs 1`, `gitleaks`) | Yes | Free |
| **G3: Black-box tests** | Per feature, once the feature's tasks pass G2 | Test-Writer writes black-box API tests (and static-fixture component tests) from contracts; **post-test `Test-Validator`**; Dispute Protocol | Yes | `@verifier` |
| **G4: Review** | Risk-based (auth/security, shared code, migrations, >300 changed lines, or a 25% sample) | Logic, edge cases, security, contract intent | Yes (when run) | `@verifier` |
| **G5: Integration** | Serial merge queue | Acquires 1 heavy-exec slot (**priority over G3/G2/G1**); deps applied, rebase, symlink + diff check, **`squawk` migration lint + up/down test + crash-safe `app_template` refresh (if schema task)**, full suite on combined code, `oasdiff` on static `openapi.json` | Yes | Free |
| **G6: Release** | Before deploy / promotion | Deploy to local blue/green (or Post-MIV staging), expand migrations, **Playwright E2E** (one browser) covering stateful multi-step UI journeys, acceptance criteria, then human approval decision | Yes | Free + human |
| **G7: Production / Live Probe** | After deploy | Pre-migration backup, expand-only migration, deploy, warm-up, **probe window with synthetic traffic**, automatic **code-only** rollback | Yes | Free |

**Division of test duties:**
- **Implementers** write white-box unit tests (G1/G2). They know their own internals.
- **The Test-Writer** writes **only black-box** tests (G3):
  - **API tests:** `httpx.AsyncClient` through the shared `client` / `auth_headers` / `seed_user(role=...)` fixtures from `conftest.py` (which record OpenAPI request-validation telemetry per call), calling only operations in the OpenAPI contract.
  - **Component tests:** Vitest + Testing Library + static contract-validated fixtures (both requests and mock responses validated against OpenAPI), importing components **only from contract export paths** and selecting **only by contract-declared `data-testid`s**. Multi-step stateful UI journeys run against the real FastAPI backend in `kind='integration'` and G6 Playwright tests.
  - **Playwright E2E** is **not** written per task. It runs at G6 (from the acceptance criteria) and in the benchmark's hidden suite.

### 11.2 Test-Validator (deterministic, post-test classification — `A7-7`)
During a G3 test run, the instrumented `client` fixture **records** every outbound HTTP call's OpenAPI request-validation status (without short-circuiting the test before its `assert` runs; negative tests may also pass `expect_invalid=True`). **After the test finishes**, `Test-Validator` classifies failures deterministically:

| Failure type | Examples | Routed to |
|---|---|---|
| **Broken test (code or invalid request)** | collection errors, `ImportError`, `ModuleNotFoundError`, `SyntaxError`, `FixtureLookupError`, `NameError`/`AttributeError` raised **inside test code**, an unknown `data-testid` not declared in the contract, OR **`invalid_test_request`** (evaluated **post-test**: the test *failed*, received HTTP `400`/`422`, and its recorded outbound request violated the locked `openapi.yaml` request schema without `expect_invalid=True`) | **Test-Writer** (with the exact validation trace). Never the Implementer. |
| **Real assertion failure** | valid outbound request (or explicit negative test) + wrong response status code, response schema mismatch, or missing UI element behavior | **Dispute protocol** (section 9.7) |
| **Infra failure** | DB connection error, timeout | Retry once, then escalate as infra |

Test-Writer self-fix attempts are capped at 2 per round.

### 11.3 Principles
- Automated checks always run **before** LLM-based checks.
- The author of the code **never** writes the black-box tests.
- Verifiers use a **different model family** when the budget/subscriptions allow (`require_verifier_family_diversity`).
- Every gate records **what it caught**, which feeds the metrics and ablations.

---

## 12. Stack packs

A **stack pack** is a plugin that defines everything stack-specific. Foundry's core doesn't depend on any particular stack.

| A stack pack provides | Pack #1 `fastapi-react-postgres` |
|---|---|
| Skeleton template | Monorepo below |
| Conventions + ownership map + default write sets | `features/*`, `models/`, `core/`, `components/ui/` |
| Shared-file strategies | Auto-discovery, nav registry, single-writer models and migrations |
| **Contract conventions + Spectral ruleset** | See 12.2 |
| Check commands | `ruff`, `mypy`, `pytest`, `eslint`, `tsc`, `vitest` |
| Contract tooling | `openapi-typescript` client, static OpenAPI-validated frontend fixtures (`A7-8`), oasdiff (on sandbox-exported `openapi.json`), bounded Schemathesis (`--hypothesis-seed=42 --max-examples=10`) |
| Migration policy tooling | Alembic offline SQL (`alembic upgrade --sql`) + **squawk** lint + an op checker for expand/contract |
| **Sandbox image** | `foundry-sandbox-fastapi-react@sha256:<digest>` (digest-pinned, pre-installed standard deps) |
| **Release env image** | App runtime + Playwright Chromium (digest-pinned) |
| Deploy adapters + templates | Local Compose (blue/green — MIV); Stage A (`vercel.json`, `render.yaml`, Neon) & Stage B Terraform (Post-MIV) |
| Standard test fixtures | `conftest.py`: `db`, `client` (recording OpenAPI request-validation telemetry for post-test G3 Validator), `seed_user(role=...)`, `auth_headers(user)`; frontend static OpenAPI contract fixtures |
| Docs to pre-index | FastAPI, SQLAlchemy 2, Pydantic 2, Alembic, React, Vite, Tailwind, TanStack Query, Testing Library |

### 12.1 Generated app layout
```
<app>/
├── backend/
│   ├── app/
│   │   ├── main.py                  # auto-discovers features/*/router.py (frozen after Wave 1)
│   │   ├── core/                    # config, db session, security, get_current_user (shared)
│   │   ├── models/                  # ALL SQLAlchemy models (Data domain, single writer)
│   │   └── features/<feature>/      # router.py, schemas.py, service.py, tests/
│   ├── migrations/                  # Alembic (Data domain, single writer)
│   ├── tests/conftest.py            # shared fixtures (created in F3)
│   ├── pyproject.toml               # changed only by Wave 1 or the merge queue
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── app/                     # AppLayout (reads nav registry), AuthProvider, router
│   │   ├── components/ui/           # shared UI (Frontend Lead domain)
│   │   ├── features/<feature>/      # feature.ts (routes/nav/guards), pages, components, tests
│   │   └── api/generated/           # generated from OpenAPI, never hand-edited
│   ├── package.json
│   └── vite.config.ts
├── contracts/
│   ├── openapi.yaml                 # mirrors the locked contract version
│   ├── schema.yaml
│   └── components.yaml              # component export paths, props, data-testids
├── e2e/                             # Playwright (G6)
├── infra/
│   ├── compose.local.yml            # local blue/green target
│   ├── vercel.template.json         # rendered per environment by the pipeline
│   ├── render.yaml
│   └── terraform/                   # Stage B
└── .github/workflows/ci.yml
```

### 12.2 Contract conventions (enforced by the Spectral ruleset)
- Every operation declares `security` (or an explicit `security: []` for public routes).
- Every operation declares its success response **and** `401`/`403` (if secured), `404` (if it has path parameters), and `422` (if it has a body or query).
- List endpoints return the shared `Page<T>` envelope (`items`, `total`, `page`, `size`).
- All errors use the shared `Error` schema.
- Auth: bearer token from `POST /api/auth/login`. The same scheme is used everywhere.
- All API routes live under `/api` (so the same-origin proxy works).
- Component contracts declare: export path, props, emitted events, and the required `data-testid`s.

---

## 13. CI/CD and deployment

### 13.1 Deploy target interface
```python
class DeployTarget(Protocol):
    name: str
    async def deploy(self, env: str, git_sha: str) -> Release           # env: local|staging|production
    async def warm_up(self, release: Release, max_wait_s: int) -> bool    # cold-start handling
    async def probe(self, release: Release, window_s: int) -> ProbeResult # synthetic traffic + health
    async def rollback(self, release: Release) -> Release                 # code-only, to previous release
    async def logs(self, release: Release, since: datetime) -> list[LogLine]
```
| Implementation | Use |
|---|---|
| `LocalComposeTarget` | **Development + benchmark.** Blue/green: the new color starts on another port → 60 s probe + Playwright smoke → Caddy switches traffic → old color stopped. Rollback = switch back. No cloud, no cold starts, deterministic. |
| `VercelRenderNeonTarget` | Stage A |
| `AwsTarget`, `AzureTarget` | Stage B (Terraform) |

### 13.2 Stage A: Vercel + Render + Neon (designed around free-tier limits)
| Part | Service | Limits handled |
|---|---|---|
| Frontend | **Vercel** (Hobby): preview per branch + production | Non-commercial use only; fine for portfolio work |
| Backend | **Render** free web services: `<app>-staging` and `<app>-prod` | **Sleeps after 15 min idle; about 1 min to wake** → warm-up probe; 750 instance-hours/month shared (sleeping services barely use them) |
| Database | **Neon** free tier: one project per app; branches `main` (prod) and `staging`, plus temporary `backup-<release>` branches | **No 30-day expiry** (unlike Render free Postgres); 1 GB per project; **max 10 branches** → the pipeline deletes old backup branches; scale-to-zero with sub-second wake-up |

**Same-origin API (fixes cross-site cookies and CORS):** the pipeline **renders `vercel.json` per environment** before each deploy:
```json
{ "rewrites": [ { "source": "/api/:path*",
                  "destination": "https://<app>-staging.onrender.com/api/:path*" } ] }
```
The browser only ever talks to `*.vercel.app`, so cookies are first-party (`SameSite=Lax` works) and CORS isn't needed.

**Cold starts:** `warm_up()` polls `GET /api/health` with backoff for up to **120 s** before G6/G7 checks begin. During probe windows, synthetic traffic keeps the service awake. Cold-start time is recorded, not counted as a failure.

**Rollback on free Render:** Render's free plan can only roll back to the most recent deploys, so Foundry keeps its own `releases` ledger. Rollback = **redeploy the previous release's git SHA** through the Render API (and promote the previous Vercel deployment).

### 13.3 Migration policy (makes rollback safe)
**Expand/contract only:**
| Release type | Allowed operations | Approval |
|---|---|---|
| **Expand** (normal) | Create tables; add nullable columns or columns with defaults; add indexes (`CONCURRENTLY`); add constraints as `NOT VALID` then validate | Part of the normal release approval |
| **Contract** (cleanup) | Drop or rename columns/tables, tighten types, make columns `NOT NULL` | **Separate release**, only after no deployed code uses the old schema; **always a human-approved Release/Infra decision** |

Enforcement:
- G5 runs `alembic upgrade --sql` → **squawk** + an op checker. Any destructive op in an expand release **fails G5**.
- **Migrations run automatically on staging**. On **production** they run as a pre-deploy step, **only after the release approval**, and **after a Neon backup branch** (`backup-<release>`) is created.
- **Rollback is code-only.** The schema stays, and because expand migrations are backward compatible, the previous code works against it.
- **Data restore** (from `backup-<release>`) is a **human-only** action, never automatic.

### 13.4 Pipeline
```
1. Merge queue (G5)        → combined branch green; deps applied; migration lint
2. Build                   → backend image, frontend static build
3. Scan                    → Trivy (image), Semgrep, gitleaks
4. Staging deploy          → Neon `staging` branch: migrate (expand) → Render staging → Vercel preview
5. Warm-up (≤120 s)        → then Playwright E2E + acceptance (G6)
6. Approval                → Release/Infra decision (always ≥ Approve)
7. Production              → Neon backup branch → migrate (expand) → Render prod → promote Vercel
8. Warm-up + probe window  → 5 min of synthetic traffic: 5xx rate, p95 latency, smoke flows (G7)
9. Auto-rollback           → on probe failure: code-only rollback + incident (sev2) to owning domain
```
Benchmark and local development use the same pipeline with `LocalComposeTarget` (steps 4–9 run locally, and approval is auto-granted in the benchmark preset).

### 13.5 Stage B: AWS / Azure (Terraform)
| | AWS | Azure |
|---|---|---|
| Backend | App Runner or ECS Fargate | Container Apps |
| Database | RDS Postgres (snapshot before migrations) | Azure Database for PostgreSQL Flexible |
| Frontend | S3 + CloudFront (`/api/*` behavior → backend, same-origin) | Static Web Apps (linked backend) |
| Canary | ALB weighted target groups | Container Apps traffic splitting |
| Credits | AWS new-account credits / free tier | **Azure for Students** (GitHub Student Pack) |

The same migration policy and `releases` ledger apply.

### 13.6 Production feedback loop
Probe failures, health alerts, and E2E failures create `incidents`. Ownership comes from the stack trace path mapped through the ownership map. A **fix task** (wave 3) enters the normal flow.

---

## 14. Infrastructure as code (Terraform + infra-mcp)

| MCP server | Purpose |
|---|---|
| HashiCorp **Terraform MCP server** (official) | Registry lookups for providers and modules, so agents write correct Terraform |
| AWS MCP servers (`awslabs/mcp`) / **Azure MCP server** | Cloud knowledge, read-only inspection |
| **`infra-mcp`** (built by you) | The **only** way to run Terraform, with safety rules built in |

| `infra-mcp` tool | Who | Behavior |
|---|---|---|
| `tf_fmt`, `tf_validate` | Infra Lead/impl. | Always allowed |
| `tf_plan(workspace)` | Infra Lead | Returns a `plan_id` + summary (+create / ~change / −destroy) |
| `tf_cost_estimate(plan_id)` | Infra Lead | Infracost monthly cost change |
| `tf_security_scan(plan_id)` | Infra Lead | Checkov |
| `tf_apply(plan_id, approval_token)` | Release Agent | Requires an approved plan + a token issued by a human decision |
| `tf_destroy` | **Humans only** | Not exposed to agents |

**Flow:** write Terraform → plan + cost + security scan → **infra decision card** (resource diff + cost) → approve → apply. State lives in a remote backend (S3 with native locking, or HCP Terraform free tier). Terraform runs in a dedicated infra sandbox that holds cloud credentials **only for the duration of the apply**.

---

## 15. MCP tool layer

Agent tools are MCP servers loaded into LangGraph via `langchain-mcp-adapters`.

| MCP server | Tools | Runs where |
|---|---|---|
| `blackboard-mcp` | `get_task`, `get_contract`, `propose_contract_change`, `write_note`, `search_code`, `search_lessons`, `send_message`, `request_dependency`, `raise_decision` | Daemon |
| `sandbox-mcp` | `read_file`, `write_file` (checks reservation), `run_command` (allowlisted; formatters scoped to the write set; acquires heavy-exec semaphore for tests/typechecks), `run_tests`, `install_package` (curated allowlist OR hard `Approve`; `--only-binary :all:` / `--ignore-scripts`; scoped `HTTP(S)_PROXY` injection; recorded in `pending_deps` — `A7-10`) | Daemon → Docker exec |
| `git-mcp` | `status`, `diff`, `log`, `commit` (**symlink/mode-`120000` check + write-set diff enforced**) | **Daemon only**, hardened git (section 19). Agents never run git inside sandboxes and `/workspace/.git` does not exist. |
| `ci-mcp` | `trigger_pipeline`, `get_run`, `get_logs` | Daemon |
| `deploy-mcp` | `deploy`, `warm_up`, `probe`, `rollback`, `logs` (wraps `DeployTarget`) | Daemon |
| `infra-mcp` | Section 14 | Daemon → infra sandbox |
| `browser-mcp` | Playwright (release env only) | Release env |
| `docs-mcp` | `search_docs(library, query)` over the **local, curated** docs index | Daemon |
| External | Vercel, Render, Neon, Terraform, GitHub MCP servers | Vendor |

### Permission matrix
| Role | blackboard | sandbox | git | ci | deploy | infra | browser | docs |
|---|---|---|---|---|---|---|---|---|
| Orchestrator | full | – | read | read | read | read | – | ✓ |
| Leads | own domain + change approvals | read | read | read | – | plan (Infra Lead) | – | ✓ |
| Implementer | task scope | own sandbox | commit own worktree | – | – | validate (Infra impl.) | – | ✓ |
| Test-Writer | spec + contracts + `conftest.py` | own sandbox, **test paths only** | – | – | – | – | – | ✓ |
| Reviewer | task scope | – | read | – | – | – | – | ✓ |
| Integration Agent | task scope | ✓ | merge-queue branch | – | – | – | – | ✓ |
| Release Agent | read + incidents | – | read | full | full | apply (approved) | ✓ | – |

**`foundry-mcp`** lets external MCP clients (Claude Desktop, Cursor, OpenCode, the Gemini CLI) start projects, check status, and answer decisions.

---

## 16. Model gateway: first-party SDK transports & supply-chain safety

A core design goal of Foundry is **universal provider flexibility with zero third-party gateway supply-chain bloat and zero account-ban risk (`A7-1`, `A7-2`)**.

> **Why Foundry does NOT use LiteLLM (`A7-1`):**
> 1. **Supply-chain security:** LiteLLM suffered two severe PyPI/Docker supply-chain compromises in 2026 (`v1.82.7`/`v1.82.8` in March 2026 and `v1.107.0` in October 2026 via `.pth` startup hooks) that harvested LLM API keys, cloud credentials, and developer sessions. Because Foundry's daemon sits on the host with access to the user's vault/keyring, pulling a sprawling third-party aggregator into the daemon creates an unacceptable blast radius.
> 2. **RAM & abstraction cleanliness:** Foundry already owns its own `ModelTransport` protocol, budget/cost ledger, rate-limit router, secret scanner, and record/replay cassettes. Removing LiteLLM saves ~200 MB of daemon RAM and eliminates leaky abstractions around provider-specific prompt caching and tool calling.

### 16.1 Core concepts & `ModelTransport` protocol
| Concept | Meaning |
|---|---|
| **Provider** | A configured official API endpoint (OpenAI, Gemini, Anthropic, xAI, Groq, DeepSeek, Mistral, Together, Fireworks, GitHub Models, optional OpenRouter preset) or a local OpenAI-compatible server (Ollama, LM Studio, vLLM). API keys live in the keychain or encrypted vault. |
| **Transport (`ModelTransport`)** | Thin adapter over one of **3 official, first-party SDKs**: `openai_compat` (`openai` SDK with configurable `base_url`), `google_genai` (`google-genai` SDK), or `anthropic` (`anthropic` SDK). |
| **Billing mode** | `paid_api` (exact per-token USD budget from provider telemetry), `free_tier` (\$0 official API tier, rate-limited, subject to `data_policy`), or `local` (\$0, local Ollama/vLLM hardware). |
| **Model profile** | A named variable (e.g., `@planner`, `@lead`, `@coder`, `@verifier`) → provider + model + transport + billing_mode + fallbacks + capabilities. |
| **Assignment** | Which profile each agent role, domain, or instance uses. |
| **Data tier** | Whether prompts may be used for provider training (`paid` / `enterprise_work` vs `free_training_allowed` vs `local`). Checked against the project's `data_policy`. |

```python
class ModelTransport(Protocol):
    name: str  # "openai_compat" | "google_genai" | "anthropic"
    async def complete(
        self,
        provider_id: str,
        provider_cfg: ProviderConfig,
        model: str,
        request: GatewayRequest,
    ) -> GatewayResponse: ...
    async def health_check(
        self, provider_id: str, provider_cfg: ProviderConfig
    ) -> TransportHealth: ...
```

All calls pass through Foundry's unified **Model Gateway**, which wraps the 3 first-party SDK transports with: assignment resolution, capability & `data_policy` validation, **rate-limit backoff & fallback routing**, exact token/prefix-cache budget tracking (`llm_calls`), **mandatory secret scanning of every prompt**, and normalized record/replay.

### 16.2 Three thin first-party SDK adapters (100+ endpoints, zero LiteLLM)
Instead of a monolithic third-party proxy library, **3 official SDKs cover virtually every LLM provider**:

| Transport | Official Package | Providers Covered & Native Strengths |
|---|---|---|
| **`openai_compat`** (`OpenAICompatibleTransport`) | `openai` | OpenAI (`api.openai.com`), **xAI Grok**, **Groq**, **DeepSeek**, **Mistral**, **Together AI**, **Fireworks**, **GitHub Models**, **Ollama** (`http://localhost:11434/v1`), **LM Studio**, **vLLM**, and **OpenRouter** (strictly as an optional `base_url` preset, never a core dependency). Native `tool_calls` and `prompt_tokens_details.cached_tokens`. |
| **`google_genai`** (`GoogleGenAITransport`) | `google-genai` | **Google Gemini Developer API** (AI Studio) and **Vertex AI**. Native structured tool calling and exact `usage_metadata.cached_content_token_count` telemetry. |
| **`anthropic`** (`AnthropicTransport`) | `anthropic` | **Anthropic Claude API**. Direct control over `cache_control: {"type": "ephemeral"}` prompt-caching breakpoints and native `tool_use` blocks. |

Adding any new OpenAI-compatible provider is a single command (`foundry models add-key <provider>`) or a 3-line entry in `models.yaml` specifying its `base_url`.

### 16.3 Why consumer CLI subscriptions are NOT used in ReAct loops (`A7-2`)
- **Vendor ToS safety:** Major providers (including Anthropic and Google) explicitly prohibit routing consumer subscription logins (`Claude Pro/Max`, personal Google `Gemini CLI` OAuth) through automated third-party multi-agent harnesses, and automated abuse detection can suspend accounts (`403 TOS_VIOLATION`). Foundry **never** scrapes, reads, or proxies internal CLI/IDE OAuth tokens, and **does not run CLI subprocess loops** for ReAct implementers or benchmarks.
- **Technical reliability:** Native first-party SDK APIs provide real prefix caching (up to 90% input discount), structured `tool_calls`, low latency, and exact token telemetry — without spawning 300–600 cold Node/Rust subprocesses or fighting hidden CLI system prompts.
- **Pluggable future option:** Because `ModelTransport` is a clean Python `Protocol`, an opt-in, **single-shot, non-tool** CLI adapter (e.g., for manual human-triggered reviews, with an explicit vendor-ToS disclaimer) can be added in Post-MIV without affecting the core gateway.

### 16.4 Assignment resolution
```
task-type override > agent instance > role > domain > project default > global default
```
Three ways to assign:
1. `models.yaml` / the CLI (`foundry models assign implementer @coder`)
2. The Settings UI model matrix (Dashboard v0.5+)
3. Orchestrator chat commands (*"use xai for verifiers and gemini_flash_lite for implementers"*) → `set_model_assignment`

### 16.5 Capability and policy validation
The gateway rejects:
- models without native tool calling for tool-using roles
- context windows too small for the role
- verifiers from the implementer's model family when `require_verifier_family_diversity` is on (a warning plus a fallback is allowed when only one provider is configured)
- **free-tier consumer providers when `data_policy.allow_free_tier` is false**
- **cross-provider fallbacks during benchmark runs (`benchmark_pin_models: true`)**, guaranteeing strict model pinning for fair evaluations (`A7-13`)

### 16.6 Record/replay with normalization
Purpose: develop and test the engine (scheduler, reservations, merge queue, events, graphs) at **\$0 API cost**.
- **Prompt normalizer** (runs before hashing): replaces volatile values with stable placeholders:
  - ULIDs/UUIDs → `<id_1>`, `<id_2>` (numbered by first appearance)
  - ISO timestamps → `<ts>`; git SHAs → `<sha_n>`
  - worktree/temp paths → `<wt>`; container IDs → `<cid>`
  - test-timing footers (`passed in 0.42s`) → `passed in <t>`
  - durations, ports, PIDs
- **Two lookup modes:**
  1. **Content mode:** hash of the normalized request
  2. **Sequential cassette mode:** key `(role, task_slug, node, step_index)`, so multi-step agent runs replay in order even if small prompt details change
- **Miss policy:** `strict` (fail, for CI), `record` (call the real provider and save), `passthrough`.
- Replayed calls are logged with `replayed=true` and cost \$0.
- Benchmark runs **never** use replay (they must measure real behavior).

### 16.7 Example `models.yaml` (First-Party Official SDK Transports Only — Zero LiteLLM)
```yaml
providers:
  # --- Official First-Party SDK Transports (Zero LiteLLM, 100% Official APIs + Local) ---
  gemini_paid:
    transport: google_genai
    billing_mode: paid_api
    key_ref: vault:gemini
    data_tier: paid

  gemini_free:
    transport: google_genai
    billing_mode: free_tier
    key_ref: vault:gemini_free
    data_tier: free_training_allowed   # rejected unless data_policy.allow_free_tier: true

  openai_paid:
    transport: openai_compat
    base_url: https://api.openai.com/v1
    billing_mode: paid_api
    key_ref: vault:openai
    data_tier: paid

  xai_paid:
    transport: openai_compat
    base_url: https://api.x.ai/v1
    billing_mode: paid_api
    key_ref: vault:xai
    data_tier: paid

  anthropic_paid:
    transport: anthropic
    billing_mode: paid_api
    key_ref: vault:anthropic
    data_tier: paid

  deepseek_paid:
    transport: openai_compat
    base_url: https://api.deepseek.com/v1
    billing_mode: paid_api
    key_ref: vault:deepseek
    data_tier: paid

  groq_paid:
    transport: openai_compat
    base_url: https://api.groq.com/openai/v1
    billing_mode: paid_api
    key_ref: vault:groq
    data_tier: paid

  openrouter_optional:                 # optional base_url preset, not a core dependency
    transport: openai_compat
    base_url: https://openrouter.ai/api/v1
    billing_mode: paid_api
    key_ref: vault:openrouter
    data_tier: paid

  ollama_local:
    transport: openai_compat
    base_url: http://localhost:11434/v1
    billing_mode: local
    data_tier: local

profiles:
  "@planner":
    provider: gemini_paid
    model: "<gemini-pro-tier>"
    price: { in: 2.00, cached_in: 0.20, out: 12.00 }
    fallbacks: ["@planner_openai"]

  "@planner_openai":
    provider: openai_paid
    model: "<gpt-5-or-o3-tier>"
    price: { in: 2.00, cached_in: 0.50, out: 8.00 }

  "@lead":
    provider: gemini_paid
    model: "<gemini-flash-tier>"
    price: { in: 1.50, cached_in: 0.15, out: 7.50 }
    fallbacks: ["@lead_openai"]

  "@lead_openai":
    provider: openai_paid
    model: "<gpt-4.1-mini-or-codex-tier>"
    price: { in: 1.50, cached_in: 0.30, out: 6.00 }

  "@coder_plus":
    provider: gemini_paid
    model: "<gemini-flash-tier>"
    price: { in: 1.50, cached_in: 0.15, out: 7.50 }
    fallbacks: ["@lead_openai"]

  "@coder":
    provider: gemini_paid
    model: "<gemini-flash-lite-tier>"
    price: { in: 0.25, cached_in: 0.025, out: 1.50 }
    fallbacks: ["@coder_deepseek"]

  "@coder_deepseek":
    provider: deepseek_paid
    model: "deepseek-chat"
    price: { in: 0.27, cached_in: 0.07, out: 1.10 }

  "@verifier":
    # Cross-family verification: uses xAI Grok or OpenAI when @coder is on Gemini
    provider: xai_paid
    model: "<grok-mid-tier>"
    price: { in: 1.25, cached_in: 0.20, out: 2.50 }
    fallbacks: ["@verifier_openai"]

  "@verifier_openai":
    provider: openai_paid
    model: "<gpt-4.1-mini-tier>"
    price: { in: 1.25, cached_in: 0.20, out: 2.50 }

  "@fast":
    provider: gemini_paid
    model: "<gemini-flash-lite-tier>"
    price: { in: 0.25, cached_in: 0.025, out: 1.50 }

assignments:
  roles:
    orchestrator: "@planner"
    lead: "@lead"
    implementer: "@coder"            # upgraded to @coder_plus on attempt 2+
    test_writer: "@verifier"
    reviewer: "@verifier"
    integration: "@coder_plus"
    release: "@fast"

policies:
  require_verifier_family_diversity: true   # true | warn | false
  upgrade_on_attempt: 2
  benchmark_pin_models: false               # set true during foundry-bench (disables fallbacks)
```

---

## 17. Human-in-the-loop decision system

### 17.1 Categories
| Category | Examples |
|---|---|
| **Strategic** | Requirements, architecture, data model, auth approach |
| **Tactical** | Breaking contract changes, unplanned dependencies, schema changes, model reassignments |
| **Operational** | Implementation choices, naming, refactors |
| **Release and infrastructure** | Production deploys, contract (destructive) migrations, Terraform apply, spending money, data restores |

### 17.2 Modes and presets (lean v1 — `A7-11`)
| Mode | Behavior |
|---|---|
| **Auto** | The agent decides, continues immediately, and logs an ADR |
| **Approve** | The agent recommends; dependent work waits for human confirmation (via CLI Decision Inbox or Dashboard) |
| **Human decides** | The agent offers 2–4 options; the human chooses or writes their own |

*(Note: `Notify` mode with a 60-second grace buffer and post-grace `Undo` compensating handlers is deferred to the Post-MIV Backlog to keep v1 state machines simple and deterministic.)*

| Preset | Strategic | Tactical | Operational | Release/Infra |
|---|---|---|---|---|
| Autopilot | Auto | Auto | Auto | Approve |
| **Balanced** (default) | Approve | Approve | Auto | Approve |
| Architect | Human decides | Approve | Auto | Approve |
| Full control | Human decides | Human decides | Approve | Approve |
| **Benchmark** (bench only) | Auto | Auto | Auto | Auto (*local target only*) |

### 17.3 Rules
- **CLI Decision Inbox (Module 2+):** Users can inspect and resolve pending decisions directly from the terminal (`foundry decisions list`, `foundry decisions resolve <id> --approve|--reject`) before Dashboard v0.5 exists.
- Release/Infra is **never below Approve** for cloud targets (hardcoded). The Benchmark preset is only allowed with `LocalComposeTarget`.
- **Hard approval for unplanned packages (`A7-10`):** Unplanned `install_package` requests outside the curated allowlist **always require hard `Approve`** (never downgraded to `Auto` in daily use).
- **Risk escalation:** a decision with confidence below 0.6, or one marked irreversible, is raised to at least Approve.
- **Only dependent work waits:** a pending decision pauses only its own LangGraph thread and the tasks listed in `blocks_task_ids`.
- **Idempotent creation:** decisions are created with `get_or_create(idempotency_key)` (`thread_id:node:revision`), so a resumed node never creates a duplicate card.
- **Optional timeout** for reversible decisions only: if unanswered after the timeout, the recommendation is taken.
- Every decision is stored as an ADR.

### 17.4 Flow
```
Agent → raise_decision → Decision Service: get_or_create(key) + policy
   ├ auto          → status auto_decided → thread continues
   └ approve/human → card via CLI Inbox / WebSocket; thread interrupt()s (only this thread)
Human → foundry decisions resolve <id>  OR  POST /api/decisions/{id}/resolve → status resolved (optimistic lock)
      → event decision.resolved → supervisor → graph.ainvoke(Command(resume=answer), thread_id)
```

---

## 18. Budget and cost model

Foundry enforces **exact token and USD accounting** directly from first-party SDK response telemetry (`openai`, `google-genai`, `anthropic`) — with zero third-party proxy overhead and support for \$0 local models (Ollama/vLLM) and \$0 normalized record/replay during engine development.

### 18.1 API prices used for planning (per 1M tokens, Nov 2026)
| Tier | Input | Cached input | Output | Notes |
|---|---|---|---|---|
| Gemini **Flash-Lite** / DeepSeek tier | \$0.25 | \$0.025 | \$1.50 | Implementers, release, summaries |
| Gemini **Flash** tier | \$0.75 intro → **\$1.50** | \$0.075 → \$0.15 | \$3.75 → **\$7.50** | Intro pricing ends Jan 2027. **Planning uses the higher price.** |
| Gemini **Pro** tier (≤200k prompt) | \$2.00 | \$0.20 | \$12.00 | Orchestrator key calls only |
| xAI Grok / OpenAI **mid** tier | \$1.25 | \$0.20 | \$2.50 | Cross-family Verifiers |

All per-token prices live in `models.yaml`.

### 18.2 Per-task token model (ReAct loop)
Assumptions:
- context grows from about 8k to 20k tokens
- about 3k new (uncached) tokens per step
- about 1.2k output tokens per step
- native provider prefix caching (`cached_content_token_count` / `prompt_tokens_details.cached_tokens` / `cache_control`) on the stable prefix (system prompt + exact contract JSON)

| Case | Steps | Input (uncached / cached) | Output | Local Ollama/vLLM | Flash-Lite API | Flash API (planning price) |
|---|---|---|---|---|---|---|
| Typical attempt | 12 | 36k / 144k | 15k | **\$0.00** | **\$0.035** | \$0.19 |
| Worst attempt (cached) | 25 | 100k / 400k | 40k | **\$0.00** | \$0.095 | \$0.51 |
| Worst attempt (no caching) | 25 | 500k / 0 | 40k | **\$0.00** | \$0.19 | \$1.05 |

**Why tasks are small:** keeping each task to ~12 steps and compacting tool outputs older than 6 steps keeps native prefix caches hot and protects per-run USD budgets.

### 18.3 Per-run estimates (Paid First-Party APIs vs Local Models)
| Component | Medium app (~25 tasks, 8 features) | Small benchmark app (~10 tasks, 3 features) | Local Ollama/vLLM |
|---|---|---|---|
| Orchestrator (Pro tier, ~3 key calls) | \$0.20 | \$0.15 | \$0.00 |
| Leads (Flash/Mid tier: contracts, approvals) | \$0.90 | \$0.35 | \$0.00 |
| Implementers (1.3 attempts avg) | \$2.10 | \$0.70 | \$0.00 |
| Black-box tests (Cross-family verifier) | \$0.40 | \$0.15 | \$0.00 |
| Reviews (risk-based ≈50%) | \$0.30 | \$0.10 | \$0.00 |
| Integration, summaries, release | \$0.20 | \$0.08 | \$0.00 |
| **Total per run** | **≈ \$4.10** (\~\$3.00 intro) | **≈ \$1.50** (\~\$1.15 intro) | **\$0.00** |

### 18.4 Reconciled limits & run kill-switch (`A7-11`)
Partitioned budgets prevent implementation retries from starving G3/G4 verification, while reconciling per-task caps with Section 18.2's retry-upgrade (`@coder_plus`) math:

```yaml
budgets:
  billing_enforcement: actual_usd   # actual_usd (exact provider telemetry) | shadow_usd (if testing local models)
  monthly_usd: 25.00
  per_run_usd: 5.00                 # medium app; benchmark specs use 2.00 (Easy/Med) or 3.00 (Hard)
  per_run_wall_clock_minutes: 90    # hard run kill-switch
  per_run_max_llm_calls: 600        # hard run call cap (prevents infinite loops even on $0 local models)
  per_task:
    implement_usd: 0.60             # covers attempt 1 (@coder) + attempt 2/3 (@coder_plus cached)
    review_usd: 0.15                # G4 reserved; covers large diffs (>300 lines)
  per_feature:
    blackbox_tests_usd: 0.30        # G3 reserved (Test-Writer + up to 2 self-fix retries)
  per_domain_lead_usd: 0.60
  agent_step_limit: 25
  max_attempts_per_task: 3
  warn_at: 0.8                      # warning event at 80%
  on_exceed: pause_and_decide       # raise limit / downgrade / stop
```

### 18.5 Cost-saving strategies
1. **Normalized record/replay cassettes** for all engine development and CI (\$0)
2. Deterministic checks before any LLM call
3. Small tasks; exact contract rows instead of prose documents; no chat logs in context
4. Native first-party **prefix caching** (stable system prompt + contracts at the start of every prompt)
5. Fast/Lite models by default (`@coder`); upgrade to `@coder_plus` only on attempt 2+
6. G3 per feature (not per task); G4 risk-based
7. Local embeddings (`fastembed` ONNX) = free RAG; optional Ollama/vLLM for \$0 local dev
8. Hard step, attempt, call-count, and wall-clock kill-switches

---

## 19. Security, safety, and privacy

### 19.1 Controls (including daemon supply-chain hardening — `A7-1`, `A7-3`, `A7-4`, `A7-10`)
| Risk | Control |
|---|---|
| **Daemon supply-chain compromise (`A7-1`)** | **Zero LiteLLM** (only 3 official first-party SDKs: `openai`, `google-genai`, `anthropic`). Commit hash-verified `uv.lock`, enforce `uv sync --frozen` (and `--require-hashes` in CI/builds), apply a **3–7 day minimum PyPI release-age cooldown** (`exclude-newer` in `uv` config), and **pin every Docker image by immutable `sha256` digest** (never `:latest`). |
| Harmful commands | Sandbox: non-root, `cap-drop ALL`, `no-new-privileges`, read-only root filesystem, pids/memory/CPU limits, command allowlist |
| **Host-side code execution & symlink exfiltration (`A7-3`)** | Export `openapi.json` **strictly inside Docker** (never import agent code on the host). Before *any* host daemon tool (`git-mcp`, `Semgrep`, `gitleaks`, `oasdiff`) touches a workspace/worktree, run a deterministic recursive walk rejecting **any symlink (`os.path.islink` / git mode `120000`), FIFO/device node, or oversized YAML bomb**. |
| Data theft / DNS tunneling / install scripts (`A7-3`, `A7-10`) | Sandbox on a dedicated **internal Docker network (`internal: true`, no external egress and no external DNS resolution)**. Blackboard DB and host daemon are unreachable from the sandbox (`REVOKE CONNECT ON DATABASE foundry_blackboard FROM PUBLIC`). Unplanned `install_package` requires **curated allowlist OR hard human `Approve`**, enforces `--only-binary :all:` (`uv`) and `--ignore-scripts` (`pnpm`), and injects `HTTP(S)_PROXY` strictly into that single `docker exec` call. |
| Sandbox escape via Docker | **The Docker socket is never mounted.** No privileged containers. Sandboxes are managed only by the daemon. |
| Writes outside the worktree | Path jail in `sandbox-mcp` (normalized paths, symlinks rejected, everything under `/workspace`); write-set diff enforcement at commit, G2, and G5 |
| Git hooks / config running on the host (`A7-4`) | Agents **never run git**. The container mounts a **`.git`-free workspace** (`workspaces/<task_id>`), so `/workspace/.git` does not exist inside Docker and the repo `.git` dir is never mounted. Daemon-side git runs only after symlink checks with `-c core.hooksPath=/dev/null -c core.fsmonitor=false`, `GIT_CONFIG_NOSYSTEM=1`, and explicit `safe.directory`. Non-infra changes to `.gitattributes`, `.gitmodules`, `.git*`, and mode `120000` entries are rejected. |
| Secrets leaking | No secrets in sandboxes. Official API keys live in keychain/vault (zero OAuth token scraping). **gitleaks-style scanner on every LLM prompt, every trace export, and every commit.** Deploy tokens are used only daemon-side. |
| Prompt injection (docs, dependencies, generated content, web) | Docs come from a **local, curated index** (no live web fetching); retrieved text is marked as untrusted data; strict per-role tool permissions; dangerous actions always require approval |
| Destructive infrastructure / data actions | `tf_destroy` and data restores are human-only; `tf_apply` requires an approved plan + token |
| Production breakage | G5–G7, expand-only migrations (`squawk`), code-only automatic rollback |
| Runaway cost / infinite loops | Reconciled partitioned budgets, hard per-run wall-clock timeout (90 min) & max LLM calls cap (600), bounded loops, rate limiter |
| Hard-to-trace actions | Append-only `events` with monotonic `publish_seq`; every action attributed to an agent |

### 19.2 Privacy: what "local-first" does and doesn't mean
- **What stays local:** the orchestration, blackboard, repo, sandboxes, traces (by default), and API keys.
- **What leaves your machine:** **prompts containing your code and specs go to the official LLM API providers you configure** (unless you run local Ollama/vLLM). If you deploy to cloud targets in Post-MIV, your code also goes to GitHub/cloud providers.
- **Free API tiers vs Paid APIs:** some consumer free API tiers allow the provider to **use prompts and outputs to improve products, including human review**, whereas paid API tiers do not. So:
  - New projects default to `data_policy.allow_free_tier: false` (paid APIs and local models only).
  - Free consumer API tiers are allowed only for public or synthetic work (e.g., dev experiments), with an explicit opt-in.
- **Tracing:** local by default. Export to LangSmith (cloud) is **opt-in per project** and runs the secret scanner/redactor first.
- The dashboard shows each project's data policy clearly in its Settings.

---

## 20. Observability

- **Local by default (zero extra RAM):** every LLM call is recorded in `llm_calls` (provider, transport, billing_mode, tokens, cached tokens, latency, exact USD cost, prompt hash, replay flag) and every state transition or tool event is recorded in `events` (with monotonic `publish_seq`). The CLI and Dashboard read directly from these two tables + the WebSocket stream.
- **Optional exporters (off by default):**
  - **Arize Phoenix (local container):** start only when inspecting traces deeply (`foundry up --with-phoenix`).
  - **LangSmith (cloud):** opt-in per project (`data_policy.allow_trace_export: true`). All payloads pass through the secret/PII redactor before leaving the daemon.
- **Tracked operational metrics** (shown in the Dashboard and exported as JSON for benchmark runs):
  - Tasks by status, attempt counts, escalation counts per level
  - **Concurrency metrics:** reservation wait time, heavy-exec semaphore wait time, merge queue wait time, merge conflict count and resolution tier (clean / Integration Agent / task rerun)
  - **Contract metrics:** additive vs breaking change requests, batch sizes, task reopens, freeze events
  - **Verification metrics:** defects caught per gate (G1–G7), post-test `Test-Validator` broken-test rejections (`invalid_test_request` vs code errors), test disputes and how each was settled
  - **Cost & latency metrics:** exact USD spend from provider telemetry, native prefix cache hit rate, provider rate-limit backoff time (`ratelimit.backoff`), wall-clock time per wave and per run
- **Replay mode:** step through any completed run's timeline in the Dashboard using the append-only `events` table ordered by `publish_seq`.

---

## 21. Dashboard and daemon API

### 21.1 Layout (controls | preview | controls)
```
┌──────────────────────────── TOP BAR ────────────────────────────┐
│ project ▾ │ wave/phase ▸ progress │ $ spend  │ autonomy ▾ │ ⏸ ▶ ⏹ │ 🔔 │ ⌘K │
├────────────┬──────────────────────────────────┬────────────────┤
│ LEFT       │ CENTER                           │ RIGHT          │
│ Spec       │ Preview (local/staging/prod,     │ Decisions ★    │
│ Agents     │   device frames, click-to-report)│ Chat           │
│ Tasks      │ Code (Monaco + diff)             │ Pipeline       │
│ Files      │ Architecture (React Flow)        │ Releases       │
│ (locks)    │ Agent Detail                     │ Costs          │
│            │ Blackboard explorer              │ Settings       │
├────────────┴──────────────────────────────────┴────────────────┤
│ BOTTOM DRAWER: Activity stream │ Terminal/Logs │ Problems        │
└────────────────────────────────────────────────────────────────┘
```
All panels are resizable and collapsible, with tabs in every section.
- **CLI Decision Inbox (Module 2):** `foundry decisions list` / `resolve` provides full human-in-the-loop control before the web UI exists.
- **Dashboard v0.5 (for the MIV):** shipping in Module 3 (P5), includes Top Bar, Tasks list/board, Agents list, Decisions inbox, Preview iframe, Model switcher, and live Activity stream.
- **Dashboard v1.0 (full):** adds React Flow architecture/agent graphs, Monaco diff viewer, full Blackboard explorer, and replay scrubber in Post-MIV (P8).

### 21.2 Daemon API (main endpoints)
```
GET    /api/projects                        POST /api/projects              (new from spec)
GET    /api/projects/{id}                   POST /api/projects/{id}/runs    (start run)
POST   /api/runs/{id}/pause|resume|stop
GET    /api/runs/{id}/agents                GET  /api/agents/{id}/timeline
POST   /api/agents/{id}/message             POST /api/agents/{id}/pause
GET    /api/runs/{id}/tasks                 GET  /api/tasks/{id}    (diff, gates, reservations)
GET    /api/runs/{id}/reservations          GET  /api/runs/{id}/files/tree
GET    /api/files?path=…&ref=…
GET    /api/runs/{id}/contracts             GET  /api/contracts/{id}/versions
GET    /api/runs/{id}/decisions?status=     POST /api/decisions/{id}/resolve
POST   /api/runs/{id}/chat                  (talk to Orchestrator)
GET    /api/runs/{id}/pipeline              GET  /api/runs/{id}/releases
POST   /api/releases/{id}/rollback          GET  /api/runs/{id}/incidents
GET    /api/runs/{id}/costs                 GET  /api/runs/{id}/metrics
GET    /api/settings/providers              GET  /api/settings/models
PUT    /api/settings/models/assignments     GET  /api/settings/autonomy
PUT    /api/settings/autonomy               GET  /api/settings/data-policy
PUT    /api/settings/data-policy            GET  /api/preview/{run_id}/url?env=local|staging|production

WS     /ws/runs/{id}                        (streams outbox events ordered by monotonic publish_seq; supports ?after_seq=)
```
Because every WebSocket message carries its strictly monotonic `publish_seq` assigned in commit-visible relay order (`A7-5`), a reconnecting browser passes `?after_seq=<last_seen>` and receives any missed events with zero commit-ordering gaps.

---

## 22. Configuration files

### Global `~/foundry/config/foundry.yaml`
```yaml
server:
  host: 127.0.0.1
  port: 4200

runtime:
  max_heavy_exec_slots: 2           # semaphore for pytest/mypy/tsc/vitest/Schemathesis/G5 (A7-6)
  max_concurrent_tasks: 4           # lightweight agent containers allowed to wait on LLM I/O
  sandbox_limits:
    memory: 1536m
    cpus: 1.5
    pids: 512
    node_max_old_space_mb: 768
  sandbox_network: internal_no_dns
  egress_proxy_allowlist:
    - pypi.org
    - files.pythonhosted.org
    - registry.npmjs.org

embeddings:
  provider: fastembed_onnx          # CPU ONNX Runtime, <200 MB RAM
  model: BAAI/bge-small-en-v1.5

observability:
  local_tracing: true
  phoenix: false
  langsmith_export: false           # opt-in per project; redacted before export

secrets:
  backend_order: [keyring, encrypted_vault]
  vault_path: ~/foundry/vault/credentials.enc

budgets:
  monthly_usd: 25.00
```

### Project `.foundry/project.yaml`
```yaml
name: taskflow
stack_pack: fastapi-react-postgres

data_policy:
  allow_free_tier: false            # true only for public/synthetic dev experiments
  allow_trace_export: false

autonomy:
  preset: balanced                  # autopilot | balanced | architect | full_control | benchmark
  overrides:
    tactical: approve
  confidence_threshold: 0.60
  decision_timeout_minutes: null

budgets:
  billing_enforcement: actual_usd
  per_run_usd: 5.00
  per_run_wall_clock_minutes: 90
  per_run_max_llm_calls: 600
  per_task:
    implement_usd: 0.60
    review_usd: 0.15
  per_feature:
    blackbox_tests_usd: 0.30
  per_domain_lead_usd: 0.60
  agent_step_limit: 25
  max_attempts_per_task: 3
  warn_at: 0.80

deploy:
  target: local_compose             # MIV default: local_compose; Post-MIV: vercel_render_neon | aws | azure

git:
  remote: github
  repo: <user>/taskflow
```

---

## 23. Evaluation and benchmark (`foundry-bench`)

### 23.1 Fixing the hidden-test contract problem (pinned interfaces)
A hidden test suite cannot call API endpoints or click UI elements if the agents invent route paths, payload field names, or selectors from scratch. Therefore, **every benchmark spec consists of two parts**:
1. **Natural-language product requirements** + **Pinned Interface Stubs**:
   - A **partial OpenAPI 3.1 file (`pinned_openapi.yaml`)** that pins the core paths, HTTP methods, auth scheme (`POST /api/auth/login` returning `{"access_token": ...}`), and required request/response fields that the hidden API tests will call.
   - A **pinned selector list (`pinned_testids.yaml`)** of 5–8 top-level `data-testid` attributes for the UI smoke test.
2. **Agent freedom with machine-enforced locks:**
   - During Section 9.1 (Contract Planning), the Data Lead designs the full DB schema, the Backend Lead expands `pinned_openapi.yaml` (adding helper endpoints, internal schemas, query params, validation rules), and the Frontend Lead designs the component tree.
   - Every JSON pointer from `pinned_openapi.yaml` and `pinned_testids.yaml` is stored in `contract_versions.pinned_pointers`.
   - Any contract proposal or change request that modifies or removes a pinned pointer is **deterministically rejected (`pinned_violation`)**.

### 23.2 Scoring
- **Primary score (80% weight): Hidden Black-Box API Acceptance Suite** (`pytest` + `httpx.AsyncClient` against the deployed container). Tests CRUD flows, auth/permissions, validation edge cases, and multi-step business invariants defined in the spec.
- **Secondary score (20% weight): Hidden Playwright Smoke Suite** (5–8 end-to-end user journeys using only the pinned `data-testid`s + accessible roles) plus `LocalComposeTarget` deployment health check.

### 23.3 Benchmark suite (8 specs: Dev Set vs. Held-Out Evaluation Set — `A7-13`)
To prevent prompt or harness overfitting, **development specs used while iterating on `B1`/`F0` are strictly separated from held-out evaluation specs** (whose hidden suites are never inspected during tuning):

| ID | Tier | Spec summary | Features | Split |
|---|---|---|---|---|
| **S1** | Easy | Bookmark manager with tags, search, and user auth | 3 | **Dev Set** (used to develop & debug B1/F0) |
| **S2** | Easy | Expense tracker with categories and monthly summary | 3 | **Dev Set** (secondary smoke spec) |
| **S3** | Medium | Multi-project task board with role-based permissions (`owner`/`member`/`viewer`) | 5 | ★ **Held-Out Core** |
| **S4** | Medium | Support ticket desk with agent assignment, status workflow, and internal notes | 5 | ★ **Held-Out Core** |
| **S5** | Medium | Room/equipment booking system with conflict detection and approval workflow | 5 | ★ **Held-Out Core** |
| **S6** | Medium | Invoice & client manager with line-item totals and status transitions | 5 | ★ **Held-Out Core** |
| **S7** | Hard | Team Kanban + comment threads + activity audit feed + webhook outbox | 6 | Extended Held-Out |
| **S8** | Hard | Marketplace listings + order state machine + mock payment webhook idempotency | 6 | Extended Held-Out |

### 23.4 Fair, equal-API-cost baselines and focused ablations (`A7-13`)
To make sure Foundry doesn't win simply by spending more tokens, using stronger models, or getting better scaffolding:
- **Strict first-party API pinning:** All benchmark runs execute exclusively via first-party SDK transports (`openai_compat`, `google_genai`, `anthropic`) with **`benchmark_pin_models: true`** (cross-provider fallback disabled), exact provider token telemetry, exact logged model version IDs, and identical per-spec USD caps (`$2.00` for Medium, `$3.00` for Hard).
- **Identical scaffolding for `B1`:** Baseline `B1` receives the exact same Wave 0 repo skeleton, `pinned_openapi.yaml` and `pinned_testids.yaml` stubs, `oasdiff` + bounded `Schemathesis` tools, and context compaction as `F0`.
- **B1 — Single Agent (Equal-Budget):** One ReAct agent with the same tools (`sandbox`, `git`, `docs`), same pinned stubs, and same total USD budget, allowed to use `@lead`/`@coder` models and run G1/G2 checks in a loop until its budget or tests finish.
- **B2 — Sequential Foundry (`max_heavy_exec_slots = 1`, `max_concurrent_tasks = 1`):** Full Foundry hierarchy, contracts, and gates, running one task at a time. **Isolates whether parallelism actually saves wall-clock time** under API rate limits.
- **F0 — Full Foundry (`max_heavy_exec_slots = 2`, `max_concurrent_tasks = 4`):** The complete MIV system.

**Focused ablations (only the 3 highest-signal ablations, run on the 4 Held-Out Core specs):**
1. **A1 — No Contract-First / No Schema Conformance:** Skip staged contract planning and disable `oasdiff`/Schemathesis blocking in G2 (agents coordinate only via task descriptions and merge queue). Tests the value of machine-checked contracts.
2. **A2 — No Independent Black-Box Verifier (No G3):** Keep G1/G2 (implementer's own tests + linters) and G5, but disable the cross-family G3 Test-Writer. Tests whether independent black-box testing catches bugs that self-tests miss.
3. **A3 — Naive Concurrency (No Write-Set Reservations / No Wave 1 Foundation):** Let feature tasks run in parallel from the start without write-set reservations (relying only on git merge at the end). Measures the merge-conflict and rework rate that Layer 1–3 prevent.

### 23.5 Statistical rigor and honest metrics (`A7-13`)
Because LLM runs have high variance (and 4 specs × 3 runs yields wide CIs), **every cell on the 4 Held-Out Core specs is run `k = 3` times** with different seeds, keeping statistical claims modest and transparent:
- **Mean ± standard deviation** and **95% paired bootstrap confidence intervals stratified by spec** for Hidden Test Pass Rate (%).
- **Pass@1** and **Majority-Pass (≥2/3 runs deploy and pass ≥80% of hidden tests)**.
- **Cost Efficiency:** pass-rate points per exact API dollar spent (`cost_usd`).
- **Wall-Clock Time Breakdown:** active compute time vs **provider rate-limit backoff wait time** vs heavy-exec semaphore wait vs merge-queue time.
- **Concurrency & Gate Telemetry:** merge conflict rate, auto-resolution rate, defects caught per gate (G1 vs G2 vs G3 vs G5).

### 23.6 Benchmark budget (exact first-party API runs)
| Experiment | Configs × Specs × Runs | Total runs | Avg API cost / run | Total API budget |
|---|---|---|---|---|
| **MIV Pilot (P7)** | (B1, F0) × 4 Held-Out Core specs × 3 runs | 24 | ~\$0.85 | **~\$20** |
| **Core Ablations (P9)** | (B2, A1, A2, A3) × 4 Held-Out Core specs × 3 runs | 48 | ~\$0.80 | **~\$38** |
| **Extended Hard Suite (P9, optional)** | (B1, F0) × 2 Hard specs (S7, S8) × 1 run | 4 | ~\$1.50 | **~\$6** |

---

## 24. Tech stack

| Area | Choice |
|---|---|
| Language | Python 3.12 (daemon inside WSL2), TypeScript (dashboard + generated frontends) |
| Package manager & supply chain (`A7-1`) | `uv` (Python: hash-verified `uv.lock`, `uv sync --frozen --require-hashes`, 3–7 day `exclude-newer` cooldown), `pnpm` (JS: `--ignore-scripts`) |
| Agent framework | **LangGraph** + `langgraph-checkpoint-postgres` + `langchain-mcp-adapters` |
| LLM gateway (`A7-1`) | **Zero-LiteLLM First-Party SDK Gateway**: `openai` (`OpenAICompatibleTransport`), `google-genai` (`GoogleGenAITransport`), `anthropic` (`AnthropicTransport`) + rate-limit router + secret-scanner + normalized cassettes |
| API server | FastAPI + WebSockets (`?after_seq=`) + Pydantic v2 |
| Database & Event Bus (`A7-5`) | Postgres 16 + pgvector (**digest-pinned**, blackboard, checkpoints, outbox with monotonic `publish_seq`, and logical sandbox DBs — **no Redis in v1**) |
| ORM / migrations | SQLAlchemy 2 + Alembic |
| Local embeddings | **fastembed (ONNX Runtime, CPU)** — `BAAI/bge-small-en-v1.5` (<200 MB RAM) |
| Code parsing | `tree-sitter-language-pack` *(Mergiraf AST merge deferred to Post-MIV)* |
| Contract & migration checks | Spectral, **oasdiff** (on sandbox-exported `openapi.json`), **bounded Schemathesis**, **squawk** (Postgres migration linter) |
| Sandboxes | Docker Engine inside WSL2 (`docker` SDK, digest-pinned images, 2-slot heavy-exec semaphore × 1.5 GB) + `.git`-free workspaces + `tinyproxy` egress proxy |
| MCP | Official MCP Python SDK |
| Testing (Foundry) | pytest, pytest-asyncio, normalized record/replay cassettes |
| Testing (generated apps) | pytest, `httpx.AsyncClient`, vitest, Testing Library, static OpenAPI fixtures, bounded Schemathesis, Playwright |
| Security scanning | Semgrep (`--max-memory 512 --jobs 1`), gitleaks, Trivy, Checkov (all run only after symlink/FIFO walk) |
| CI & deploy | GitHub Actions, **`LocalComposeTarget` (Caddy blue/green — MIV)**; Post-MIV: Vercel + Render + Neon (Stage A), Terraform → AWS / Azure (Stage B) |
| Observability | Built-in `llm_calls` + `events` tables; optional Phoenix (local) and LangSmith (opt-in, redacted) |
| Dashboard & UX | CLI Decision Inbox (M2); React, Vite, TS, Tailwind, shadcn/ui, Recharts, Zustand, TanStack Query (M3 v0.5); React Flow + Monaco (Post-MIV v1.0) |
| Secrets | `keyring` (where native/WSL-interop backend exists) + **encrypted local vault** (`cryptography` scrypt + Fernet) |
| License | Apache-2.0 |

---

## 25. Modular repository structure

Foundry is organized into **loosely coupled packages** around clean Python `Protocol` interfaces (`ModelTransport`, `SandboxProvider`, `DeployTarget`, `StackPack`). This allows us to build, test, and use the project module-by-module without circular dependencies:

```
foundry/
├── AGENTS.md                    # Development rules: Plan-First workflow & architectural invariants
├── foundry_design_document.md   # This authoritative specification (v1.4)
├── pyproject.toml               # uv config (exclude-newer cooldown, strict ruff/mypy/pytest)
├── uv.lock                      # Hash-verified lockfile (enforced via `uv sync --frozen`)
├── packages/
│   └── foundry/
│       ├── cli/                 # [Module 1→2] `foundry` CLI (init, up, new, build, models, decisions)
│       ├── config/              # [Module 1] foundry.yaml, models.yaml, project.yaml loaders & schemas
│       ├── security/            # [Module 1] Vault + keyring, secret scanner, symlink/FIFO guard, hardened git
│       ├── gateway/             # [Module 1] First-Party SDK Model Gateway (Zero LiteLLM):
│       │   ├── transports/      #   openai_compat.py, google_genai.py, anthropic_sdk.py
│       │   ├── rate_limit.py    #   rate-limit backoff & fallback router (disabled in benchmark_pin_models)
│       │   ├── normalizer.py    #   prompt normalizer + cassette record/replay engine
│       │   └── router.py        #   profile assignment, exact token/cache ledger & run kill-switch
│       ├── sandbox/             # [Module 1] SandboxProvider, LocalDockerProvider (.git-free workspaces),
│       │                        #   HeavyExecSemaphore (2 slots), LogicalDBManager (pg_terminate_backend)
│       ├── stack_packs/         # [Module 1] StackPack protocol + `fastapi_react_postgres` skeleton
│       ├── mcp_servers/         # [Module 1→4] sandbox, git, docs (M1) → blackboard (M2) → ci, deploy (M4)
│       ├── blackboard/          # [Module 2] Postgres DDL/models, PG LISTEN/NOTIFY + publish_seq relay, fastembed
│       ├── contracts/           # [Module 2] Staged planning, Spectral/oasdiff classifier, batching, freeze caps
│       ├── supervisor/          # [Module 2→3] Waves (Wave 1 → Wave 2), thread spawner, scheduler, deadlock check
│       ├── agents/              # [Module 1→3]
│       │   ├── single/          #   [Module 1] Single-Agent full-stack builder (also Baseline B1)
│       │   ├── graphs/          #   [Module 2→3] Orchestrator, Domain Leads, Implementer, Verifiers
│       │   └── prompts/         #   Versioned role prompts
│       ├── concurrency/         # [Module 3] write_reservations, symlink+diff enforcer, serial merge queue
│       ├── verification/        # [Module 1→3] G1/G2 (M1) → G3 Black-Box Test-Writer, post-test Validator, Disputes, G4/G5 (M3)
│       ├── decisions/           # [Module 2→3] Idempotent get_or_create, Auto/Approve presets, CLI inbox, ADRs
│       ├── api/                 # [Module 3→4] FastAPI + WebSocket (?after_seq= replay)
│       └── pipeline/            # [Module 4] LocalComposeTarget (MIV); Post-MIV: VercelRenderNeonTarget, Aws/Azure
├── dashboard/                   # [Module 3→4] React app (v0.5 in M3, v1.0 in Post-MIV)
├── bench/                       # [Module 1→4]
│   ├── specs/                   # Dev (S1–S2) + Held-Out Core (S3–S6) + Extended (S7–S8)
│   ├── hidden_tests/            # API + Playwright acceptance suites (isolated from agents)
│   └── runner/                  # Runs B1 (from M1), B2, F0, A1–A3; computes paired bootstrap CIs
├── infra/
│   ├── docker-compose.yml       # Digest-pinned Postgres+pgvector + tinyproxy egress proxy (no Redis in v1)
│   ├── sandbox.Dockerfile       # Digest-pinned pre-built stack-pack sandbox image
│   └── terraform-modules/       # Post-MIV Stage B reusable modules
├── tests/                       # Unit + integration tests (100% $0 via normalized cassettes)
├── LICENSE                      # Apache-2.0
└── README.md
```

---

## 26. Modular roadmap (MIV Modules 1–4 + Deferred Post-MIV Backlog)

### 26.1 Lean MIV Modules (P0–P7)
Instead of waiting until Phase P7 to have a usable product, the roadmap is grouped into **4 lean, progressive modules**. At the end of **every single module**, you have a complete, runnable version of Foundry:

#### Module 1: Core Kernel, First-Party SDK Gateway & Single-Agent Builder (v0.1)
*Goal: A supply-chain-hardened local CLI tool using 3 thin first-party SDKs (`openai`, `google-genai`, `anthropic` — zero LiteLLM) that builds and tests full-stack FastAPI + React apps inside a `.git`-free Docker sandbox. Also delivers Baseline `B1` early.*

| Phase | Milestone | Deliverable & "Done When…" | Learning focus |
|---|---|---|---|
| **P0: Foundations & First-Party SDK Gateway** | **Module 1** | Repo with hash-verified `uv.lock` + `exclude-newer` cooldown, digest-pinned `docker-compose.yml` (Postgres+pgvector + `tinyproxy`, no Redis), Vault + `keyring`, secret scanner, First-Party SDK Model Gateway (`OpenAICompatibleTransport`, `GoogleGenAITransport`, `AnthropicTransport`), rate-limit backoff router, exact token/cache ledger + run kill-switch, and **normalized record/replay cassettes**. *Done when:* `foundry up` and `foundry models test --replay` pass at \$0 cost with zero LiteLLM dependencies. | First-party async SDKs, prompt caching, supply-chain hardening |
| **P1: Hardened Sandbox, Symlink/Git Guard & Stack Pack** | **Module 1** | Hardened daemon-side `git-mcp` (`hooksPath=/dev/null`, symlink/FIFO/mode-`120000` pre-walk, write-set diff check), `sandbox-mcp` with `LocalDockerProvider` (`.git`-free container workspaces, internal no-DNS network, scoped `HTTP(S)_PROXY` for `install_package`), 2-slot `HeavyExecSemaphore`, crash-safe Postgres template DBs (`pg_terminate_backend` + `REVOKE CONNECT`), and `fastapi-react-postgres` skeleton. *Done when:* sandbox creation + DB cloning takes <2s and symlink/out-of-scope edits are blocked deterministically. | Docker SDK, Postgres template DB isolation, MCP |
| **P2: Single-Agent Builder (Usable v0.1 & Baseline B1)** | **★ Module 1 Complete** | Implementer LangGraph loop with tool-output compaction, Gates G1/G2 (`ruff`, `mypy`, `pytest`, in-sandbox `openapi.json` export → host `oasdiff`, bounded `Schemathesis`, `tsc`, `vitest`), `foundry build "<spec>"` CLI, and **Single-Agent Baseline (B1)** runner on Dev Spec `S1`. *Done when:* you can run `foundry build` to generate a verified full-stack app end-to-end. | **LangGraph fundamentals**, Schemathesis, oasdiff |

#### Module 2: Postgres Blackboard, Staged Contracts & Two-Wave Hierarchy (v0.2)
*Goal: Upgrade from a single agent to contract-first multi-agent planning (`Data → Backend → Frontend/Infra`), two-wave foundation-first execution, and human decisions via CLI.*

| Phase | Milestone | Deliverable & "Done When…" | Learning focus |
|---|---|---|---|
| **P3: Blackboard, Staged Contracts, CLI Inbox & Wave 1→2** | **★ Module 2 Complete** | Postgres Blackboard schema + `LISTEN/NOTIFY` outbox with serialized monotonic `publish_seq` (zero Redis), **CLI Decision Inbox** (`foundry decisions list|resolve`, `Auto` vs `Approve`/`Human`), independent LangGraph threads for Orchestrator and Domain Leads (`Data → Backend → Frontend/Infra`), re-run-safe `draft`/`approve` nodes, Wave 1 Foundation tasks (F1–F4 including `seed_user(role=...)` in `conftest.py`) → Wave 2 features. *Done when:* Leads lock consistent DB/OpenAPI (`x-foundry-rules`)/UI contracts and Wave 1 merges cleanly before feature tasks run. | SQLAlchemy 2, Postgres outbox ordering, multi-thread LangGraph |

#### Module 3: Parallel Concurrency, Black-Box Verification & Dashboard v0.5 (v0.3)
*Goal: Run multiple feature implementers concurrently under formal conflict control, verify them with a cross-family blind Test-Writer, and supervise from a live web UI.*

| Phase | Milestone | Deliverable & "Done When…" | Learning focus |
|---|---|---|---|
| **P4: Lean Concurrency Control + Contract Protocol** | **Module 3** | Static feature-directory `write_reservations` (`pg_advisory_xact_lock`), daemon symlink + diff enforcement, serial rebase-and-test Merge Queue + `pending_deps` (curated allowlist / hard `Approve`), 2-slot heavy-exec semaphore with G5 priority, schema-barrier serialization + crash-safe `app_template` refresh, contract change classification/batching/freeze caps, and state-dependent delta tasks. *Done when:* concurrent agents build a multi-feature app with zero unresolved conflicts or livelocks. | Concurrency control, serial merge queue |
| **P5: Black-Box Verification + Dashboard v0.5** | **★ Module 3 Complete** | Cross-family G3 Black-Box Test-Writer, deterministic **post-test `Test-Validator`** (`invalid_test_request` + code errors), Dispute Protocol (using `x-foundry-rules`), risk-based G4 Reviewer, and **Dashboard v0.5** (Tasks, Agents, Decisions inbox, Model switcher, Preview, live Activity stream via `?after_seq=`). *Done when:* planted API/logic bugs are caught at G2/G3 and human decisions pause only dependent tasks in the UI. | Eval design, **React basics**, WebSockets |

#### Module 4: Local Blue/Green CI/CD & Held-Out Benchmark Release (v1.0 MIV)
*Goal: Ship generated apps locally with migration-safe auto-rollback and prove Foundry's thesis on held-out benchmark specs.*

| Phase | Milestone | Deliverable & "Done When…" | Learning focus |
|---|---|---|---|
| **P6: Local Blue/Green CI/CD & Safe Migrations** | **Module 4** | `LocalComposeTarget` (Caddy blue/green + 60s synthetic health probe + Playwright E2E smoke suite + automatic code-only rollback), `squawk` expand-only migration gate in G5, incident creation → Wave 3 fix loop. *Done when:* an app deploys locally and automatically rolls back when a bad release is injected. | Blue/green CI/CD, Playwright E2E, `squawk` safe migrations |
| **P7: Held-Out Pilot Benchmark & MIV Release** | **★ Module 4 (MIV) Complete** | `foundry-bench` runner + 4 **Held-Out Core specs (S3, S4, S5, S6)** with pinned contracts and hidden suites; run **B1 vs F0 (24 runs, strictly pinned first-party APIs, identical scaffolding)**; compute **paired bootstrap CIs**; publish README with architecture + honest benchmark results. *Done when:* you have a polished, benchmarked daily-driver tool and statistically grounded results. | Experimental evaluation, paired bootstrap CIs |

---

### 26.2 Deferred / Post-MIV Expansion Backlog (Intentionally Excluded from MIV — Plug-In Ready)
Every feature deliberately skipped in v1.4 to keep MIV lean, fast, and reliable is preserved below with its exact integration point so we can add it cleanly later whenever needed:

| Deferred Feature | Why Excluded from MIV | Exact Plug-In Point When Added Later | Target Phase |
|---|---|---|---|
| **Stage A Cloud Deploy (`Vercel + Render + Neon`)** | Free-tier limits and platform APIs churn frequently; adds cloud-account setup friction before proving the core multi-agent engine. | Implement `VercelRenderNeonTarget` behind `DeployTarget` protocol in `packages/foundry/pipeline/` (zero core changes). | **P8** |
| **Dashboard v1.0 Polish (React Flow, Monaco Diff, Blackboard Explorer, Replay Scrubber)** | Dashboard v0.5 + CLI Decision Inbox already cover full supervision and decisions for MIV. | Extend `dashboard/` panels consuming existing `/api/runs/*` and `?after_seq=` WebSocket stream. | **P8** |
| **Core Ablations (`B2`, `A1`, `A2`, `A3`) & Extended Hard Specs (`S7`, `S8`)** | Run after Pilot Benchmark (`B1` vs `F0` on S3–S6) confirms the core signal. | Additional configs in `bench/runner/` using the same held-out harness. | **P9** |
| **Mergiraf AST-Aware Structural Merge** | Static feature-directory write sets + serial rebase queue already avoid most conflicts; add AST merge only once empirical conflict telemetry proves it saves reruns. | Plug into Step 3 of `concurrency/merge_queue.py` between git rebase and Integration Agent. | **P9 (if needed)** |
| **Mid-Task Dynamic Write-Set Expansion (`request_write_set_expansion`) & 5-Min Pause Preemption** | Static feature-directory write sets + Lead escalation keep Module 3 concurrency simple; add dynamic expansion/preemption only if real multi-feature runs experience lock stalls. | Add atomic expansion check + WIP-commit pause handler to `concurrency/reservations.py` and `blackboard-mcp`. | **P9 (if needed)** |
| **`Notify` Decision Mode + 60s `pending_notify_grace` Buffer & Post-Grace `Undo`** | Binary `Auto` vs `Approve`/`Human` avoids fragile multi-step rollback state machines in v1. | Extend `decisions/service.py` with grace timer and forward compensating action handlers (`A6-9`). | **P9** |
| **Redis Streams Event Broker** | Single-process local daemon only needs Postgres `events` + `LISTEN/NOTIFY` + `publish_seq`; Redis only pays off if Foundry scales to multi-worker distributed daemons (e.g., hosted SaaS). | Swap the in-process dispatcher behind the event bus interface in `blackboard/events.py`. | **P10 / SaaS** |
| **Opt-In Single-Shot CLI Adapter (Non-Tool Calls Only)** | Multi-step ReAct CLI loops violate/risk vendor ToS and break prefix caching; an opt-in single-shot adapter (e.g., Codex CLI with explicit user ToS warning) can still be offered later for non-tool planning/review. | Plug a new `ModelTransport` implementation into `packages/foundry/gateway/transports/` (zero router changes). | **Post-MIV (optional)** |
| **Stage B Terraform (`infra-mcp`, `AwsTarget`, `AzureTarget`)** | Heavy DevOps scope; MIV proves safe migrations & rollback via `LocalComposeTarget` first. | Add `mcp_servers/infra.py` + `AwsTarget`/`AzureTarget` behind `DeployTarget`. | **P10** |
| **Brownfield Repo Adoption & Next.js Stack Pack (`nextjs-postgres`)** | Greenfield single-stack isolates the core architecture cleanly; brownfield adoption is the #1 expansion for broader daily developer adoption after MIV. | New `StackPack` plugin + codebase ingestion workflow in `agents/graphs/`. | **Post-MIV** |

---

## 27. Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Supply-chain compromise in gateway dependencies (`A7-1`)** | **Zero LiteLLM**; only 3 thin first-party SDKs (`openai`, `google-genai`, `anthropic`), hash-verified `uv.lock` (`--frozen`/`--require-hashes`), 3–7 day `exclude-newer` PyPI cooldown, and `sha256`-pinned Docker images |
| **Account / ToS suspension (`A7-2`)** | **Zero OAuth token scraping and zero automated CLI subscription loops**: official developer API keys + local Ollama/vLLM only |
| **Host-side code execution & symlink exfiltration (`A7-3`)** | Export `openapi.json` strictly inside Docker; deterministic symlink/FIFO/mode-`120000`/YAML-bomb pre-walk before any host tool reads a worktree; `.git`-free container workspaces; internal no-DNS network; `REVOKE CONNECT` on blackboard DB |
| **API budget exhaustion & runaway loops (`A7-11`)** | Normalized \$0 record/replay for all engine dev, native prefix caching, reconciled partitioned sub-budgets, hard 90-min wall-clock + 600-call kill-switch, Flash-Lite default with retry upgrade |
| **Contract revision livelocks** | Change classification (pinned/additive/breaking), 3-min batching, max 2 reopens per task → freeze + joint co-design |
| **Test-Writer hallucinating imports/selectors or bad requests (`A7-7`)** | Restricted to black-box API (`seed_user(role=...)` + `conftest.py`) and static-fixture component tests; **post-test `Test-Validator`** routes code errors and `invalid_test_request` back to Test-Writer; Dispute Protocol uses `x-foundry-rules` |
| **16 GB RAM OOMs (`A7-6`)** | Native Docker Engine inside WSL2 (`memory=10GB`, `autoMemoryReclaim=gradual`), **2-slot heavy-execution semaphore** (× 1.5 GB), zero LiteLLM (~200 MB saved), zero Redis, shared Postgres with crash-safe template DB cloning, `fastembed` ONNX (<200 MB) |
| **Destructive migration breaks rollback** | Expand/contract policy enforced by `squawk` at G5; pre-migration backup; code-only auto-rollback |
| **Project feels too large before anything works (`A7-12`)** | **Lean 4-Module MIV** with explicit Post-MIV Backlog: Module 1 (P0–P2) delivers a usable Single-Agent app builder (`B1`) before adding multi-agent hierarchy in Module 2 |

---

## 28. Resume bullets and interview talking points

### Resume bullets (fill in your measured numbers from P7/P9)
- *Built **Foundry**, an open-source, local-first multi-agent software engineering platform (LangGraph, FastAPI, Postgres, Docker) that designs, builds in parallel, verifies, and deploys full-stack web apps from natural-language specifications.*
- *Engineered a **formal concurrency control & isolation layer** (convention-based write-set reservations with Postgres advisory locks, `.git`-free sandbox workspaces, daemon-enforced symlink + git diff guards, single-writer foundation waves, and a serial merge queue with a 2-slot heavy-execution semaphore), preventing cross-agent collisions on a 16 GB laptop.*
- *Designed a **contract-first coordination & verification pipeline** combining staged contract locking, blocking OpenAPI conformance (`oasdiff` + bounded `Schemathesis`), and cross-model-family black-box test generation with a deterministic post-test Validator—improving hidden acceptance test pass rate from Y% (equal-API-budget single agent) to X% (±95% paired bootstrap CI) across 4 held-out specs.*
- *Implemented **migration-safe autonomous CI/CD** targeting Docker Compose Blue/Green (`squawk` expand-only migration linting, Playwright E2E smoke tests, and automatic code-only rollback), with pluggable cloud adapters for Stage A/B deployments.*
- *Built a **supply-chain-hardened, zero-LiteLLM Model Gateway** over 3 first-party official SDKs (`openai`, `google-genai`, `anthropic`) with hash-verified lockfiles, native prefix-cache telemetry, partitioned task budgets, secret scanning, and a prompt-normalized \$0 record/replay engine.*

### Interview talking points
1. **Why naive multi-agent coding fails** (context drift, compounding errors, concurrent file collisions, contract ping-pong) and the specific mechanisms that fix each one.
2. **Database concurrency control applied to code:** write-set reservations, prefix locking, single-writer shared files, Postgres `publish_seq` outbox ordering, and serial merge-queue verification.
3. **Supply-chain & sandbox security in AI coding tools:** why we removed LiteLLM in favor of 3 thin first-party SDKs, how we prevented host-side code execution (`openapi.json` inside Docker) and symlink exfiltration (mode `120000` rejection), and why `.git`-free container workspaces beat `/dev/null` bind-mounts.
4. **Why LangGraph threads are decoupled** (`{run}:lead:{domain}`, `{run}:task:{id}`) instead of using `Send()` inside one thread when human interrupts are involved, plus why nodes are split into `draft` and idempotent `approve` steps.
5. **Black-box verification & post-test `Test-Validator`:** why a blind test-writer can't write white-box unit tests, why `invalid_test_request` must be evaluated post-test rather than pre-assertion, and how `x-foundry-rules` settles disputes cleanly.
6. **Honest benchmarking:** separating dev specs from held-out specs, giving the single-agent baseline identical scaffolding and pinned API budgets, and reporting paired bootstrap CIs.

---

## 29. Distribution, system requirements, and future work

### 29.1 How other people use Foundry (and where it deploys)
Because Foundry is an **open-source, local-first development tool** (like Docker Desktop, VS Code, or Supabase CLI), **you do not host a central server that runs other people's containers or pays for their LLM calls.**
Instead, there are three ways people experience or use it:

| Audience | How they use it | What you host / pay for |
|---|---|---|
| **1. Recruiters & hiring managers (30-second check)** | They click your **live Replay Demo URL** (a static build of the Dashboard deployed for **\$0 on GitHub Pages / static host**) pre-loaded with recorded runs from your benchmark, plus a 3-minute demo video and screenshots/demos of the apps Foundry built. They can scrub through the timeline, inspect agent diffs, view contracts, and inspect Decision cards without installing anything. | **\$0 / 0 RAM** (static site) |
| **2. Developers running Foundry (`uv` / Docker)** | They run `uv tool install foundry-dev && foundry init && foundry up` on their own machine (macOS, Linux, or Windows WSL2 with native Docker Engine), plug in **their own official API keys or local Ollama/vLLM**, and build + deploy apps locally (or to their own cloud accounts in Post-MIV). | **\$0 / 0 RAM** (runs on the user's machine) |
| **3. Users with weak laptops (8 GB RAM) or cloud preference** | Because `SandboxProvider` (Section 5.5) is pluggable, a user can either: (a) run `foundry up` on a cheap cloud VM (e.g., a \$6–12/mo Hetzner/DigitalOcean VM or GitHub Codespace) and open the dashboard in their browser, or (b) keep the lightweight daemon (<400 MB) on their laptop and set `sandbox_provider: remote_docker` / `e2b` so only the heavy test containers run remotely. | **\$0** to you (user brings their own VM/E2B key) |

### 29.2 System requirements for end users
| Mode | Minimum specs | Recommended specs |
|---|---|---|
| **Full Local Mode** (daemon + Postgres + 2 heavy-exec Docker slots on WSL2 native Docker Engine) | 16 GB RAM, 4-core CPU, 15 GB free disk (SSD), Docker Engine | 16–32 GB RAM, 6+ cores |
| **Lite Local Mode** (`max_heavy_exec_slots: 1`, no local preview container) | **8–12 GB RAM**, 4-core CPU | 16 GB RAM |
| **Remote-Sandbox Mode** (daemon local, sandboxes on a remote Docker host / Codespace) | **8 GB RAM** (any laptop; local footprint is <1 GB) | Any modern laptop |

### 29.3 Future work (if you turn it into a paid product later)
If you ever decide to monetize Foundry as a **hosted SaaS**:
- **Control Plane (cheap):** A hosted FastAPI + Postgres (+ Redis Streams if horizontally scaled) service runs the Dashboard, Blackboard, and LangGraph state machines.
- **Sandbox Runners (where the heavy resources go):** Per-task sandboxes run on ephemeral microVMs (**Fly Machines, AWS Fargate Spot, Modal, or E2B**) that spin up for 30–90 seconds while running tests and shut down to \$0 immediately after.
- **Other future extensions:** Tauri desktop wrapper, adopting existing brownfield repositories, Next.js stack pack (`nextjs-postgres`), and team multi-user approvals.

---

## 30. Glossary
| Term | Meaning |
|---|---|
| **Blackboard** | Shared state across agents in Postgres 16 + pgvector: hard memory (exact tables), fuzzy memory (`pgvector`), and outbox events (`publish_seq`) |
| **Hard / Fuzzy Memory Split** | Keeping exact facts (contracts, schemas, tasks) strictly in Postgres rows while using vector search only for code signatures, lessons, and docs |
| **Contract** | Machine-checked interface between domains (`db_schema`, `openapi` with `x-foundry-rules`, `component`, `deps`) |
| **Pinned Pointer** | A JSON pointer inside a contract that is locked by the user or benchmark and cannot be modified by agents |
| **Wave 1 / Wave 2** | Execution stages: Wave 1 builds shared foundations (deps, models+initial migration, auth+`conftest.py`, UI shell); Wave 2 builds features in parallel |
| **Write Set** | Normalized file or directory paths a task is allowed to modify |
| **Write Reservation** | Time-limited, advisory-lock-guarded claim on a task's write-set paths |
| **Diff & Symlink Enforcement** | Deterministic pre-walk + `git status`/`git diff` rejecting any symlink (`mode 120000`), FIFO, or edit outside the task's reserved write set |
| **Heavy-Execution Semaphore** | 2-slot semaphore capping concurrent heavy container commands (`pytest`, `mypy`, `tsc`, `vitest`, `Schemathesis`, G5) while letting multiple lightweight agent threads think on LLM I/O |
| **Post-Test `Test-Validator`** | Deterministic classifier at Gate G3 evaluating test-code errors and recorded OpenAPI `invalid_test_request` traces *after* test execution so broken tests route back to the Test-Writer |
| **Expand / Contract Migration** | Two-phase schema change: normal releases only add backward-compatible schema elements (`expand`, enforced by `squawk`), keeping code-only rollbacks safe |
| **ModelTransport** | Pluggable gateway interface backed by 3 first-party official SDKs (`openai_compat`, `google_genai`, `anthropic`) — zero LiteLLM |
| **MIV** | Minimum Impressive Version (Modules 1–4 / Phases P0–P7): the smallest complete, benchmarked version of Foundry |
| **Cassette (Record/Replay)** | Stored, prompt-normalized LLM responses used to develop and test the orchestration engine at \$0 |

---

## Appendix A: Architecture Review Resolutions (v1.0 → v1.4)

Every issue raised across all review passes (v1.1 through v1.4) is accounted for below:

| Review Issue | Verdict | Concrete Fix in v1.1 – v1.4 | Sections Updated |
|---|---|---|---|
| **A1-1 / A4-12:** Infinite `contract.changed` ping-pong & cascade | **Accepted** | Change classifier (`pinned_violation` / `additive` / `breaking`), 3-min batching window, scope-filtered reopening, hard cap of **2 reopens per task** and **3 breaking versions per wave**, followed by `FROZEN` state + single joint Lead co-design call. | 8.2, 9.4, 9.5 |
| **A1-2:** Dishonest contracts & mock drift at Join Barrier | **Accepted** | Spectral contract linting + mandatory blocking **oasdiff** conformance and bounded **Schemathesis** validation in Gate G2 before `VERIFIED`. | 9.1, 10 (L5), 11.1, 12.2 |
| **A1-3 / A2-2 / A3-6 / A4-9:** Blind Test-Writer writes unrunnable tests & test disputes | **Accepted** | Restricted G3 Test-Writer to black-box API (`AsyncClient`) and contract `data-testid` component tests; granted read-only `conftest.py` access; added deterministic **Test-Validator** and **Test Dispute Protocol**. | 6, 9.7, 11.1, 11.2, 15 |
| **A1-4 / A2-3 / A3-9 / A4-14:** 16 GB RAM OOM crashes in sandboxes | **Accepted (Modified)** | Kept lightweight Docker containers for security, removed per-sandbox Postgres in favor of logical DBs (`CREATE DATABASE sbx_<task> TEMPLATE app_template`) on shared Postgres, pre-baked sandbox image, moved Playwright to on-demand Release env, `.wslconfig memory=10GB`. | 3, 5.2–5.4, 22 |
| **A1-5:** Cut `k3d`/Prometheus bloat in local pipeline | **Accepted** | Replaced with `LocalComposeTarget` (Docker Compose Blue/Green + Caddy + 60s probe & Playwright smoke test + auto-rollback). | 5.2, 13.1, 13.4 |
| **A1-6:** Context bloat & RAG hallucinations on contracts | **Accepted** | Enforced strict Hard/Fuzzy Memory Split: exact JSON rows from Postgres for contracts/tasks/decisions, zero chat logs in implementer prompts, `pgvector` only for AST code search, lessons, and local docs. | 7.5, 8.1 |
| **A1-7 / A2-10 / A4-15:** Sandbox escape, `.git/hooks` host execution, egress filtering, prompt injection | **Accepted** | Ran git daemon-side only with `-c core.hooksPath=/dev/null`, never mounted Docker socket, added `tinyproxy` egress proxy for package installs, jailed file paths, used local curated docs index (no live web fetch), removed all secrets from sandboxes. *(Upgraded in `A7-3`/`A7-4`.)* | 4, 5.4, 15, 19.1 |
| **A1-8 / A4-1 / A4-2 / A4-18:** Benchmark validity (hidden tests vs agent-designed APIs), token math, & parallelism measurement | **Accepted** | Added **pinned OpenAPI & `data-testid` stubs** (`pinned_pointers`) per benchmark spec; designed equal-budget Single-Agent (`B1`) and Sequential (`B2`) baselines + 3 focused ablations (`A1–A3`). *(Hardened in `A7-13`.)* | 3, 8.2, 9.4, 18.1–18.4, 23.1–23.6 |
| **A2-1 / A3-1 / A4-5:** SQL lease `ON CONFLICT (path)` ignores hierarchy + lease redundancy | **Accepted** | Consolidated leases into `write_reservations`, restricted paths to normalized files or trailing-`/` directories, serialized acquisition with `pg_advisory_xact_lock` + prefix overlap SQL check. | 8.2, 10 (L2) |
| **A2-4 / A3-7:** LangGraph `Send` + `interrupt()` blocks sibling branches | **Accepted** | Removed cross-domain `Send`; Run Supervisor spawns independent LangGraph `thread_id`s per Lead, per Task attempt, and per Verification round. | 7.2, 7.3, 7.4 |
| **A2-5 / A3-2:** `run_command` bypasses `write_file` lease checks | **Accepted** | Added mandatory deterministic write-set diff enforcement (`git status --porcelain` / `git diff --name-only`) in daemon-side `git-mcp` (on commit) and in Gates G2 and G5; scoped formatters to write-set paths. | 10 (L4), 11.1, 15 |
| **A2-6 / A2-9 / A3-3 / A3-4 / A4-7 / A4-8:** Lead ordering, SQLAlchemy circular deps, Data Lead bottleneck, & disconnected frontend shell | **Accepted** | Added staged contract planning (`Data → Backend → Frontend∥Infra`) and **Wave 1 (Foundation)** where Data impl. generates centralized models (`backend/app/models/`) + `0001_initial`, Backend impl. builds Auth + `conftest.py`, and Frontend impl. builds `AppLayout` (`feature.ts` nav registries) before Wave 2 parallel features. | 6, 9.1, 9.2, 10 (L3), 12.1 |
| **A2-7 / A3-8:** Reopening already-`MERGED` tasks on contract change | **Accepted** | State-dependent handling: active tasks updated/reopened; `MERGED`/`DEPLOYED` tasks are never reopened—supervisor creates a fresh `delta` task branched off `main`. | 9.4 |
| **A2-8:** Live early rebasing (`main.updated`) corrupts active loops | **Accepted** | Buffered `main.updated`; rebasing happens in Merge Queue (with clean restart fallback for schema barriers in `A7-9`). | 7.5, 10 (L4) |
| **A2-11:** Task budget starvation across G3/G4 | **Accepted** | Partitioned budgets into reserved `implement_usd`, `review_usd`, and per-feature `blackbox_tests_usd`. *(Reconciled in `A7-11`.)* | 8.2, 18.4, 22 |
| **A2-12 / A3-13:** Record/replay cache misses on dynamic strings | **Accepted** | Added Prompt Normalizer (stripping ULIDs, timestamps, git SHAs, worktree paths, test timings) + sequential cassette mode keyed by `(role, task_slug, node, step_index)`. | 16.6 |
| **A3-5:** Unblock dependency request queue | **Accepted** | Planned packages installed in Wave 1 F1; approved unplanned packages installed ephemerally and recorded in `tasks.pending_deps` for deterministic Merge Queue application. *(Hardened in `A7-10`.)* | 8.2, 10 (L3, L4), 15 |
| **A3-10:** PyTorch CPU RAM bloat for local embeddings | **Accepted** | Switched local embeddings to `fastembed` (ONNX Runtime, CPU, <200 MB RAM). | 3, 5.3, 22, 24 |
| **A3-11:** `keyring` fails on headless WSL2 | **Accepted** | Used `keyring` when a working native/WSL-interop backend exists, falling back automatically to an scrypt + Fernet encrypted local vault (`vault/credentials.enc`). | 3, 5.6, 5.7, 22, 24 |
| **A3-12 / A4-3 / A4-4:** CI/CD rollbacks vs DB migrations, cross-origin cookies, cold starts | **Accepted** | Enforced expand/contract migrations via `squawk` at G5 so code-only rollback is safe; `LocalComposeTarget` in MIV; Neon branching + same-origin `vercel.json` + 120s warm-up preserved for Post-MIV Stage A. | 8.2, 12.1, 13.2–13.4, 26.2 |
| **A4-6:** Re-running LangGraph nodes after `interrupt()` duplicates LLM calls & decision cards | **Accepted** | Split nodes into `draft_*` (LLM call, checkpointed) and `approve_*` (`interrupt()`), and made `decisions.get_or_create(idempotency_key)` idempotent. | 7.4, 8.2, 17.3 |
| **A4-10:** Declared write sets accuracy | **Accepted** | Write sets assigned by Leads from stack-pack directory conventions; static write sets + Lead escalation in MIV, dynamic expansion deferred to Post-MIV (`A7-12`). | 10 (L1), 26.2 |
| **A4-11:** Missing schema tables (`incidents`, `threads`, `version` columns) | **Accepted** | Added `incidents`, `releases`, `threads`, `write_reservations`, `contract_change_requests`, `processed_events`, and `version` columns on `tasks`, `contracts`, and `decisions`. | 8.2, 8.3 |
| **A4-13:** Run daemon inside WSL2, not Windows native | **Accepted** | Daemon runs natively inside WSL2 (with native Docker Engine) with repos/worktrees on WSL ext4 filesystem. | 4, 5.1, 5.2, 5.6 |
| **A4-16:** Privacy vs "local-first" (free API tiers & cloud traces) | **Accepted** | Added Section 19.2 & `runs.data_policy`: defaults to `allow_free_tier: false` and `allow_trace_export: false`; local tracing by default; mandatory secret scanning before prompts and trace exports. | 8.2, 16.5, 19.2, 20, 22 |
| **A4-17:** Scope & early baseline | **Accepted** | Defined **Minimum Impressive Version (MIV)** in Section 2 (Phases P0–P7) and moved Single-Agent Baseline (`B1`) to **P2**. | 2, 26 |
| **A4-19:** Positioning claims vs MetaGPT, ChatDev, Replit Agent, Lovable | **Accepted** | Rewrote Section 1 positioning to credit existing tools and focus claims on formal conflict control, machine-checked contracts, cross-family black-box verification, and equal-cost ablations. | 1 |
| **A5-1 / A5-3 (v1.2):** Universal provider support & 4-module progressive architecture | **Accepted (Superseded in part by `A7-1`)** | Organized repo around plug-and-play `Protocol` boundaries and 4 progressive modules (M1–M4). Universal API coverage retained via 3 first-party SDKs instead of LiteLLM (`A7-1`). | 2, 3, 16, 25, 26 |
| **A6-1 – A6-9 (v1.3):** Initial runtime hardening pass | **Superseded / Refined by `A7-1` – `A7-12`** | Retained task-scoped heartbeats, sequential sandbox execution, schema chaining, `install_package` script disabling, and `openapi.yaml` request validation; refined/replaced `cli_bridge` loops (`A7-2`), pre-assertion validator (`A7-7`), stateful MSW (`A7-8`), schema rebase recovery (`A7-9`), `Notify+Undo` (`A7-11`), and host scanner isolation (`A7-3`). | See `A7-1`–`A7-13` |
| **A7-1 (v1.4):** Remove LiteLLM after 2026 PyPI/Docker supply-chain compromises (`v1.82.7`/`v1.82.8`, `v1.107.0`) + add daemon supply-chain guardrails | **Accepted** | Removed `litellm` completely (~200 MB RAM saved, zero proxy blast radius); replaced with 3 thin first-party official SDK transports (`openai_compat` via `openai`, `google_genai` via `google-genai`, `anthropic` via `anthropic`); enforced hash-verified `uv.lock` (`--frozen`/`--require-hashes`), 3–7 day PyPI `exclude-newer` cooldown, and `sha256`-pinned Docker images. | 1, 2, 3, 4, 5, 8.2, 16, 18, 19.1, 24, 25, 26, 27, 28 |
| **A7-2 (v1.4):** `"100% ToS-compliant CLI bridge"` claim & ReAct `cli_bridge` latency/prompt fights | **Accepted** | Removed `"100% ToS-compliant"` claim and dropped `cli_bridge` from multi-step ReAct loops and benchmarks (Anthropic & Google actively restrict consumer OAuth in third-party harnesses; cold CLI boots destroy prefix caching and `--sandbox read-only` allows host reads). Defaulted strictly to official first-party API keys + local Ollama/vLLM; kept `ModelTransport` pluggable for any future opt-in single-shot adapter. | 1, 2, 3, 4, 5, 16.3, 16.7, 18, 26.2, 27 |
| **A7-3 (v1.4):** Host-side `openapi.json` code execution, symlink (`mode 120000`) exfiltration, DNS tunneling & DB isolation | **Accepted** | Export `openapi.json` strictly inside Docker as a static artifact before host-side `oasdiff`; run deterministic recursive symlink/FIFO/mode-`120000`/YAML-bomb pre-walk before *any* host daemon tool (`git-mcp`, `Semgrep`, `gitleaks`, `oasdiff`) reads a workspace/worktree; disable external DNS on internal sandbox network; isolate blackboard DB (`REVOKE CONNECT … FROM PUBLIC`). | 4, 5.4, 7.5, 10 (L4–L5), 11.1, 15, 19.1, 27 |
| **A7-4 (v1.4):** `/dev/null` bind-mount over `.git` breaks `hatchling`/`pytest`/`ruff`/`vite` | **Accepted** | Replaced `/dev/null` masking with a **`.git`-free container workspace** (`workspaces/<task_id>`) synced to/from daemon-owned `worktrees/<task_id>` after symlink validation so `/workspace/.git` does not exist inside Docker. | 5.4, 5.6, 10 (L2), 15, 19.1, 25 |
| **A7-5 (v1.4):** Redis over-engineering for local daemon & outbox `bigserial` out-of-order commit gaps | **Accepted** | Dropped Redis in v1; kept Postgres-only `events` outbox + `LISTEN/NOTIFY` + advisory-lock-serialized monotonic `publish_seq` + in-process `asyncio` dispatcher + `processed_events` + WebSocket `?after_seq=` cursoring. Deferred Redis Streams to Post-MIV Backlog. | 3, 4, 5.2, 8.2, 8.3, 21.2, 24, 25, 26.2 |
| **A7-6 (v1.4):** Holding container RAM slots idle across LLM I/O & Docker Desktop vs native WSL2 Engine | **Accepted** | Separated lightweight thinking containers (`max_concurrent_tasks: 4`) from a **2-slot `HeavyExecSemaphore`** (`pytest`/`mypy`/`tsc`/`vitest`/`Schemathesis`/G5, with G5 priority); documented native Docker Engine inside WSL2 + `autoMemoryReclaim=gradual` in `.wslconfig`. | 3, 4, 5.2, 5.3, 7.5, 10 (L4), 11.1, 22, 27 |
| **A7-7 (v1.4):** Pre-assertion `Test-Validator` race, weak Dispute Step 1, G3 role seeding, & unbounded Schemathesis | **Accepted** | Recorded outbound OpenAPI validation per call and applied `invalid_test_request` classification **post-test** (only on failed tests receiving `400`/`422` without `expect_invalid=True`); added contract-defined `seed_user(role=...)` to `conftest.py`; added per-operation `x-foundry-rules` so behavioral disputes go to Leads; bounded Schemathesis in G2 (`--hypothesis-seed=42 --max-examples=10`, status/schema conformance only). | 9.2, 9.7, 10 (L5), 11.1, 11.2, 12 |
| **A7-8 (v1.4):** Auto-generated stateful MSW mocks from OpenAPI are impossible without drift | **Accepted** | Dropped auto-generated stateful MSW claims; frontend G1/G2 tests use static OpenAPI-validated fixtures (validating both requests and mock responses); multi-step stateful UI behavior runs against the live FastAPI backend in `kind='integration'` and G6 Playwright tests. | 9.2, 9.3, 10 (L5), 11.1, 12 |
| **A7-9 (v1.4):** Schema boundary rebase conflict recovery & `app_template` crash-safe refresh | **Accepted** | Treated Wave 2 `schema` tasks as serialized barriers with clean conflict recovery (discard WIP branch, increment `attempt`, restart cleanly from new `main` SHA); wrapped `app_template` refresh with `pg_terminate_backend` + daemon startup cleanup under a Postgres advisory lock. | 5.4, 7.5, 9.2, 10 (L3–L4), 11.1 |
| **A7-10 (v1.4):** `Balanced` preset (`Tactical=Notify`) bypasses `install_package` approval & container-wide proxy exposure | **Accepted** | Gated unplanned `install_package` strictly by curated allowlist OR hard human `Approve` (never `Notify`/`Auto`), and injected `HTTP(S)_PROXY` strictly into that single `docker exec` call. | 5.4, 10 (L3), 15, 17.3, 19.1 |
| **A7-11 (v1.4):** Reconciled budget caps, per-run kill-switches, simplified `Auto`/`Approve` decisions & CLI Decision Inbox | **Accepted** | Reconciled caps (`implement_usd: 0.60`, `review_usd: 0.15`, `blackbox_tests_usd: 0.30`); added 90-min wall-clock + 600-call run kill-switch; simplified v1 decisions to `Auto` vs `Approve`/`Human` (deferring `Notify+Undo` to Post-MIV) and added a **CLI Decision Inbox** in Module 2. | 9.8, 17.2–17.4, 18.4, 21.1, 22, 26.2 |
| **A7-12 (v1.4):** Lean MIV scope & explicit Deferred Post-MIV Backlog | **Accepted** | Kept MIV laser-focused on core thesis (Modules 1–4: static feature write sets + serial merge queue + `LocalComposeTarget` + held-out benchmark); moved Stage A Vercel/Render/Neon, Mergiraf, mid-task write-set expansion, 5-min pause preemption, Redis Streams, `Notify+Undo`, and Stage B Terraform into the explicit plug-in backlog (**Section 26.2**). | 2, 10, 26.1, 26.2 |
| **A7-13 (v1.4):** Benchmark rigor (dev vs held-out split, pinned API telemetry, equal `B1` scaffolding, paired bootstrap CIs) | **Accepted** | Split Dev specs (`S1–S2`) from Held-Out Core specs (`S3–S6`); enforced `benchmark_pin_models: true` on first-party APIs (exact token telemetry, zero fallback swaps); gave `B1` identical Wave 0 skeleton, pinned stubs, `oasdiff`/`Schemathesis`, and compaction; computed 95% **paired bootstrap CIs by spec**. | 3, 16.5, 23.3–23.6, 26.1, 28 |
