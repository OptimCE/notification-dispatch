"""The email transport port.

A Protocol plus interchangeable adapters, mirroring ``billing/ports/events.py``
and ``document-generation``'s adapter split. Structural typing: an adapter does
NOT subclass this, matching ``ports/email_noop.py``.

Deliberately NOT ``billing/ports/email.py``'s ``async def send(msg) -> None``.
The dispatch loop has to decide retry-vs-fail and whether the ADDRESS was at
fault, so a transport must raise ``TransportError`` with that judgement rather
than a bare exception the loop would have to guess about.

The timeout lives on the constructor, not on ``send``. That is partly ruff
(``ASYNC109`` flags a ``timeout`` parameter on an ``async def``) and partly
design: the factory validates a transport's settings at boot, and a per-call
timeout would put that knob somewhere the factory never sees.
"""

from __future__ import annotations

from typing import Protocol

from domain.message import EmailMessage


class EmailTransport(Protocol):
    async def send(self, message: EmailMessage) -> str | None:
        """Deliver ``message``.

        Returns the provider's message id when it has one (useful for
        correlating with a provider dashboard), or ``None``.

        Raises ``TransportError`` on failure, with ``permanent`` set to say
        whether retrying could ever help.
        """
        ...

    async def aclose(self) -> None:
        """Release any pooled connections. Called once on worker shutdown."""
        ...
