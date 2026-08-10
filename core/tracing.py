"""OpenTelemetry logs + metrics export, worker-only.

Trimmed from the sibling services: no FastAPI/ASGI span helpers (this service has
no HTTP surface). In LOCAL/TEST the default no-op providers are kept, so every
metric ``.add()``/``.record()`` is a cheap no-op and nothing is exported.
"""

from __future__ import annotations

import logging

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from core.config import Environment, settings
from core.logging import RequestIdFilter

logger = logging.getLogger(__name__)

_EXPORTER_TIMEOUT_MS = 5000
_METRIC_EXPORT_INTERVAL_MS = 15000


def setup_tracer_provider() -> None:
    """Configure OTLP logs + metrics. No-op in local/test (no collector)."""
    if settings.ENV in (Environment.LOCAL, Environment.TEST):
        return

    resource = Resource.create({"service.name": "document-generation", "env": settings.ENV})
    headers = {"Authorization": f"Bearer {settings.LOGGING_TOKEN}"}

    # --- Logs ---
    log_exporter = OTLPLogExporter(
        endpoint=settings.LOGGING_LOGS_URL,
        headers=headers,
        timeout=_EXPORTER_TIMEOUT_MS,
    )
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    handler = LoggingHandler(level=logging.INFO, logger_provider=log_provider)
    handler.addFilter(RequestIdFilter())
    logging.getLogger().addHandler(handler)

    # --- Metrics ---
    metric_exporter = OTLPMetricExporter(
        endpoint=settings.LOGGING_METRICS_URL,
        headers=headers,
        timeout=_EXPORTER_TIMEOUT_MS,
    )
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter, export_interval_millis=_METRIC_EXPORT_INTERVAL_MS
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    logger.info("OpenTelemetry telemetry configured (logs, metrics)")
