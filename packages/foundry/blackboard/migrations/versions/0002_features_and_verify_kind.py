"""Add features table and explicit verify/valid kinds constraint on tasks.

Revision ID: 0002_features_and_verify_kind
Revises: 0001_blackboard_initial
Create Date: 2026-11-25
"""

from __future__ import annotations

from alembic import op

revision = "0002_features_and_verify_kind"
down_revision = "0001_blackboard_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS features (
            run_id                    text NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            name                      text NOT NULL,
            status                    text NOT NULL DEFAULT 'pending'
                                      CHECK (status IN (
                                          'pending', 'in_progress', 'verified', 'merged', 'failed'
                                      )),
            budget_blackbox_tests_usd numeric(8,4) NOT NULL DEFAULT 0.30,
            spent_blackbox_tests_usd  numeric(8,4) NOT NULL DEFAULT 0,
            dispute_rounds            smallint NOT NULL DEFAULT 0,
            updated_at                timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, name)
        );
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_tasks_kind'
            ) THEN
                ALTER TABLE tasks
                ADD CONSTRAINT ck_tasks_kind
                CHECK (
                    kind IN ('build', 'schema', 'delta', 'fix', 'integration', 'deps', 'verify')
                );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_kind;")
    op.execute("DROP TABLE IF EXISTS features;")
