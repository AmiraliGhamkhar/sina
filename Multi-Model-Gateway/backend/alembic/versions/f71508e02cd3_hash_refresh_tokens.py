"""hash refresh tokens

Revision ID: f71508e02cd3
Revises: 129e95bdca11
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f71508e02cd3'
down_revision: Union[str, Sequence[str], None] = '129e95bdca11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing refresh tokens are already invalid (the JWT secret was rotated),
    # so clear them before swapping the plaintext column for a hashed one.
    op.execute("DELETE FROM refresh_tokens")
    op.drop_column('refresh_tokens', 'token')
    op.add_column('refresh_tokens', sa.Column('token_hash', sa.String(), nullable=False))
    op.create_index(
        op.f('ix_refresh_tokens_token_hash'),
        'refresh_tokens',
        ['token_hash'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_refresh_tokens_token_hash'), table_name='refresh_tokens')
    op.drop_column('refresh_tokens', 'token_hash')
    op.add_column('refresh_tokens', sa.Column('token', sa.VARCHAR(), nullable=False))
