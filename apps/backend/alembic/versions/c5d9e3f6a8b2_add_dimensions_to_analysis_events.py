"""add dimensions column to analysis_events

Revision ID: c5d9e3f6a8b2
Revises: b4c8d2e5f7a1
Create Date: 2026-02-21 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

# revision identifiers, used by Alembic.
revision: str = 'c5d9e3f6a8b2'
down_revision: Union[str, None] = 'b4c8d2e5f7a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analysis_events', sa.Column('dimensions', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('analysis_events', 'dimensions')
