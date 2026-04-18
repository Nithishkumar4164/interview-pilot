"""add index on analysis_events.transcript_id

Foreign key existed since inception but was unindexed, so joins and
cascade checks scanned the table. Also converts the three event-time
String columns (transcripts.timestamp, code_snapshots.timestamp,
hint_events.displayed_at) to TIMESTAMP WITH TIME ZONE so ordering and
range queries no longer rely on lexicographic ISO-string compare.

Revision ID: f8a1b2c3d4e5
Revises: e7f2a3b4c5d6
Create Date: 2026-04-18 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f8a1b2c3d4e5"
down_revision: Union[str, None] = "e7f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_analysis_events_transcript_id",
        "analysis_events",
        ["transcript_id"],
        unique=False,
    )

    # Cast String event-time columns to timestamptz. USING
    # ``<col>::timestamptz`` parses existing ISO-8601 values (the code
    # path always wrote ``datetime.now(UTC).isoformat()``); rows with
    # invalid strings will fail the cast, which is what we want — we'd
    # rather see it in the migration than silently ship broken data.
    op.execute(
        "ALTER TABLE transcripts "
        "ALTER COLUMN timestamp TYPE TIMESTAMP WITH TIME ZONE "
        "USING timestamp::timestamptz"
    )
    op.execute(
        "ALTER TABLE code_snapshots "
        "ALTER COLUMN timestamp TYPE TIMESTAMP WITH TIME ZONE "
        "USING timestamp::timestamptz"
    )
    op.execute(
        "ALTER TABLE hint_events "
        "ALTER COLUMN displayed_at TYPE TIMESTAMP WITH TIME ZONE "
        "USING displayed_at::timestamptz"
    )


def downgrade() -> None:
    # Reversible but lossy in principle (tz info collapses to UTC ISO strings).
    op.execute(
        "ALTER TABLE transcripts "
        "ALTER COLUMN timestamp TYPE VARCHAR "
        "USING timestamp::text"
    )
    op.execute(
        "ALTER TABLE code_snapshots "
        "ALTER COLUMN timestamp TYPE VARCHAR "
        "USING timestamp::text"
    )
    op.execute(
        "ALTER TABLE hint_events "
        "ALTER COLUMN displayed_at TYPE VARCHAR "
        "USING displayed_at::text"
    )
    op.drop_index("ix_analysis_events_transcript_id", table_name="analysis_events")
