"""add branching columns to conversations

The Conversation model gained parent_id / branched_from_message_id for the
branch feature, but no migration was generated (c999d3487714 is empty), so
any SELECT on conversations failed against a fresh database.

Revision ID: 9b3d6f1c2a47
Revises: 3fa8c20b911e
Create Date: 2026-06-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9b3d6f1c2a47'
down_revision: Union[str, Sequence[str], None] = '3fa8c20b911e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("conversations", sa.Column("parent_id", postgresql.UUID(), nullable=True))
    op.add_column("conversations", sa.Column("branched_from_message_id", postgresql.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_conversations_parent_id", "conversations", "conversations",
        ["parent_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_conversations_branched_from_message_id", "conversations", "messages",
        ["branched_from_message_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_conversations_branched_from_message_id", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_parent_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "branched_from_message_id")
    op.drop_column("conversations", "parent_id")
