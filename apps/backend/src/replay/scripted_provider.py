"""Input-sensitive deterministic LLM provider used as a parity oracle.

MockProvider returns one canned string per TaskType, so any two call sites
sharing a TaskType (analysis, contradiction check and fact extraction all use
REAL_TIME_ANALYSIS) get identical output regardless of what context they sent.
A replay recorded against it cannot tell whether a reimplementation assembled
the right prompt.

ScriptedProvider instead:
  - classifies each call by its system prompt, returning the JSON shape that
    call site actually parses, so every downstream branch (red flags, hint
    signals, fact merging, contradictions) is exercised;
  - derives every field from a hash of the prompt, so a change in assembled
    context changes the output;
  - records every call into the active ``LLMCallRecorder`` for golden diffs;
  - optionally takes time per call site (``behavior(latency_ms=...)``), so
    concurrent background work interleaves the way it does against a real
    provider rather than completing before the caller's next await;
  - fails a call about an utterance marked ``[scripted-fail:<kind>]``, to
    drive error paths from a scenario. Only the prompt's last line counts —
    the utterance the call is about (latest utterance, newest buffered one,
    the utterance being mined) — so the marker does not also fail every later
    call whose context merely quotes it.
"""

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import AsyncIterator, Iterator
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from src.llm.interface import (
    AnalysisContext,
    ILLMProvider,
    LLMConfig,
    ProviderHealthStatus,
    ProviderStatus,
    StructuredOutput,
    TaskType,
)
from src.llm.providers.mock import MOCK_RESPONSES

_SCORES = ("excellent", "good", "adequate", "weak", "poor")
_LEVELS = ("high", "medium", "low")
_HINT_SIGNALS = ("none", "nudge", "guide", "direct")
_DIRECTNESS = ("subtle", "moderate", "direct")


@dataclass
class RecordedCall:
    seq: int
    kind: str
    task_type: str
    system_prompt_sha: str
    user_content: str
    max_tokens: int
    temperature: float
    response_format: str | None


@dataclass
class LLMCallRecorder:
    calls: list[RecordedCall] = field(default_factory=list)

    def record(self, kind: str, context: AnalysisContext, config: LLMConfig) -> None:
        self.calls.append(
            RecordedCall(
                seq=len(self.calls),
                kind=kind,
                task_type=context.task_type.value,
                system_prompt_sha=_sha(context.system_prompt)[:12],
                user_content=context.user_content,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                response_format=config.response_format,
            )
        )

    def to_list(self) -> list[dict]:
        return [asdict(c) for c in self.calls]


_active_recorder: ContextVar[LLMCallRecorder | None] = ContextVar("scripted_llm_recorder", default=None)
_active_latency: ContextVar[dict[str, float] | None] = ContextVar("scripted_llm_latency", default=None)


class ScriptedFailureError(RuntimeError):
    """Raised for a call whose prompt carries a ``[scripted-fail:<kind>]`` marker."""


@contextlib.contextmanager
def recording(recorder: LLMCallRecorder) -> Iterator[LLMCallRecorder]:
    """Route ScriptedProvider calls made in this context (and tasks it spawns) to ``recorder``."""
    token = _active_recorder.set(recorder)
    try:
        yield recorder
    finally:
        _active_recorder.reset(token)


@contextlib.contextmanager
def behavior(latency_ms: dict[str, float]) -> Iterator[None]:
    """Make calls of each kind (see ``classify``) take ``latency_ms[kind]`` in this context."""
    token = _active_latency.set(latency_ms)
    try:
        yield
    finally:
        _active_latency.reset(token)


def fail_marker(kind: str) -> str:
    return f"[scripted-fail:{kind}]"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _pick(seq: tuple[str, ...], h: int, salt: int = 0) -> str:
    return seq[(h >> salt) % len(seq)]


def classify(context: AnalysisContext) -> str:
    """Name the call site that issued this request, from its system prompt."""
    sp = context.system_prompt
    if context.task_type == TaskType.SUMMARY_COMPRESSION:
        return "summary"
    if sp.startswith("Detect contradictions"):
        return "contradiction"
    if sp.startswith("Extract factual claims"):
        return "fact_extraction"
    if context.task_type == TaskType.REAL_TIME_ANALYSIS:
        return "utterance_analysis"
    if context.task_type == TaskType.CODE_RATING:
        return "code_rating"
    if context.task_type == TaskType.HINT_GENERATION:
        return "hint"
    return context.task_type.value


def _utterance_analysis(h: int) -> dict:
    quality = 1 + h % 5
    return {
        "answer_quality": quality,
        "on_track": quality >= 3,
        "insight": f"scripted-insight-{h % 10_000:04d}",
        "red_flags": [f"scripted-red-flag-{h % 97}"] if h % 3 == 0 else [],
        "suggestion_for_interviewer": f"scripted-suggestion-{h % 1_000:03d}",
        "hint_signal": _pick(_HINT_SIGNALS, h, 4),
        "dimensions": {
            "relevance": {
                "score": _pick(_SCORES, h, 1),
                "on_topic": h % 7 != 0,
                "justification": "scripted",
            },
            "behavioral_signal": {
                "score": _pick(_SCORES, h, 2),
                "signals_detected": ["structured_thinking"] if h % 2 else [],
                "flags": ["vagueness"] if h % 5 == 0 else [],
                "justification": "scripted",
            },
            "contextual_consistency": {
                "score": _pick(_SCORES, h, 3),
                "consistent": h % 4 != 0,
                "contradictions": [],
                "justification": "scripted",
            },
            "hint_reception": {
                "score": _pick(_SCORES, h, 5),
                "acted_on_hint": h % 2 == 0,
                "incorporation_level": _pick(("full", "partial", "none"), h, 6),
                "justification": "scripted",
            },
            "depth_completeness": {
                "score": _pick(_SCORES, h, 7),
                "key_points_covered": ["scripted-covered"],
                "key_points_missed": ["scripted-missed"] if h % 2 else [],
                "justification": "scripted",
            },
            "communication_quality": {
                "score": _pick(_SCORES, h, 8),
                "clarity": _pick(_LEVELS, h, 9),
                "structure": _pick(_LEVELS, h, 10),
                "confidence": _pick(_LEVELS, h, 11),
                "justification": "scripted",
            },
        },
    }


_UTTERANCE_RE = re.compile(r"Utterance \(by [^)]*\): (.*)", re.DOTALL)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _fact_extraction(user_content: str) -> dict:
    """Extract facts deterministically from the utterance text itself.

    Content-derived (not hash-derived) so the resulting fact store feeds real
    claims into the contradiction service's rule-based and tech-overlap paths.
    """
    m = _UTTERANCE_RE.search(user_content)
    text = m.group(1).strip() if m else ""
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s]
    experience = [s for s in sentences if re.search(r"\b(experience|led|built|worked|used|years?)\b", s, re.I)]
    numbers = [s for s in sentences if re.search(r"\d", s)]
    out: dict = {}
    if experience:
        out["claimed_experience"] = experience
    if numbers:
        out["key_numbers"] = numbers
    if re.search(r"\b(I think|I prefer|I would)\b", text, re.I):
        out["technical_positions"] = [s for s in sentences if re.search(r"\b(think|prefer|would)\b", s, re.I)]
    return out


def _contradiction(h: int) -> dict:
    return {
        "is_contradiction": h % 2 == 0,
        "confidence": 0.9 if h % 2 == 0 else 0.3,
        "explanation": f"scripted-contradiction-{h % 1_000:03d}",
        "suggested_probe": f"scripted-probe-{h % 1_000:03d}",
    }


def _code_rating(h: int) -> dict:
    return {
        "approach_match": _pick(("optimal", "brute_force", "partial_optimal", "novel", "unclear"), h),
        "approach_name": f"scripted-approach-{h % 100:02d}",
        "correctness": _pick(("correct", "mostly_correct", "partially_correct", "incorrect"), h, 3),
        "correctness_issues": [],
        "complexity_actual": {"time": "O(n)", "space": "O(n)"},
        "complexity_expected": {"time": "O(n)", "space": "O(n)"},
        "complexity_match": h % 2 == 0,
        "pitfalls_hit": [],
        "pitfalls_avoided": [],
        "strengths": ["scripted-strength"],
        "improvement_areas": [],
        "overall_score": 1 + h % 5,
        "summary": f"scripted-code-summary-{h % 1_000:03d}",
    }


def _hint(h: int) -> dict:
    # Union of the runtime-hint shape and the plan-service hint-set shape.
    return {
        "hint_text": f"scripted-hint-{h % 10_000:04d}",
        "directness": _pick(_DIRECTNESS, h, 2),
        "hints": [{"level": lvl, "text": f"scripted-hint-L{lvl}-{h % 1_000:03d}"} for lvl in (1, 2, 3)],
    }


def respond(kind: str, context: AnalysisContext) -> str:
    h = int(_sha(context.system_prompt + "\x00" + context.user_content)[:16], 16)
    if kind == "summary":
        return f"scripted-summary-{h % 1_000_000:06d}"
    if kind == "utterance_analysis":
        return json.dumps(_utterance_analysis(h))
    if kind == "fact_extraction":
        return json.dumps(_fact_extraction(context.user_content))
    if kind == "contradiction":
        return json.dumps(_contradiction(h))
    if kind == "code_rating":
        return json.dumps(_code_rating(h))
    if kind == "hint":
        return json.dumps(_hint(h))
    return MOCK_RESPONSES.get(context.task_type, '{"result": "scripted"}')


class ScriptedProvider(ILLMProvider):
    def __init__(self, model: str = "scripted-v1") -> None:
        self.model = model

    @property
    def provider_name(self) -> str:
        return "scripted"

    async def complete(self, prompt: str, config: LLMConfig) -> AsyncIterator[str]:
        yield f"scripted-completion-{_sha(prompt)[:8]}"

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        kind = classify(context)
        recorder = _active_recorder.get()
        if recorder is not None:
            recorder.record(kind, context, config)

        latency = (_active_latency.get() or {}).get(kind, 0.0)
        if latency:
            await asyncio.sleep(latency / 1000)
        if fail_marker(kind) in context.user_content.rsplit("\n", 1)[-1]:
            raise ScriptedFailureError(f"scripted failure for {kind}")

        content = respond(kind, context)
        parsed = None
        if config.response_format == "json":
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(content)

        return StructuredOutput(
            content=content,
            parsed=parsed,
            model=self.model,
            provider="scripted",
            input_tokens=len(context.user_content) // 4,
            output_tokens=len(content) // 4,
            latency_ms=0.0,
        )

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            provider="scripted",
            status=ProviderHealthStatus.HEALTHY,
            last_check_at=datetime.now(UTC).isoformat(),
            average_latency_ms=0.0,
        )
