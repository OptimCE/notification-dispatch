"""The queue, as SQL.

Every statement here is written out rather than expressed through the ORM,
because the claim is the one piece of this service that has to be exactly right:
``FOR UPDATE SKIP LOCKED`` inside a CTE, ``attempts`` incremented by the claim
itself, and every terminal update guarded on ``status = CLAIMED`` so a reaped
and re-claimed row cannot be written twice.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import cast

from sqlalchemy import CursorResult, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.notifications.contract import NotificationCategory
from domain.message import ClaimedMessage, OutboundStatus, SuppressionReason

logger = logging.getLogger(__name__)


class OutboundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reclaim_stale(self, *, stale_seconds: int) -> int:
        """Return rows a worker claimed and then died holding.

        Without this a message that kills its worker mid-send stays CLAIMED
        forever and is never retried or failed — it simply vanishes from every
        query the loop makes. ``attempts`` was already incremented by the claim,
        so a genuine poison message still exhausts its budget instead of looping.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE outbound_message
                   SET status = :pending,
                       scheduled_for = now(),
                       last_error = 'reclaimed: worker died mid-send'
                 WHERE status = :claimed
                   AND claimed_at < now() - make_interval(secs => :stale)
                """
            ),
            {
                "pending": int(OutboundStatus.PENDING),
                "claimed": int(OutboundStatus.CLAIMED),
                "stale": stale_seconds,
            },
        )
        return cast(CursorResult, result).rowcount or 0

    async def claim_batch(self, *, limit: int) -> list[ClaimedMessage]:
        """Claim up to ``limit`` due messages for this worker.

        ``FOR UPDATE SKIP LOCKED`` inside the CTE is what lets several workers
        run with no coordination: each skips rows another already holds.

        ``attempts`` is incremented HERE, not when a send is caught failing. If
        it were incremented on failure, a message that crashes the process would
        be reclaimed with the same attempt count forever.

        ``ORDER BY scheduled_for, id`` — the id tiebreaker matters, because
        ``scheduled_for`` alone is not unique and an unstable order makes
        concurrent claims nondeterministic and the tests flaky.

        There is deliberately NO join here. Postgres rejects ``FOR UPDATE`` on
        the nullable side of an outer join, and ``outbound_message`` carries no
        ``id_user`` to join on anyway — the recipient's address and name were
        denormalised onto the row at enqueue precisely so this query stays flat.
        """
        result = await self._session.execute(
            text(
                """
                WITH claimed AS (
                    SELECT id
                      FROM outbound_message
                     WHERE status = :pending
                       AND scheduled_for <= now()
                     ORDER BY scheduled_for, id
                       FOR UPDATE SKIP LOCKED
                     LIMIT :limit
                )
                UPDATE outbound_message m
                   SET status = :claimed,
                       attempts = m.attempts + 1,
                       claimed_at = now()
                  FROM claimed c
                 WHERE m.id = c.id
                RETURNING m.id, m.type, m.category, m.recipient, m.recipient_name,
                          m.locale, m.data, m.dedupe_key, m.attempts,
                          m.id_community, m.id_notification
                """
            ),
            {
                "pending": int(OutboundStatus.PENDING),
                "claimed": int(OutboundStatus.CLAIMED),
                "limit": limit,
            },
        )
        return [
            ClaimedMessage(
                id=row.id,
                type=row.type,
                category=NotificationCategory(row.category),
                recipient=row.recipient,
                recipient_name=row.recipient_name,
                locale=row.locale or "",
                data=row.data or {},
                dedupe_key=row.dedupe_key,
                attempts=row.attempts,
                id_community=row.id_community,
                id_notification=row.id_notification,
            )
            for row in result.mappings().all()
        ]

    async def find_suppressed(self, addresses: Sequence[str]) -> set[str]:
        """Which of ``addresses`` must never be emailed. One query per batch.

        Compared lower-cased on both sides: the table stores normalised
        addresses, but ``app_user.email`` is case-sensitive and providers report
        bounces in whatever case they please.
        """
        if not addresses:
            return set()
        result = await self._session.execute(
            text("SELECT email FROM email_suppression WHERE email = ANY(:addresses)").bindparams(
                bindparam("addresses", expanding=False)
            ),
            {"addresses": sorted({address.strip().lower() for address in addresses})},
        )
        return {row[0] for row in result.all()}

    async def community_names(self, ids: Sequence[int]) -> dict[int, str]:
        """Display names for the batch's communities. One query, not one per row."""
        wanted = sorted({i for i in ids if i is not None})
        if not wanted:
            return {}
        result = await self._session.execute(
            text("SELECT id, name FROM community WHERE id = ANY(:ids)"),
            {"ids": wanted},
        )
        return {row[0]: row[1] for row in result.all()}

    async def mark_sent(self, message_id: int) -> None:
        await self._terminal(
            message_id,
            "SET status = :status, sent_at = now(), last_error = NULL",
            {"status": int(OutboundStatus.SENT)},
        )

    async def mark_suppressed(self, message_ids: Sequence[int]) -> int:
        """Bulk-drop messages to a suppressed address. Guarded on CLAIMED."""
        if not message_ids:
            return 0
        result = await self._session.execute(
            text(
                """
                UPDATE outbound_message
                   SET status = :suppressed,
                       last_error = 'recipient is on the suppression list'
                 WHERE id = ANY(:ids) AND status = :claimed
                """
            ),
            {
                "suppressed": int(OutboundStatus.SUPPRESSED),
                "claimed": int(OutboundStatus.CLAIMED),
                "ids": sorted(message_ids),
            },
        )
        return cast(CursorResult, result).rowcount or 0

    async def mark_failed(self, message_id: int, *, error: str) -> None:
        await self._terminal(
            message_id,
            "SET status = :status, last_error = :error",
            {"status": int(OutboundStatus.FAILED), "error": error[:2000]},
        )

    async def reschedule(self, message_id: int, *, error: str, delay_seconds: int) -> None:
        """Return a message to PENDING, due ``delay_seconds`` from now.

        The delay is applied by the database, not computed here as an absolute
        timestamp, so clock skew between the worker and Postgres cannot make a
        message due in the past or the distant future.
        """
        await self._terminal(
            message_id,
            (
                "SET status = :status, last_error = :error, "
                "scheduled_for = now() + make_interval(secs => :delay)"
            ),
            {
                "status": int(OutboundStatus.PENDING),
                "error": error[:2000],
                "delay": delay_seconds,
            },
        )

    async def _terminal(self, message_id: int, assignment: str, params: dict[str, object]) -> None:
        """Apply a terminal update, guarded on the row still being CLAIMED.

        The guard is what makes a reaped-then-reclaimed row safe: the original
        worker's late write finds ``status <> CLAIMED`` and changes nothing,
        instead of stamping SENT over a message another worker is mid-send on.
        """
        result = await self._session.execute(
            text(
                f"UPDATE outbound_message {assignment} "
                "WHERE id = :id AND status = :claimed_status"
            ),
            {**params, "id": message_id, "claimed_status": int(OutboundStatus.CLAIMED)},
        )
        if not cast(CursorResult, result).rowcount:
            logger.warning(
                "outbound_message %s was no longer CLAIMED; terminal update skipped",
                message_id,
                extra={"operation": "dispatch:terminal", "outbound_id": message_id},
            )

    async def suppress(
        self, address: str, *, reason: SuppressionReason, detail: str | None
    ) -> None:
        """Add one address to the suppression list. Idempotent.

        ``DO NOTHING`` rather than an upsert: the FIRST reason an address was
        suppressed is the interesting one, and a later softer signal must not
        overwrite a hard bounce.
        """
        await self._session.execute(
            text(
                """
                INSERT INTO email_suppression (email, reason, detail)
                VALUES (:email, :reason, :detail)
                ON CONFLICT (email) DO NOTHING
                """
            ),
            {
                "email": address.strip().lower(),
                "reason": int(reason),
                "detail": (detail or "")[:2000] or None,
            },
        )

    async def suppress_many(self, rows: Sequence[tuple[str, SuppressionReason, str | None]]) -> int:
        """Bulk form of :meth:`suppress`, for the provider suppression sync."""
        inserted = 0
        for address, reason, detail in rows:
            await self.suppress(address, reason=reason, detail=detail)
            inserted += 1
        return inserted
