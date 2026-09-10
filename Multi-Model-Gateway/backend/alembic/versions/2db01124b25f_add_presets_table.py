"""add presets table

Revision ID: 2db01124b25f
Revises: e0c9b829531e
Create Date: 2026-05-28 11:10:17.092195

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY


# revision identifiers, used by Alembic.
revision: str = '2db01124b25f'
down_revision: Union[str, Sequence[str], None] = 'e0c9b829531e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'presets',
        sa.Column('id', UUID(), primary_key=True),
        sa.Column('user_id', UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('temperature', sa.Float(), nullable=True),
        sa.Column('token_limit', sa.Integer(), nullable=True),
        sa.Column('context_overflow', sa.String(), nullable=True),
        sa.Column('stop_strings', ARRAY(sa.String()), nullable=True),
        sa.Column('top_k', sa.Integer(), nullable=True),
        sa.Column('top_p', sa.Float(), nullable=True),
        sa.Column('min_p', sa.Float(), nullable=True),
        sa.Column('repeat_penalty', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

def downgrade() -> None:
    op.drop_table('presets')