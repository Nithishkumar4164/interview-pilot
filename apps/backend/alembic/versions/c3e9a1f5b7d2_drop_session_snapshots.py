"""drop session_snapshots

SessionStateService wrote a snapshot of each live session here every 30 s
and at session end; GET /api/sessions/{id}/state read it back. The LangGraph
session pipeline keeps that state in its checkpoints, /state reads them, and
nothing writes or reads this table any more.

Destructive: the rows are deleted. Downgrade recreates the empty table.

Revision ID: c3e9a1f5b7d2
Revises: b2d8f4a6c1e9
Create Date: 2026-10-07 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c3e9a1f5b7d2"
down_revision: Union[str, None] = "b2d8f4a6c1e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_session_snapshots_session_created", table_name="session_snapshots")
    op.drop_table("session_snapshots")


def downgrade() -> None:
    op.create_table(
        "session_snapshots",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_data", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["interview_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_session_snapshots_session_created", "session_snapshots", ["session_id", "created_at"], unique=False
    )
