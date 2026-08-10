"""The value objects the dispatch loop moves around.

Pure: this module imports nothing but the stdlib and the shared channel
encoding, so the send path can be unit-tested without a database, a broker or a
mail server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from core.notifications.contract import NotificationCategory


class OutboundStatus(IntEnum):
    """Lifecycle of one queued message. The on-disk encoding of
    ``outbound_message.status``; never renumber.

    CLAIMED is not decoration. The worker flips a row to CLAIMED and increments
    ``attempts`` in the SAME statement that claims it, then sends outside any
    transaction. That is what stops a message which kills the process mid-send
    from being reclaimed forever, and it is what lets a reaper tell "being sent
    right now" from "abandoned by a dead worker" via ``claimed_at``.
    """

    PENDING = 1
    SENT = 2
    FAILED = 3
    SUPPRESSED = 4
    CLAIMED = 5


class SuppressionReason(IntEnum):
    """Why an address landed on the suppression list."""

    HARD_BOUNCE = 1
    COMPLAINT = 2
    UNSUBSCRIBED = 3
    MANUAL = 4


@dataclass(frozen=True, slots=True)
class ClaimedMessage:
    """One row this worker has claimed and is about to render and send."""

    id: int
    type: str
    category: NotificationCategory
    recipient: str
    recipient_name: str | None
    locale: str
    data: dict[str, Any]
    dedupe_key: str
    attempts: int
    id_community: int | None
    id_notification: int | None


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A fully rendered email, ready for any transport.

    Rendering is finished before this exists, so a transport never sees a
    template, a locale or a notification type — which is what keeps the SMTP and
    Brevo adapters interchangeable and trivially fakeable in tests.
    """

    to: str
    subject: str
    html: str
    text: str
    to_name: str | None = None
    # Stable, derived from the dedupe key. A crash between "sent" and "marked
    # SENT" causes an at-least-once redelivery; an identical Message-ID lets the
    # recipient's client collapse the duplicate, and lets the provider dashboard
    # correlate the two attempts.
    message_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """What one pass of the loop did. Returned so tests can assert on it."""

    claimed: int = 0
    sent: int = 0
    suppressed: int = 0
    retried: int = 0
    failed: int = 0
    reclaimed: int = 0


# The notification types this service ships an email template for. It cannot
# import the annexes' `types.py`, so this list can drift — which is exactly why
# `templates/email/_default/` exists and why an unknown type degrades to it
# instead of dead-lettering.
EMAIL_TYPES: tuple[str, ...] = (
    "member_invitation.received",
    "manager_invitation.received",
    "invoice.issued",
    "invoice.overdue",
    "admin_deadline.due_soon",
    "admin_deadline.missed",
)

#: Rendered when `type` has no template of its own.
DEFAULT_TEMPLATE = "_default"
