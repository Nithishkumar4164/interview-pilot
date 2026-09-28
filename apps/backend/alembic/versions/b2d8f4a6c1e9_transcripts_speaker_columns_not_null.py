"""make transcripts.root_speaker_id / speech_vector_id NOT NULL

a3b7c9d1e4f2 added both columns as nullable with server_default '', while
the model declares them NOT NULL (default ""). Every producer writes a str,
so the model is authoritative; this aligns the database and clears the
drift ``alembic check`` reports.

SET NOT NULL normally scans the table under ACCESS EXCLUSIVE. Validating a
CHECK (col IS NOT NULL) constraint first (SHARE UPDATE EXCLUSIVE, writes
continue) lets Postgres 12+ skip that scan.

Revision ID: b2d8f4a6c1e9
Revises: a9c4e1d2b3f7
Create Date: 2026-09-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "b2d8f4a6c1e9"
down_revision: Union[str, None] = "a9c4e1d2b3f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("root_speaker_id", "speech_vector_id")


def upgrade() -> None:
    for col in COLUMNS:
        op.execute(f"UPDATE transcripts SET {col} = '' WHERE {col} IS NULL")
        op.execute(f"ALTER TABLE transcripts ADD CONSTRAINT ck_transcripts_{col}_not_null CHECK ({col} IS NOT NULL) NOT VALID")
        op.execute(f"ALTER TABLE transcripts VALIDATE CONSTRAINT ck_transcripts_{col}_not_null")
        op.execute(f"ALTER TABLE transcripts ALTER COLUMN {col} SET NOT NULL")
        op.execute(f"ALTER TABLE transcripts DROP CONSTRAINT ck_transcripts_{col}_not_null")


def downgrade() -> None:
    for col in COLUMNS:
        op.execute(f"ALTER TABLE transcripts ALTER COLUMN {col} DROP NOT NULL")
