"""add token_provenance column to messages

How tokens_used was derived: "exact" (provider reported usage or the
off-path /v1/tokenize/encode sync wrote real counts) vs "chunk_count"
(local streamed fallback). Nullable so legacy rows stay untouched.

Revision ID: f3a9c1d5e7b2
Revises: d4a8b2c6f9e7
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c1d5e7b2'
down_revision: Union[str, Sequence[str], None] = 'b8e4f1a2c9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("token_provenance", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "token_provenance")
