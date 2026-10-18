"""add session_questions

The interview's questionnaire: each question the interviewer generated during
the interview and used, in order, with when it was asked.

Revision ID: f3b8d1e5a9c7
Revises: e2a7c9d4f1b6
Create Date: 2026-10-18 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f3b8d1e5a9c7"
down_revision: Union[str, None] = "e2a7c9d4f1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("question", postgresql.JSON(), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "position", name="uq_session_questions_position"),
    )
    op.create_index("ix_session_questions_session_id", "session_questions", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_questions_session_id", table_name="session_questions")
    op.drop_table("session_questions")
