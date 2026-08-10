"""Keep ``email_suppression`` current from the provider's own blocklist.

Brevo reports hard bounces, complaints and unsubscribes **asynchronously, by
webhook only** — a send returns ``201`` and the bounce arrives minutes later.
This service has no inbound HTTP surface by design (it is a worker; nothing
about sending mail requires listening), so a webhook is not available to it.

Without a replacement, ``email_suppression`` would only ever be written from the
handful of failures we observe at send time, and §1.6's "a hard bounce must stop
future sends to that address" would be false for the production transport. So
the sync runs the other way round: the worker periodically PULLs Brevo's own
blocked-contact list, which is the same data the webhook would have pushed, just
later. Outbound only — no port, no gateway route, no inbound authentication to
get wrong.

``modifiedSince`` makes each pass incremental after the first.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

import httpx

from core.config import Settings
from domain.message import SuppressionReason
from infra.outbound_repository import OutboundRepository

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100
# Bounded so a provider returning an unbounded list cannot spin forever.
_MAX_PAGES = 50

# Brevo's blocked-contact reasons -> our coded values. Anything unrecognised is
# recorded as a hard bounce: over-suppressing an address is recoverable by hand,
# under-suppressing one burns the sending domain's reputation.
_REASON_MAP = {
    "hardBounce": SuppressionReason.HARD_BOUNCE,
    "hard_bounce": SuppressionReason.HARD_BOUNCE,
    "spam": SuppressionReason.COMPLAINT,
    "complaint": SuppressionReason.COMPLAINT,
    "unsubscribed": SuppressionReason.UNSUBSCRIBED,
    "unsubscribedViaApi": SuppressionReason.UNSUBSCRIBED,
    "adminBlocked": SuppressionReason.MANUAL,
    "contactFlaggedAsSpam": SuppressionReason.COMPLAINT,
}


async def sync_provider_suppressions(
    *,
    repo: OutboundRepository,
    cfg: Settings,
    since: datetime.datetime | None,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Pull blocked contacts modified since ``since`` and record them.

    Returns the number of addresses seen. A no-op for any transport other than
    Brevo — SMTP has no equivalent list, and its hard bounces are already
    classified at send time.
    """
    if cfg.EMAIL_TRANSPORT.strip().upper() != "BREVO" or not cfg.BREVO_API_KEY.strip():
        return 0

    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(cfg.BREVO_TIMEOUT_SECONDS),
        headers={"api-key": cfg.BREVO_API_KEY, "accept": "application/json"},
    )
    seen = 0
    try:
        for page in range(_MAX_PAGES):
            params: dict[str, Any] = {"limit": _PAGE_SIZE, "offset": page * _PAGE_SIZE}
            if since is not None:
                params["startDate"] = since.date().isoformat()
            try:
                response = await http.get(
                    f"{cfg.BREVO_BASE_URL.rstrip('/')}/v3/smtp/blockedContacts", params=params
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Best-effort by design: the send path still classifies what it
                # can see, and the next tick tries again. A provider outage must
                # not take the dispatch loop down with it.
                logger.warning("Brevo suppression sync unreachable: %s", exc)
                return seen
            if response.status_code != 200:
                logger.warning(
                    "Brevo suppression sync returned %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return seen

            contacts = _contacts(response)
            if not contacts:
                return seen
            for contact in contacts:
                address = str(contact.get("email", "")).strip()
                if not address:
                    continue
                reason = _REASON_MAP.get(
                    str(contact.get("reason", {}).get("code", ""))
                    if isinstance(contact.get("reason"), dict)
                    else str(contact.get("reason", "")),
                    SuppressionReason.HARD_BOUNCE,
                )
                await repo.suppress(
                    address, reason=reason, detail=f"brevo blockedContacts: {contact.get('reason')}"
                )
                seen += 1
            if len(contacts) < _PAGE_SIZE:
                return seen
        logger.warning("Brevo suppression sync hit the %s-page cap", _MAX_PAGES)
        return seen
    finally:
        if owns_client:
            await http.aclose()


def _contacts(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return []
    if isinstance(body, dict):
        contacts = body.get("contacts", [])
        return [c for c in contacts if isinstance(c, dict)] if isinstance(contacts, list) else []
    return []
