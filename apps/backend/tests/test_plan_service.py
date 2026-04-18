"""Tests for the plan generation service."""

import uuid

import pytest

from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.schemas.session import InterviewLevel
from src.services.plan_service import (
    GeneratedPlan,
    PlanPhaseData,
    PlanService,
    QuestionData,
)


def _mock_failover_manager() -> FailoverManager:
    """Create a FailoverManager wired to use MockProvider for all task types."""
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


def _sample_bank_questions() -> list[dict]:
    """Return a few sample questions as if from the question bank."""
    return [
        {
            "id": str(uuid.uuid4()),
            "text": "Two Sum: find indices that add up to target.",
            "category": "coding",
            "difficulty": 1,
            "tags": ["arrays", "hash_map", "fundamentals"],
            "expected_time_min": 15,
            "hints": [
                {"level": 1, "text": "What data structure gives O(1) lookups?"},
                {"level": 2, "text": "Use a hash map."},
                {"level": 3, "text": "Check for complement in the map."},
            ],
            "optimal_approaches": [
                {
                    "name": "Hash Map",
                    "data_structures": ["hash_map"],
                    "expected_complexity": {"time": "O(n)", "space": "O(n)"},
                    "key_steps": ["Iterate", "Check complement", "Return"],
                }
            ],
            "common_pitfalls": ["Using same element twice"],
            "alternates": ["3Sum"],
        },
        {
            "id": str(uuid.uuid4()),
            "text": "Reverse a linked list iteratively.",
            "category": "coding",
            "difficulty": 1,
            "tags": ["linked_list", "fundamentals"],
            "expected_time_min": 15,
            "hints": [
                {"level": 1, "text": "Track three pointers."},
                {"level": 2, "text": "prev, curr, next."},
                {"level": 3, "text": "Reverse curr.next to prev."},
            ],
            "optimal_approaches": None,
            "common_pitfalls": ["Losing next reference"],
            "alternates": ["Reverse in groups of K"],
        },
        {
            "id": str(uuid.uuid4()),
            "text": "LRU Cache: O(1) get and put.",
            "category": "coding",
            "difficulty": 3,
            "tags": ["data_structures", "design_patterns", "hash_map"],
            "expected_time_min": 25,
            "hints": [
                {"level": 1, "text": "Two data structures together."},
                {"level": 2, "text": "Hash map + doubly linked list."},
                {"level": 3, "text": "Map keys to DLL nodes."},
            ],
            "optimal_approaches": None,
            "common_pitfalls": ["Sync issues"],
            "alternates": ["LFU Cache"],
        },
        {
            "id": str(uuid.uuid4()),
            "text": "Design a URL shortener like bit.ly.",
            "category": "system_design",
            "difficulty": 2,
            "tags": ["web_services", "databases", "scalability"],
            "expected_time_min": 25,
            "hints": [],
            "optimal_approaches": None,
            "common_pitfalls": ["Missing collision handling"],
            "alternates": ["Design Pastebin"],
        },
        {
            "id": str(uuid.uuid4()),
            "text": "Tell me about a production incident you handled.",
            "category": "behavioral",
            "difficulty": 2,
            "tags": ["incident_response", "debugging"],
            "expected_time_min": 8,
            "hints": [],
            "optimal_approaches": None,
            "common_pitfalls": ["Being vague"],
            "alternates": [],
        },
    ]


class TestPlanGeneration:
    @pytest.fixture
    def service(self) -> PlanService:
        return PlanService(llm=_mock_failover_manager())

    @pytest.mark.asyncio
    async def test_generate_plan_sde2_45min(self, service: PlanService):
        """Generate a 45-minute SDE2 plan."""
        plan = await service.generate_plan(
            session_id=str(uuid.uuid4()),
            level=InterviewLevel.SDE2,
            duration_minutes=45,
            focus_areas=["algorithms", "system_design"],
            bank_questions=_sample_bank_questions(),
        )

        assert plan.total_minutes == 45
        assert len(plan.phases) > 0
        assert plan.level == "sde2"
        total_question_time = sum(q.time_minutes for p in plan.phases for q in p.questions)
        # Questions should fit within available time
        assert total_question_time <= plan.total_minutes

    @pytest.mark.asyncio
    async def test_generate_plan_intern_30min(self, service: PlanService):
        """Generate a 30-minute intern plan."""
        plan = await service.generate_plan(
            session_id=str(uuid.uuid4()),
            level=InterviewLevel.INTERN,
            duration_minutes=30,
            focus_areas=["fundamentals"],
            bank_questions=_sample_bank_questions(),
        )

        assert plan.total_minutes == 30
        # Intern should have coding and behavioral phases
        phase_names = [p.phase for p in plan.phases]
        assert "coding" in phase_names

    @pytest.mark.asyncio
    async def test_generate_plan_without_bank(self, service: PlanService):
        """Plan generation works even without bank questions (uses LLM)."""
        plan = await service.generate_plan(
            session_id=str(uuid.uuid4()),
            level=InterviewLevel.SDE1,
            duration_minutes=45,
            focus_areas=["algorithms"],
            bank_questions=None,
        )

        assert plan.total_minutes == 45
        assert len(plan.phases) > 0
        # Should have generated questions via mock LLM
        total_questions = sum(len(p.questions) for p in plan.phases)
        assert total_questions > 0

    @pytest.mark.asyncio
    async def test_coding_questions_have_hints(self, service: PlanService):
        """Coding questions should have 3-level hints."""
        plan = await service.generate_plan(
            session_id=str(uuid.uuid4()),
            level=InterviewLevel.SDE2,
            duration_minutes=45,
            focus_areas=["algorithms"],
            bank_questions=_sample_bank_questions(),
        )

        for phase in plan.phases:
            for q in phase.questions:
                if q.category == "coding":
                    assert len(q.hints) >= 3, f"Question '{q.text}' has {len(q.hints)} hints"

    @pytest.mark.asyncio
    async def test_plan_serialization_roundtrip(self, service: PlanService):
        """Plan can be serialized and deserialized."""
        plan = await service.generate_plan(
            session_id=str(uuid.uuid4()),
            level=InterviewLevel.SDE2,
            duration_minutes=45,
            focus_areas=["algorithms"],
            bank_questions=_sample_bank_questions(),
        )

        plan_dict = service.plan_to_dict(plan)
        restored = PlanService.plan_from_dict(plan_dict)

        assert restored.session_id == plan.session_id
        assert restored.total_minutes == plan.total_minutes
        assert len(restored.phases) == len(plan.phases)
        for orig_p, rest_p in zip(plan.phases, restored.phases, strict=False):
            assert len(rest_p.questions) == len(orig_p.questions)


class TestPlanEditing:
    def _make_plan(self) -> GeneratedPlan:
        """Create a sample plan for editing tests."""
        return GeneratedPlan(
            session_id=str(uuid.uuid4()),
            phases=[
                PlanPhaseData(
                    phase="coding",
                    start_minute=3,
                    duration_minutes=30,
                    questions=[
                        QuestionData(
                            id="q1",
                            text="Two Sum",
                            category="coding",
                            difficulty=1,
                            time_minutes=15,
                            alternates=["Three Sum", "Four Sum"],
                            hints=[
                                {"level": 1, "text": "Hint 1"},
                                {"level": 2, "text": "Hint 2"},
                                {"level": 3, "text": "Hint 3"},
                            ],
                        ),
                        QuestionData(
                            id="q2",
                            text="LRU Cache",
                            category="coding",
                            difficulty=3,
                            time_minutes=15,
                            alternates=["LFU Cache"],
                        ),
                    ],
                ),
                PlanPhaseData(
                    phase="behavioral",
                    start_minute=33,
                    duration_minutes=10,
                    questions=[
                        QuestionData(
                            id="q3",
                            text="Tell me about a challenge",
                            category="behavioral",
                            difficulty=2,
                            time_minutes=10,
                        ),
                    ],
                ),
            ],
            total_minutes=45,
            intro_minutes=3,
            wrap_up_minutes=5,
            buffer_minutes=4,
            level="sde2",
            focus_areas=["algorithms"],
        )

    @pytest.fixture
    def service(self) -> PlanService:
        return PlanService(llm=_mock_failover_manager())

    def test_swap_question(self, service: PlanService):
        plan = self._make_plan()
        service.swap_question(plan, phase_index=0, question_index=0, alternate_index=0)

        assert plan.phases[0].questions[0].text == "Three Sum"
        assert "Two Sum" in plan.phases[0].questions[0].alternates
        # Hints should be cleared after swap
        assert plan.phases[0].questions[0].hints == []

    def test_swap_invalid_phase_raises(self, service: PlanService):
        plan = self._make_plan()
        with pytest.raises(ValueError, match="Phase index"):
            service.swap_question(plan, phase_index=99, question_index=0, alternate_index=0)

    def test_swap_invalid_question_raises(self, service: PlanService):
        plan = self._make_plan()
        with pytest.raises(ValueError, match="Question index"):
            service.swap_question(plan, phase_index=0, question_index=99, alternate_index=0)

    def test_swap_invalid_alternate_raises(self, service: PlanService):
        plan = self._make_plan()
        with pytest.raises(ValueError, match="Alternate index"):
            service.swap_question(plan, phase_index=0, question_index=0, alternate_index=99)

    def test_reorder_questions(self, service: PlanService):
        plan = self._make_plan()
        original_first = plan.phases[0].questions[0].text
        original_second = plan.phases[0].questions[1].text

        service.reorder_questions(plan, phase_index=0, new_order=[1, 0])

        assert plan.phases[0].questions[0].text == original_second
        assert plan.phases[0].questions[1].text == original_first

    def test_reorder_invalid_permutation_raises(self, service: PlanService):
        plan = self._make_plan()
        with pytest.raises(ValueError, match="permutation"):
            service.reorder_questions(plan, phase_index=0, new_order=[0, 0])

    def test_remove_question(self, service: PlanService):
        plan = self._make_plan()
        assert len(plan.phases[0].questions) == 2

        service.remove_question(plan, phase_index=0, question_index=0)

        assert len(plan.phases[0].questions) == 1
        assert plan.phases[0].questions[0].text == "LRU Cache"
        # Remaining question should get the full phase time
        assert plan.phases[0].questions[0].time_minutes == 30

    def test_add_question(self, service: PlanService):
        plan = self._make_plan()
        assert len(plan.phases[0].questions) == 2

        new_q = QuestionData(
            id="q4",
            text="Merge Intervals",
            category="coding",
            difficulty=2,
            time_minutes=15,
        )
        service.add_question(plan, phase_index=0, question=new_q)

        assert len(plan.phases[0].questions) == 3
        assert plan.phases[0].questions[2].text == "Merge Intervals"
        # All questions should be rebalanced to fit 30 minutes
        total = sum(q.time_minutes for q in plan.phases[0].questions)
        assert total == 30

    def test_remove_invalid_index_raises(self, service: PlanService):
        plan = self._make_plan()
        with pytest.raises(ValueError, match="Question index"):
            service.remove_question(plan, phase_index=0, question_index=99)
