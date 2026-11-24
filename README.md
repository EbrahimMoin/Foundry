# Foundry

**An open-source, local-first, contract-first multi-agent software engineering environment.**

You describe the application you want, and Foundry coordinates a hierarchy of AI agents across **staged contracts**, **serialized foundation waves**, **hardened `.git`-free Docker sandboxes**, and **machine-checked verification gates** while keeping humans in control through a CLI Decision Inbox (and upcoming Dashboard).

See [foundry_design_document.md](foundry_design_document.md) (`v1.4.1`) for the full architecture specification and [AGENTS.md](AGENTS.md) for engineering invariants.

---

## Key Architectural Principles

- **Zero-LiteLLM First-Party SDK Gateway (`foundry.gateway`):** Only three thin official SDK transports (`openai_compat` via `openai`, `google_genai` via `google-genai`, and `anthropic` via `anthropic`) plus local Ollama/vLLM. Enforces pre-prompt secret scanning, encrypted local credential storage (`keyring` with scrypt + Fernet vault fallback), partitioned USD/call/wall-clock kill-switches, and `$0` prompt-normalized record/replay cassettes.
- **Hard / Fuzzy Memory Split & Postgres-Only Outbox (`foundry.blackboard`):** Exact facts (contracts, tasks, decisions, write reservations) live in PostgreSQL 16 rows rather than vector RAG. `pgvector` indexes code chunks, lessons, and local docs, while a transactional `LISTEN/NOTIFY` outbox assigns strictly monotonic `publish_seq` values via Postgres advisory locks (no Redis in v1).
- **Hardened `.git`-Free Sandboxes (`foundry.sandbox`, `foundry.security`):** Agents never execute `git` inside containers and `/workspace/.git` never exists in Docker. Before any host tool reads a workspace or worktree, a deterministic pre-walk rejects symlinks (`mode 120000`), FIFOs, device nodes, and YAML bombs. Each task uses an isolated logical Postgres DB (`sbx_<task>` cloned from `app_template` with `REVOKE CONNECT` protecting `foundry_blackboard`), a read-only rootfs, internal no-DNS networking, a scoped `tinyproxy` egress allowlist for approved `install_package` calls, and a 2-slot priority `HeavyExecSemaphore` built for 16 GB laptops.
- **Contract-First Coordination & Binary Decision Inbox (`foundry.contracts`, `foundry.decisions`, `foundry.supervisor`):** Staged contract locking (`Data -> Backend -> Frontend || Infra`) validates RFC 6901 `pinned_pointers`, `x-foundry-rules`, and UI `data-testid`s. Wave 1 Foundation (`F1 -> F2 -> F3 || F4`) merges cleanly before Wave 2 Feature tasks run across independent LangGraph threads (`AsyncPostgresSaver`), supervised via binary governance presets (`Auto` / `Approve` / `Human`) and the `foundry decisions` CLI inbox.

---

## Roadmap & Build Status

Foundry is being built cleanly across four lean MIV modules:

| Module & Phases | Status | Scope Delivered / Planned |
|---|---|---|
| **Module 1 (P0–P2): Kernel, Gateway, Hardened Sandbox & Builder v0.1 (`B1`)** | **Complete** | Zero-LiteLLM SDK transports, secret scanner & encrypted vault, `$0` cassette store, digest-pinned Docker sandbox + `tinyproxy` + logical DBs, symlink/git guard, `fastapi-react-postgres` stack pack, G1/G2 gates (`ruff`, strict `mypy`, `pytest`, in-sandbox `openapi.json`, bounded `Schemathesis`), Single-Agent Builder v0.1, Dev Spec S1 (`s1_bookmarks`) + hidden acceptance suite |
| **Module 2 (P3): Postgres Blackboard, Decision Inbox, Staged Contracts & Wave 1 → 2** | **Complete** | Alembic-managed Blackboard schema + pgvector indexes, monotonic `publish_seq` outbox + `EventDispatcher`, `DecisionService` presets & CLI Decision Inbox, staged Lead contracts (`Data -> Backend -> Frontend || Infra`), `blackboard-mcp`, independent LangGraph threads + `AsyncPostgresSaver`, and Wave 1 → Wave 2 orchestration |
| **Module 3 (P4–P5): Parallel Concurrency, Cross-Family G3 & Dashboard `v0.5`** | **Next** | Advisory-locked prefix-aware `write_reservations`, serial rebase-and-test Merge Queue, contract-change classification/batching/freeze caps, cross-family black-box G3 (`Test-Validator` + Dispute Protocol), G4 risk review, and Dashboard `v0.5` (`?after_seq=` WebSocket stream) |
| **Module 4 (P6–P7): Local Blue/Green CI/CD & Held-Out Benchmark** | **Planned** | `LocalComposeTarget` Caddy blue/green deploy, `squawk` expand-only migration gate, Playwright E2E smoke suite + auto-rollback, and held-out benchmark (`S3–S6`, `B1` vs `F0` paired bootstrap CIs) |

Deferred Post-MIV items (`VercelRenderNeonTarget` Stage A, Dashboard `v1.0`, core ablations, Mergiraf, dynamic write-set expansion, `Notify+Undo`, Redis Streams, Terraform Stage B, brownfield adoption) live behind existing `Protocol` boundaries in Section 26.2 of [foundry_design_document.md](foundry_design_document.md).

---

## Repository Structure

```text
Foundry/
├── AGENTS.md                        # Mandatory Plan-First rules & architectural guardrails
├── foundry_design_document.md       # Authoritative design spec (v1.4.1)
├── pyproject.toml & uv.lock         # Hash-verified dependencies, ruff/mypy/pytest config
├── infra/                           # Digest-pinned docker-compose.yml, sandbox.Dockerfile, tinyproxy
├── packages/foundry/
│   ├── cli/                         # `foundry` Typer CLI (`init`, `up`, `db`, `decisions`, `run`, `build`, `bench`)
│   ├── config/                      # Runtime/project schemas and YAML loaders
│   ├── security/                    # Secret scanner, encrypted vault, hardened git & symlink/FIFO guard
│   ├── gateway/                     # First-party SDK transports, router, budgets, normalizer, cassettes
│   ├── sandbox/                     # LocalDockerProvider, HeavyExecSemaphore, LogicalDBManager, workspace sync
│   ├── stack_packs/                 # StackPack protocol & `fastapi-react-postgres` Wave 0 generator
│   ├── verification/                # G1/G2 gate runner, OpenAPI breaking diff & pinned-pointer checks
│   ├── blackboard/                  # SQLAlchemy 2 models, Alembic migrations, repositories, LISTEN/NOTIFY relay
│   ├── decisions/                   # Governance preset matrix, idempotent decisions, ADR writer
│   ├── contracts/                   # Staged contract service, validator & pinned-pointer enforcement
│   ├── mcp_servers/                 # `sandbox-mcp`, `git-mcp`, and `blackboard-mcp` (official `mcp` SDK)
│   ├── agents/                      # Single-agent builder (`B1`), module prompts, Project & Lead LangGraph graphs
│   └── supervisor/                  # RunSupervisor, AsyncPostgresSaver helpers & Wave 1 -> Wave 2 scheduler
├── bench/                           # `s1_bookmarks` spec, pinned stubs, hidden API tests & B1 runner
├── scripts/                         # Digest pinning & maintenance helpers
└── tests/                           # Offline `$0` replay unit suite, cassettes & live Postgres/Docker tests
```

---

## Quick Start

### Prerequisites
- **Python 3.12+** managed with [`uv`](https://docs.astral.sh/uv/)
- **Docker Engine** (native Docker inside WSL2 Ubuntu on Windows, or native Linux/macOS)

### Installation

```bash
uv sync --frozen
uv run --no-sync foundry --help
```

### Core CLI Commands

```bash
# 1. Initialize workspace, config, vault, and Postgres + tinyproxy containers
foundry init
foundry up
foundry status
foundry db migrate

# 2. Configure official API keys or local Ollama/vLLM and verify providers
foundry models list
foundry models add-key openai
foundry models assign coder openai:gpt-4o-mini
foundry models test

# 3. Inspect stack pack & sandbox/Docker hardening
foundry stack scaffold ./my-app
foundry sandbox check

# 4. Run single-agent builder v0.1 (`B1`) or replay benchmark smoke test ($0)
foundry build --spec "Bookmark manager with tags" --workspace ./my-app
foundry bench run-b1 --replay

# 5. Orchestrate multi-thread runs and answer human-in-the-loop decisions
foundry run start --spec path/to/spec.md
foundry run status <run_id>
foundry decisions list
foundry decisions show <decision_id>
foundry decisions resolve <decision_id> --approve
foundry run resume <run_id>

# 6. Stop local Postgres & egress-proxy infrastructure
foundry down
```

---

## Development & Verification

All offline unit and orchestration tests run at **$0 API cost** using normalized record/replay cassettes:

```bash
# Formatting, linting, and strict type checks
uv run --no-sync ruff format --check packages tests scripts bench
uv run --no-sync ruff check packages tests scripts bench
uv run --no-sync mypy packages/foundry bench

# Offline $0 test suite (default CI suite)
uv run --no-sync pytest -v -m "not live_docker"

# Live Postgres + Docker integration suite (requires Docker Engine running)
uv run --no-sync foundry up
uv run --no-sync foundry db migrate
uv run --no-sync pytest -v -m live_docker
uv run --no-sync foundry down
```

---

## License

Licensed under the [Apache License 2.0](LICENSE).
