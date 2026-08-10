# core/logging.py
import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from core.config import Environment, settings


class RequestIdFilter(logging.Filter):
    """Stamp the current ``request_id`` onto every log record.

    A single point of enrichment: the worker sets ``current_request_id`` per
    message, and every line emitted while handling it carries that id without
    each call site repeating it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Lazy import to avoid a circular import — configure_logging() runs at
        # startup before other core modules are fully initialised.
        from core.context_vars import current_request_id

        record.request_id = current_request_id.get() or "-"
        return True


def configure_logging() -> None:
    """Configure the root logger from the environment. Called once at startup.

    local      → human-readable console output, DEBUG level
    staging    → JSON stdout, INFO level
    production → logs exported via OpenTelemetry (OTLP); no console handler

    Never configure per-module — use ``logging.getLogger(__name__)`` elsewhere.
    """
    root = logging.getLogger()
    root.handlers.clear()

    if settings.ENV == Environment.LOCAL:
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | req=%(request_id)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
    elif settings.ENV == Environment.STAGING:
        handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(
            JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
                rename_fields={"levelname": "level", "asctime": "timestamp"},
            )
        )
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    else:
        # production: logs exported via OpenTelemetry (OTLP), no console output
        root.setLevel(logging.INFO)
