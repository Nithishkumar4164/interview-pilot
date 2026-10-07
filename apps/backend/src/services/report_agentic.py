"""Agentic report scoring: per-criterion evidence loops and one synthesis.

The single-call report shows the model the first and last ~3000 characters
of the transcript and asks it to guess key-moment timestamps. Here:

- ``EvidenceIndex`` numbers every final transcript entry (T1..Tn) with its
  elapsed time from the first entry, and holds the analyses, code timeline,
  contradictions and hints.
- Each criterion is scored by its own tool loop (TaskType.REPORT_CRITERION,
  Sonnet) with read-only tools over that index. Every score must cite
  transcript entries by id with a quote taken from the entry; citations are
  validated, and one repair turn is allowed.
- One synthesis call (TaskType.REPORT_GENERATION, Opus) sees all seven
  results and writes the recommendation, key moments (also by entry id, so
  their timestamps are real), strengths and growth areas.

The output is the same GeneratedReport the single-call path produces.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, ChatMessage, LLMConfig, TaskType, ToolCall, ToolSpec
from src.llm.tools import ToolLoopResult, run_tool_loop
from src.schemas.report import CriterionScoreSchema, KeyMomentSchema, Recommendation
from src.services.memory_service import FactStore
from src.services.report_generator import ALL_CRITERIA, REPORT_MAX_TOKENS, GeneratedReport, ReportInput
from src.utils.logging import get_logger

logger = get_logger("report_agentic")

CRITERION_GUIDES = {
    "problem_solving": "How the candidate understood the problem, found and refined an approach, and reasoned about correctness and complexity.",
    "code_quality": "Correctness, structure, naming, edge-case handling and complexity of the code they wrote (see the code timeline).",
    "communication": "Clarity and structure of their explanations, how they thought aloud, and how they handled questions.",
    "hint_responsiveness": "How they used hints: whether they needed them, understood them and built on them.",
    "system_design": "Architecture, trade-offs and scalability reasoning. If no design question was asked, set applicable to false and score 3.",
    "resume_verification": "Whether claimed experience held up under questioning; contradictions between their statements.",
    "behavioral": "Collaboration signals, ownership, conflict handling and professionalism.",
}

VALID_TRENDS = {"improving", "stable", "declining"}
VALID_MOMENT_TYPES = {
    "breakthrough",
    "struggle",
    "hint",
    "contradiction",
    "code_milestone",
    "strong_answer",
    "weak_answer",
}

CRITERION_SYSTEM_PROMPT = """You score one criterion of a technical interview from its evidence.

Use the tools to read what actually happened: search the transcript, read windows of it, and look at the analyses, code timeline, contradictions and hints as needed. Do not guess.

When done, reply with JSON only:
{
  "criterion": "<the criterion you were given>",
  "applicable": true/false,
  "score": 1-5,
  "trend": "improving|stable|declining",
  "citations": [
    {"entry_id": "T<number>", "quote": "<words copied exactly from that transcript entry>", "why": "<what it shows>"}
  ]
}

Rules:
- Cite 1-4 transcript entries. entry_id must be an id the tools returned; quote must be copied from that entry's text.
- Score scale: 1=Poor, 2=Below average, 3=Average, 4=Good, 5=Excellent.
- Judge against the target level."""

SYNTHESIS_SYSTEM_PROMPT = """You write the final assessment of a technical interview from per-criterion scores that are already grounded in the transcript.

Reply with JSON only:
{
  "recommendation": "strong_hire|hire|lean_hire|lean_no_hire|no_hire",
  "confidence_level": 0.0-1.0,
  "key_moments": [
    {"entry_id": "T<number>", "title": "...", "description": "...", "sentiment": "positive|negative|neutral",
     "impact_on_assessment": "...", "type": "breakthrough|struggle|hint|contradiction|code_milestone|strong_answer|weak_answer"}
  ],
  "strengths": ["3-5 strengths with evidence"],
  "areas_for_growth": ["2-4 areas, constructively framed"],
  "level_comparison": "How the candidate compares to the target level"
}

Rules:
- 3-8 key moments; entry_id must be one of the entry ids cited in the criterion results.
- Stay consistent with the criterion scores; do not invent evidence."""


# ── Evidence index ─────────────────────────────────────────────────────────


def _elapsed(ts: str, start: datetime | None) -> str:
    try:
        at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "00:00:00"
    if start is None:
        return "00:00:00"
    seconds = max(0, int((at - start).total_seconds()))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


@dataclass
class EvidenceIndex:
    entries: list[dict[str, Any]]  # id, elapsed, speaker, text
    analyses: list[dict[str, Any]]
    code_timeline: list[dict[str, Any]]
    contradictions: list[dict[str, Any]]
    hints: list[dict[str, Any]]
    hint_metrics: dict[str, Any]
    facts_text: str
    summary: str
    target_level: str
    duration_minutes: float
    questions: list[str] = field(default_factory=list)

    def entry(self, entry_id: str) -> dict[str, Any] | None:
        m = re.fullmatch(r"T(\d+)", entry_id.strip())
        if not m:
            return None
        n = int(m.group(1))
        return self.entries[n - 1] if 1 <= n <= len(self.entries) else None

    def overview(self) -> str:
        parts = [
            f"Target level: {self.target_level}",
            f"Duration: {self.duration_minutes:.0f} minutes",
            f"Transcript: {len(self.entries)} entries (T1-T{len(self.entries)})"
            if self.entries
            else "Transcript: empty",
        ]
        if self.questions:
            parts.append("Questions asked: " + "; ".join(self.questions))
        if self.summary:
            parts.append(f"Summary: {self.summary}")
        if self.facts_text:
            parts.append(f"Facts: {self.facts_text}")
        return "\n".join(parts)


def _plan_questions(plan_data: dict | None) -> list[str]:
    out: list[str] = []
    for phase in (plan_data or {}).get("phases", []) or []:
        for q in phase.get("questions", []) or []:
            text = q.get("text") if isinstance(q, dict) else None
            if text:
                out.append(str(text)[:120])
    return out


def _facts_text(fact_store: Any) -> str:
    if isinstance(fact_store, FactStore):
        return fact_store.to_text()
    if isinstance(fact_store, dict):
        return FactStore.from_dict(fact_store).to_text()
    return str(fact_store or "")


def build_evidence_index(report_input: ReportInput) -> EvidenceIndex:
    transcript = [t for t in (report_input.full_transcript or []) if str(t.get("text", "")).strip()]
    start = None
    for t in transcript:
        try:
            start = datetime.fromisoformat(str(t.get("timestamp", "")).replace("Z", "+00:00"))
            break
        except ValueError:
            continue
    entries = [
        {
            "id": f"T{i}",
            "elapsed": _elapsed(str(t.get("timestamp", "")), start),
            "speaker": t.get("speaker_role", "unknown"),
            "text": str(t.get("text", "")),
        }
        for i, t in enumerate(transcript, start=1)
    ]
    analyses = [
        a if isinstance(a, dict) else a.model_dump()
        for a in (report_input.all_analysis_events or report_input.analysis_results or [])
    ]
    metrics = report_input.hint_metrics
    return EvidenceIndex(
        entries=entries,
        analyses=[
            {k: a.get(k) for k in ("answer_quality", "on_track", "insight", "red_flags", "hint_signal")}
            for a in analyses
        ],
        code_timeline=list(report_input.code_snapshots or []),
        contradictions=[c if isinstance(c, dict) else c.__dict__ for c in (report_input.contradictions or [])],
        hints=list(report_input.hint_events_db or []),
        hint_metrics=metrics if isinstance(metrics, dict) else metrics.model_dump(),
        facts_text=_facts_text(report_input.fact_store),
        summary=report_input.transcript_summary or "",
        target_level=report_input.target_level,
        duration_minutes=report_input.duration_minutes,
        questions=_plan_questions(report_input.plan_data),
    )


# ── Tools ──────────────────────────────────────────────────────────────────

_SPEAKER = {"type": "string", "enum": ["candidate", "interviewer", "any"]}

EVIDENCE_TOOLS = [
    ToolSpec(
        name="search_transcript",
        description="Find transcript entries containing words of a query. Returns up to 8 entries as 'T<n> [elapsed] speaker: text'.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "speaker": _SPEAKER},
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="read_transcript",
        description="Read consecutive transcript entries from one id to another (at most 15), e.g. T10 to T20.",
        input_schema={
            "type": "object",
            "properties": {"from_id": {"type": "string"}, "to_id": {"type": "string"}},
            "required": ["from_id", "to_id"],
        },
    ),
    ToolSpec(
        name="get_analyses",
        description="The real-time analyses of the candidate's answers (quality 1-5, on track, insight, red flags), in order.",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="get_code_timeline",
        description="Snapshots of the candidate's code over time: progress state and detected approach.",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="get_contradictions",
        description="Contradictions detected between the candidate's statements.",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="get_hints",
        description="Hints given during the interview and how the candidate responded, with summary metrics.",
        input_schema={"type": "object", "properties": {}},
    ),
]


def _fmt(entry: dict[str, Any]) -> str:
    return f"{entry['id']} [{entry['elapsed']}] {entry['speaker']}: {entry['text']}"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def make_executor(index: EvidenceIndex):
    async def execute(call: ToolCall) -> str:
        args = call.arguments
        if call.name == "search_transcript":
            wanted = _words(str(args.get("query", "")))
            speaker = args.get("speaker", "any")
            scored = []
            for e in index.entries:
                if speaker in ("candidate", "interviewer") and e["speaker"] != speaker:
                    continue
                hits = len(wanted & _words(e["text"]))
                if hits:
                    scored.append((-hits, int(e["id"][1:]), e))
            found = [_fmt(e) for _, _, e in sorted(scored)[:8]]
            return "\n".join(found) if found else "No matching entries."
        if call.name == "read_transcript":
            lo, hi = index.entry(str(args.get("from_id", ""))), index.entry(str(args.get("to_id", "")))
            if lo is None or hi is None:
                return f"Unknown entry id; valid ids are T1-T{len(index.entries)}."
            a, b = sorted((int(lo["id"][1:]), int(hi["id"][1:])))
            return "\n".join(_fmt(e) for e in index.entries[a - 1 : min(b, a + 14)])
        if call.name == "get_analyses":
            return json.dumps(index.analyses[:60]) if index.analyses else "No analyses."
        if call.name == "get_code_timeline":
            return json.dumps(index.code_timeline[-30:]) if index.code_timeline else "No code was written."
        if call.name == "get_contradictions":
            return json.dumps(index.contradictions) if index.contradictions else "No contradictions detected."
        if call.name == "get_hints":
            return json.dumps({"hints": index.hints, "metrics": index.hint_metrics}, default=str)
        return f"Unknown tool {call.name!r}."

    return execute


# ── Validation ─────────────────────────────────────────────────────────────


def _quote_matches(quote: str, text: str) -> bool:
    """The quote is taken from the entry (whitespace/case-insensitive; minor edits tolerated)."""
    q, t = " ".join(quote.lower().split()), " ".join(text.lower().split())
    if q and q in t:
        return True
    qw = _words(quote)
    return bool(qw) and len(qw & _words(text)) / len(qw) >= 0.8


def validate_criterion(data: Any, criterion: str, index: EvidenceIndex) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["reply was not a JSON object"]
    if data.get("criterion") != criterion:
        errors.append(f'criterion must be "{criterion}"')
    score = data.get("score")
    if not isinstance(score, int) or not 1 <= score <= 5:
        errors.append("score must be an integer 1-5")
    citations = data.get("citations")
    if not isinstance(citations, list) or not citations:
        errors.append("cite at least one transcript entry")
        return errors
    for c in citations:
        entry = index.entry(str((c or {}).get("entry_id", ""))) if isinstance(c, dict) else None
        if entry is None:
            errors.append(f"citation {c!r} does not name an existing entry id")
        elif not _quote_matches(str(c.get("quote", "")), entry["text"]):
            errors.append(f"quote for {entry['id']} is not from that entry")
    return errors


def validate_synthesis(data: Any, cited: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["reply was not a JSON object"]
    try:
        Recommendation(data.get("recommendation"))
    except ValueError:
        errors.append("recommendation must be one of strong_hire, hire, lean_hire, lean_no_hire, no_hire")
    moments = data.get("key_moments")
    if not isinstance(moments, list) or not moments:
        errors.append("give 3-8 key moments")
    else:
        for m in moments:
            if not isinstance(m, dict) or m.get("entry_id") not in cited:
                errors.append(f"key moment {m!r} must use an entry id cited in the criterion results")
    return errors


def _json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", stripped, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


# ── Cost ───────────────────────────────────────────────────────────────────

# Relative prices per token kind (Anthropic's published pattern): output 5x input,
# cache reads 0.1x, cache writes 1.25x.
OUTPUT_PRICE, CACHE_READ_PRICE, CACHE_WRITE_PRICE = 5.0, 0.1, 1.25


def cost_units(
    task_type: TaskType, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0
) -> float:
    """Price of a call in Opus-input-token units (Sonnet tasks scaled by REPORT_COST_SONNET_RATIO)."""
    from src.config import settings

    model = 1.0 if task_type == TaskType.REPORT_GENERATION else settings.report_cost_sonnet_ratio
    uncached = input_tokens - cache_read - cache_write
    return model * (
        uncached + CACHE_READ_PRICE * cache_read + CACHE_WRITE_PRICE * cache_write + OUTPUT_PRICE * output_tokens
    )


# ── Steps ──────────────────────────────────────────────────────────────────


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add_loop(self, loop: ToolLoopResult) -> None:
        self.input_tokens += loop.input_tokens
        self.output_tokens += loop.output_tokens
        self.calls += loop.turns


# Thinking counts toward max_tokens on current models; leave room for it.
CRITERION_CONFIG = LLMConfig(max_tokens=6000, temperature=0.2)
CRITERION_MAX_STEPS = 4


async def score_criterion(
    failover: FailoverManager, session_id: str, criterion: str, index: EvidenceIndex
) -> dict[str, Any]:
    """{"criterion", "valid", "result" (validated JSON) | "errors", "usage"}. LLM errors propagate."""
    usage = Usage()
    user = f"{index.overview()}\n\nCriterion: {criterion}\nWhat it covers: {CRITERION_GUIDES[criterion]}"
    execute = make_executor(index)
    loop = await run_tool_loop(
        failover,
        TaskType.REPORT_CRITERION,
        CRITERION_SYSTEM_PROMPT,
        user,
        EVIDENCE_TOOLS,
        execute,
        CRITERION_CONFIG,
        session_id=session_id,
        max_steps=CRITERION_MAX_STEPS,
        final_instruction="Stop using tools. Reply now with the JSON object only, as specified.",
    )
    usage.add_loop(loop)
    data = _json(loop.text)
    errors = validate_criterion(data, criterion, index)
    if errors:
        # One repair turn, continuing the same conversation.
        repair_messages = [
            *loop.messages,
            ChatMessage(
                role="user",
                content="Fix these problems and reply with the corrected JSON only:\n- " + "\n- ".join(errors),
            ),
        ]
        turn = await failover.invoke_with_tools(
            TaskType.REPORT_CRITERION,
            CRITERION_SYSTEM_PROMPT,
            repair_messages,
            EVIDENCE_TOOLS,
            CRITERION_CONFIG,
            session_id=session_id,
            tool_choice="none",
        )
        usage.input_tokens += turn.input_tokens
        usage.output_tokens += turn.output_tokens
        usage.calls += 1
        data = _json(turn.text)
        errors = validate_criterion(data, criterion, index)
    out: dict[str, Any] = {"criterion": criterion, "usage": usage.__dict__}
    if errors:
        logger.warning("criterion_invalid", criterion=criterion, errors=errors[:5])
        return {**out, "valid": False, "errors": errors}
    return {**out, "valid": True, "result": data}


def _criterion_digest(result: dict[str, Any], index: EvidenceIndex) -> str:
    lines = [
        f"{result['criterion']}: score {result['score']}, trend {result.get('trend', 'stable')}"
        + ("" if result.get("applicable", True) else " (not applicable)")
    ]
    for c in result["citations"]:
        entry = index.entry(c["entry_id"])
        if entry:
            lines.append(f'  - {entry["id"]} [{entry["elapsed"]}] "{c.get("quote", "")}" - {c.get("why", "")}')
    return "\n".join(lines)


async def synthesize(
    failover: FailoverManager, session_id: str, criteria: dict[str, dict[str, Any]], index: EvidenceIndex
) -> dict[str, Any]:
    """{"valid", "result" | "errors", "usage"}. LLM errors propagate."""
    usage = Usage()
    cited = {c["entry_id"] for r in criteria.values() for c in r["citations"]}
    extra = []
    if index.contradictions:
        extra.append("Contradictions: " + json.dumps(index.contradictions))
    if index.hint_metrics:
        extra.append("Hint metrics: " + json.dumps(index.hint_metrics, default=str))
    user = "\n\n".join(
        [
            index.overview(),
            "Criterion results:\n" + "\n".join(_criterion_digest(criteria[c], index) for c in ALL_CRITERIA),
            *extra,
        ]
    )
    config = LLMConfig(max_tokens=REPORT_MAX_TOKENS, temperature=0.2, response_format="json")
    data: Any = None
    errors: list[str] = []
    for attempt in range(2):
        content = (
            user if attempt == 0 else user + "\n\nYour previous reply had problems; fix them:\n- " + "\n- ".join(errors)
        )
        result = await failover.analyze(
            AnalysisContext(
                system_prompt=SYNTHESIS_SYSTEM_PROMPT, user_content=content, task_type=TaskType.REPORT_GENERATION
            ),
            config,
            session_id=session_id,
        )
        usage.input_tokens += result.input_tokens
        usage.output_tokens += result.output_tokens
        usage.calls += 1
        data = result.parsed if result.parsed is not None else _json(result.content)
        errors = validate_synthesis(data, cited)
        if not errors:
            return {"valid": True, "result": data, "usage": usage.__dict__}
    logger.warning("synthesis_invalid", errors=errors[:5])
    return {"valid": False, "errors": errors, "usage": usage.__dict__}


def assemble_report(
    session_id: str, criteria: dict[str, dict[str, Any]], synthesis: dict[str, Any], index: EvidenceIndex
) -> GeneratedReport:
    scores = []
    for name in ALL_CRITERIA:
        r = criteria[name]
        evidence = []
        for c in r["citations"]:
            entry = index.entry(c["entry_id"])
            if entry:
                evidence.append(f'[{entry["elapsed"]}] "{c.get("quote", "")}" - {c.get("why", "")}'.rstrip(" -"))
        trend = r.get("trend") if r.get("trend") in VALID_TRENDS else "stable"
        scores.append(CriterionScoreSchema(criterion=name, score=int(r["score"]), evidence=evidence, trend=trend))
    moments = []
    for m in synthesis["key_moments"]:
        entry = index.entry(m["entry_id"])
        moments.append(
            KeyMomentSchema(
                timestamp=entry["elapsed"] if entry else "00:00:00",
                title=str(m.get("title", "")),
                description=str(m.get("description", "")),
                sentiment=str(m.get("sentiment", "neutral")),
                impact_on_assessment=str(m.get("impact_on_assessment", "")),
                type=m.get("type") if m.get("type") in VALID_MOMENT_TYPES else None,
            )
        )
    return GeneratedReport(
        session_id=session_id,
        recommendation=Recommendation(synthesis["recommendation"]),
        confidence_level=min(1.0, max(0.0, float(synthesis.get("confidence_level", 0.5)))),
        scores=scores,
        key_moments=moments,
        strengths=[str(s) for s in synthesis.get("strengths", [])],
        areas_for_growth=[str(s) for s in synthesis.get("areas_for_growth", [])],
        level_comparison=str(synthesis.get("level_comparison", "")),
    )
