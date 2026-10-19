"""add interview_sessions.candidate_phone and candidate_role

The candidate's phone and current role, filled from the parsed resume or
typed by the interviewer; both stay empty for a guest session.

Revision ID: e2a7c9d4f1b6
Revises: d4f1b8c2e6a3
Create Date: 2026-10-18 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e2a7c9d4f1b6"
down_revision: Union[str, None] = "d4f1b8c2e6a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("interview_sessions", sa.Column("candidate_phone", sa.String(), nullable=True))
    op.add_column("interview_sessions", sa.Column("candidate_role", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_sessions", "candidate_role")
    op.drop_column("interview_sessions", "candidate_phone")
