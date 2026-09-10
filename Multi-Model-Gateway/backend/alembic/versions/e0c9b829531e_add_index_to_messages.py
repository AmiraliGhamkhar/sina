"""add index to messages

Revision ID: e0c9b829531e
Revises: 4b602081a1e1
Create Date: 2026-05-27 13:27:59.194607

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e0c9b829531e'
down_revision: Union[str, Sequence[str], None] = '4b602081a1e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('index', sa.Integer(), nullable=True))

def downgrade() -> None:
    op.drop_column('messages', 'index')
