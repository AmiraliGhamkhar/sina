"""add prompt_templates table

Revision ID: 129e95bdca11
Revises: 2db01124b25f
Create Date: 2026-05-28 14:33:21.099530

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '129e95bdca11'
down_revision: Union[str, Sequence[str], None] = '2db01124b25f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'prompt_templates',
        sa.Column('id', UUID(), primary_key=True),
        sa.Column('user_id', UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text, nullable= True),
        sa.Column('structure', sa.Text, nullable= False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

def downgrade() -> None:
    op.drop_table('prompt_templates')