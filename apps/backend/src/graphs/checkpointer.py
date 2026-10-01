"""Postgres checkpointer shared by every LangGraph graph in the backend.

The checkpointer talks to Postgres through psycopg3 on its own pool; the rest
of the app keeps using SQLAlchemy + asyncpg. Checkpoint tables are owned by
``AsyncPostgresSaver.setup()`` (its own migration table), not Alembic — see
``CHECKPOINT_TABLES`` and ``alembic/env.py``.
"""

import asyncio
import contextlib
import time
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
        self._lock_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
        self._saver: AsyncPostgresSaver | None = None

    @staticmethod
    def _new_pool(conninfo: str, min_size: int, max_size: int) -> AsyncConnectionPool[AsyncConnection[DictRow]]:
        # AsyncPostgresSaver requires autocommit, dict rows, and no server-side
        # prepared statements (same kwargs as AsyncPostgresSaver.from_conn_string).
        return AsyncConnectionPool(
            conninfo,
            connection_class=AsyncConnection[DictRow],
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=False,
        )

    async def open(
        self, conninfo: str, *, min_size: int = 1, max_size: int = 10, lock_pool_size: int = 10
    ) -> AsyncPostgresSaver:
        if self._saver is not None:
            return self._saver
        pool = self._new_pool(conninfo, min_size, max_size)
        # Advisory locks are held on a connection for the length of a graph run,
        # and that run needs saver connections to write checkpoints. Separate
        # pools mean lock holders can never starve the writes they wait on.
        lock_pool = self._new_pool(conninfo, 1, lock_pool_size)
        await pool.open(wait=True)
        await lock_pool.open(wait=True)
        saver = AsyncPostgresSaver(pool, serde=build_serializer())
        await saver.setup()
        self._pool, self._lock_pool, self._saver = pool, lock_pool, saver
        logger.info("checkpointer_ready", max_pool_size=max_size, lock_pool_size=lock_pool_size)
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
        if self._lock_pool is None:
            raise RuntimeError("Checkpointer is not open; call open() during app startup")
        async with self._lock_pool.connection() as conn:
            row = await (
                await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked", (key,))
            ).fetchone()
            locked = bool(row and row["locked"])
            try:
                yield locked
            finally:
                if locked:
                    await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    @contextlib.asynccontextmanager
    async def advisory_lock(self, key: str, *, timeout_s: float = 30.0, poll_s: float = 0.02) -> AsyncIterator[None]:
        """Hold the advisory lock for ``key``, waiting up to ``timeout_s`` for it.

        Polls ``pg_try_advisory_lock`` rather than blocking in
        ``pg_advisory_lock``, so a cancelled waiter never leaves a backend
        stuck waiting on the server. Raises TimeoutError if not acquired.
        """
        if self._lock_pool is None:
            raise RuntimeError("Checkpointer is not open; call open() during app startup")
        deadline = time.monotonic() + timeout_s
        async with self._lock_pool.connection() as conn:
            while True:
                row = await (
                    await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked", (key,))
                ).fetchone()
                if row and row["locked"]:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"advisory lock {key!r} not acquired in {timeout_s}s")
                await asyncio.sleep(poll_s)
            try:
                yield
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    async def delete_threads(self, *thread_ids: str) -> None:
        for thread_id in thread_ids:
            await self.saver.adelete_thread(thread_id)

    async def close(self) -> None:
        for pool in (self._pool, self._lock_pool):
            if pool is not None:
                await pool.close()
        self._pool, self._lock_pool, self._saver = None, None, None
