"""One pass of the queue: reap, claim, filter, render, send, settle.

``dispatch_once`` takes its session and transport as arguments, mirroring
``billing/worker/persistence.py::process_billing_run``. That is what lets the
whole loop be driven from a test against a rolled-back session and a recording
transport, with no broker, no mail server and no container.

The transaction shape is the load-bearing part. The claim commits BEFORE any
send, so a stalled SMTP or HTTP round trip never holds a database transaction
open. The cost is at-least-once delivery: a crash between "the provider accepted
it" and "the row says SENT" re-sends on the next tick. That is unavoidable
without a two-phase transport, and the stable Message-ID lets the recipient's
client collapse the duplicate.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from adapters.templates.renderer import EmailRenderer
from adapters.transports.factory import build_transport
from core import metrics as app_metrics
from core.config import Settings
from core.config import settings as default_settings
from core.database.database import AsyncSessionCRMFactory
from domain.backoff import backoff_seconds
from domain.errors import TemplateError, TransportError
from domain.message import ClaimedMessage, DispatchResult, SuppressionReason
from domain.transport import EmailTransport
from infra.outbound_repository import OutboundRepository

logger = logging.getLogger(__name__)


async def dispatch_once(
    *,
    crm_session: AsyncSession | None = None,
    transport: EmailTransport | None = None,
    settings: Settings | None = None,
) -> DispatchResult:
    """Reap, claim and deliver one batch. Returns what it did."""
    cfg = settings or default_settings
    own_session = crm_session is None
    session = crm_session or AsyncSessionCRMFactory()
    own_transport = transport is None
    sender = transport or build_transport(cfg)
    repo = OutboundRepository(session)
    renderer = EmailRenderer(
        app_url=cfg.APP_URL,
        default_locale=cfg.DEFAULT_LOCALE,
        message_id_domain=_message_id_domain(cfg),
    )

    try:
        reclaimed = await repo.reclaim_stale(stale_seconds=cfg.DISPATCH_CLAIM_STALE_SECONDS)
        if reclaimed:
            app_metrics.messages_reclaimed.add(reclaimed)
        batch = await repo.claim_batch(limit=cfg.DISPATCH_BATCH_SIZE)
        if not batch and not reclaimed:
            await session.commit()
            return DispatchResult()

        suppressed_addresses = await repo.find_suppressed([m.recipient for m in batch])
        blocked = [m for m in batch if m.recipient.strip().lower() in suppressed_addresses]
        deliverable = [m for m in batch if m not in blocked]
        suppressed = await repo.mark_suppressed([m.id for m in blocked])
        if suppressed:
            app_metrics.messages_dispatched.add(suppressed, {"outcome": "suppressed"})

        names = await repo.community_names([m.id_community for m in batch if m.id_community])

        # Commit the claim before sending. Holding this transaction open across
        # an SMTP/HTTP round trip would pin a pooled connection for the duration
        # and eventually trip idle_in_transaction_session_timeout.
        await session.commit()

        result = DispatchResult(claimed=len(batch), suppressed=suppressed, reclaimed=reclaimed)
        for message in deliverable:
            outcome = await _deliver(
                message,
                repo=repo,
                renderer=renderer,
                transport=sender,
                community_name=names.get(message.id_community or -1),
                cfg=cfg,
            )
            app_metrics.messages_dispatched.add(1, {"outcome": outcome})
            result = _record(result, outcome)
            # Settle each message on its own, so one failure cannot roll back
            # another's SENT and cause a duplicate send on the next tick.
            await session.commit()
        return result
    finally:
        if own_transport:
            await sender.aclose()
        if own_session:
            await session.close()


async def _deliver(
    message: ClaimedMessage,
    *,
    repo: OutboundRepository,
    renderer: EmailRenderer,
    transport: EmailTransport,
    community_name: str | None,
    cfg: Settings,
) -> str:
    """Render and send one message, then write its terminal state.

    Returns ``"sent"``, ``"retried"`` or ``"failed"``.
    """
    try:
        email = renderer.render(message, community_name=community_name)
    except TemplateError as exc:
        # Always permanent. A missing template or an undefined variable is a
        # code or payload defect; five retries only delay the FAILED row.
        logger.error(
            "Email render failed permanently: %s",
            exc,
            extra={"operation": "dispatch:render", "outbound_id": message.id},
        )
        await repo.mark_failed(message.id, error=str(exc))
        return "failed"

    try:
        provider_id = await transport.send(email)
    except TransportError as exc:
        if exc.suppress_address:
            # The provider named the ADDRESS, not the request or the connection.
            # Recording it here is what keeps a dead mailbox from burning the
            # sending reputation one retry at a time.
            await repo.suppress(
                exc.suppress_address,
                reason=SuppressionReason.HARD_BOUNCE,
                detail=str(exc),
            )
            app_metrics.addresses_suppressed.add(1, {"reason": "hard_bounce"})
        if exc.permanent or message.attempts >= cfg.DISPATCH_MAX_ATTEMPTS:
            logger.error(
                "Giving up on outbound_message %s after %s attempt(s): %s",
                message.id,
                message.attempts,
                exc,
                extra={"operation": "dispatch:send", "outbound_id": message.id},
            )
            await repo.mark_failed(message.id, error=str(exc))
            return "failed"
        delay = backoff_seconds(
            attempts=message.attempts,
            base_seconds=cfg.DISPATCH_BACKOFF_BASE_SECONDS,
            # Jitter so a provider outage does not synchronise every queued
            # message onto one retry instant. Not security-sensitive, but
            # `secrets` costs nothing and keeps ruff's S311 quiet.
            jitter=0.5 + secrets.randbelow(1000) / 1000,
        )
        logger.warning(
            "Retrying outbound_message %s in %ss (attempt %s): %s",
            message.id,
            delay,
            message.attempts,
            exc,
            extra={"operation": "dispatch:send", "outbound_id": message.id},
        )
        await repo.reschedule(message.id, error=str(exc), delay_seconds=delay)
        return "retried"

    logger.info(
        "Sent outbound_message %s (%s)",
        message.id,
        message.type,
        extra={
            "operation": "dispatch:send",
            "outbound_id": message.id,
            "provider_message_id": provider_id,
        },
    )
    await repo.mark_sent(message.id)
    return "sent"


def _record(result: DispatchResult, outcome: str) -> DispatchResult:
    return DispatchResult(
        claimed=result.claimed,
        sent=result.sent + (outcome == "sent"),
        suppressed=result.suppressed,
        retried=result.retried + (outcome == "retried"),
        failed=result.failed + (outcome == "failed"),
        reclaimed=result.reclaimed,
    )


def _message_id_domain(cfg: Settings) -> str:
    """Domain for the stable Message-ID: explicit, else the sender's."""
    if cfg.EMAIL_MESSAGE_ID_DOMAIN.strip():
        return cfg.EMAIL_MESSAGE_ID_DOMAIN.strip()
    _, _, domain = cfg.EMAIL_FROM_ADDRESS.partition("@")
    return domain.strip()
