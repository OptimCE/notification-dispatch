"""This worker must report under its OWN identity.

`core/tracing.py` is copy-pasted between the annexes and the `service.name` line
is the one that gets forgotten. This one read `document-generation` - byte-
identical to that sibling's - and BOTH services are deployed, so in the
observability backend they were a single identity: a rising error rate could have
been either, and neither could be alerted on alone.

Nothing could have caught it. `setup_tracer_provider` returns early for LOCAL and
TEST, so no test and no dev run reaches the line, and the only symptom is on a
dashboard nobody owns yet.
"""

import re
from pathlib import Path

import pytest

SERVICE = "notification-dispatch"
TRACING = Path(__file__).resolve().parents[1] / "core" / "tracing.py"


def _resource_service_name() -> str:
    """Read it out of the source rather than by calling the function.

    `setup_tracer_provider()` cannot be called here: under ENV=test it returns at
    the first line, and forcing ENV past that would build real OTLP exporters,
    attach a handler to the root logger and burn the process's one-shot
    MeterProvider slot inside the suite.
    """
    match = re.search(r'"service\.name":\s*"([^"]+)"', TRACING.read_text(encoding="utf-8"))
    assert match, "no service.name in core/tracing.py"
    return match.group(1)


def test_the_resource_names_this_service() -> None:
    assert _resource_service_name() == SERVICE


def test_it_is_not_a_siblings_name() -> None:
    """The specific regression, named. `document-generation` is a real, deployed
    service in the same compose project."""
    assert _resource_service_name() != "document-generation"


def test_the_meter_and_the_resource_agree() -> None:
    """They disagreed for as long as the bug existed: the meter was already
    `notification-dispatch` while the resource claimed to be another service."""
    from core import metrics

    metrics_src = (Path(__file__).resolve().parents[1] / "core" / "metrics.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'get_meter\(\s*"([^"]+)"', metrics_src)
    assert match, "no get_meter name in core/metrics.py"
    assert match.group(1) == _resource_service_name()
    assert metrics is not None


@pytest.mark.parametrize("env", ["local", "test"])
def test_it_stays_a_no_op_where_there_is_no_collector(env: str) -> None:
    """The early return is what keeps the suite from building real exporters -
    and it is also why nothing here can assert the resource at runtime."""
    from core.config import Environment, settings
    from core.tracing import setup_tracer_provider

    original = settings.ENV
    try:
        settings.ENV = Environment(env)
        setup_tracer_provider()  # must not raise, must not export
    finally:
        settings.ENV = original
