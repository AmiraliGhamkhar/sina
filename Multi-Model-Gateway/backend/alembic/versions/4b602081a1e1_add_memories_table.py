"""add memories table

Revision ID: 4b602081a1e1
Revises: b65222cd7976
Create Date: 2026-05-26 17:22:14.395679

"""
from typing import Sequence, Union

from pgvector.sqlalchemy import Vector
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b602081a1e1'
down_revision: Union[str, Sequence[str], None] = 'b65222cd7976'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.create_table(
        'memories',
        sa.Column('id', sa.dialects.postgresql.UUID(), primary_key=True),
        sa.Column('conversation_id', sa.dialects.postgresql.UUID(), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('embedding', Vector(768), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS memories CASCADE")
