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
    RUNTIME_HINT_GENERATION = "runtime_hint_generation"
    CODE_RATING = "code_rating"
    SUMMARY_COMPRESSION = "summary_compression"
    RESUME_EXTRACTION = "resume_extraction"
    QUESTION_GENERATION = "question_generation"
    REPORT_CRITERION = "report_criterion"


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
    truncated: bool = False  # the answer stopped at max_tokens


@dataclass
class ToolSpec:
    """A tool the model may call. ``input_schema`` is a JSON Schema object."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    """One message of a provider-neutral tool conversation.

    - ``user``: ``content``
    - ``assistant``: ``content`` (may be empty) and the ``tool_calls`` it made
    - ``tool``: the result (``content``) of the call ``tool_call_id``
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""


@dataclass
class ToolTurn:
    """One model turn of a tool conversation."""

    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # "tool_use" | "end" | "max_tokens"
    model: str = ""
    provider: str = ""
    input_tokens: int = 0  # all input, cached or not
    output_tokens: int = 0
    latency_ms: float = 0
    cache_read_tokens: int = 0  # part of input_tokens served from the prompt cache
    cache_write_tokens: int = 0  # part of input_tokens written to it


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

    async def invoke_with_tools(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        config: LLMConfig,
        tool_choice: str = "auto",
    ) -> ToolTurn:
        """Run one model turn of a tool conversation (native tool use).

        ``tool_choice="none"`` keeps the tools defined (the conversation may
        contain earlier calls) but makes the model answer in text.
        """
        raise NotImplementedError(f"{self.provider_name} does not support tool calling")

    @abstractmethod
    async def health_check(self) -> ProviderStatus:
        """Check if the provider is healthy and responsive."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'anthropic', 'openai')."""
        ...
