"""PlanGraph — interview plan generation with parallel fan-out.

    calibrate ─► Send(select_questions) × phases ─► plan_hints
              ─► Send(generate_hints) × technical questions ─► assemble

The previous implementation ran every phase's question generation and every
question's hint generation one after another. Here phases run in parallel,
then all hint requests run in parallel, bounded by ``max_concurrency``.
The result — including phase order, start minutes and time rebalancing — is
identical to the sequential version (tests/test_plan_golden.py).

No checkpointer: plan generation belongs to a single HTTP request, so a
resumed run after a crash would have no one to deliver the plan to.
"""

from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from src.config import settings
from src.schemas.session import InterviewLevel
from src.services.calibration_service import (
    CalibrationProfile,
    PhaseAllocation,
    build_phases_from_focus_areas,
    compute_time_allocation,
    get_calibration_profile,
)
from src.services.plan_service import GeneratedPlan, PlanPhaseData, PlanService, QuestionData
from src.utils.logging import get_logger

logger = get_logger("graphs.plan")

# Categories whose questions get pre-computed hints when they arrive with fewer than 3.
HINTED_CATEGORIES = ("coding", "system_design", "aptitude")


def _merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class PhaseSlot(TypedDict):
    index: int
    allocation: PhaseAllocation
    minutes: int


class PlanState(TypedDict, total=False):
    session_id: str
    level: InterviewLevel
    duration_minutes: int
    focus_areas: list[str]
    resume_data: dict | None
    bank_questions: list[dict] | None

    profile: CalibrationProfile
    intro_minutes: int
    wrap_up_minutes: int
    buffer_minutes: int
    slots: list[PhaseSlot]
    # Written by parallel branches, keyed so order is restored in assemble.
    phase_questions: Annotated[dict[int, list[QuestionData]], _merge]
    hints: Annotated[dict[tuple[int, int], list[dict]], _merge]
    plan: GeneratedPlan


class SelectTask(TypedDict):
    slot: PhaseSlot
    profile: CalibrationProfile
    focus_areas: list[str]
    resume_data: dict | None
    bank_questions: list[dict] | None


class HintTask(TypedDict):
    key: tuple[int, int]
    question: QuestionData


def build_plan_graph(service: PlanService) -> CompiledStateGraph:
    def calibrate(state: PlanState) -> dict:
        profile = get_calibration_profile(state["level"])
        focus_phases = build_phases_from_focus_areas(state["focus_areas"])
        time_alloc = compute_time_allocation(profile, state["duration_minutes"], phases_override=focus_phases)
        slots: list[PhaseSlot] = [
            {"index": i, "allocation": alloc, "minutes": minutes}
            for i, alloc in enumerate(focus_phases)
            if (minutes := time_alloc.phase_minutes.get(alloc.phase, 0)) > 0
        ]
        logger.info(
            "plan_generation_started",
            session_id=state["session_id"],
            level=state["level"].value,
            duration=state["duration_minutes"],
            phases=list(time_alloc.phase_minutes.keys()),
        )
        return {
            "profile": profile,
            "intro_minutes": time_alloc.intro_minutes,
            "wrap_up_minutes": time_alloc.wrap_up_minutes,
            "buffer_minutes": time_alloc.buffer_minutes,
            "slots": slots,
        }

    def fan_out_phases(state: PlanState) -> list[Send] | str:
        if not state["slots"]:
            return "assemble"
        return [
            Send(
                "select_questions",
                SelectTask(
                    slot=slot,
                    profile=state["profile"],
                    focus_areas=state["focus_areas"],
                    resume_data=state.get("resume_data"),
                    bank_questions=state.get("bank_questions"),
                ),
            )
            for slot in state["slots"]
        ]

    async def select_questions(state: SelectTask) -> dict:
        slot = state["slot"]
        questions = await service.select_questions_for_phase(
            phase_alloc=slot["allocation"],
            phase_minutes=slot["minutes"],
            profile=state["profile"],
            focus_areas=state["focus_areas"],
            resume_data=state["resume_data"],
            bank_questions=state["bank_questions"],
        )
        return {"phase_questions": {slot["index"]: questions}}

    def plan_hints(state: PlanState) -> dict:
        return {}  # fan-in point: runs once after every phase is selected

    def fan_out_hints(state: PlanState) -> list[Send] | str:
        tasks = [
            Send("generate_hints", HintTask(key=(phase_index, q_index), question=q))
            for phase_index, questions in sorted(state["phase_questions"].items())
            for q_index, q in enumerate(questions)
            if q.category in HINTED_CATEGORIES and len(q.hints) < 3
        ]
        return tasks or "assemble"

    async def generate_hints(state: HintTask) -> dict:
        return {"hints": {state["key"]: await service.generate_hints(state["question"])}}

    def assemble(state: PlanState) -> dict:
        hints = state.get("hints", {})
        phases: list[PlanPhaseData] = []
        current_minute = state["intro_minutes"]
        for slot in state["slots"]:
            questions = state["phase_questions"][slot["index"]]
            for q_index, q in enumerate(questions):
                if (slot["index"], q_index) in hints:
                    q.hints = hints[(slot["index"], q_index)]
            phases.append(
                PlanPhaseData(
                    phase=slot["allocation"].phase,
                    start_minute=current_minute,
                    duration_minutes=slot["minutes"],
                    questions=questions,
                )
            )
            current_minute += slot["minutes"]

        plan = GeneratedPlan(
            session_id=state["session_id"],
            phases=phases,
            total_minutes=state["duration_minutes"],
            intro_minutes=state["intro_minutes"],
            wrap_up_minutes=state["wrap_up_minutes"],
            buffer_minutes=state["buffer_minutes"],
            level=state["level"].value,
            focus_areas=state["focus_areas"],
        )
        logger.info(
            "plan_generation_completed",
            session_id=state["session_id"],
            total_questions=sum(len(p.questions) for p in phases),
        )
        return {"plan": plan}

    graph = StateGraph(PlanState)
    graph.add_node("calibrate", calibrate)
    graph.add_node("select_questions", select_questions, input_schema=SelectTask)
    graph.add_node("plan_hints", plan_hints)
    graph.add_node("generate_hints", generate_hints, input_schema=HintTask)
    graph.add_node("assemble", assemble)
    graph.add_edge(START, "calibrate")
    graph.add_conditional_edges("calibrate", fan_out_phases, ["select_questions", "assemble"])
    graph.add_edge("select_questions", "plan_hints")
    graph.add_conditional_edges("plan_hints", fan_out_hints, ["generate_hints", "assemble"])
    graph.add_edge("generate_hints", "assemble")
    graph.add_edge("assemble", END)
    return graph.compile()


async def run_plan_graph(service: PlanService, **request: Any) -> GeneratedPlan:
    state = await build_plan_graph(service).ainvoke(
        {"phase_questions": {}, "hints": {}, **request},
        config={"max_concurrency": settings.plan_generation_max_concurrency},
    )
    return state["plan"]
