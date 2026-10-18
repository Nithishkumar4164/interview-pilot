"""add interview_sessions.meeting_url

The Google Meet link for the interview. The dashboard hands it to the
extension, which opens and tracks that tab for the session; the report
shows it.

Revision ID: d4f1b8c2e6a3
Revises: c3e9a1f5b7d2
Create Date: 2026-10-18 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d4f1b8c2e6a3"
down_revision: Union[str, None] = "c3e9a1f5b7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("interview_sessions", sa.Column("meeting_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("interview_sessions", "meeting_url")
