from dataclasses import dataclass

from src.llm.interface import TaskType


@dataclass
class TaskProviderMapping:
    """Maps a task type to its primary and fallback provider/model pairs."""
    task_type: TaskType
    primary_provider: str
    primary_model: str
    fallback_provider: str
    fallback_model: str


# Default task-to-provider mappings (can be overridden via DB config)
DEFAULT_TASK_MAPPINGS: dict[TaskType, TaskProviderMapping] = {
    TaskType.REAL_TIME_ANALYSIS: TaskProviderMapping(
        task_type=TaskType.REAL_TIME_ANALYSIS,
        primary_provider="anthropic",
        primary_model="claude-sonnet-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4o",
    ),
    TaskType.CODE_ANALYSIS: TaskProviderMapping(
        task_type=TaskType.CODE_ANALYSIS,
        primary_provider="anthropic",
        primary_model="claude-sonnet-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4o",
    ),
    TaskType.REPORT_GENERATION: TaskProviderMapping(
        task_type=TaskType.REPORT_GENERATION,
        primary_provider="anthropic",
        primary_model="claude-opus-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4-turbo",
    ),
    TaskType.HINT_GENERATION: TaskProviderMapping(
        task_type=TaskType.HINT_GENERATION,
        primary_provider="anthropic",
        primary_model="claude-sonnet-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4o",
    ),
    TaskType.SUMMARY_COMPRESSION: TaskProviderMapping(
        task_type=TaskType.SUMMARY_COMPRESSION,
        primary_provider="anthropic",
        primary_model="claude-sonnet-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4o-mini",
    ),
    TaskType.RESUME_EXTRACTION: TaskProviderMapping(
        task_type=TaskType.RESUME_EXTRACTION,
        primary_provider="anthropic",
        primary_model="claude-sonnet-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4o",
    ),
    TaskType.CODE_RATING: TaskProviderMapping(
        task_type=TaskType.CODE_RATING,
        primary_provider="anthropic",
        primary_model="claude-sonnet-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4o",
    ),
    TaskType.QUESTION_GENERATION: TaskProviderMapping(
        task_type=TaskType.QUESTION_GENERATION,
        primary_provider="anthropic",
        primary_model="claude-sonnet-4-20250514",
        fallback_provider="openai",
        fallback_model="gpt-4o",
    ),
}
