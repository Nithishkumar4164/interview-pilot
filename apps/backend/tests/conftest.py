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
