"""add candidate_name, candidate_email, resume_data to interview_sessions

Revision ID: d6e1f2a3b4c5
Revises: c5d9e3f6a8b2
Create Date: 2026-02-21 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

# revision identifiers, used by Alembic.
revision: str = 'd6e1f2a3b4c5'
down_revision: Union[str, None] = 'c5d9e3f6a8b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('interview_sessions', sa.Column('candidate_name', sa.String(), nullable=True))
    op.add_column('interview_sessions', sa.Column('candidate_email', sa.String(), nullable=True))
    op.add_column('interview_sessions', sa.Column('resume_data', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('interview_sessions', 'resume_data')
    op.drop_column('interview_sessions', 'candidate_email')
    op.drop_column('interview_sessions', 'candidate_name')
