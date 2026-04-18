"""Regression tests for the background report generation flow.

Verifies the fix for `A4 — LLM call holds DB connection open`:
`_generate_report_background` must release the DB session before invoking
the LLM and re-open a fresh session only to persist the result.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.report import Recommendation
from src.services.memory_service import FactStore
from src.services.report_generator import GeneratedReport, ReportInput


class _TrackedSession:
    """AsyncSession stand-in that records whether it is currently open."""

    def __init__(self, monitor: "SessionMonitor") -> None:
        self._monitor = monitor

    async def __aenter__(self) -> "_TrackedSession":
        self._monitor.open_count += 1
        self._monitor.currently_open += 1
        self._monitor.timeline.append(("open", None))
        return self

    async def __aexit__(self, *args: object) -> None:
        self._monitor.currently_open -= 1
        self._monitor.timeline.append(("close", None))

    async def commit(self) -> None:
        pass


class SessionMonitor:
    def __init__(self) -> None:
        self.open_count = 0
        self.currently_open = 0
        self.timeline: list[tuple[str, str | None]] = []

    def __call__(self) -> _TrackedSession:
        return _TrackedSession(self)


@pytest.mark.asyncio
async def test_background_report_does_not_hold_session_across_llm_call(monkeypatch):
    """The LLM call must run with no DB session open."""
    from src.api import sessions as sessions_module

    monitor = SessionMonitor()

    # Capture whether any session is open while the LLM call runs.
    llm_call_snapshot = {"sessions_open_during_llm": None}

    async def fake_load_inputs(self, session_id, snapshots, db):
        monitor.timeline.append(("load_inputs", session_id))
        return ReportInput(
            session_id=session_id,
            duration_minutes=30,
            target_level="sde2",
            fact_store=FactStore(),
            analysis_results=[],
            hint_events=[],
            hint_metrics={},
            contradictions=[],
            transcript_summary="",
            code_progress="unknown",
        )

    async def fake_generate_from_inputs(self, report_input):
        monitor.timeline.append(("llm_call", report_input.session_id))
        llm_call_snapshot["sessions_open_during_llm"] = monitor.currently_open
        return GeneratedReport(
            id="rep-1",
            session_id=report_input.session_id,
            recommendation=Recommendation.LEAN_HIRE,
            confidence_level=0.7,
            scores=[],
            key_moments=[],
            strengths=[],
            areas_for_growth=[],
            level_comparison="",
            generated_at="2024-01-01T00:00:00",
        )

    monkeypatch.setattr(
        "src.services.report_service.ReportService.load_inputs",
        fake_load_inputs,
    )
    monkeypatch.setattr(
        "src.services.report_service.ReportService.generate_from_inputs",
        fake_generate_from_inputs,
    )

    # get_by_id returns None so the persist branch short-circuits cleanly.
    fake_report_repo = MagicMock()
    fake_report_repo.get_by_id = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "src.repositories.report_repo.ReportRepository",
        lambda db: fake_report_repo,
    )

    await sessions_module._generate_report_background(
        report_id="00000000-0000-0000-0000-000000000001",
        session_id="session-abc",
        snapshots={},
        fm=MagicMock(),
        session_factory=monitor,
    )

    assert llm_call_snapshot["sessions_open_during_llm"] == 0, "LLM call ran while a DB session was still open"

    # Expect two distinct session windows around the LLM call.
    stages = [stage for stage, _ in monitor.timeline]
    # Sessions are opened/closed exactly twice: once for load_inputs, once
    # for persist; the LLM call sits between the first close and the
    # second open.
    first_open = stages.index("open")
    first_close = stages.index("close", first_open)
    llm_idx = stages.index("llm_call")
    second_open = stages.index("open", first_close)
    assert first_close < llm_idx < second_open
    assert monitor.open_count == 2
