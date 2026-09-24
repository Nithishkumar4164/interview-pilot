"""OpenTelemetry tracing setup.

Tracing is off unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` (the standard OTel env
var) is set: without a configured TracerProvider every span is a no-op, so
nothing leaves the process by default. Exporter settings (headers, protocol
paths) follow the standard ``OTEL_EXPORTER_OTLP_*`` variables.

Spans never carry prompt or completion text — prompts contain candidate
transcripts and resumes.
"""

import os

from opentelemetry import trace

from src.utils.logging import get_logger

logger = get_logger("tracing")

SERVICE_NAME = "interview-pilot-backend"

_configured = False


def setup_tracing() -> bool:
    """Install an OTLP-exporting TracerProvider if an endpoint is configured.

    Returns True when tracing was enabled.
    """
    global _configured
    if _configured:
        return True
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        logger.info("tracing_disabled", reason="OTEL_EXPORTER_OTLP_ENDPOINT not set")
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True
    logger.info("tracing_enabled", endpoint=os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])
    return True


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
