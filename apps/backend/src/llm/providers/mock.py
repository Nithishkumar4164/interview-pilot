import json
import time
from collections.abc import AsyncIterator
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

# Canned responses for each task type
MOCK_RESPONSES: dict[TaskType, str] = {
    TaskType.REAL_TIME_ANALYSIS: json.dumps({
        "answer_quality": 3,
        "on_track": True,
        "insight": "Candidate is working through the problem methodically.",
        "red_flags": [],
        "suggestion_for_interviewer": "Consider asking about edge cases.",
    }),
    TaskType.CODE_ANALYSIS: json.dumps({
        "approaches": [
            {
                "name": "Hash Map Approach",
                "data_structures": ["hash_map"],
                "expected_complexity": {"time": "O(n)", "space": "O(n)"},
                "key_steps": ["Iterate array", "Check complement", "Return indices"],
            }
        ],
        "common_bugs": ["Off-by-one in loop bounds"],
        "hints": [
            {"level": 1, "text": "What data structure gives O(1) lookups?"},
            {"level": 2, "text": "A hash map could help. What would be the key?"},
            {"level": 3, "text": "Map each element to its index. Check for complement."},
        ],
    }),
    TaskType.REPORT_GENERATION: json.dumps({
        "recommendation": "hire",
        "confidence_level": 0.75,
        "scores": [
            {"criterion": "problem_solving", "score": 4, "evidence": ["Good approach"], "trend": "stable"},
            {"criterion": "code_quality", "score": 3, "evidence": ["Clean code"], "trend": "improving"},
            {"criterion": "communication", "score": 4, "evidence": ["Clear explanations"], "trend": "stable"},
            {"criterion": "hint_responsiveness", "score": 3, "evidence": ["Picked up hints"], "trend": "stable"},
        ],
        "key_moments": [],
        "strengths": ["Strong problem solving"],
        "areas_for_growth": ["Edge case coverage"],
    }),
    TaskType.HINT_GENERATION: json.dumps({
        "hints": [
            {"level": 1, "text": "Think about the data structure needed."},
            {"level": 2, "text": "A hash map would be efficient here."},
            {"level": 3, "text": "Store each element's index in the map."},
        ]
    }),
    TaskType.SUMMARY_COMPRESSION: "The candidate discussed their experience with distributed systems. They claimed to have optimized database queries resulting in 40% latency reduction. Currently working through a hash map based solution for the coding problem.",
    TaskType.RESUME_EXTRACTION: json.dumps({
        "name": "Test Candidate",
        "current_role": "Senior Software Engineer",
        "years_experience": 5,
        "tech_stack": ["Python", "Java", "PostgreSQL", "Redis"],
        "key_projects": [{"name": "API Gateway", "technologies": ["Go", "gRPC"]}],
        "verifiable_claims": [
            {"claim": "Reduced latency by 40%", "probe": "What profiling tools did you use?"}
        ],
    }),
    TaskType.QUESTION_GENERATION: json.dumps({
        "questions": [
            {
                "text": "Implement a function to find two numbers that add up to a target.",
                "category": "coding",
                "difficulty": 2,
                "time_minutes": 20,
            }
        ]
    }),
}


class MockProvider(ILLMProvider):
    """Mock LLM provider for testing. Returns deterministic canned responses."""

    def __init__(self, model: str = "mock-v1") -> None:
        self.model = model
        self._healthy = True

    @property
    def provider_name(self) -> str:
        return "mock"

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy

    async def complete(self, prompt: str, config: LLMConfig) -> AsyncIterator[str]:
        if not self._healthy:
            raise ConnectionError("Mock provider is unhealthy")

        response = "This is a mock completion response for testing."
        # Simulate streaming by yielding word by word
        for word in response.split():
            yield word + " "

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        if not self._healthy:
            raise ConnectionError("Mock provider is unhealthy")

        start = time.monotonic()
        content = MOCK_RESPONSES.get(context.task_type, '{"result": "mock"}')
        latency = (time.monotonic() - start) * 1000

        parsed = None
        if config.response_format == "json":
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                pass

        return StructuredOutput(
            content=content,
            parsed=parsed,
            model=self.model,
            provider="mock",
            input_tokens=len(context.user_content) // 4,
            output_tokens=len(content) // 4,
            latency_ms=latency,
        )

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            provider="mock",
            status=ProviderHealthStatus.HEALTHY if self._healthy else ProviderHealthStatus.DOWN,
            last_check_at=datetime.now(UTC).isoformat(),
            average_latency_ms=1.0 if self._healthy else None,
        )
