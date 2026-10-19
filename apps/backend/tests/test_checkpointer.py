"""Checkpointer integration tests against a real Postgres (not MemorySaver).

Skipped when Postgres at INTERVIEW_PILOT_DATABASE_URL_SYNC is unreachable.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph

from src.config import settings
from src.graphs.checkpointer import CHECKPOINT_TABLES, Checkpointer
from src.graphs.serde import CHECKPOINT_TYPES
from src.replay.scripted_provider import _utterance_analysis
from src.schemas.analysis import EnhancedAnalysisResult
from src.services.analysis_service import parse_analysis
from src.services.contradiction_service import Contradiction
from src.services.memory_service import FactStore, Utterance


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


class _State(TypedDict, total=False):
    analysis: EnhancedAnalysisResult
    facts: FactStore
    working_memory: list[Utterance]
    contradictions: list[Contradiction]
    other: Any


def _sample_state() -> dict:
    raw = _utterance_analysis(12345)
    analysis = parse_analysis(json.dumps(raw), raw)
    facts = FactStore()
    facts.merge({"claimed_experience": ["5 years of Kafka"], "key_numbers": ["1B events/day"]})
    return {
        "analysis": analysis,
        "facts": facts,
        "working_memory": [Utterance("candidate", "I led the platform", "2026-01-01T00:00:00+00:00")],
        "contradictions": [
            Contradiction(
                claim="5 years of Kafka",
                new_statement="not sure what a partition is",
                explanation="e",
                confidence=0.9,
                suggested_probe="p",
                detection_method="llm",
            )
        ],
    }


def _graph(update: dict):
    g = StateGraph(_State)
    g.add_node("write", lambda _s: update)
    g.add_edge(START, "write")
    g.add_edge("write", END)
    return g


@pytest.fixture
async def checkpointer():
    cp = Checkpointer()
    await cp.open(settings.database_url_sync, max_size=2)
    thread_ids: list[str] = []
    yield cp, thread_ids
    await cp.delete_threads(*thread_ids)
    await cp.close()


def _thread(thread_ids: list[str]) -> dict:
    thread_id = f"test-{uuid.uuid4()}"
    thread_ids.append(thread_id)
    return {"configurable": {"thread_id": thread_id}}


async def test_state_types_round_trip_through_a_fresh_saver(checkpointer) -> None:
    """Session recovery reads state written by a previous process."""
    cp, thread_ids = checkpointer
    expected = _sample_state()
    config = _thread(thread_ids)
    await _graph(expected).compile(checkpointer=cp.saver).ainvoke({}, config)

    # A new pool + saver, as after a backend restart.
    fresh = Checkpointer()
    await fresh.open(settings.database_url_sync, max_size=1)
    try:
        state = await _graph({}).compile(checkpointer=fresh.saver).aget_state(config)
    finally:
        await fresh.close()

    values = state.values
    # Strict mode returns a plain dict (not an error) for an unregistered type,
    # so assert the class, not just equality.
    assert isinstance(values["analysis"], EnhancedAnalysisResult)
    assert isinstance(
        values["analysis"].dimensions.relevance.score, type(expected["analysis"].dimensions.relevance.score)
    )
    assert isinstance(values["facts"], FactStore)
    assert all(isinstance(u, Utterance) for u in values["working_memory"])
    assert all(isinstance(c, Contradiction) for c in values["contradictions"])
    assert values == expected


async def test_unregistered_type_is_refused(checkpointer) -> None:
    @dataclass
    class NotRegistered:
        x: int

    assert NotRegistered not in CHECKPOINT_TYPES
    cp, thread_ids = checkpointer
    config = _thread(thread_ids)
    app = _graph({"other": NotRegistered(1)}).compile(checkpointer=cp.saver)
    await app.ainvoke({}, config)

    state = await app.aget_state(config)
    assert not isinstance(state.values["other"], NotRegistered)
    assert state.values["other"] == {"x": 1}


async def test_delete_threads_removes_all_state(checkpointer) -> None:
    cp, thread_ids = checkpointer
    config = _thread(thread_ids)
    app = _graph(_sample_state()).compile(checkpointer=cp.saver)
    await app.ainvoke({}, config)
    assert (await app.aget_state(config)).values

    await cp.delete_threads(config["configurable"]["thread_id"])
    assert (await app.aget_state(config)).values == {}


async def test_setup_creates_exactly_the_tables_alembic_ignores(checkpointer) -> None:
    async with await psycopg.AsyncConnection.connect(settings.database_url_sync) as conn:
        rows = await (
            await conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'checkpoint%'"
            )
        ).fetchall()
    assert {r[0] for r in rows} == CHECKPOINT_TABLES


def _autogenerate_ops(include_object=None) -> list[str]:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from src.models import Base

    opts = {"include_object": include_object} if include_object else {}
    engine = create_engine(settings.database_url_sync)
    try:
        with engine.connect() as conn:
            return [str(op) for op in compare_metadata(MigrationContext.configure(conn, opts=opts), Base.metadata)]
    finally:
        engine.dispose()


async def test_alembic_autogenerate_ignores_checkpoint_tables(checkpointer) -> None:
    from src.graphs.checkpointer import alembic_include_object

    def touches_checkpoints(ops: list[str]) -> bool:
        return any(f"'{t}'" in op or f"{t}_thread_id_idx" in op for op in ops for t in CHECKPOINT_TABLES)

    # Sensitivity: without the hook, autogenerate wants to drop them.
    assert touches_checkpoints(_autogenerate_ops())
    assert not touches_checkpoints(_autogenerate_ops(alembic_include_object))
