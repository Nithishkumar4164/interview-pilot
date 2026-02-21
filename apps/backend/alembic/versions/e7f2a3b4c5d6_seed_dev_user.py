"""seed default dev user

Revision ID: e7f2a3b4c5d6
Revises: d6e1f2a3b4c5
Create Date: 2026-02-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7f2a3b4c5d6'
down_revision: Union[str, None] = 'd6e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO users (id, email, name, role, created_at, updated_at)
        VALUES (
            '00000000-0000-0000-0000-000000000000',
            'dev@interviewpilot.local',
            'Dev User',
            'interviewer',
            NOW(),
            NOW()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM users WHERE id = '00000000-0000-0000-0000-000000000000'"
    )
