"""add device_id column to refresh_tokens

Client-generated device binding for refresh replay protection. Nullable so
legacy rows stay untouched (and keep working — no device binding, no check).

Revision ID: b83db11a1dc0
Revises: f3a9c1d5e7b2
Create Date: 2026-08-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b83db11a1dc0'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1d5e7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("refresh_tokens", sa.Column("device_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("refresh_tokens", "device_id")
