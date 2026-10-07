"""ReportGraph — post-interview report generation as a checkpointed graph.

    load_inputs ─► generate ─► assemble ─► persist

- load_inputs: read transcripts/analyses/hints/code/plan from the DB. The
  connection is released before the LLM call.
- generate:    one report-generation LLM call through FailoverManager. A failed
  call is recorded (not raised) so assemble can fall back.
- assemble:    parse the output (or build the minimal fallback report) and add
  the deterministic hint summary and annotated transcript.
- persist:     write the report row; idempotent (keyed by report_id).

Each node's output is checkpointed (durability="sync") on thread
``report:{report_id}``. If the process dies mid-run, ``resume_pending``
continues from the last completed node on the next startup, so a finished
LLM call is not paid for twice. The thread is deleted once the run ends.
"""

import uuid
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.graphs.checkpointer import Checkpointer
from src.llm.failover import FailoverManager
from src.llm.retry import retry_transient
from src.models.report import Report
from src.repositories.report_repo import ReportRepository
from src.services.report_agentic import assemble_report, build_evidence_index, score_criterion, synthesize
from src.services.report_generator import ALL_CRITERIA, GeneratedReport, ReportGenerator, ReportInput
from src.services.report_service import ReportService
from src.utils.logging import get_logger

logger = get_logger("graphs.report")


def _merge(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class ReportState(TypedDict, total=False):
    report_id: str
    session_id: str
    snapshots: dict[str, Any]
    report_input: ReportInput
    llm: dict[str, Any]
    report: GeneratedReport
    # agentic scoring (REPORT_AGENTIC_SCORING)
    criteria: Annotated[dict[str, dict[str, Any]], _merge]
    synthesis: dict[str, Any]


class CriterionTask(TypedDict):
    session_id: str
    criterion: str
    report_input: ReportInput


def thread_id_for(report_id: str) -> str:
    return f"report:{report_id}"


def _config(report_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id_for(report_id)}}


async def persist_report(db: AsyncSession, report_id: str, generated: GeneratedReport) -> bool:
    """Write a generated report onto its placeholder row. Idempotent. Returns False if the row is gone."""
    report_repo = ReportRepository(db)
    report = await report_repo.get_by_id(uuid.UUID(report_id))
    if report is None:
        return False
    report.report_data = {
        "scores": [s.model_dump() for s in generated.scores],
        "key_moments": [m.model_dump() for m in generated.key_moments],
        "strengths": generated.strengths,
        "areas_for_growth": generated.areas_for_growth,
        "level_comparison": generated.level_comparison,
        "hint_summary": generated.hint_summary,
        "annotated_transcript": generated.annotated_transcript,
        "generated_at": generated.generated_at,
    }
    report.recommendation = generated.recommendation.value
    report.confidence_level = generated.confidence_level
    report.is_final = True
    report.status = "completed"
    await report_repo.update(report)
    return True


def build_report_graph(
    failover_manager: FailoverManager,
    session_factory: async_sessionmaker[AsyncSession] | Callable[[], Any],
    agentic: bool | None = None,
) -> StateGraph:
    """The report graph; ``agentic`` (REPORT_AGENTIC_SCORING) adds per-criterion scoring.

    agentic:  load_inputs -> Send(score_criterion) x 7 -> synthesize -+-> assemble_agentic -> persist
                                                                      +-> generate -> assemble -> persist
              (the single-call path is the fallback when any criterion or the synthesis is invalid)
    """
    if agentic is None:
        from src.config import settings

        agentic = settings.report_agentic_scoring
    service = ReportService(failover_manager)
    generator = ReportGenerator(failover_manager)

    async def load_inputs(state: ReportState) -> dict:
        async with session_factory() as db:
            report_input = await service.load_inputs(state["session_id"], state.get("snapshots") or {}, db)
        return {"report_input": report_input}

    async def generate(state: ReportState) -> dict:
        # No DB session is open here: a slow call (or provider failover) holds no connection.
        return {"llm": await generator.call_llm(state["report_input"])}

    def assemble(state: ReportState) -> dict:
        report_input = state["report_input"]
        report = generator.report_from_llm(state["llm"], report_input)
        return {"report": generator.enrich(report, report_input)}

    async def persist(state: ReportState) -> dict:
        async with session_factory() as db:
            found = await persist_report(db, state["report_id"], state["report"])
            await db.commit()
        if not found:
            logger.warning("report_row_missing", report_id=state["report_id"])
        else:
            logger.info(
                "report_generation_completed",
                report_id=state["report_id"],
                session_id=state["session_id"],
                recommendation=state["report"].recommendation.value,
            )
        return {}

    def fan_out_criteria(state: ReportState) -> list[Send]:
        return [
            Send(
                "score_criterion",
                CriterionTask(session_id=state["session_id"], criterion=c, report_input=state["report_input"]),
            )
            for c in ALL_CRITERIA
        ]

    async def score_criterion_node(state: CriterionTask) -> dict:
        index = build_evidence_index(state["report_input"])
        try:
            result = await retry_transient(
                lambda: score_criterion(failover_manager, state["session_id"], state["criterion"], index)
            )
        except Exception as e:
            logger.warning("criterion_scoring_failed", criterion=state["criterion"], error=str(e))
            result = {"criterion": state["criterion"], "valid": False, "errors": [str(e)], "usage": {}}
        return {"criteria": {state["criterion"]: result}}

    async def synthesize_node(state: ReportState) -> dict:
        criteria = state.get("criteria", {})
        invalid = sorted(c for c in ALL_CRITERIA if not criteria.get(c, {}).get("valid"))
        if invalid:
            logger.warning("agentic_report_fallback", reason="invalid_criteria", criteria=invalid)
            return {"synthesis": {"valid": False, "errors": [f"invalid criteria: {invalid}"]}}
        index = build_evidence_index(state["report_input"])
        results = {c: criteria[c]["result"] for c in ALL_CRITERIA}
        try:
            synthesis = await retry_transient(lambda: synthesize(failover_manager, state["session_id"], results, index))
        except Exception as e:
            synthesis = {"valid": False, "errors": [str(e)]}
        if not synthesis["valid"]:
            logger.warning("agentic_report_fallback", reason="invalid_synthesis")
        return {"synthesis": synthesis}

    def after_synthesis(state: ReportState) -> str:
        return "assemble_agentic" if state["synthesis"].get("valid") else "generate"

    def assemble_agentic(state: ReportState) -> dict:
        report_input = state["report_input"]
        index = build_evidence_index(report_input)
        results = {c: state["criteria"][c]["result"] for c in ALL_CRITERIA}
        report = assemble_report(report_input.session_id, results, state["synthesis"]["result"], index)
        return {"report": generator.enrich(report, report_input)}

    graph = StateGraph(ReportState)
    graph.add_node("load_inputs", load_inputs)
    graph.add_node("generate", generate)
    graph.add_node("assemble", assemble)
    graph.add_node("persist", persist)
    graph.add_edge(START, "load_inputs")
    if agentic:
        graph.add_node("score_criterion", score_criterion_node, input_schema=CriterionTask)
        graph.add_node("synthesize", synthesize_node)
        graph.add_node("assemble_agentic", assemble_agentic)
        graph.add_conditional_edges("load_inputs", fan_out_criteria, ["score_criterion"])
        graph.add_edge("score_criterion", "synthesize")
        graph.add_conditional_edges("synthesize", after_synthesis, ["assemble_agentic", "generate"])
        graph.add_edge("assemble_agentic", "persist")
    else:
        graph.add_edge("load_inputs", "generate")
    graph.add_edge("generate", "assemble")
    graph.add_edge("assemble", "persist")
    graph.add_edge("persist", END)
    return graph


class ReportRunner:
    """Runs ReportGraph for one report at a time per report id, across processes.

    A Postgres advisory lock per report ensures that of several workers (or a
    request and a startup resume) only one runs a given report; the lock is
    freed automatically if its holder dies.
    """

    def __init__(
        self,
        failover_manager: FailoverManager,
        session_factory: async_sessionmaker[AsyncSession] | Callable[[], Any],
        checkpointer: Checkpointer,
    ) -> None:
        self._session_factory = session_factory
        self._checkpointer = checkpointer
        self._graph = build_report_graph(failover_manager, session_factory)
        self._compiled: CompiledStateGraph | None = None

    def _app(self) -> CompiledStateGraph:
        if self._compiled is None:
            self._compiled = self._graph.compile(checkpointer=self._checkpointer.saver)
        return self._compiled

    async def run(self, report_id: str, session_id: str, snapshots: dict) -> None:
        """Generate a report from scratch. Never raises; failures mark the report failed."""
        initial: ReportState = {"report_id": report_id, "session_id": session_id, "snapshots": snapshots}
        await self._execute(report_id, initial)

    async def resume(self, report_id: str) -> bool:
        """Continue an interrupted run from its last checkpoint. Returns False if there is nothing to resume."""
        state = await self._app().aget_state(_config(report_id))
        if not state.next:
            return False
        await self._execute(report_id, None)
        return True

    async def resume_pending(self) -> list[str]:
        """Resume every pending report that has an interrupted run. Called at startup."""
        async with self._session_factory() as db:
            pending = (await db.execute(select(Report.id).where(Report.status == "pending"))).scalars().all()
        resumed: list[str] = []
        for rid in pending:
            report_id = str(rid)
            try:
                if await self.resume(report_id):
                    resumed.append(report_id)
            except Exception:
                logger.exception("report_resume_failed", report_id=report_id)
        if resumed:
            logger.info("reports_resumed", count=len(resumed), report_ids=resumed)
        return resumed

    async def _execute(self, report_id: str, graph_input: ReportState | None) -> None:
        async with self._checkpointer.try_advisory_lock(thread_id_for(report_id)) as locked:
            if not locked:
                logger.info("report_run_skipped_locked", report_id=report_id)
                return
            try:
                await self._app().ainvoke(graph_input, _config(report_id), durability="sync")
            except Exception as e:
                logger.error("report_generation_failed", report_id=report_id, error=str(e))
                await self._mark_failed(report_id)
            await self._checkpointer.delete_threads(thread_id_for(report_id))

    async def _mark_failed(self, report_id: str) -> None:
        try:
            async with self._session_factory() as db:
                report = await ReportRepository(db).get_by_id(uuid.UUID(report_id))
                if report is not None:
                    report.status = "failed"
                await db.commit()
        except Exception:
            logger.exception("report_mark_failed_error", report_id=report_id)
