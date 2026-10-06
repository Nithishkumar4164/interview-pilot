"""Every model in the task -> provider map exists (real API calls; opt-in).

    LIVE_LLM=1 uv run pytest tests/test_live_models.py

Each call sends only "Reply with the single word OK." with a 5-token cap.
The map pointed at retired Claude 4 IDs that the API answered with 404, so
every call failed over (or failed outright where no OpenAI key was set).
"""

import os

import pytest

from src.config import settings
from src.llm.config import DEFAULT_TASK_MAPPINGS

pytestmark = pytest.mark.skipif(os.environ.get("LIVE_LLM") != "1", reason="real API calls; set LIVE_LLM=1")

PROMPT = "Reply with the single word OK."


def _configured(provider: str) -> list[str]:
    models = set()
    for m in DEFAULT_TASK_MAPPINGS.values():
        if m.primary_provider == provider:
            models.add(m.primary_model)
        if m.fallback_provider == provider:
            models.add(m.fallback_model)
    return sorted(models)


@pytest.mark.parametrize("model", _configured("anthropic"))
async def test_anthropic_model_exists(model: str) -> None:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(model=model, max_tokens=5, messages=[{"role": "user", "content": PROMPT}])
    assert response.content


@pytest.mark.parametrize("model", _configured("openai"))
async def test_openai_model_exists(model: str) -> None:
    if settings.openai_api_key in ("", "test-key"):  # conftest's placeholder
        pytest.skip("no OpenAI key configured")
    import openai

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=model, max_tokens=5, messages=[{"role": "user", "content": PROMPT}]
    )
    assert response.choices
