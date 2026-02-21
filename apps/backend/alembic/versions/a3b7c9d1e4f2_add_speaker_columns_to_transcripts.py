"""add speaker columns to transcripts

Revision ID: a3b7c9d1e4f2
Revises: 6ce9f25f0b60
Create Date: 2026-02-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3b7c9d1e4f2'
down_revision: Union[str, None] = '6ce9f25f0b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('transcripts', sa.Column('root_speaker_id', sa.String(), nullable=True, server_default=''))
    op.add_column('transcripts', sa.Column('speech_vector_id', sa.String(), nullable=True, server_default=''))


def downgrade() -> None:
    op.drop_column('transcripts', 'speech_vector_id')
    op.drop_column('transcripts', 'root_speaker_id')
