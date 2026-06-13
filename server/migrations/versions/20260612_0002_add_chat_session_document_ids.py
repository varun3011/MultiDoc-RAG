"""add multi-document chat session selection

Revision ID: 20260612_0002
Revises: 20260217_0001
Create Date: 2026-06-12
"""

from alembic import op


revision = "20260612_0002"
down_revision = "20260217_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE chat_sessions
            ADD COLUMN IF NOT EXISTS document_ids UUID[] NOT NULL DEFAULT '{}';

        UPDATE chat_sessions
        SET document_ids = ARRAY[document_id]::UUID[]
        WHERE document_id IS NOT NULL
          AND COALESCE(array_length(document_ids, 1), 0) = 0;

        CREATE INDEX IF NOT EXISTS idx_chat_sessions_document_ids
            ON chat_sessions USING GIN (document_ids);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_sessions_document_ids")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS document_ids")
