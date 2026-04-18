"""Hierarchical memory service (3-tier).

Manages interview context across three memory tiers:
  Tier 1 (Working Memory): Last 10 verbatim utterances
  Tier 2 (Rolling Summary): Compressed summary of all utterances (<400 words)
  Tier 3 (Fact Store): Structured facts extracted from conversation

Token budgets enforce context window limits per tier.
"""

import asyncio
import contextlib
import json
from collections import deque
from dataclasses import dataclass, field

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.utils.logging import get_logger
from src.utils.token_budget import (
    BUDGET_FACT_STORE,
    BUDGET_ROLLING_SUMMARY,
    BUDGET_WORKING_MEMORY,
    estimate_tokens,
    truncate_to_budget,
)

logger = get_logger("memory_service")

# Tier 1 settings
WORKING_MEMORY_SIZE = 10

# Tier 2 settings — compress after this many new utterances
SUMMARY_TRIGGER_COUNT = 20

SUMMARY_SYSTEM_PROMPT = """You are a concise interview summarizer. Given the current summary and a batch of new utterances, produce an updated summary that:
1. Preserves all factual claims and technical details from the previous summary
2. Integrates the new utterances chronologically
3. Stays under 400 words
4. Uses third-person ("The candidate said..." / "The interviewer asked...")
5. Highlights any claimed experience, technical positions, or notable moments

Return ONLY the updated summary text, no preamble."""

FACT_EXTRACTION_PROMPT = """Analyze this interview utterance and extract any factual claims into the following JSON structure. Only include categories with actual findings. Return valid JSON only.

{{
  "claimed_experience": ["list of specific claims about experience or skills"],
  "technical_positions": ["list of technical opinions or approach preferences"],
  "behavioral_signals": ["list of communication or behavioral observations"],
  "key_numbers": ["list of specific metrics, numbers, or quantified claims"]
}}

Utterance (by {speaker}): {text}"""


@dataclass
class Utterance:
    """A single utterance stored in working memory."""

    speaker: str
    text: str
    timestamp: str
    is_final: bool = True


@dataclass
class FactStore:
    """Tier 3: Structured facts extracted from the conversation."""

    claimed_experience: list[str] = field(default_factory=list)
    technical_positions: list[str] = field(default_factory=list)
    behavioral_signals: list[str] = field(default_factory=list)
    key_numbers: list[str] = field(default_factory=list)
    hint_interactions: list[dict] = field(default_factory=list)

    def merge(self, extracted: dict) -> None:
        """Merge newly extracted facts, avoiding duplicates."""
        for key in (
            "claimed_experience",
            "technical_positions",
            "behavioral_signals",
            "key_numbers",
        ):
            existing = getattr(self, key)
            for item in extracted.get(key, []):
                if item and item not in existing:
                    existing.append(item)

    def to_text(self) -> str:
        """Render fact store as concise text for the LLM context."""
        parts = []
        if self.claimed_experience:
            parts.append("Experience: " + "; ".join(self.claimed_experience))
        if self.technical_positions:
            parts.append("Technical positions: " + "; ".join(self.technical_positions))
        if self.behavioral_signals:
            parts.append("Behavioral: " + "; ".join(self.behavioral_signals))
        if self.key_numbers:
            parts.append("Numbers: " + "; ".join(self.key_numbers))
        if self.hint_interactions:
            parts.append(f"Hint interactions: {len(self.hint_interactions)} recorded")
        return "\n".join(parts) if parts else "(No facts extracted yet)"

    def to_dict(self) -> dict:
        return {
            "claimed_experience": list(self.claimed_experience),
            "technical_positions": list(self.technical_positions),
            "behavioral_signals": list(self.behavioral_signals),
            "key_numbers": list(self.key_numbers),
            "hint_interactions": list(self.hint_interactions),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FactStore":
        return cls(
            claimed_experience=data.get("claimed_experience", []),
            technical_positions=data.get("technical_positions", []),
            behavioral_signals=data.get("behavioral_signals", []),
            key_numbers=data.get("key_numbers", []),
            hint_interactions=data.get("hint_interactions", []),
        )


class MemoryService:
    """Manages hierarchical interview context memory.

    Usage:
        memory = MemoryService(session_id, failover_manager)
        await memory.add_utterance(speaker, text, timestamp)
        context = memory.assemble_context(question_text, hint_text)
    """

    def __init__(self, session_id: str, failover_manager: FailoverManager) -> None:
        self.session_id = session_id
        self._failover = failover_manager

        # Tier 1: Working Memory
        self._working_memory: deque[Utterance] = deque(maxlen=WORKING_MEMORY_SIZE)

        # Tier 2: Rolling Summary
        self._rolling_summary: str = ""
        self._unsummarized_buffer: list[Utterance] = []

        # Tier 3: Fact Store
        self.fact_store = FactStore()

        # Background task tracking
        self._bg_compress_task: asyncio.Task | None = None
        self._bg_extract_tasks: set[asyncio.Task] = set()

        # Stats
        self._total_utterances = 0

    async def add_utterance(
        self,
        speaker: str,
        text: str,
        timestamp: str,
        is_final: bool = True,
    ) -> None:
        """Add a new utterance to the memory system.

        Only sync operations run inline (deque append, buffer append).
        LLM calls (compression, fact extraction) are scheduled as background tasks.
        """
        if not text.strip():
            return

        utterance = Utterance(
            speaker=speaker,
            text=text,
            timestamp=timestamp,
            is_final=is_final,
        )

        # Tier 1: Add to working memory
        self._working_memory.append(utterance)
        self._total_utterances += 1

        # Buffer for Tier 2
        self._unsummarized_buffer.append(utterance)

        # Tier 2: Schedule compression in background if buffer is large enough
        if len(self._unsummarized_buffer) >= SUMMARY_TRIGGER_COUNT:
            self._schedule_compress()

        # Tier 3: Schedule fact extraction in background for substantial utterances
        if is_final and len(text.split()) > 20:
            self._schedule_extract_facts(speaker, text)

    # ── Background Task Scheduling ─────────────────────────────────

    def _is_compress_running(self) -> bool:
        """Check if a background compression task is already in flight."""
        return self._bg_compress_task is not None and not self._bg_compress_task.done()

    def _schedule_compress(self) -> None:
        """Snapshot the buffer and schedule compression in background.

        Skips if a compression is already running to avoid overlapping LLM calls.
        """
        if self._is_compress_running():
            return

        # Snapshot and clear the buffer immediately (sync)
        buffer_snapshot = list(self._unsummarized_buffer)
        self._unsummarized_buffer.clear()

        self._bg_compress_task = asyncio.create_task(self._compress_summary_from_snapshot(buffer_snapshot))

    async def _compress_summary_from_snapshot(self, buffer_snapshot: list[Utterance]) -> None:
        """Run LLM compression on a provided buffer snapshot (background)."""
        if not buffer_snapshot:
            return

        buffer_text = "\n".join(f"[{u.speaker}]: {u.text}" for u in buffer_snapshot)
        user_content = f"Current summary:\n{self._rolling_summary or '(empty)'}\n\nNew utterances:\n{buffer_text}"

        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt=SUMMARY_SYSTEM_PROMPT,
                    user_content=user_content,
                    task_type=TaskType.SUMMARY_COMPRESSION,
                ),
                LLMConfig(max_tokens=500, temperature=0.3),
                session_id=self.session_id,
            )
            self._rolling_summary = result.content

            logger.debug(
                "summary_compressed",
                session_id=self.session_id,
                summary_tokens=estimate_tokens(self._rolling_summary),
            )
        except Exception as e:
            # Re-prepend items to buffer for retry on next trigger
            self._unsummarized_buffer = buffer_snapshot + self._unsummarized_buffer
            logger.error(
                "summary_compression_failed",
                session_id=self.session_id,
                error=str(e),
            )

    async def _compress_summary(self) -> None:
        """Blocking compression of the unsummarized buffer.

        Used only by drain() to ensure all data is flushed before shutdown.
        """
        if not self._unsummarized_buffer:
            return

        buffer_text = "\n".join(f"[{u.speaker}]: {u.text}" for u in self._unsummarized_buffer)

        user_content = f"Current summary:\n{self._rolling_summary or '(empty)'}\n\nNew utterances:\n{buffer_text}"

        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt=SUMMARY_SYSTEM_PROMPT,
                    user_content=user_content,
                    task_type=TaskType.SUMMARY_COMPRESSION,
                ),
                LLMConfig(max_tokens=500, temperature=0.3),
                session_id=self.session_id,
            )
            self._rolling_summary = result.content
            self._unsummarized_buffer.clear()

            logger.debug(
                "summary_compressed",
                session_id=self.session_id,
                summary_tokens=estimate_tokens(self._rolling_summary),
            )
        except Exception as e:
            logger.error(
                "summary_compression_failed",
                session_id=self.session_id,
                error=str(e),
            )
            # Keep the buffer — will retry on next trigger

    def _schedule_extract_facts(self, speaker: str, text: str) -> None:
        """Schedule fact extraction as a background task."""
        task = asyncio.create_task(self._extract_facts(speaker, text))
        self._bg_extract_tasks.add(task)
        task.add_done_callback(self._bg_extract_tasks.discard)

    async def _extract_facts(self, speaker: str, text: str) -> None:
        """Extract structured facts from a substantial utterance."""
        prompt = FACT_EXTRACTION_PROMPT.format(speaker=speaker, text=text)
        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt="Extract factual claims from the utterance. Return valid JSON only.",
                    user_content=prompt,
                    task_type=TaskType.REAL_TIME_ANALYSIS,
                ),
                LLMConfig(max_tokens=300, temperature=0.2, response_format="json"),
                session_id=self.session_id,
            )
            if result.parsed:
                self.fact_store.merge(result.parsed)
            else:
                # Try parsing content directly
                try:
                    parsed = json.loads(result.content)
                    self.fact_store.merge(parsed)
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logger.error(
                "fact_extraction_failed",
                session_id=self.session_id,
                error=str(e),
            )

    def add_hint_interaction(self, hint_data: dict) -> None:
        """Record a hint interaction in the fact store."""
        self.fact_store.hint_interactions.append(hint_data)

    async def reset_for_new_question(self) -> None:
        """Reset memory for a new question transition.

        1. Clear Tier 1 working memory immediately (sync)
        2. Wait for any in-flight compression to finish (to not lose data)
        3. Schedule compression of remaining buffer in background
        4. Keep Tier 2 (rolling summary) and Tier 3 (fact store) intact
        """
        self._working_memory.clear()

        # Wait for in-flight compression so we don't lose its result
        if self._is_compress_running():
            with contextlib.suppress(TimeoutError, Exception):
                await asyncio.wait_for(self._bg_compress_task, timeout=10.0)

        # Schedule compression of remaining buffer in background
        if self._unsummarized_buffer:
            self._schedule_compress()

        logger.info(
            "memory_reset_for_new_question",
            session_id=self.session_id,
            summary_preserved=bool(self._rolling_summary),
            facts_preserved=bool(self.fact_store.to_text()),
        )

    async def drain(self) -> None:
        """Await all in-flight background tasks for safe shutdown.

        Waits for compression and extraction tasks (with 10s timeout),
        then flushes any remaining unsummarized buffer via blocking compression.
        """
        tasks: list[asyncio.Task] = []
        if self._bg_compress_task and not self._bg_compress_task.done():
            tasks.append(self._bg_compress_task)
        tasks.extend(t for t in self._bg_extract_tasks if not t.done())

        if tasks:
            logger.info(
                "memory_drain_waiting",
                session_id=self.session_id,
                task_count=len(tasks),
            )
            await asyncio.wait(tasks, timeout=10.0)

        # Flush any remaining unsummarized utterances
        if self._unsummarized_buffer:
            await self._compress_summary()

    # ── Context Assembly ──────────────────────────────────────────────

    def assemble_context(
        self,
        question_text: str = "",
        hint_text: str = "",
    ) -> str:
        """Assemble the full context for an LLM analysis call.

        Returns a formatted string combining all tiers within token budgets.
        """
        parts = []

        # Current question context (first for LLM primacy effect)
        if question_text:
            parts.append(f"## Current Question\n{question_text}")

        # Active hints
        if hint_text:
            parts.append(f"## Active Hints\n{hint_text}")

        # Tier 3: Fact Store (most compressed, highest signal)
        facts_text = truncate_to_budget(self.fact_store.to_text(), BUDGET_FACT_STORE)
        if facts_text:
            parts.append(f"## Key Facts\n{facts_text}")

        # Tier 2: Rolling Summary
        if self._rolling_summary:
            summary = truncate_to_budget(self._rolling_summary, BUDGET_ROLLING_SUMMARY)
            parts.append(f"## Interview Summary\n{summary}")

        # Tier 1: Recent Utterances
        recent_text = "\n".join(f"[{u.speaker}]: {u.text}" for u in self._working_memory)
        if recent_text:
            recent_text = truncate_to_budget(recent_text, BUDGET_WORKING_MEMORY)
            parts.append(f"## Recent Conversation\n{recent_text}")

        return "\n\n".join(parts)

    def get_working_memory_text(self) -> str:
        """Get the current working memory as formatted text."""
        return "\n".join(f"[{u.speaker}]: {u.text}" for u in self._working_memory)

    def get_summary(self) -> str:
        """Return the current tier-2 rolling summary text (may be empty).

        Preferred over reaching into ``_rolling_summary`` from callers.
        """
        return self._rolling_summary

    # ── State Serialization ──────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "working_memory": [
                {"speaker": u.speaker, "text": u.text, "timestamp": u.timestamp} for u in self._working_memory
            ],
            "rolling_summary": self._rolling_summary,
            "unsummarized_count": len(self._unsummarized_buffer),
            "fact_store": self.fact_store.to_dict(),
            "total_utterances": self._total_utterances,
        }

    @classmethod
    def from_dict(cls, data: dict, failover_manager: FailoverManager) -> "MemoryService":
        service = cls(data["session_id"], failover_manager)
        for u in data.get("working_memory", []):
            service._working_memory.append(Utterance(speaker=u["speaker"], text=u["text"], timestamp=u["timestamp"]))
        service._rolling_summary = data.get("rolling_summary", "")
        service.fact_store = FactStore.from_dict(data.get("fact_store", {}))
        service._total_utterances = data.get("total_utterances", 0)
        return service
