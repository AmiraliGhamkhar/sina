"""messages.index NOT NULL + unique per conversation

Backfills any legacy NULL indices (assigned by created_at order, continuing past
the conversation's current max), then enforces NOT NULL + UNIQUE(conversation_id,
index) — the total order the exchange invariant assumes. See issues.md CR-9/DEV-2.

NOTE: if pre-existing rows already share a (conversation_id, index) pair (from the
old non-atomic save before the row lock), the unique constraint creation will fail;
those duplicates must be resolved by hand first.

Revision ID: a1f4e7c8d92b
Revises: 9b3d6f1c2a47
Create Date: 2026-06-13

"""
from typing import Sequence, Union

from alembic import op


revision: str = "a1f4e7c8d92b"
down_revision: Union[str, Sequence[str], None] = "9b3d6f1c2a47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. backfill NULL indices, continuing after each conversation's current max
    op.execute(
        """
        WITH numbered AS (
            SELECT m.id,
                   COALESCE(
                       (SELECT MAX(m2.index) FROM messages m2
                        WHERE m2.conversation_id = m.conversation_id AND m2.index IS NOT NULL),
                       0
                   ) + ROW_NUMBER() OVER (PARTITION BY m.conversation_id ORDER BY m.created_at) AS new_index
            FROM messages m
            WHERE m.index IS NULL
        )
        UPDATE messages SET index = numbered.new_index
        FROM numbered WHERE messages.id = numbered.id
        """
    )
    # 2. enforce NOT NULL + uniqueness
    op.alter_column("messages", "index", nullable=False)
    op.create_unique_constraint(
        "uq_messages_conversation_index", "messages", ["conversation_id", "index"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_messages_conversation_index", "messages", type_="unique")
    op.alter_column("messages", "index", nullable=True)
