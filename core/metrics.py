"""Application metrics for the notification-dispatch worker.

Instruments are created at import against the OTel proxy provider and rebind to
the real provider once ``core.tracing.setup_tracer_provider`` installs it (in
staging/production). In local/test the proxy stays a no-op, so every ``.add()``
is a cheap dispatch with no side effects.

Naming follows OTel semantic conventions: dotted lowercase, ``.total`` on
counters.

These are the numbers worth alerting on. A queue that only ever *retries* looks
identical to a healthy one in the logs, and a rising ``failed`` count is the
signal that a template or a provider credential broke — the whole class of
failure this workstream exists to stop being silent.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("notification-dispatch")

messages_dispatched = _meter.create_counter(
    name="notification.dispatch.messages.total",
    description="Outbound messages settled by the worker, labelled by outcome",
    unit="1",
)

messages_reclaimed = _meter.create_counter(
    name="notification.dispatch.reclaimed.total",
    description="Messages returned to PENDING because the worker holding them died",
    unit="1",
)

addresses_suppressed = _meter.create_counter(
    name="notification.dispatch.suppressed_addresses.total",
    description="Addresses added to the suppression list, labelled by reason",
    unit="1",
)
