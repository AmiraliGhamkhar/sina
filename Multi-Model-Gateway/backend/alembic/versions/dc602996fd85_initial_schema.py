"""initial schema

Revision ID: dc602996fd85
Revises: 
Create Date: 2026-05-23 15:04:55.157275
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'dc602996fd85'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        'users',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    op.create_table(
        'conversations',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('token_count', sa.Integer(), nullable=True),
    )

    op.create_table(
        'messages',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('conversation_id', UUID, sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('model_used', sa.String(), nullable=True),
        sa.Column('tokens_used', sa.Integer(), nullable=True),
    )

    op.create_table(
        'refresh_tokens',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('refresh_tokens')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')