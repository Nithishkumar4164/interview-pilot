import json

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.services.storage_service import StorageService
from src.utils.logging import get_logger
from src.utils.text_extraction import (
    detect_file_type,
    extract_text_from_docx,
    extract_text_from_pdf,
)

logger = get_logger("resume")

RESUME_EXTRACTION_SYSTEM_PROMPT = """You are an expert resume analyzer. Extract structured data from the resume text provided.

Return a JSON object with these exact fields:
{
  "name": "Full name",
  "email": "Email address, or null if the resume has none",
  "phone": "Phone number as written, or null if the resume has none",
  "current_role": "Current or most recent job title",
  "years_experience": <number>,
  "companies": ["Company 1", "Company 2"],
  "tech_stack": ["Technology 1", "Technology 2"],
  "key_projects": [
    {
      "name": "Project name",
      "description": "Brief description",
      "technologies": ["Tech 1", "Tech 2"],
      "claims": ["Specific measurable claim"]
    }
  ],
  "education": [
    {
      "degree": "Degree name",
      "institution": "University name",
      "year": <year or null>
    }
  ],
  "certifications": ["Cert 1"],
  "verifiable_claims": [
    {
      "claim": "The specific claim from the resume",
      "probe_question": "A question to verify this claim that only someone who did the work could answer"
    }
  ]
}

Copy name, email and phone exactly as the resume writes them; never invent contact details.

Focus on extracting verifiable_claims — specific, measurable assertions that can be probed during an interview. For example:
- "Improved API latency by 40%" → "What profiling tools did you use? What was the baseline latency?"
- "Led a team of 8 engineers" → "How did you handle disagreements within the team? Can you describe your sprint process?"
- "Built a real-time data pipeline processing 1M events/sec" → "What was your partitioning strategy? How did you handle backpressure?"

Return ONLY valid JSON, no markdown fences or explanation."""


# A full extraction of a detailed 5-job resume took 3,050 output tokens on
# claude-sonnet-5 (2026-10-08); only generated tokens are billed.
RESUME_EXTRACTION_MAX_TOKENS = 8000


class ResumeService:
    """Orchestrates resume upload, text extraction, and LLM-powered structured extraction."""

    def __init__(self, storage: StorageService | None, llm: FailoverManager) -> None:
        self.storage = storage
        self.llm = llm

    def upload_resume(self, file_content: bytes, content_type: str, filename: str) -> str:
        """Upload a resume file and return its reference ID."""
        return self.storage.upload_file(file_content, content_type, filename)

    def extract_text(self, file_content: bytes) -> str:
        """Extract plain text from a resume file (PDF or DOCX)."""
        file_type = detect_file_type(file_content)

        if file_type == "pdf":
            return extract_text_from_pdf(file_content)
        elif file_type == "docx":
            return extract_text_from_docx(file_content)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")

    async def extract_structured_data(self, resume_text: str) -> dict:
        """Send resume text through LLM to extract structured data."""
        context = AnalysisContext(
            system_prompt=RESUME_EXTRACTION_SYSTEM_PROMPT,
            user_content=f"Resume text:\n\n{resume_text}",
            task_type=TaskType.RESUME_EXTRACTION,
        )

        config = LLMConfig(
            max_tokens=RESUME_EXTRACTION_MAX_TOKENS,
            temperature=0.3,
            response_format="json",
        )

        result = await self.llm.analyze(context, config)
        if result.truncated:
            logger.error(
                "resume_extraction_truncated", max_tokens=config.max_tokens, output_tokens=result.output_tokens
            )
            raise ValueError(
                f"Resume extraction was cut off at the {config.max_tokens}-token output limit; "
                "the resume is too long to extract in one pass"
            )

        if result.parsed:
            logger.info(
                "resume_extracted",
                name=result.parsed.get("name"),
                claims_count=len(result.parsed.get("verifiable_claims", [])),
                latency_ms=round(result.latency_ms, 1),
            )
            return result.parsed

        # Attempt to parse the raw content if parsed is None
        try:
            # Strip markdown fences if present
            content = result.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                content = content.rsplit("```", 1)[0]
            return json.loads(content)
        except (json.JSONDecodeError, IndexError) as e:
            logger.error("resume_extraction_parse_failed", content=result.content[:500])
            raise ValueError("Failed to parse LLM response as structured resume data") from e

    async def process_resume(self, file_content: bytes) -> dict:
        """Full pipeline: extract text → extract structured data."""
        text = self.extract_text(file_content)
        if not text.strip():
            raise ValueError("No text could be extracted from the resume")

        structured = await self.extract_structured_data(text)
        structured["_raw_text"] = text
        return structured
