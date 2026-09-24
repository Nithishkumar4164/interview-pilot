"""The SessionPipeline seam: conformance and the reads handlers depend on."""

from pathlib import Path

from src.replay.runner import ReplayRunner, build_scripted_failover_manager
from src.replay.scenario import load_scenario
from src.workers.analysis_worker import AnalysisWorker
from src.workers.session_pipeline import SessionPipeline, create_session_pipeline

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def test_factory_returns_a_session_pipeline() -> None:
    pipeline = create_session_pipeline("s-1", build_scripted_failover_manager())
    assert isinstance(pipeline, SessionPipeline)
    assert isinstance(pipeline, AnalysisWorker)


async def test_report_snapshot_reflects_the_session(monkeypatch) -> None:
    """End-session builds report inputs from report_snapshot(); check it carries real session data."""
    captured: dict[str, AnalysisWorker] = {}
    original_init = AnalysisWorker.__init__

    def capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["worker"] = self

    monkeypatch.setattr(AnalysisWorker, "__init__", capturing_init)
    scenario = load_scenario(SCENARIOS_DIR / "03_contradiction_resume_vs_response.scenario.json")
    report = await ReplayRunner(scenario, llm_mode="scripted").run()
    assert report.error is None

    snap = captured["worker"].report_snapshot()
    assert set(snap) == {"fact_store", "rolling_summary", "analysis_results", "contradictions"}
    assert len(snap["analysis_results"]) == report.summary.analyses
    assert len(snap["contradictions"]) == report.summary.contradictions >= 1
    assert snap["fact_store"]["claimed_experience"]


async def test_get_transcripts_by_ids_returns_matching_finals_in_order() -> None:
    worker = create_session_pipeline("s-2", build_scripted_failover_manager())
    worker._transcript_history.extend({"id": i, "text": f"t{i}"} for i in ("a", "b", "c"))
    assert [t["id"] for t in worker.get_transcripts_by_ids({"c", "a"})] == ["a", "c"]
    assert worker.get_transcripts_by_ids(set()) == []
