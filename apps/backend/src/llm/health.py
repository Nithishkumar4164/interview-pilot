import asyncio
from datetime import UTC, datetime

from src.llm.interface import ILLMProvider, ProviderHealthStatus, ProviderStatus
from src.utils.logging import get_logger

logger = get_logger("llm.health")

# Circuit breaker thresholds
FAILURE_THRESHOLD = 3  # Open circuit after N consecutive failures
HALF_OPEN_INTERVAL_S = 30  # Try a health check after this many seconds in open state
HEALTH_CHECK_INTERVAL_S = 10  # Check every N seconds


class CircuitState:
    """Tracks circuit breaker state for a single provider."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        self.consecutive_failures = 0
        self.is_open = False
        self.last_failure_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.average_latency_ms: float | None = None

    @property
    def is_available(self) -> bool:
        if not self.is_open:
            return True
        # Half-open: allow a check if enough time has passed
        if self.last_failure_at:
            elapsed = (datetime.now(UTC) - self.last_failure_at).total_seconds()
            return elapsed >= HALF_OPEN_INTERVAL_S
        return True

    def record_success(self, latency_ms: float) -> None:
        self.consecutive_failures = 0
        self.is_open = False
        self.last_success_at = datetime.now(UTC)
        # Exponential moving average
        if self.average_latency_ms is None:
            self.average_latency_ms = latency_ms
        else:
            self.average_latency_ms = 0.7 * self.average_latency_ms + 0.3 * latency_ms

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_at = datetime.now(UTC)
        if self.consecutive_failures >= FAILURE_THRESHOLD:
            self.is_open = True
            logger.warning(
                "circuit_opened",
                provider=self.provider_name,
                failures=self.consecutive_failures,
            )

    def to_status(self) -> ProviderStatus:
        if self.is_open:
            status = ProviderHealthStatus.DOWN
        elif self.consecutive_failures > 0:
            status = ProviderHealthStatus.DEGRADED
        else:
            status = ProviderHealthStatus.HEALTHY

        return ProviderStatus(
            provider=self.provider_name,
            status=status,
            last_check_at=datetime.now(UTC).isoformat(),
            consecutive_failures=self.consecutive_failures,
            average_latency_ms=self.average_latency_ms,
        )


class HealthMonitor:
    """Monitors LLM provider health using circuit breaker pattern."""

    def __init__(self) -> None:
        self._circuits: dict[str, CircuitState] = {}
        self._providers: dict[str, ILLMProvider] = {}
        self._task: asyncio.Task | None = None

    def register_provider(self, name: str, provider: ILLMProvider) -> None:
        self._providers[name] = provider
        self._circuits[name] = CircuitState(name)

    def is_available(self, provider_name: str) -> bool:
        circuit = self._circuits.get(provider_name)
        # Unknown provider = not yet registered = assume available
        return circuit.is_available if circuit else True

    def get_status(self, provider_name: str) -> ProviderStatus | None:
        circuit = self._circuits.get(provider_name)
        return circuit.to_status() if circuit else None

    def get_all_statuses(self) -> dict[str, ProviderStatus]:
        return {name: circuit.to_status() for name, circuit in self._circuits.items()}

    def record_success(self, provider_name: str, latency_ms: float) -> None:
        circuit = self._circuits.get(provider_name)
        if circuit:
            circuit.record_success(latency_ms)

    def record_failure(self, provider_name: str) -> None:
        circuit = self._circuits.get(provider_name)
        if circuit:
            circuit.record_failure()

    async def _check_loop(self) -> None:
        """Background loop that pings providers every HEALTH_CHECK_INTERVAL_S."""
        while True:
            for name, provider in self._providers.items():
                circuit = self._circuits.get(name)
                if circuit and (not circuit.is_open or circuit.is_available):
                    try:
                        status = await provider.health_check()
                        if status.status == ProviderHealthStatus.HEALTHY:
                            self.record_success(name, status.average_latency_ms or 0)
                        else:
                            self.record_failure(name)
                    except Exception:
                        self.record_failure(name)
            await asyncio.sleep(HEALTH_CHECK_INTERVAL_S)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._check_loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
