"""Report service — orchestrates report generation and persistence.

Collects data from all session services and delegates to ReportGenerator.
"""

from src.llm.failover import FailoverManager
from src.services.analysis_service import AnalysisService
from src.services.code_analysis_service import CodeAnalysisService
from src.services.contradiction_service import ContradictionService
from src.services.hint_service import HintService
from src.services.memory_service import MemoryService
from src.services.report_generator import GeneratedReport, ReportGenerator, ReportInput
from src.utils.logging import get_logger

logger = get_logger("report_service")


class ReportService:
    """Orchestrates report generation from session data.

    Usage:
        service = ReportService(failover_manager)
        report = await service.generate_report(
            session_id, memory, analysis, code_analysis,
            hints, contradictions, duration, level
        )
    """

    def __init__(self, failover_manager: FailoverManager) -> None:
        self._generator = ReportGenerator(failover_manager)

    async def generate_report(
        self,
        session_id: str,
        memory: MemoryService,
        analysis: AnalysisService,
        code_analysis: CodeAnalysisService | None,
        hints: HintService,
        contradictions: ContradictionService,
        duration_minutes: float,
        target_level: str,
    ) -> GeneratedReport:
        """Generate a comprehensive interview report.

        Collects data from all services and delegates to ReportGenerator.
        """
        # Build ReportInput from all services
        code_progress = "unknown"
        detected_approach = None
        if code_analysis:
            result = code_analysis.get_latest_result()
            if result:
                code_progress = result.progress_state.value
                if result.detected_approach:
                    detected_approach = result.detected_approach.name

        report_input = ReportInput(
            session_id=session_id,
            duration_minutes=duration_minutes,
            target_level=target_level,
            fact_store=memory.fact_store,
            analysis_results=analysis.get_recent_analyses(count=50),
            hint_events=hints._hint_events,
            hint_metrics=hints.get_metrics(),
            contradictions=contradictions.contradictions,
            transcript_summary=memory._rolling_summary,
            code_progress=code_progress,
            detected_approach=detected_approach,
        )

        report = await self._generator.generate(report_input)

        logger.info(
            "report_generated",
            session_id=session_id,
            recommendation=report.recommendation.value,
            confidence=report.confidence_level,
        )

        return report
