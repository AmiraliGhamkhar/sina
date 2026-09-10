"""add agents tables (user-created agents + versioned marketplace + file edits)

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-18

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("preset_id", postgresql.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("allowed_tools", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("max_iterations", sa.Integer(), nullable=False, server_default=sa.text("'6'")),
        sa.Column("token_budget", sa.Integer(), nullable=False, server_default=sa.text("'24000'")),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("'1'")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["preset_id"], ["presets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agents_user_id", "agents", ["user_id"])

    op.create_table(
        "agent_installs",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("agent_id", postgresql.UUID(), nullable=False),
        sa.Column("pinned_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "agent_id", name="uq_agent_installs_user_agent"),
    )
    op.create_index("ix_agent_installs_agent_id", "agent_installs", ["agent_id"])
    op.create_index("ix_agent_installs_user_id", "agent_installs", ["user_id"])

    op.create_table(
        "file_edits",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("agent_id", postgresql.UUID(), nullable=True),
        sa.Column("store", sa.String(length=16), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("patch", sa.Text(), nullable=False),
        sa.Column("before_hash", sa.String(length=40), nullable=True),
        sa.Column("after_hash", sa.String(length=40), nullable=True),
        sa.Column("tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_file_edits_agent_id", "file_edits", ["agent_id"])
    op.create_index("ix_file_edits_user_id", "file_edits", ["user_id"])

    op.add_column("conversations", sa.Column("agent_id", postgresql.UUID(), nullable=True))
    op.add_column("conversations", sa.Column("agent_version", sa.Integer(), nullable=True))
    op.create_index("ix_conversations_agent_id", "conversations", ["agent_id"])
    op.create_foreign_key("fk_conversations_agent_id", "conversations", "agents", ["agent_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_conversations_agent_id", "conversations", type_="foreignkey")
    op.drop_index("ix_conversations_agent_id", table_name="conversations")
    op.drop_column("conversations", "agent_version")
    op.drop_column("conversations", "agent_id")
    op.drop_index("ix_file_edits_user_id", table_name="file_edits")
    op.drop_index("ix_file_edits_agent_id", table_name="file_edits")
    op.drop_table("file_edits")
    op.drop_index("ix_agent_installs_user_id", table_name="agent_installs")
    op.drop_index("ix_agent_installs_agent_id", table_name="agent_installs")
    op.drop_table("agent_installs")
    op.drop_index("ix_agents_user_id", table_name="agents")
    op.drop_table("agents")
