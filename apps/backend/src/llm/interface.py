from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskType(str, Enum):
    REAL_TIME_ANALYSIS = "real_time_analysis"
    CODE_ANALYSIS = "code_analysis"
    REPORT_GENERATION = "report_generation"
    HINT_GENERATION = "hint_generation"
    SUMMARY_COMPRESSION = "summary_compression"
    RESUME_EXTRACTION = "resume_extraction"
    QUESTION_GENERATION = "question_generation"


class ProviderHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class LLMConfig:
    """Configuration for a single LLM call."""
    max_tokens: int = 1000
    temperature: float = 0.7
    stream: bool = False
    response_format: str | None = None  # "json" for structured output


@dataclass
class AnalysisContext:
    """Context assembled for analysis calls."""
    system_prompt: str
    user_content: str
    task_type: TaskType
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StructuredOutput:
    """Structured response from an LLM analysis call."""
    content: str
    parsed: dict[str, Any] | None = None
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0


@dataclass
class ProviderStatus:
    """Health status of an LLM provider."""
    provider: str
    status: ProviderHealthStatus
    last_check_at: str
    consecutive_failures: int = 0
    average_latency_ms: float | None = None


class ILLMProvider(ABC):
    """Abstract interface for LLM providers. All business logic uses this interface only."""

    @abstractmethod
    async def complete(self, prompt: str, config: LLMConfig) -> AsyncIterator[str]:
        """Stream a completion response. Yields text chunks."""
        ...

    @abstractmethod
    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        """Run a structured analysis and return parsed output."""
        ...

    @abstractmethod
    async def health_check(self) -> ProviderStatus:
        """Check if the provider is healthy and responsive."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'anthropic', 'openai')."""
        ...
