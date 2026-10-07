"""Tests for M6 — Code Analysis & Progressive Hints.

Covers:
- Code normalization and progress detection
- Approach detection (keyword matching)
- Stuck detection (oscillation)
- Progressive hint service (3 levels)
- Hint metrics computation
"""

from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.schemas.hint import HintResponseQuality
from src.services.code_analysis_service import (
    ApproachInfo,
    CodeAnalysisResult,
    ProgressState,
    analyze_locally,
    detect_approach_local,
    detect_progress,
    detect_stuck,
    extract_data_structures,
    make_snapshot,
    normalize_code,
)
from src.services.hint_service import HintService


def _mock_failover_manager() -> FailoverManager:
    health = HealthMonitor()
    fm = FailoverManager(health)
    for task_type in TaskType:
        fm.update_mapping(
            TaskProviderMapping(
                task_type=task_type,
                primary_provider="mock",
                primary_model="mock-v1",
                fallback_provider="mock",
                fallback_model="mock-v1",
            )
        )
    return fm


# ── Code Progress Detection ──────────────────────────────────────────


class TestProgressDetection:
    def test_empty_code(self):
        assert detect_progress("") == ProgressState.EMPTY
        assert detect_progress("   \n  \n  ") == ProgressState.EMPTY

    def test_skeleton_code(self):
        code = "def two_sum(nums, target):\n    pass"
        assert detect_progress(code) == ProgressState.SKELETON

    def test_partial_code(self):
        code = """def two_sum(nums, target):
    result = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in result:
            return
"""
        assert detect_progress(code) == ProgressState.PARTIAL

    def test_near_complete_code(self):
        code = """def two_sum(nums, target):
    seen = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in seen:
            return [seen[complement], i]
        seen[num] = i
    return []

# Test cases
result = two_sum([2, 7, 11, 15], 9)
print(result)
"""
        progress = detect_progress(code)
        assert progress in (ProgressState.NEAR_COMPLETE, ProgressState.COMPLETE)

    def test_complete_code_with_edge_cases(self):
        code = """def two_sum(nums, target):
    if nums is None or len(nums) == 0:
        return []
    seen = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in seen:
            return [seen[complement], i]
        seen[num] = i
    return []

# Edge cases
assert two_sum([], 5) == []
assert two_sum([1], 1) == []
assert two_sum([2, 7, 11, 15], 9) == [0, 1]
print("All tests passed!")
"""
        assert detect_progress(code) == ProgressState.COMPLETE


# ── Approach Detection ───────────────────────────────────────────────


class TestApproachDetection:
    def test_hash_map_approach(self):
        code = """
seen = {}
hash_map = dict()
for num in nums:
    if target - num in seen:
        return [seen[target-num], i]
    seen[num] = i
"""
        approach = detect_approach_local(code)
        assert approach is not None
        assert approach.name == "hash_map"

    def test_two_pointer_approach(self):
        code = """
left = 0
right = len(nums) - 1
while left < right:
    total = nums[left] + nums[right]
"""
        approach = detect_approach_local(code)
        assert approach is not None
        assert approach.name == "two_pointer"

    def test_bfs_approach(self):
        code = """
from collections import deque
queue = deque([start])
visited = set()
while queue:
    node = queue.popleft()
    for neighbor in graph[node]:
        if neighbor not in visited:
            queue.append(neighbor)
"""
        approach = detect_approach_local(code)
        assert approach is not None
        assert approach.name == "bfs"

    def test_empty_code_no_approach(self):
        assert detect_approach_local("") is None
        assert detect_approach_local("   ") is None

    def test_data_structure_extraction(self):
        code = """
seen = {}
result = []
heap = heapq.heappush([], val)
"""
        structures = extract_data_structures(code)
        assert "hash_map" in structures
        assert "array/list" in structures
        assert "heap" in structures


# ── Stuck Detection ──────────────────────────────────────────────────


class TestStuckDetection:
    def test_not_stuck_with_few_snapshots(self):
        assert detect_stuck([50, 60, 70]) is False

    def test_stuck_oscillation(self):
        # Adding and removing code repeatedly
        assert detect_stuck([100, 120, 95, 130, 90]) is True

    def test_stuck_no_progress(self):
        # Code length barely changes
        assert detect_stuck([100] * 5) is True

    def test_not_stuck_steady_growth(self):
        assert detect_stuck([50 + i * 30 for i in range(5)]) is False


# ── Local analysis of a snapshot ─────────────────────────────────────


class TestLocalAnalysis:
    def test_analyze_empty_snapshot(self):
        snapshot = make_snapshot("", "python")
        assert analyze_locally(snapshot, [snapshot]).progress_state == ProgressState.EMPTY

    def test_analyze_real_code(self):
        code = """def two_sum(nums, target):
    seen = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in seen:
            return [seen[complement], i]
        seen[num] = i
"""
        snapshot = make_snapshot(code, "python")
        result = analyze_locally(snapshot, [snapshot])
        assert result.progress_state != ProgressState.EMPTY
        assert result.detected_approach is not None

    def test_normalize_code(self):
        normalized = normalize_code("  def foo():  \n    pass  \n\n\n")
        assert not normalized.endswith("\n")
        assert "  \n" not in normalized  # No trailing spaces on lines

    def test_result_to_dict(self):
        result = CodeAnalysisResult(
            progress_state=ProgressState.PARTIAL,
            detected_approach=ApproachInfo(name="hash_map", confidence=0.8),
            bugs=["Off-by-one in loop"],
        )
        d = result.to_dict()
        assert d["progress_state"] == "partial"
        assert d["detected_approach"]["name"] == "hash_map"
        assert len(d["bugs"]) == 1


# ── Hint Service ─────────────────────────────────────────────────────


class TestHintService:
    def setup_method(self):
        self.service = HintService("test-session")
        self.service.register_hints(
            "q1",
            {
                1: "What data structure gives O(1) lookups?",
                2: "A hash map could help. What would be the key and value?",
                3: "Map each element to its index. For each element, check if target - element exists.",
            },
        )

    def test_initial_state(self):
        assert self.service.get_current_level() == 0
        assert self.service.get_current_hint() is None

    def test_activate_question(self):
        self.service.activate_question("q1")
        assert self.service._active_question_id == "q1"
        assert self.service.get_current_level() == 0

    def test_advance_level_1(self):
        self.service.activate_question("q1")
        hint = self.service.advance_hint()
        assert hint is not None
        assert hint.level == 1
        assert "O(1) lookups" in hint.text
        assert self.service.get_current_level() == 1

    def test_advance_all_levels(self):
        self.service.activate_question("q1")

        h1 = self.service.advance_hint()
        assert h1.level == 1

        h2 = self.service.advance_hint()
        assert h2.level == 2
        assert "hash map" in h2.text

        h3 = self.service.advance_hint()
        assert h3.level == 3
        assert "Map each element" in h3.text

        # No more hints
        h4 = self.service.advance_hint()
        assert h4 is None

    def test_advance_without_activation(self):
        hint = self.service.advance_hint()
        assert hint is None

    def test_advance_unknown_question(self):
        self.service.activate_question("unknown")
        hint = self.service.advance_hint()
        assert hint is None

    def test_acknowledge_hint(self):
        self.service.activate_question("q1")
        hint = self.service.advance_hint()

        result = self.service.acknowledge_hint(
            hint.hint_id,
            interviewer_used=True,
            candidate_response_quality=HintResponseQuality.FULL,
            time_to_respond_ms=5000,
        )
        assert result is True

        recorded = self.service.get_current_hint()
        assert recorded.interviewer_used is True
        assert recorded.candidate_response_quality == HintResponseQuality.FULL
        assert recorded.time_to_respond_ms == 5000

    def test_acknowledge_unknown_hint(self):
        result = self.service.acknowledge_hint("nonexistent")
        assert result is False


# ── Hint Metrics ─────────────────────────────────────────────────────


class TestHintMetrics:
    def setup_method(self):
        self.service = HintService("test-session")
        self.service.register_hints(
            "q1",
            {
                1: "Hint 1",
                2: "Hint 2",
                3: "Hint 3",
            },
        )
        self.service.register_hints(
            "q2",
            {
                1: "Q2 Hint 1",
                2: "Q2 Hint 2",
                3: "Q2 Hint 3",
            },
        )

    def test_empty_metrics(self):
        metrics = self.service.get_metrics()
        assert metrics.total_hints_given == 0
        assert metrics.average_response_time_ms is None
        assert metrics.incorporation_rate == 0.0
        assert metrics.independence_trajectory == "stable"

    def test_metrics_with_hints(self):
        self.service.activate_question("q1")
        h1 = self.service.advance_hint()
        self.service.acknowledge_hint(
            h1.hint_id,
            interviewer_used=True,
            candidate_response_quality=HintResponseQuality.FULL,
            time_to_respond_ms=3000,
        )
        h2 = self.service.advance_hint()
        self.service.acknowledge_hint(
            h2.hint_id,
            interviewer_used=True,
            candidate_response_quality=HintResponseQuality.NONE,
            time_to_respond_ms=8000,
        )

        metrics = self.service.get_metrics()
        assert metrics.total_hints_given == 2
        assert metrics.average_response_time_ms == 5500.0
        assert metrics.incorporation_rate == 0.5  # 1/2 incorporated
        assert metrics.level_distribution == {1: 1, 2: 1}

    def test_schema_events(self):
        self.service.activate_question("q1")
        self.service.advance_hint()
        events = self.service.to_schema_events()
        assert len(events) == 1
        assert events[0].level == 1
        assert events[0].session_id == "test-session"

    def test_serialization(self):
        self.service.activate_question("q1")
        self.service.advance_hint()
        data = self.service.to_dict()
        assert data["current_level"] == 1
        assert len(data["hint_events"]) == 1
        assert "metrics" in data
