"""Tests for M6 — Code Analysis & Progressive Hints.

Covers:
- Code normalization and progress detection
- Approach detection (keyword matching)
- Stuck detection (oscillation)
- Progressive hint service (3 levels)
- Hint metrics computation
"""

import pytest

from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.schemas.hint import HintResponseQuality
from src.services.code_analysis_service import (
    ApproachInfo,
    CodeAnalysisResult,
    CodeAnalysisService,
    CodeSnapshot,
    ProgressState,
)
from src.services.hint_service import HintService


def _mock_failover_manager() -> FailoverManager:
    health = HealthMonitor()
    fm = FailoverManager(health)
    for task_type in TaskType:
        fm.update_mapping(TaskProviderMapping(
            task_type=task_type,
            primary_provider="mock",
            primary_model="mock-v1",
            fallback_provider="mock",
            fallback_model="mock-v1",
        ))
    return fm


# ── Code Progress Detection ──────────────────────────────────────────


class TestProgressDetection:
    def setup_method(self):
        self.service = CodeAnalysisService("test", _mock_failover_manager())

    def test_empty_code(self):
        assert self.service._detect_progress("") == ProgressState.EMPTY
        assert self.service._detect_progress("   \n  \n  ") == ProgressState.EMPTY

    def test_skeleton_code(self):
        code = "def two_sum(nums, target):\n    pass"
        assert self.service._detect_progress(code) == ProgressState.SKELETON

    def test_partial_code(self):
        code = """def two_sum(nums, target):
    result = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in result:
            return
"""
        assert self.service._detect_progress(code) == ProgressState.PARTIAL

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
        progress = self.service._detect_progress(code)
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
        assert self.service._detect_progress(code) == ProgressState.COMPLETE


# ── Approach Detection ───────────────────────────────────────────────


class TestApproachDetection:
    def setup_method(self):
        self.service = CodeAnalysisService("test", _mock_failover_manager())

    def test_hash_map_approach(self):
        code = """
seen = {}
hash_map = dict()
for num in nums:
    if target - num in seen:
        return [seen[target-num], i]
    seen[num] = i
"""
        approach = self.service._detect_approach_local(code)
        assert approach is not None
        assert approach.name == "hash_map"

    def test_two_pointer_approach(self):
        code = """
left = 0
right = len(nums) - 1
while left < right:
    total = nums[left] + nums[right]
"""
        approach = self.service._detect_approach_local(code)
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
        approach = self.service._detect_approach_local(code)
        assert approach is not None
        assert approach.name == "bfs"

    def test_empty_code_no_approach(self):
        assert self.service._detect_approach_local("") is None
        assert self.service._detect_approach_local("   ") is None

    def test_data_structure_extraction(self):
        code = """
seen = {}
result = []
heap = heapq.heappush([], val)
"""
        structures = self.service._extract_data_structures(code)
        assert "hash_map" in structures
        assert "array/list" in structures
        assert "heap" in structures


# ── Stuck Detection ──────────────────────────────────────────────────


class TestStuckDetection:
    def setup_method(self):
        self.service = CodeAnalysisService("test", _mock_failover_manager())

    def test_not_stuck_with_few_snapshots(self):
        for i in range(3):
            self.service._snapshots.append(
                CodeSnapshot(code=f"code {i}", char_count=50 + i * 10)
            )
        assert self.service._detect_stuck() is False

    def test_stuck_oscillation(self):
        # Simulate adding/removing code repeatedly
        lengths = [100, 120, 95, 130, 90]
        for l in lengths:
            self.service._snapshots.append(
                CodeSnapshot(code="x" * l, char_count=l)
            )
        assert self.service._detect_stuck() is True

    def test_stuck_no_progress(self):
        # Code length barely changes
        for _ in range(5):
            self.service._snapshots.append(
                CodeSnapshot(code="x" * 100, char_count=100)
            )
        assert self.service._detect_stuck() is True

    def test_not_stuck_steady_growth(self):
        for i in range(5):
            length = 50 + i * 30  # Steady growth
            self.service._snapshots.append(
                CodeSnapshot(code="x" * length, char_count=length)
            )
        assert self.service._detect_stuck() is False


# ── Code Analysis Service (Full) ─────────────────────────────────────


class TestCodeAnalysisService:
    def setup_method(self):
        self.service = CodeAnalysisService("test", _mock_failover_manager())

    @pytest.mark.asyncio
    async def test_analyze_empty_snapshot(self):
        result = await self.service.analyze_snapshot("", "python")
        assert result.progress_state == ProgressState.EMPTY

    @pytest.mark.asyncio
    async def test_analyze_real_code(self):
        code = """def two_sum(nums, target):
    seen = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in seen:
            return [seen[complement], i]
        seen[num] = i
"""
        result = await self.service.analyze_snapshot(code, "python")
        assert result.progress_state != ProgressState.EMPTY
        assert result.detected_approach is not None

    @pytest.mark.asyncio
    async def test_normalize_code(self):
        code = "  def foo():  \n    pass  \n\n\n"
        normalized = self.service._normalize_code(code)
        assert not normalized.endswith("\n")
        assert "  \n" not in normalized  # No trailing spaces on lines

    @pytest.mark.asyncio
    async def test_set_question_resets_state(self):
        await self.service.analyze_snapshot("code", "python")
        assert len(self.service._snapshots) == 1

        await self.service.set_question("New question")
        assert len(self.service._snapshots) == 0

    @pytest.mark.asyncio
    async def test_serialization(self):
        await self.service.analyze_snapshot("def foo(): pass", "python")
        data = self.service.to_dict()
        assert data["session_id"] == "test"
        assert data["snapshot_count"] == 1

    @pytest.mark.asyncio
    async def test_result_to_dict(self):
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
        self.service.register_hints("q1", {
            1: "What data structure gives O(1) lookups?",
            2: "A hash map could help. What would be the key and value?",
            3: "Map each element to its index. For each element, check if target - element exists.",
        })

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
        self.service.register_hints("q1", {
            1: "Hint 1", 2: "Hint 2", 3: "Hint 3",
        })
        self.service.register_hints("q2", {
            1: "Q2 Hint 1", 2: "Q2 Hint 2", 3: "Q2 Hint 3",
        })

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
