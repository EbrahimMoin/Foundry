"""Initial Postgres Blackboard, fuzzy memory, outbox sequence, and NOTIFY trigger.

Revision ID: 0001_blackboard_initial
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

revision = "0001_blackboard_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'task_status') THEN
                CREATE TYPE task_status AS ENUM (
                    'PENDING', 'BLOCKED', 'READY', 'IN_PROGRESS', 'IN_REVIEW',
                    'VERIFIED', 'QUEUED', 'MERGED', 'DEPLOYED', 'FAILED',
                    'ESCALATED', 'FROZEN', 'CANCELLED'
                );
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id          text PRIMARY KEY,
            project_id  text NOT NULL,
            phase       text NOT NULL DEFAULT 'planning',
            budget_usd  numeric(8,2) NOT NULL,
            spent_usd   numeric(8,4) NOT NULL DEFAULT 0,
            data_policy jsonb NOT NULL DEFAULT (
                '{"allow_free_tier": false, "allow_trace_export": false}'::jsonb
            ),
            started_at  timestamptz NOT NULL DEFAULT now(),
            ended_at    timestamptz
        );

        CREATE TABLE IF NOT EXISTS agents (
            id              text PRIMARY KEY,
            run_id          text NOT NULL REFERENCES runs(id),
            role            text NOT NULL,
            domain          text,
            parent_agent_id text REFERENCES agents(id),
            model_profile   text NOT NULL,
            status          text NOT NULL DEFAULT 'idle',
            graph_thread_id text
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id                   text PRIMARY KEY,
            run_id               text NOT NULL REFERENCES runs(id),
            wave                 smallint NOT NULL,
            domain               text NOT NULL,
            feature              text,
            kind                 text NOT NULL DEFAULT 'build',
            title                text NOT NULL,
            description          text NOT NULL,
            acceptance           jsonb NOT NULL DEFAULT '[]',
            status               task_status NOT NULL DEFAULT 'PENDING',
            owner_agent_id       text REFERENCES agents(id),
            parent_task_id       text REFERENCES tasks(id),
            attempt              smallint NOT NULL DEFAULT 0,
            max_attempts         smallint NOT NULL DEFAULT 3,
            contract_reopens     smallint NOT NULL DEFAULT 0,
            base_sha             text,
            branch               text,
            worktree_path        text,
            graph_thread_id      text,
            pending_deps         jsonb NOT NULL DEFAULT '[]',
            budget_implement_usd numeric(8,4) NOT NULL,
            budget_review_usd    numeric(8,4) NOT NULL,
            spent_implement_usd  numeric(8,4) NOT NULL DEFAULT 0,
            spent_review_usd     numeric(8,4) NOT NULL DEFAULT 0,
            version              integer NOT NULL DEFAULT 1,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS tasks_run_status ON tasks (run_id, status);

        CREATE TABLE IF NOT EXISTS task_dependencies (
            task_id            text REFERENCES tasks(id) ON DELETE CASCADE,
            depends_on_task_id text REFERENCES tasks(id) ON DELETE CASCADE,
            kind               text NOT NULL CHECK (kind IN ('blocks', 'join')),
            PRIMARY KEY (task_id, depends_on_task_id)
        );

        CREATE TABLE IF NOT EXISTS task_write_sets (
            task_id text REFERENCES tasks(id) ON DELETE CASCADE,
            path    text NOT NULL,
            mode    text NOT NULL CHECK (mode IN ('write', 'read')),
            source  text NOT NULL DEFAULT 'planned' CHECK (source IN ('planned', 'expansion')),
            PRIMARY KEY (task_id, path, mode)
        );

        CREATE TABLE IF NOT EXISTS write_reservations (
            id           bigserial PRIMARY KEY,
            run_id       text NOT NULL REFERENCES runs(id),
            task_id      text NOT NULL REFERENCES tasks(id),
            path         text NOT NULL,
            expires_at   timestamptz NOT NULL,
            heartbeat_at timestamptz NOT NULL DEFAULT now(),
            released_at  timestamptz
        );
        CREATE INDEX IF NOT EXISTS write_res_active
            ON write_reservations (run_id) WHERE released_at IS NULL;

        CREATE TABLE IF NOT EXISTS contracts (
            id               text PRIMARY KEY,
            run_id           text NOT NULL REFERENCES runs(id),
            name             text NOT NULL,
            domain           text NOT NULL,
            kind             text NOT NULL CHECK (
                kind IN ('db_schema', 'openapi', 'component', 'deps', 'event')
            ),
            owner_agent_id   text REFERENCES agents(id),
            current_version  integer NOT NULL DEFAULT 0,
            status           text NOT NULL DEFAULT 'draft' CHECK (
                status IN ('draft', 'locked', 'frozen')
            ),
            breaking_in_wave smallint NOT NULL DEFAULT 0,
            version          integer NOT NULL DEFAULT 1,
            UNIQUE (run_id, name)
        );

        CREATE TABLE IF NOT EXISTS contract_versions (
            contract_id      text REFERENCES contracts(id),
            version          integer NOT NULL,
            body             jsonb NOT NULL,
            pinned_pointers  text[] NOT NULL DEFAULT '{}',
            change_kind      text NOT NULL CHECK (
                change_kind IN ('initial', 'additive', 'breaking')
            ),
            changed_elements text[] NOT NULL DEFAULT '{}',
            change_summary   text,
            author_agent_id  text,
            approved_by      text,
            created_at       timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (contract_id, version)
        );

        CREATE TABLE IF NOT EXISTS contract_subscriptions (
            contract_id text REFERENCES contracts(id),
            task_id     text REFERENCES tasks(id) ON DELETE CASCADE,
            scope       text[] NOT NULL DEFAULT '{}',
            PRIMARY KEY (contract_id, task_id)
        );

        CREATE TABLE IF NOT EXISTS contract_change_requests (
            id            text PRIMARY KEY,
            contract_id   text NOT NULL REFERENCES contracts(id),
            requested_by  text NOT NULL REFERENCES tasks(id),
            proposed_body jsonb NOT NULL,
            reason        text NOT NULL,
            classified_as text CHECK (
                classified_as IN ('additive', 'breaking', 'pinned_violation')
            ),
            status        text NOT NULL DEFAULT 'pending' CHECK (
                status IN ('pending', 'batched', 'approved', 'rejected', 'escalated')
            ),
            batch_id      text,
            created_at    timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id              text PRIMARY KEY,
            run_id          text NOT NULL REFERENCES runs(id),
            idempotency_key text NOT NULL UNIQUE,
            category        text NOT NULL CHECK (
                category IN ('strategic', 'tactical', 'operational', 'release_infra')
            ),
            title           text NOT NULL,
            context         text NOT NULL,
            options         jsonb NOT NULL,
            recommendation  jsonb,
            confidence      real,
            reversibility   text CHECK (reversibility IN ('reversible', 'hard', 'irreversible')),
            impact          jsonb NOT NULL DEFAULT '{}',
            mode            text NOT NULL CHECK (mode IN ('auto', 'notify', 'approve', 'human')),
            status          text NOT NULL DEFAULT 'pending' CHECK (
                status IN ('pending', 'auto_decided', 'resolved', 'undone', 'expired')
            ),
            answer          jsonb,
            decided_by      text,
            graph_thread_id text,
            blocks_task_ids text[] NOT NULL DEFAULT '{}',
            version         integer NOT NULL DEFAULT 1,
            created_at      timestamptz NOT NULL DEFAULT now(),
            decided_at      timestamptz
        );

        CREATE TABLE IF NOT EXISTS threads (
            id         text PRIMARY KEY,
            run_id     text NOT NULL REFERENCES runs(id),
            kind       text NOT NULL CHECK (
                kind IN ('agent_dm', 'task', 'contract', 'incident', 'human_chat', 'dispute')
            ),
            subject_id text,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS messages (
            id         text PRIMARY KEY,
            thread_id  text NOT NULL REFERENCES threads(id),
            from_id    text NOT NULL,
            to_id      text,
            body       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS notes (
            id         text PRIMARY KEY,
            run_id     text NOT NULL REFERENCES runs(id),
            task_id    text REFERENCES tasks(id),
            feature    text,
            kind       text NOT NULL CHECK (kind IN ('note', 'gotcha')),
            body       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS releases (
            id                  text PRIMARY KEY,
            run_id              text NOT NULL REFERENCES runs(id),
            env                 text NOT NULL CHECK (env IN ('local', 'staging', 'production')),
            target              text NOT NULL,
            git_sha             text NOT NULL,
            image_ref           text,
            migration_head      text,
            migration_kind      text CHECK (migration_kind IN ('none', 'expand', 'contract')),
            db_backup_ref       text,
            status              text NOT NULL CHECK (
                status IN ('deploying', 'warming', 'probing', 'live', 'rolled_back', 'failed')
            ),
            previous_release_id text REFERENCES releases(id),
            created_at          timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS incidents (
            id            text PRIMARY KEY,
            run_id        text NOT NULL REFERENCES runs(id),
            release_id    text REFERENCES releases(id),
            severity      text NOT NULL CHECK (severity IN ('sev1', 'sev2', 'sev3')),
            source        text NOT NULL CHECK (source IN ('probe', 'e2e', 'health', 'user', 'ci')),
            summary       text NOT NULL,
            evidence      jsonb NOT NULL DEFAULT '{}',
            owning_domain text,
            fix_task_id   text REFERENCES tasks(id),
            status        text NOT NULL DEFAULT 'open' CHECK (
                status IN ('open', 'mitigated', 'resolved')
            ),
            created_at    timestamptz NOT NULL DEFAULT now(),
            resolved_at   timestamptz
        );

        CREATE TABLE IF NOT EXISTS llm_calls (
            id              bigserial PRIMARY KEY,
            run_id          text,
            agent_id        text,
            task_id         text,
            graph_thread_id text,
            node            text,
            provider        text NOT NULL,
            model           text NOT NULL,
            profile         text NOT NULL,
            transport       text NOT NULL DEFAULT 'openai_compat' CHECK (
                transport IN ('openai_compat', 'google_genai', 'anthropic')
            ),
            billing_mode    text NOT NULL DEFAULT 'paid_api' CHECK (
                billing_mode IN ('paid_api', 'free_tier', 'local')
            ),
            input_tokens    integer NOT NULL,
            cached_tokens   integer NOT NULL DEFAULT 0,
            output_tokens   integer NOT NULL,
            cost_usd        numeric(10,6) NOT NULL,
            est_value_usd   numeric(10,6) NOT NULL DEFAULT 0,
            latency_ms      integer,
            replayed        boolean NOT NULL DEFAULT false,
            prompt_hash     text,
            created_at      timestamptz NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS events_publish_seq;

        CREATE TABLE IF NOT EXISTS events (
            id           bigserial PRIMARY KEY,
            publish_seq  bigint UNIQUE,
            run_id       text NOT NULL,
            type         text NOT NULL,
            subject_id   text,
            payload      jsonb NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        );
        CREATE INDEX IF NOT EXISTS events_unpublished ON events (id) WHERE publish_seq IS NULL;
        CREATE INDEX IF NOT EXISTS events_run_seq
            ON events (run_id, publish_seq) WHERE publish_seq IS NOT NULL;

        CREATE TABLE IF NOT EXISTS processed_events (
            consumer_group text NOT NULL,
            publish_seq    bigint NOT NULL,
            PRIMARY KEY (consumer_group, publish_seq)
        );

        -- Fuzzy-memory pgvector tables (Section 8.1)
        CREATE TABLE IF NOT EXISTS code_chunks (
            id           text PRIMARY KEY,
            source_ref   text NOT NULL,
            content      text NOT NULL,
            content_hash text NOT NULL,
            embedding    vector(384),
            tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        );
        CREATE INDEX IF NOT EXISTS code_chunks_emb_idx
            ON code_chunks USING hnsw (embedding vector_cosine_ops);
        CREATE INDEX IF NOT EXISTS code_chunks_tsv_idx ON code_chunks USING gin (tsv);

        CREATE TABLE IF NOT EXISTS lessons (
            id           text PRIMARY KEY,
            source_ref   text NOT NULL,
            content      text NOT NULL,
            content_hash text NOT NULL,
            embedding    vector(384),
            tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        );
        CREATE INDEX IF NOT EXISTS lessons_emb_idx
            ON lessons USING hnsw (embedding vector_cosine_ops);
        CREATE INDEX IF NOT EXISTS lessons_tsv_idx ON lessons USING gin (tsv);

        CREATE TABLE IF NOT EXISTS docs (
            id           text PRIMARY KEY,
            source_ref   text NOT NULL,
            content      text NOT NULL,
            content_hash text NOT NULL,
            embedding    vector(384),
            tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
        );
        CREATE INDEX IF NOT EXISTS docs_emb_idx ON docs USING hnsw (embedding vector_cosine_ops);
        CREATE INDEX IF NOT EXISTS docs_tsv_idx ON docs USING gin (tsv);

        -- Transactional NOTIFY trigger for outbox relay (A7-5)
        CREATE OR REPLACE FUNCTION notify_events_new() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify('events_new', NEW.id::text);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS events_after_insert_notify ON events;
        CREATE TRIGGER events_after_insert_notify
            AFTER INSERT ON events
            FOR EACH ROW
            EXECUTE FUNCTION notify_events_new();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS events_after_insert_notify ON events;
        DROP FUNCTION IF EXISTS notify_events_new();
        DROP TABLE IF EXISTS docs, lessons, code_chunks;
        DROP TABLE IF EXISTS processed_events, events;
        DROP SEQUENCE IF EXISTS events_publish_seq;
        DROP TABLE IF EXISTS llm_calls, incidents, releases, notes, messages, threads;
        DROP TABLE IF EXISTS decisions, contract_change_requests, contract_subscriptions;
        DROP TABLE IF EXISTS contract_versions, contracts, write_reservations;
        DROP TABLE IF EXISTS task_write_sets, task_dependencies, tasks, agents, runs;
        DROP TYPE IF EXISTS task_status;
        """
    )
