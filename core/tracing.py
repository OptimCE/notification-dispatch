"""OpenTelemetry logs + metrics export, worker-only.

Trimmed from the sibling services: no FastAPI/ASGI span helpers (this service has
no HTTP surface). In LOCAL/TEST the default no-op providers are kept, so every
metric ``.add()``/``.record()`` is a cheap no-op and nothing is exported.
"""

from __future__ import annotations

import atexit
import logging
import os
import uuid

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

# SECONDS. The OTLP HTTP exporters take `timeout` in seconds - the SDK's own
# DEFAULT_TIMEOUT is 10 and it is used as `deadline_sec = time() + self._timeout`
# - while `MeterProvider.shutdown` and every MetricReader take MILLISECONDS.
# Handing one constant named `_MS` to both, which this template did, gave the
# exporters an 83-MINUTE HTTP timeout: a collector that accepts a connection and
# then hangs holds the export thread for the rest of the afternoon, and every
# batch queued behind it with it.
_EXPORTER_TIMEOUT_SECONDS = 5
_EXPORTER_TIMEOUT_MS = _EXPORTER_TIMEOUT_SECONDS * 1000
_METRIC_EXPORT_INTERVAL_MS = 15000


def setup_tracer_provider() -> None:
    """Configure OTLP logs + metrics. No-op in local/test (no collector)."""
    if settings.ENV in (Environment.LOCAL, Environment.TEST):
        return

    # AND when there is nowhere to send it, whatever the ENV.
    #
    # The LOGGING_* triple is enforced in `core/config.py` only under PRODUCTION,
    # while this function exports for anything that is not LOCAL - and the
    # staging template ships the URLs BLANK. A blank endpoint is not inert: the
    # OTLP exporter resolves it to its DEFAULT_ENDPOINT, `http://localhost:4318/`,
    # so every container quietly starts a 15-second export loop and an OTLP
    # root-log handler aimed at a port nothing is listening on.
    #
    # Verified against the pinned SDK, not inferred.
    if not settings.LOGGING_METRICS_URL or not settings.LOGGING_LOGS_URL:
        logger.warning(
            "telemetry not configured - LOGGING_LOGS_URL/LOGGING_METRICS_URL are "
            "blank, so no exporter is started (env=%s)",
            settings.ENV,
        )
        return

    # THIS SERVICE'S NAME. It read `document-generation` - byte-identical to that
    # sibling's, because `core/tracing.py` is copied between the annexes and this
    # line was the one nobody adapted. Both services are deployed, so in the
    # observability backend they were one identity: a rising error rate could
    # have been either, and neither could be alerted on alone.
    #
    # No `-backend` suffix, unlike billing or live-data: this service has no API
    # at all, only `Dockerfile.worker`. `core/metrics.py` already names its meter
    # `notification-dispatch`, so this also makes the two agree.
    resource = Resource.create(
        {
            "service.name": "notification-dispatch",
            "env": settings.ENV,
            # `component`-free, unlike live-data: these services have one
            # container each today. What this separates is REPLICAS - two
            # processes of the same deployment otherwise emit byte-identical
            # stream identities and the backend merges their cumulative
            # histograms into nonsense.
            #
            # The container id, which Docker puts in HOSTNAME. The uuid fallback
            # is for a bare process; it changes on restart, which is what
            # `service.instance.id` is defined to do.
            "service.instance.id": os.getenv("HOSTNAME") or str(uuid.uuid4()),
        }
    )
    headers = {"Authorization": f"Bearer {settings.LOGGING_TOKEN}"}

    # --- Logs ---
    log_exporter = OTLPLogExporter(
        endpoint=settings.LOGGING_LOGS_URL,
        headers=headers,
        timeout=_EXPORTER_TIMEOUT_SECONDS,
    )
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    handler = LoggingHandler(level=logging.INFO, logger_provider=log_provider)
    handler.addFilter(RequestIdFilter())
    logging.getLogger().addHandler(handler)

    global _log_handler
    _log_handler = handler

    # --- Metrics ---
    metric_exporter = OTLPMetricExporter(
        endpoint=settings.LOGGING_METRICS_URL,
        headers=headers,
        timeout=_EXPORTER_TIMEOUT_SECONDS,
    )
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter, export_interval_millis=_METRIC_EXPORT_INTERVAL_MS
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    global _meter_provider, _log_provider
    _meter_provider = meter_provider
    _log_provider = log_provider

    # AFTER the SDK's own, so LIFO puts this one first. See
    # `shutdown_telemetry`: the SDK flushes too, on a 30-second budget
    # against Docker's 10-second stop grace.
    atexit.register(shutdown_telemetry)

    logger.info("OpenTelemetry telemetry configured (logs, metrics)")


# Kept so `shutdown_telemetry` can reach them. `metrics.get_meter_provider()`
# returns the API-level object, which has no `shutdown`.
_meter_provider: MeterProvider | None = None
_log_provider: LoggerProvider | None = None
# Held so the flush can DETACH it - see `shutdown_telemetry`.
_log_handler: logging.Handler | None = None


def shutdown_telemetry(timeout_millis: int = _EXPORTER_TIMEOUT_MS) -> None:
    """Flush the last batch, WITHIN A BUDGET DOCKER WILL ALLOW.

    ---------------------------------------------------------------------------
    THE SDK ALREADY FLUSHES AT EXIT. THIS IS ABOUT THE TIMEOUT, NOT THE FLUSH.

    `MeterProvider.__init__` takes `shutdown_on_exit=True` by default and
    registers `atexit(self.shutdown)`, and that handler really does export - one
    final collect happens even with the interval nowhere near due.

    What it does NOT do is bound itself usefully. `atexit` calls `shutdown()`
    with no arguments, so the budget is the SDK's default of **30 seconds**,
    while Docker sends SIGKILL **10 seconds** after SIGTERM. Against an
    unreachable or slow collector - which is exactly when a shutdown blocks - the
    container is killed mid-flush: the data is lost anyway AND every deploy takes
    the full grace period.

    Registered with `atexit` below rather than called from each entrypoint.
    atexit is LIFO and the SDK registers its handler inside `MeterProvider(...)`,
    so this one runs FIRST, with this budget, and the SDK's becomes a no-op
    ("shutdown can only be called once"). It therefore covers every entrypoint,
    including any added later. Nothing helps against SIGKILL itself.
    ---------------------------------------------------------------------------

    Safe when no provider was installed, and safe to call twice.
    """
    # LOGS FIRST, metrics second: a caller's last log line is written before this
    # runs. `BatchLogRecordProcessor` batches on its own timer exactly like the
    # metric reader, so an unflushed log provider drops the final lines that
    # distinguish a clean stop from a kill.
    #
    # `force_flush` for logs and `shutdown` for metrics, because those are the
    # two that take a BUDGET: `LoggerProvider.shutdown()` has no timeout
    # parameter at all and would run unbounded.
    global _log_handler
    if _log_provider is not None:
        try:
            _log_provider.force_flush(timeout_millis=timeout_millis)
            # AND DETACH IT. `logging.shutdown` is registered with atexit by the
            # logging module at import, so LIFO runs it LAST - after the
            # interpreter has begun tearing down, where `LoggingHandler.flush()`
            # spawning a thread raises `RuntimeError: can't create new thread at
            # interpreter shutdown`. Every container printed that traceback on
            # exit. Once the batch is flushed there is nothing left to lose by
            # removing the handler, and nothing left for `logging.shutdown` to do.
            if _log_handler is not None:
                logging.getLogger().removeHandler(_log_handler)
                _log_handler = None
        except Exception:
            # A collector that is down or slow must not stop a container from
            # exiting. The process is on its way out; there is nowhere to report
            # to, and the next line would go to the handler being flushed.
            logger.warning("flushing logs on shutdown failed", exc_info=True)

    if _meter_provider is not None:
        try:
            _meter_provider.shutdown(timeout_millis=timeout_millis)
        except Exception:
            logger.warning("flushing metrics on shutdown failed", exc_info=True)
