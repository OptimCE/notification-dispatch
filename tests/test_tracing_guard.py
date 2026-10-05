"""Telemetry must stay OFF when there is nowhere to send it.

---------------------------------------------------------------------------
WHY THIS EXISTS.

`core/config.py` enforces the `LOGGING_*` triple only under PRODUCTION, while
`setup_tracer_provider` exports for anything that is not LOCAL - and
`.env.staging.exemple` ships the URLs BLANK, in several annexes with a comment
saying that blank "boots cleanly".

It did boot. A blank endpoint is not inert: the OTLP HTTP exporter resolves it to
its own `DEFAULT_ENDPOINT`, `http://localhost:4318/`, so each container started a
15-second export loop and attached an OTLP handler to the ROOT logger, both aimed
at a port nothing was listening on.

Nothing could have caught it. The function returns early under LOCAL, so no dev
run reached the code - which is why the same defect was in all eight annexes at
once. `core/tracing.py` is copy-pasted between them.
---------------------------------------------------------------------------

THESE TESTS WATCH THE EXPORTER CONSTRUCTORS, NOT THE GLOBAL PROVIDER.

The obvious assertion - "the meter provider is still the API's no-op proxy" -
is not available here. `metrics.set_meter_provider` is once-only per PROCESS and
several of these repos have a `tests/core/test_metrics.py` that installs a real
`MeterProvider` of its own, so by the time this file runs the global is already
whatever that test left behind. Asserting on it passes or fails by collection
order.

Patching the two exporter classes is order-independent and says exactly what is
meant: with nowhere to send, nothing is constructed.
"""

import inspect
import logging
from typing import Any

import pytest

import core.tracing as tracing
from core.config import Environment, settings
from core.tracing import setup_tracer_provider

# Untyped on purpose. `live-data`'s `setup_tracer_provider` takes a `component`
# argument and every other annexe's takes none; this file is copied between them
# verbatim, so a statically-typed call would be a mypy error in seven repos or
# the eighth. The signature decides at runtime.
_SETUP: Any = setup_tracer_provider


def _setup() -> None:
    if inspect.signature(_SETUP).parameters:
        _SETUP("test")
    else:
        _SETUP()


@pytest.fixture
def no_exporter_may_be_built(monkeypatch):
    """Both OTLP constructors become landmines. Returns the build log."""
    built: list[str] = []

    def forbid(name):
        def _ctor(*args, **kwargs):
            built.append(name)
            raise AssertionError(f"{name} was constructed with nowhere to send")

        return _ctor

    monkeypatch.setattr(tracing, "OTLPMetricExporter", forbid("OTLPMetricExporter"))
    monkeypatch.setattr(tracing, "OTLPLogExporter", forbid("OTLPLogExporter"))
    return built


@pytest.fixture
def record_what_is_built(monkeypatch):
    """The positive control's counterpart: record instead of raising, and keep
    the real provider out of this process."""
    built: list[str] = []

    class Recorder:
        def __init__(self, name):
            self._name = name

        def __call__(self, *args, **kwargs):
            built.append(self._name)
            return object()

    monkeypatch.setattr(tracing, "OTLPMetricExporter", Recorder("metrics"))
    monkeypatch.setattr(tracing, "OTLPLogExporter", Recorder("logs"))
    monkeypatch.setattr(tracing, "BatchLogRecordProcessor", lambda *a, **k: object())
    monkeypatch.setattr(tracing, "PeriodicExportingMetricReader", lambda *a, **k: object())
    monkeypatch.setattr(tracing, "LoggerProvider", lambda *a, **k: _NullProvider())
    monkeypatch.setattr(tracing, "MeterProvider", lambda *a, **k: object())
    monkeypatch.setattr(tracing.metrics, "set_meter_provider", lambda *a, **k: None)
    monkeypatch.setattr(tracing, "LoggingHandler", lambda *a, **k: logging.NullHandler())
    return built


class _NullProvider:
    def add_log_record_processor(self, *args, **kwargs):
        return None


@pytest.fixture
def built_with(monkeypatch):
    """Every kwargs dict the two OTLP constructors were called with."""
    calls: list[dict] = []

    class Recorder:
        def __call__(self, *args, **kwargs):
            calls.append(kwargs)
            return object()

    monkeypatch.setattr(tracing, "OTLPMetricExporter", Recorder())
    monkeypatch.setattr(tracing, "OTLPLogExporter", Recorder())
    return calls


@pytest.fixture
def built_resources(monkeypatch):
    """Every Resource the setup built, as a plain dict."""
    seen: list[dict] = []
    real = tracing.Resource.create

    def spy(attributes):
        seen.append(dict(attributes))
        return real(attributes)

    monkeypatch.setattr(tracing.Resource, "create", staticmethod(spy))
    return seen


def test_blank_urls_build_no_exporter(monkeypatch, no_exporter_may_be_built):
    """The shipped staging configuration, verbatim."""
    monkeypatch.setattr(settings, "ENV", Environment.STAGING)
    monkeypatch.setattr(settings, "LOGGING_METRICS_URL", "")
    monkeypatch.setattr(settings, "LOGGING_LOGS_URL", "")
    monkeypatch.setattr(settings, "LOGGING_TOKEN", "")

    setup_tracer_provider()
    assert no_exporter_may_be_built == []


def test_it_says_so_rather_than_failing_silently(monkeypatch, caplog):
    """Silence is indistinguishable from a working exporter, which is how the
    original defect survived: the only symptom was on a dashboard."""
    monkeypatch.setattr(settings, "ENV", Environment.STAGING)
    monkeypatch.setattr(settings, "LOGGING_METRICS_URL", "")
    monkeypatch.setattr(settings, "LOGGING_LOGS_URL", "")

    with caplog.at_level(logging.WARNING, logger="core.tracing"):
        setup_tracer_provider()
    assert any("telemetry not configured" in record.message for record in caplog.records)


def test_local_builds_no_exporter_even_with_urls_set(monkeypatch, no_exporter_may_be_built):
    """The other guard, which this file must not accidentally depend on."""
    monkeypatch.setattr(settings, "ENV", Environment.LOCAL)
    monkeypatch.setattr(settings, "LOGGING_METRICS_URL", "http://collector:4318/v1/metrics")
    monkeypatch.setattr(settings, "LOGGING_LOGS_URL", "http://collector:4318/v1/logs")

    setup_tracer_provider()
    assert no_exporter_may_be_built == []


def test_with_urls_it_does_build_them(monkeypatch, record_what_is_built):
    """THE POSITIVE CONTROL, and the one that makes the rest mean anything.

    Without it every assertion above is satisfied by a function that returns on
    its first line - which is precisely the bug in the opposite direction, and
    would silently disable telemetry in production.
    """
    monkeypatch.setattr(settings, "ENV", Environment.STAGING)
    monkeypatch.setattr(settings, "LOGGING_METRICS_URL", "http://collector:4318/v1/metrics")
    monkeypatch.setattr(settings, "LOGGING_LOGS_URL", "http://collector:4318/v1/logs")
    monkeypatch.setattr(settings, "LOGGING_TOKEN", "a-token")

    setup_tracer_provider()
    assert sorted(record_what_is_built) == ["logs", "metrics"]


def test_a_blank_endpoint_really_does_resolve_to_localhost():
    """The mechanism, pinned. If a future SDK stopped defaulting the endpoint the
    guard would be belt without braces - worth knowing, not worth removing."""
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

    assert "localhost:4318" in OTLPMetricExporter(endpoint="")._endpoint


def test_the_exporters_get_seconds_not_milliseconds(monkeypatch, record_what_is_built, built_with):
    """5000 handed to a constructor that takes SECONDS is 83 minutes.

    The SDK's own `DEFAULT_TIMEOUT` is 10 and it is used as
    `deadline_sec = time() + self._timeout`, while `MeterProvider.shutdown` and
    every MetricReader take MILLISECONDS. One constant named `_MS` was passed to
    both, so a collector that accepted a connection and then hung would hold the
    export thread for the rest of the afternoon.
    """
    monkeypatch.setattr(settings, "ENV", Environment.STAGING)
    monkeypatch.setattr(settings, "LOGGING_METRICS_URL", "http://collector:4318/v1/metrics")
    monkeypatch.setattr(settings, "LOGGING_LOGS_URL", "http://collector:4318/v1/logs")
    _setup()

    assert len(built_with) == 2, f"both exporters must be built, got {built_with}"
    for kwargs in built_with:
        timeout = kwargs.get("timeout")
        assert timeout is not None, f"an exporter was built with no timeout: {kwargs}"
        assert timeout < 60, (
            f"timeout={timeout} is being read as SECONDS by the exporter "
            f"({timeout / 60:.0f} minutes). Pass the *_SECONDS constant."
        )


def test_replicas_are_distinguishable(monkeypatch, record_what_is_built, built_resources):
    """Without `service.instance.id` two replicas emit byte-identical stream
    identities and the backend merges their cumulative histograms."""
    monkeypatch.setattr(settings, "ENV", Environment.STAGING)
    monkeypatch.setattr(settings, "LOGGING_METRICS_URL", "http://collector:4318/v1/metrics")
    monkeypatch.setattr(settings, "LOGGING_LOGS_URL", "http://collector:4318/v1/logs")
    _setup()

    assert built_resources, "no Resource was built"
    for attributes in built_resources:
        assert attributes.get("service.instance.id"), attributes
        assert attributes.get("service.name"), attributes


def test_the_flush_is_bounded_below_dockers_stop_grace():
    """The SDK's atexit handler flushes too - on a 30-second budget, against
    Docker's 10-second stop grace. Against a slow collector the container is
    SIGKILLed mid-flush: the data is lost anyway and every deploy pays the full
    grace period."""
    default = inspect.signature(tracing.shutdown_telemetry).parameters["timeout_millis"].default
    assert default < 10_000, "Docker's default stop grace period"


def test_the_bounded_flush_is_registered_before_the_sdks(record_what_is_built, monkeypatch):
    """atexit is LIFO, so ours must be registered AFTER the SDK's to run BEFORE
    it. Registered inside `setup_tracer_provider`, after `MeterProvider(...)`."""
    registered: list = []
    monkeypatch.setattr(tracing.atexit, "register", lambda fn, *a, **k: registered.append(fn))

    monkeypatch.setattr(settings, "ENV", Environment.STAGING)
    monkeypatch.setattr(settings, "LOGGING_METRICS_URL", "http://collector:4318/v1/metrics")
    monkeypatch.setattr(settings, "LOGGING_LOGS_URL", "http://collector:4318/v1/logs")
    _setup()

    assert tracing.shutdown_telemetry in registered, (
        "the bounded flush is not registered with atexit, so only the SDK's "
        "unbounded 30-second handler runs"
    )


def test_flushing_detaches_the_log_handler(monkeypatch):
    """`logging.shutdown` is registered with atexit by the logging module at
    import, so LIFO runs it LAST - after the interpreter has begun tearing down,
    where `LoggingHandler.flush()` spawning a thread raises `RuntimeError: can't
    create new thread at interpreter shutdown`. Every container printed that
    traceback on exit."""
    flushed: list = []

    class Provider:
        def force_flush(self, timeout_millis=None):
            flushed.append(timeout_millis)

    handler = logging.NullHandler()
    logging.getLogger().addHandler(handler)
    monkeypatch.setattr(tracing, "_log_provider", Provider())
    monkeypatch.setattr(tracing, "_log_handler", handler)
    monkeypatch.setattr(tracing, "_meter_provider", None)
    try:
        tracing.shutdown_telemetry()
        assert flushed, "the log provider was not flushed"
        assert handler not in logging.getLogger().handlers, (
            "the OTLP handler is still attached, so logging.shutdown will try to "
            "flush it during interpreter teardown"
        )
    finally:
        logging.getLogger().removeHandler(handler)
