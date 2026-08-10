"""Context for log enrichment.

This worker has no request, no user and no community: it polls a table and sends
what it finds. The only context worth carrying is the id of the
``outbound_message`` being handled, so a line in the log traces back to a row.
``core.logging.RequestIdFilter`` stamps it onto every record.
"""

from __future__ import annotations

from contextvars import ContextVar

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
