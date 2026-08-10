"""Failure classification for the send path.

One flag decides everything downstream, exactly as ``DocGenError.permanent``
does in ``document-generation``: a permanent failure is marked FAILED and never
retried, a transient one goes back on the queue with exponential backoff until
the attempt budget runs out.

Getting the classification wrong is expensive in both directions. Treating a
permanent failure as transient burns five sends on a mailbox that will never
accept them and drags the sending reputation down with it; treating a transient
failure as permanent silently drops a real message.
"""

from __future__ import annotations


class TransportError(Exception):
    """A send failed.

    ``permanent`` is the single source of truth for ack-vs-retry.
    ``suppress_address`` is set when the provider identified the ADDRESS as the
    problem (a hard bounce, an invalid mailbox, a blocked contact) rather than
    the request or the connection — that address then goes on the suppression
    list so no later message wastes a send on it.
    """

    def __init__(
        self,
        message: str,
        *,
        permanent: bool,
        suppress_address: str | None = None,
    ) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.suppress_address = suppress_address

    def __str__(self) -> str:
        kind = "permanent" if self.permanent else "transient"
        return f"[{kind}] {super().__str__()}"


class TemplateError(Exception):
    """A message could not be rendered.

    Always permanent. A missing template or an undefined variable is a code or
    payload defect, not a hiccup: retrying it five times just delays the FAILED
    row by an hour and buries the real signal in the log.
    """
