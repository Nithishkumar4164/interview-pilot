import os

# Set dummy API keys before importing app modules so startup validation passes
os.environ.setdefault("INTERVIEW_PILOT_ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("INTERVIEW_PILOT_OPENAI_API_KEY", "test-key")

import pytest

from src.llm.providers.mock import MockProvider
from src.llm.health import HealthMonitor
from src.llm.failover import FailoverManager


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def health_monitor() -> HealthMonitor:
    return HealthMonitor()


@pytest.fixture
def failover_manager(health_monitor: HealthMonitor) -> FailoverManager:
    return FailoverManager(health_monitor)
