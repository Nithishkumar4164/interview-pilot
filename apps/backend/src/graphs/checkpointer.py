"""Postgres checkpointer shared by every LangGraph graph in the backend.

The checkpointer talks to Postgres through psycopg3 on its own pool; the rest
of the app keeps using SQLAlchemy + asyncpg. Checkpoint tables are owned by
``AsyncPostgresSaver.setup()`` (its own migration table), not Alembic — see
``CHECKPOINT_TABLES`` and ``alembic/env.py``.
"""

import contextlib
from collections.abc import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from src.graphs.serde import build_serializer
from src.utils.logging import get_logger

logger = get_logger("graphs.checkpointer")

# Tables created by AsyncPostgresSaver.setup(); Alembic must ignore them.
CHECKPOINT_TABLES = frozenset(
    {
        "checkpoints",
        "checkpoint_writes",
        "checkpoint_blobs",
        "checkpoint_migrations",
    }
)


def alembic_include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Alembic ``include_object`` hook: hide checkpointer-owned tables and their indexes.

    Without it, autogenerate sees these reflected tables with no model and emits
    ``drop_table`` for them.
    """
    if type_ == "table":
        return name not in CHECKPOINT_TABLES
    table = getattr(obj, "table", None)
    return table is None or table.name not in CHECKPOINT_TABLES


class Checkpointer:
    """Owns the psycopg pool and the saver built on it.

    Usage (FastAPI lifespan):
        await checkpointer.open(settings.database_url_sync)
        ...
        await checkpointer.close()
    """

    def __init__(self) -> None:
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._saver: AsyncPostgresSaver | None = None

    async def open(self, conninfo: str, *, min_size: int = 1, max_size: int = 10) -> AsyncPostgresSaver:
        if self._saver is not None:
            return self._saver
        # AsyncPostgresSaver requires autocommit, dict rows, and no server-side
        # prepared statements (same kwargs as AsyncPostgresSaver.from_conn_string).
        pool = AsyncConnectionPool(
            conninfo,
            connection_class=AsyncConnection[DictRow],
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=False,
        )
        await pool.open(wait=True)
        saver = AsyncPostgresSaver(pool, serde=build_serializer())
        await saver.setup()
        self._pool, self._saver = pool, saver
        logger.info("checkpointer_ready", max_pool_size=max_size)
        return saver

    @property
    def saver(self) -> AsyncPostgresSaver:
        if self._saver is None:
            raise RuntimeError("Checkpointer is not open; call open() during app startup")
        return self._saver

    @contextlib.asynccontextmanager
    async def try_advisory_lock(self, key: str) -> AsyncIterator[bool]:
        """Hold a session-level Postgres advisory lock for ``key`` if it is free.

        Yields False (without waiting) when another holder has it. The lock
        lives on a pooled connection held for the duration of the block, so
        it is released automatically if this process dies — letting another
        worker take over a crashed run.
        """
        if self._pool is None:
            raise RuntimeError("Checkpointer is not open; call open() during app startup")
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked", (key,))
            ).fetchone()
            locked = bool(row and row["locked"])
            try:
                yield locked
            finally:
                if locked:
                    await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    async def delete_threads(self, *thread_ids: str) -> None:
        for thread_id in thread_ids:
            await self.saver.adelete_thread(thread_id)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
        self._pool, self._saver = None, None
