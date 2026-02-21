"""add report status column

Revision ID: b4c8d2e5f7a1
Revises: a3b7c9d1e4f2
Create Date: 2026-02-21 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4c8d2e5f7a1'
down_revision: Union[str, None] = 'a3b7c9d1e4f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reports',
        sa.Column('status', sa.String(20), nullable=False, server_default='completed'),
    )


def downgrade() -> None:
    op.drop_column('reports', 'status')
