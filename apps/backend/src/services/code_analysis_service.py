"""Code analysis service.

Analyzes candidate code snapshots during live coding interviews:
  - Language detection and normalization
  - Approach detection against pre-computed solution patterns
  - Progress tracking (empty → skeleton → partial → near_complete → complete)
  - Stuck detection (oscillation over snapshots)
  - Bug detection via LLM analysis
"""

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.utils.logging import get_logger

logger = get_logger("code_analysis_service")

# Debounce interval between snapshots (seconds)
CODE_DEBOUNCE_S = 2.0

# Snapshots kept for stuck detection
MAX_SNAPSHOT_HISTORY = 20


class ProgressState(str, Enum):
    EMPTY = "empty"
    SKELETON = "skeleton"
    PARTIAL = "partial"
    NEAR_COMPLETE = "near_complete"
    COMPLETE = "complete"


@dataclass
class CodeSnapshot:
    """A snapshot of the candidate's code at a point in time."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    code: str = ""
    language: str = "unknown"
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    line_count: int = 0
    char_count: int = 0


@dataclass
class ApproachInfo:
    """Detected approach for the current problem."""

    name: str
    confidence: float
    data_structures: list[str] = field(default_factory=list)
    expected_complexity: dict[str, str] = field(default_factory=dict)


@dataclass
class CodeAnalysisResult:
    """Result of analyzing a code snapshot."""

    progress_state: ProgressState = ProgressState.EMPTY
    detected_approach: ApproachInfo | None = None
    is_stuck: bool = False
    bugs: list[str] = field(default_factory=list)
    complexity_estimate: dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "progress_state": self.progress_state.value,
            "detected_approach": {
                "name": self.detected_approach.name,
                "confidence": self.detected_approach.confidence,
                "data_structures": self.detected_approach.data_structures,
                "expected_complexity": self.detected_approach.expected_complexity,
            }
            if self.detected_approach
            else None,
            "is_stuck": self.is_stuck,
            "bugs": self.bugs,
            "complexity_estimate": self.complexity_estimate,
            "timestamp": self.timestamp,
        }


# ── Pre-computed Solution Patterns ────────────────────────────────────

# Simple keyword-based approach detection patterns
APPROACH_PATTERNS: dict[str, list[str]] = {
    "hash_map": ["dict(", "= {}", "hashmap", "HashMap", "dictionary", "defaultdict", "Counter"],
    "two_pointer": [r"\bleft\b", r"\bright\b", "two pointer", "lo =", "hi =", "start =", "end ="],
    "sliding_window": ["window", "sliding", "expand", "shrink"],
    "binary_search": ["binary search", "bisect", "lo =", "hi =", "mid ="],
    "dfs": ["dfs", "depth first", "stack", "visited", "recursive"],
    "bfs": ["bfs", "breadth first", "queue", "deque"],
    "dynamic_programming": [
        "dp[",
        "memo",
        "tabulation",
        "cache",
        "memoize",
        "bottom_up",
        "top_down",
    ],
    "sorting": ["sort(", "sorted(", "heapq", "heap", "priority"],
    "greedy": ["greedy", "max(", "min(", "optimal"],
    "recursion": ["recursive", "base case"],
    "graph": ["graph", "adjacency", "neighbors", "visited", "edges", "nodes"],
    "tree": ["TreeNode", "root.left", "root.right", "inorder", "preorder"],
}

CODE_ANALYSIS_PROMPT = """Analyze this code written during a coding interview.

Question: {question}

Code ({language}):
```{language}
{code}
```

Previous approach detection: {approach}
Previous progress: {progress}

Respond in JSON:
{{
  "progress_state": "empty|skeleton|partial|near_complete|complete",
  "approach": {{
    "name": "approach name",
    "confidence": 0.0-1.0,
    "data_structures": ["list of data structures used"],
    "complexity": {{"time": "O(?)", "space": "O(?)"}}
  }},
  "bugs": ["list of bugs or potential issues found"],
  "suggestions": ["list of improvement suggestions"]
}}"""


# ── Pure helpers (shared by CodeAnalysisService and the LangGraph session graph) ──


def code_report_fields(latest: "CodeAnalysisResult | None") -> dict:
    """The report inputs derived from the latest code analysis (``unknown`` before any code)."""
    if latest is None:
        return {"code_progress": "unknown", "detected_approach": None}
    return {
        "code_progress": latest.progress_state.value,
        "detected_approach": latest.detected_approach.name if latest.detected_approach else None,
    }


def normalize_code(code: str) -> str:
    """Normalize code whitespace and remove trailing spaces."""
    lines = code.splitlines()
    lines = [line.rstrip() for line in lines]
    # Remove leading/trailing blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def detect_progress(code: str) -> ProgressState:
    """Detect code progress state heuristically."""
    if not code.strip():
        return ProgressState.EMPTY

    lines = [
        l for l in code.splitlines() if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("//")
    ]
    non_comment_lines = len(lines)

    if non_comment_lines <= 3:
        return ProgressState.SKELETON

    # Check for function/class definitions with bodies
    has_function = bool(re.search(r"def\s+\w+|function\s+\w+|public\s+\w+\s+\w+\s*\(", code))
    has_return = "return " in code
    has_loop = bool(re.search(r"\bfor\b|\bwhile\b", code))

    if not has_function:
        return ProgressState.SKELETON

    if non_comment_lines < 10 or not (has_return or has_loop):
        return ProgressState.PARTIAL

    # Check for edge case handling
    has_edge_cases = bool(re.search(r"if.*(?:None|null|empty|len.*==.*0|\.length.*===?\s*0)", code))

    if has_edge_cases and non_comment_lines >= 12:
        return ProgressState.COMPLETE

    return ProgressState.NEAR_COMPLETE


def detect_approach_local(code: str) -> ApproachInfo | None:
    """Detect the coding approach using keyword matching."""
    if not code.strip():
        return None

    code_lower = code.lower()
    scores: dict[str, float] = {}

    for approach_name, keywords in APPROACH_PATTERNS.items():
        score = 0.0
        for keyword in keywords:
            if keyword.startswith(r"\b"):
                # Regex pattern for word-boundary matching
                if re.search(keyword, code_lower):
                    score += 1.0
            elif keyword.lower() in code_lower:
                score += 1.0
        if score > 0:
            scores[approach_name] = score / len(keywords)

    if not scores:
        return None

    best = max(scores, key=scores.get)  # type: ignore
    confidence = min(1.0, scores[best])

    return ApproachInfo(
        name=best,
        confidence=confidence,
        data_structures=extract_data_structures(code),
    )


def extract_data_structures(code: str) -> list[str]:
    """Extract data structures used in the code."""
    structures = []
    patterns = {
        "array/list": r"\[.*\]|list\(|ArrayList|Array\(",
        "hash_map": r"dict\(|\{\}|HashMap|Map\(|defaultdict|Counter",
        "set": r"set\(|HashSet|Set\(",
        "stack": r"stack|\.append.*\.pop|Stack\(",
        "queue": r"queue|deque|Queue\(",
        "heap": r"heapq|heappush|PriorityQueue",
        "tree": r"TreeNode|BinaryTree",
        "linked_list": r"ListNode|LinkedList|\.next",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, code, re.IGNORECASE):
            structures.append(name)
    return structures


def detect_stuck(char_counts: Sequence[int]) -> bool:
    """Detect if the candidate is stuck based on code oscillation.

    Looks at the last 5+ snapshot lengths for oscillation (adding and
    removing code without net progress) or no significant change.
    """
    if len(char_counts) < 5:
        return False

    lengths = list(char_counts[-5:])

    # Check for oscillation: length goes up-down-up-down
    deltas = [lengths[i + 1] - lengths[i] for i in range(len(lengths) - 1)]
    sign_changes = sum(1 for i in range(len(deltas) - 1) if deltas[i] * deltas[i + 1] < 0)

    # If sign changes >= 3 in last 4 deltas, candidate is oscillating
    if sign_changes >= 3:
        return True

    # Also stuck if code length hasn't changed significantly
    max_len = max(lengths)
    min_len = min(lengths)
    return bool(max_len > 0 and (max_len - min_len) / max_len < 0.05)


def should_run_llm_analysis(question: str, history: Sequence[CodeSnapshot]) -> bool:
    """Whether the newest snapshot in ``history`` warrants the (expensive) LLM analysis."""
    if not question:
        return False
    snapshot = history[-1]

    # Run on first substantial snapshot
    if len(history) == 1 and snapshot.line_count > 5:
        return True

    # Run every 5 snapshots
    if len(history) % 5 == 0:
        return True

    # Run on significant code growth
    if len(history) >= 2:
        prev = history[-2]
        growth = abs(snapshot.char_count - prev.char_count)
        if growth > 200:
            return True

    return False


def make_snapshot(code: str, language: str) -> CodeSnapshot:
    """A normalized snapshot of ``code``."""
    code = normalize_code(code)
    return CodeSnapshot(
        code=code,
        language=language,
        line_count=code.count("\n") + 1 if code.strip() else 0,
        char_count=len(code),
    )


def analyze_locally(snapshot: CodeSnapshot, history: Sequence[CodeSnapshot]) -> CodeAnalysisResult:
    """Heuristic analysis of the newest snapshot (``history`` ends with it). No I/O."""
    return CodeAnalysisResult(
        progress_state=detect_progress(snapshot.code),
        detected_approach=detect_approach_local(snapshot.code),
        is_stuck=detect_stuck([s.char_count for s in history]),
    )


async def llm_analyze_code(
    failover: FailoverManager,
    session_id: str,
    question: str,
    code: str,
    language: str,
    progress: ProgressState,
    approach: ApproachInfo | None,
) -> CodeAnalysisResult | None:
    """Run LLM-based code analysis for deeper insights."""
    prompt = CODE_ANALYSIS_PROMPT.format(
        question=question,
        language=language,
        code=code,
        approach=approach.name if approach else "none detected",
        progress=progress.value,
    )

    try:
        result = await failover.analyze(
            AnalysisContext(
                system_prompt="Analyze interview code. Return JSON.",
                user_content=prompt,
                task_type=TaskType.CODE_ANALYSIS,
            ),
            LLMConfig(max_tokens=400, temperature=0.2, response_format="json"),
            session_id=session_id,
        )

        parsed = result.parsed
        if not parsed:
            try:
                parsed = json.loads(result.content)
            except json.JSONDecodeError:
                return None

        approach_data = parsed.get("approach", {})
        detected_approach = None
        if approach_data and approach_data.get("name"):
            detected_approach = ApproachInfo(
                name=approach_data["name"],
                confidence=approach_data.get("confidence", 0.5),
                data_structures=approach_data.get("data_structures", []),
                expected_complexity=approach_data.get("complexity", {}),
            )

        return CodeAnalysisResult(
            progress_state=ProgressState(parsed.get("progress_state", progress.value)),
            detected_approach=detected_approach or approach,
            bugs=parsed.get("bugs", []),
            complexity_estimate=approach_data.get("complexity", {}),
        )
    except Exception as e:
        logger.error(
            "llm_code_analysis_failed",
            session_id=session_id,
            error=str(e),
        )
        return None


class CodeAnalysisService:
    """Analyzes candidate code snapshots in real-time.

    Usage:
        service = CodeAnalysisService(session_id, failover_manager)
        await service.set_question("Implement two sum", approaches)
        result = await service.analyze_snapshot(code, language)
    """

    def __init__(
        self,
        session_id: str,
        failover_manager: FailoverManager,
    ) -> None:
        self.session_id = session_id
        self._failover = failover_manager

        # Current question context
        self._current_question: str = ""
        self._precomputed_approaches: list[dict] = []

        # Snapshot history for stuck detection
        self._snapshots: list[CodeSnapshot] = []
        self._max_snapshot_history = MAX_SNAPSHOT_HISTORY

        # Analysis state
        self._current_progress = ProgressState.EMPTY
        self._current_approach: ApproachInfo | None = None
        self._last_analysis_result: CodeAnalysisResult | None = None

    async def set_question(
        self,
        question_text: str,
        precomputed_approaches: list[dict] | None = None,
    ) -> None:
        """Set the current question and optionally pre-computed solution patterns."""
        self._current_question = question_text
        self._precomputed_approaches = precomputed_approaches or []
        self._current_progress = ProgressState.EMPTY
        self._current_approach = None
        self._snapshots.clear()

    def reset_for_new_question(self) -> None:
        """Start code tracking over for the next question (history, progress, approach)."""
        self._snapshots.clear()
        self._current_progress = ProgressState.EMPTY
        self._current_approach = None

    async def analyze_snapshot(
        self,
        code: str,
        language: str = "unknown",
    ) -> CodeAnalysisResult:
        """Analyze a code snapshot and return structured results."""
        snapshot = make_snapshot(code, language)
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self._max_snapshot_history:
            self._snapshots.pop(0)

        # Quick local analysis
        result = analyze_locally(snapshot, self._snapshots)
        is_stuck = result.is_stuck

        # LLM analysis for deeper insights (only on significant changes)
        if self._should_run_llm_analysis(snapshot):
            llm_result = await self._llm_analyze(
                snapshot.code, language, result.progress_state, result.detected_approach
            )
            if llm_result:
                result = llm_result
                result.is_stuck = is_stuck

        self._current_progress = result.progress_state
        if result.detected_approach:
            self._current_approach = result.detected_approach
        self._last_analysis_result = result

        return result

    def _normalize_code(self, code: str) -> str:
        return normalize_code(code)

    def _detect_progress(self, code: str) -> ProgressState:
        return detect_progress(code)

    def _detect_approach_local(self, code: str) -> ApproachInfo | None:
        return detect_approach_local(code)

    def _extract_data_structures(self, code: str) -> list[str]:
        return extract_data_structures(code)

    def _detect_stuck(self) -> bool:
        return detect_stuck([s.char_count for s in self._snapshots])

    def _should_run_llm_analysis(self, snapshot: CodeSnapshot) -> bool:
        return should_run_llm_analysis(self._current_question, self._snapshots)

    async def _llm_analyze(
        self,
        code: str,
        language: str,
        progress: ProgressState,
        approach: ApproachInfo | None,
    ) -> CodeAnalysisResult | None:
        return await llm_analyze_code(
            self._failover, self.session_id, self._current_question, code, language, progress, approach
        )

    def get_latest_result(self) -> CodeAnalysisResult | None:
        return self._last_analysis_result

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "current_question": self._current_question,
            "snapshot_count": len(self._snapshots),
            "current_progress": self._current_progress.value,
            "current_approach": self._current_approach.name if self._current_approach else None,
            "last_result": self._last_analysis_result.to_dict() if self._last_analysis_result else None,
        }
