"""The dispatch loop against a real Postgres and a recording transport.

`dispatch_once` takes its session and transport as arguments precisely so this
is possible without a broker, a mail server or a container of its own.
"""

import datetime

import pytest
from sqlalchemy import text

from core.config import Settings
from domain.errors import TransportError
from domain.message import EmailMessage, OutboundStatus
from tests.fixtures.payloads import PAYLOADS
from worker.dispatch import dispatch_once


def _settings(**overrides) -> Settings:
    base = {
        "ENV": "test",
        "CRM_DATABASE_URL": "postgresql+asyncpg://x/y",
        "EMAIL_TRANSPORT": "NOOP",
        "EMAIL_FROM_ADDRESS": "no-reply@optimce.test",
        "EMAIL_MESSAGE_ID_DOMAIN": "optimce.test",
        "APP_URL": "https://app.optimce.test",
        "DEFAULT_LOCALE": "fr",
        "DISPATCH_MAX_ATTEMPTS": 3,
        "DISPATCH_BACKOFF_BASE_SECONDS": 60,
        "DISPATCH_CLAIM_STALE_SECONDS": 300,
    }
    return Settings(**{**base, **overrides})


class RecordingTransport:
    """Sends nothing, records everything. The house style for faking IO here."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self._error = error

    async def send(self, message: EmailMessage) -> str | None:
        self.sent.append(message)
        if self._error is not None:
            raise self._error
        return "provider-1"

    async def aclose(self) -> None:
        return None


async def _seed_community(session, name: str = "Test Community") -> int:
    result = await session.execute(
        text("INSERT INTO community (name, auth_community_id) VALUES (:n, :a) RETURNING id"),
        {"n": name, "a": f"auth-{name}"},
    )
    return int(result.scalar_one())


async def _queue(
    session,
    *,
    id_community: int | None = None,
    type_: str = "invoice.issued",
    recipient: str = "member@example.test",
    locale: str = "",
    category: int = 1,
    status: int = int(OutboundStatus.PENDING),
    scheduled_for: str = "now()",
    claimed_at: str | None = None,
    attempts: int = 0,
    dedupe_key: str | None = None,
) -> int:
    import json

    result = await session.execute(
        text(
            f"""
            INSERT INTO outbound_message
                (id_community, channel, recipient, recipient_name, locale, type,
                 category, data, dedupe_key, status, attempts, scheduled_for, claimed_at)
            VALUES
                (:cid, 2, :recipient, 'Member User', :locale, :type,
                 :category, CAST(:data AS jsonb), :dedupe, :status, :attempts,
                 {scheduled_for}, {claimed_at or 'NULL'})
            RETURNING id
            """
        ),
        {
            "cid": id_community,
            "recipient": recipient,
            "locale": locale,
            "type": type_,
            "category": category,
            "data": json.dumps(PAYLOADS.get(type_, {})),
            "dedupe": dedupe_key or f"2:{type_}:u{recipient}:{status}{attempts}{locale}",
            "status": status,
            "attempts": attempts,
        },
    )
    await session.flush()
    return int(result.scalar_one())


async def _row(session, message_id: int):
    result = await session.execute(
        text(
            "SELECT status, attempts, sent_at, last_error, claimed_at, scheduled_for "
            "FROM outbound_message WHERE id = :id"
        ),
        {"id": message_id},
    )
    return result.mappings().one()


# ---- the happy path --------------------------------------------------------


async def test_a_pending_message_is_claimed_rendered_sent_and_marked(db_session):
    cid = await _seed_community(db_session)
    message_id = await _queue(db_session, id_community=cid)
    transport = RecordingTransport()

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.claimed == 1
    assert result.sent == 1
    assert len(transport.sent) == 1
    email = transport.sent[0]
    assert email.to == "member@example.test"
    assert email.to_name == "Member User"
    assert "2026-0001" in email.subject
    # The community name comes from a separate batched SELECT, not a join: the
    # claim cannot join, because FOR UPDATE is illegal on the nullable side of an
    # outer join and there is no id_user to join on anyway.
    assert "Test Community" in email.html

    row = await _row(db_session, message_id)
    assert row["status"] == int(OutboundStatus.SENT)
    assert row["sent_at"] is not None
    assert row["last_error"] is None


async def test_attempts_is_incremented_by_the_claim_not_by_the_failure(db_session):
    """The crash-safety property.

    If `attempts` only moved on a caught failure, a message that kills the worker
    mid-send would be reclaimed with the same count forever and never exhaust its
    budget. Incrementing at claim time is what bounds a poison message.
    """
    message_id = await _queue(db_session)
    await dispatch_once(
        crm_session=db_session, transport=RecordingTransport(), settings=_settings()
    )
    assert (await _row(db_session, message_id))["attempts"] == 1


async def test_a_message_scheduled_in_the_future_is_not_claimed(db_session):
    await _queue(db_session, scheduled_for="now() + interval '1 hour'")
    result = await dispatch_once(
        crm_session=db_session, transport=RecordingTransport(), settings=_settings()
    )
    assert result.claimed == 0


# ---- suppression -----------------------------------------------------------


async def test_a_suppressed_recipient_is_dropped_without_sending(db_session):
    await db_session.execute(
        text("INSERT INTO email_suppression (email, reason) VALUES ('blocked@example.test', 1)")
    )
    message_id = await _queue(db_session, recipient="blocked@example.test")
    transport = RecordingTransport()

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.suppressed == 1
    assert result.sent == 0
    assert transport.sent == []
    assert (await _row(db_session, message_id))["status"] == int(OutboundStatus.SUPPRESSED)


async def test_the_suppression_check_is_case_insensitive(db_session):
    """The list is normalised but `app_user.email` is not, and providers report
    bounces in whatever case they please."""
    await db_session.execute(
        text("INSERT INTO email_suppression (email, reason) VALUES ('blocked@example.test', 1)")
    )
    await _queue(db_session, recipient="Blocked@Example.Test")
    result = await dispatch_once(
        crm_session=db_session, transport=RecordingTransport(), settings=_settings()
    )
    assert result.suppressed == 1


async def test_a_provider_hard_bounce_adds_the_address_to_the_suppression_list(db_session):
    """Without an inbound webhook, this is how suppression populates at all."""
    message_id = await _queue(db_session, recipient="gone@example.test")
    transport = RecordingTransport(
        error=TransportError(
            "550 unknown mailbox", permanent=True, suppress_address="gone@example.test"
        )
    )

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.failed == 1
    assert (await _row(db_session, message_id))["status"] == int(OutboundStatus.FAILED)
    suppressed = await db_session.execute(text("SELECT email, reason FROM email_suppression"))
    assert suppressed.all() == [("gone@example.test", 1)]


async def test_suppression_records_the_first_reason_and_does_not_overwrite_it(db_session):
    """A later, softer signal must not overwrite a hard bounce."""
    await db_session.execute(
        text("INSERT INTO email_suppression (email, reason) VALUES ('gone@example.test', 1)")
    )
    await _queue(db_session, recipient="gone@example.test", dedupe_key="k1")
    # Already suppressed, so the send never happens — record a second reason by
    # hand through the same path the sync uses.
    from domain.message import SuppressionReason
    from infra.outbound_repository import OutboundRepository

    await OutboundRepository(db_session).suppress(
        "gone@example.test", reason=SuppressionReason.UNSUBSCRIBED, detail="later"
    )
    reason = await db_session.execute(
        text("SELECT reason FROM email_suppression WHERE email = 'gone@example.test'")
    )
    assert reason.scalar_one() == 1


# ---- failure classification ------------------------------------------------


async def test_a_transient_failure_is_rescheduled_with_backoff(db_session):
    message_id = await _queue(db_session)
    transport = RecordingTransport(error=TransportError("503 upstream", permanent=False))

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.retried == 1
    row = await _row(db_session, message_id)
    assert row["status"] == int(OutboundStatus.PENDING)
    assert row["attempts"] == 1
    assert "503 upstream" in row["last_error"]
    # Due in the future, so the very next tick does not immediately retry it.
    assert row["scheduled_for"] > datetime.datetime.now(datetime.UTC)


async def test_a_permanent_failure_is_not_retried(db_session):
    message_id = await _queue(db_session)
    transport = RecordingTransport(error=TransportError("400 malformed", permanent=True))

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.failed == 1
    row = await _row(db_session, message_id)
    assert row["status"] == int(OutboundStatus.FAILED)
    assert row["attempts"] == 1  # one attempt, not the full budget


async def test_the_attempt_budget_is_bounded(db_session):
    """At DISPATCH_MAX_ATTEMPTS the message fails instead of retrying forever."""
    message_id = await _queue(db_session, attempts=2)  # claim makes it 3
    transport = RecordingTransport(error=TransportError("503 upstream", permanent=False))

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.failed == 1
    assert (await _row(db_session, message_id))["status"] == int(OutboundStatus.FAILED)


async def test_an_unrenderable_message_fails_permanently(db_session):
    """A missing template or an undefined variable is a defect, not a hiccup.

    Retrying it five times only delays the FAILED row by an hour and buries the
    real signal in the log.
    """
    message_id = await _queue(db_session, type_="invoice.issued")
    # Strip the payload the template requires.
    await db_session.execute(
        text("UPDATE outbound_message SET data = '{}'::jsonb WHERE id = :id"),
        {"id": message_id},
    )
    transport = RecordingTransport()

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.failed == 1
    assert transport.sent == []
    assert (await _row(db_session, message_id))["status"] == int(OutboundStatus.FAILED)


# ---- the reaper ------------------------------------------------------------


async def test_a_row_a_dead_worker_left_claimed_is_returned_to_pending(db_session):
    """Without the reaper, a message whose worker died mid-send stays CLAIMED
    forever: never retried, never failed, invisible to every query."""
    message_id = await _queue(
        db_session,
        status=int(OutboundStatus.CLAIMED),
        attempts=1,
        claimed_at="now() - interval '1 hour'",
    )
    transport = RecordingTransport()

    result = await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())

    assert result.reclaimed == 1
    # Reclaimed AND re-claimed in the same tick, so it is sent immediately.
    assert result.sent == 1
    row = await _row(db_session, message_id)
    assert row["status"] == int(OutboundStatus.SENT)
    # The claim incremented it again — a genuine poison message still exhausts
    # its budget rather than looping.
    assert row["attempts"] == 2


async def test_a_freshly_claimed_row_is_left_alone_by_the_reaper(db_session):
    """Otherwise the reaper would yank messages another worker is mid-send on."""
    await _queue(
        db_session,
        status=int(OutboundStatus.CLAIMED),
        attempts=1,
        claimed_at="now()",
    )
    result = await dispatch_once(
        crm_session=db_session, transport=RecordingTransport(), settings=_settings()
    )
    assert result.reclaimed == 0
    assert result.claimed == 0


async def test_an_empty_queue_is_a_cheap_no_op(db_session):
    result = await dispatch_once(
        crm_session=db_session, transport=RecordingTransport(), settings=_settings()
    )
    assert result.claimed == 0
    assert result.sent == 0


# ---- locale ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("locale", "expected_fragment"),
    [
        ("fr", "facture"),
        ("nl", "factuur"),
        ("de", "Rechnung"),
        ("en", "invoice"),
        # '' means the recipient never chose a language, so the configured
        # default (fr) applies rather than English.
        ("", "facture"),
        # Region tag falls back to the language.
        ("fr-BE", "facture"),
    ],
)
async def test_the_queued_locale_selects_the_template(db_session, locale, expected_fragment):
    await _queue(db_session, locale=locale, dedupe_key=f"k-{locale or 'empty'}")
    transport = RecordingTransport()
    await dispatch_once(crm_session=db_session, transport=transport, settings=_settings())
    assert expected_fragment.lower() in transport.sent[0].text.lower()
