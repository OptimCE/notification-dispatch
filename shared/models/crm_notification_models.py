"""Mappings of the CRM notification-delivery tables.

All three are owned by ``crm-backend`` (see
``database_script/2026-08-03_notification_delivery.sql``). A producer annexe
INSERTs into ``outbound_message`` and SELECTs from ``notification_preference``
through ``core/notifications``; ``email_suppression`` is mapped for completeness
and is written only by the ``notification-dispatch`` worker.

**This module is byte-identical across every service that carries it** —
news-board, billing, administrative-document and notification-dispatch — and is
covered by the same diff gate as ``core/notifications/*.py``
(IMPLEMENTATION_PLAN §1.8). It deliberately imports nothing but ``CrmBase``, so
it lifts out verbatim in Phase 2. It is kept separate from ``crm_models.py``
precisely because that file is NOT byte-identical (its docstrings differ per
service), and the byte-identical ``repository.py`` binds to these definitions:
a one-character divergence in a column length would be an invisible,
per-service runtime bug.

The integer codes are the on-disk encoding shared with
``core/notifications/contract.py`` and crm-backend's ``notification.types.ts``.
Never renumber them.
"""

import datetime
from typing import Any

from sqlalchemy import TIMESTAMP, BigInteger, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.database.database import CrmBase


class OutboundMessageModel(CrmBase):
    """One queued outbound message: (message, channel, recipient).

    Staged inside ``publish``'s SAVEPOINT on the producer's own transaction, so
    "the business write committed => the message is queued" is an invariant
    rather than a hope. Driven to completion by ``notification-dispatch``, which
    is the only thing that ever writes a ``status`` past PENDING.

    ``id_notification`` is nullable because an invitation to an address with no
    account has no notification row to hang off (``notification.id_user`` is NOT
    NULL). ``recipient`` / ``recipient_name`` are resolved at enqueue time and
    stored literally, so a later change of address never redirects an
    already-queued message — which is also why there is no ``id_user`` column
    and no join back to ``app_user``.
    """

    __tablename__ = "outbound_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id_notification: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    id_community: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Channel: 1 INAPP, 2 EMAIL
    channel: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # '' means "unknown"; the dispatcher applies its own default locale.
    locale: Mapped[str] = mapped_column(String(8), nullable=False, server_default="")
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    # NotificationCategory: 1 TRANSACTIONAL, 2 INFORMATIONAL. Persisted because
    # the dispatcher decides from it whether to render an opt-out footer, and
    # deriving that from `type` would duplicate the producer's policy statement.
    category: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # 1 PENDING, 2 SENT, 3 FAILED, 4 SUPPRESSED, 5 CLAIMED
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class EmailSuppressionModel(CrmBase):
    """An address that must never be emailed again. Stored LOWER-CASED.

    ``app_user.email`` is a case-sensitive TEXT UNIQUE and providers report
    bounces with arbitrary case, so every write path normalises or the list
    silently misses.

    Written only by ``notification-dispatch``: at enqueue there is nothing useful
    to check (a bounce can land after a message is queued), and the worker's
    check before each send is the only one that can be authoritative.
    """

    __tablename__ = "email_suppression"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    # 1 HARD_BOUNCE, 2 COMPLAINT, 3 UNSUBSCRIBED, 4 MANUAL
    reason: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class NotificationPreferenceModel(CrmBase):
    """What a recipient wants done with a (type prefix, channel) pair.

    Read only for INFORMATIONAL notifications: TRANSACTIONAL overrides
    preference entirely and never touches this table, because an invoice, an
    invitation or a missed regulatory deadline is not opt-out-able.

    ``type_prefix`` is ``''`` for the catch-all default, else the FIRST
    dot-segment of a type key (``invoice``, ``admin_deadline``, …). Resolution is
    most-specific-wins per (user, channel); an absent row means IMMEDIATE.
    """

    __tablename__ = "notification_preference"

    id_user: Mapped[int] = mapped_column(Integer, primary_key=True)
    type_prefix: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Channel: 1 INAPP, 2 EMAIL
    channel: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    # 1 IMMEDIATE, 3 OFF (2 DAILY_DIGEST reserved; no runner, rejected by a CHECK)
    mode: Mapped[int] = mapped_column(SmallInteger, nullable=False)
