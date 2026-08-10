"""The transport factory and the two real adapters.

The adapters are exercised against their own fakes — an `httpx.MockTransport`
for Brevo, a patched `aiosmtplib.send` for SMTP — never through the factory and
never over a network. That is the house style: hand-rolled fakes that record
calls, no `respx` anywhere in this monorepo.
"""

import httpx
import pytest

from adapters.transports.brevo import BrevoTransport
from adapters.transports.factory import build_transport
from adapters.transports.noop import NoopTransport
from adapters.transports.smtp import SmtpTransport
from core.config import Settings
from domain.errors import TransportError
from domain.message import EmailMessage

MESSAGE = EmailMessage(
    to="member@example.test",
    to_name="Member User",
    subject="Your invoice 2026-0001 is available",
    html="<p>hello</p>",
    text="hello",
    message_id="<abc@optimce.test>",
)


def _settings(**overrides) -> Settings:
    base = {
        "ENV": "test",
        "CRM_DATABASE_URL": "postgresql+asyncpg://x/y",
        "EMAIL_FROM_ADDRESS": "no-reply@optimce.test",
    }
    return Settings(**{**base, **overrides})


# ---- factory ---------------------------------------------------------------


def test_factory_selects_by_name_case_insensitively():
    assert isinstance(build_transport(_settings(EMAIL_TRANSPORT="noop")), NoopTransport)
    assert isinstance(
        build_transport(_settings(EMAIL_TRANSPORT="smtp", SMTP_HOST="mailpit")), SmtpTransport
    )
    assert isinstance(
        build_transport(_settings(EMAIL_TRANSPORT="Brevo", BREVO_API_KEY="k")), BrevoTransport
    )


def test_factory_validates_that_adapter_s_settings_and_not_the_others():
    """The whole point of validating inside the branch.

    A Brevo deployment must not be forced to set SMTP_HOST, and an SMTP one must
    not need an API key.
    """
    with pytest.raises(ValueError, match="SMTP_HOST"):
        build_transport(_settings(EMAIL_TRANSPORT="SMTP"))
    with pytest.raises(ValueError, match="BREVO_API_KEY"):
        build_transport(_settings(EMAIL_TRANSPORT="BREVO"))
    # ... and the other adapter's missing settings are irrelevant.
    build_transport(_settings(EMAIL_TRANSPORT="SMTP", SMTP_HOST="mailpit", BREVO_API_KEY=""))


def test_an_unknown_transport_is_refused_at_construction():
    """No "not configured → silently disabled" branch, unlike the cache factory.

    Caching is optional; transactional email is not. A stack that cannot send is
    exactly the bug this workstream exists to fix.
    """
    with pytest.raises(ValueError, match="EMAIL_TRANSPORT must be one of"):
        _settings(EMAIL_TRANSPORT="SENDGRID")


def test_noop_is_refused_outside_local_and_test():
    """The dangerous configuration: everything marked SENT, no mail, all green."""
    with pytest.raises(ValueError, match="NOOP is not allowed"):
        _settings(
            ENV="production",
            EMAIL_TRANSPORT="NOOP",
            APP_URL="https://x",
            LOGGING_TOKEN="t",
            LOGGING_LOGS_URL="u",
            LOGGING_METRICS_URL="m",
        )


# ---- Brevo -----------------------------------------------------------------


def _brevo(handler) -> BrevoTransport:
    return BrevoTransport(
        api_key="key",
        base_url="https://api.brevo.test",
        from_address="no-reply@optimce.test",
        from_name="OptimCE",
        reply_to="contact@optimce.test",
        timeout_seconds=5,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_brevo_posts_the_documented_shape_and_returns_the_message_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(201, json={"messageId": "<brevo-1@smtp>"})

    transport = _brevo(handler)
    assert await transport.send(MESSAGE) == "<brevo-1@smtp>"
    await transport.aclose()

    assert seen["url"] == "https://api.brevo.test/v3/smtp/email"
    body = seen["body"]
    assert body["sender"] == {"email": "no-reply@optimce.test", "name": "OptimCE"}
    assert body["to"] == [{"email": "member@example.test", "name": "Member User"}]
    assert body["subject"] == MESSAGE.subject
    # Rendered in-repo and sent as content: templates stay in version control and
    # code review rather than being split across two systems.
    assert body["htmlContent"] == MESSAGE.html
    assert body["textContent"] == MESSAGE.text
    assert body["replyTo"] == {"email": "contact@optimce.test"}
    assert body["headers"]["Message-Id"] == "<abc@optimce.test>"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 401, 403])
async def test_brevo_treats_provider_and_auth_failures_as_transient(status: int):
    """401/403 are transient on purpose.

    A rotated or mistyped key WILL be fixed; burning every queued message to
    FAILED meanwhile turns a five-minute outage into a manual re-queue.
    """
    transport = _brevo(lambda request: httpx.Response(status, json={"message": "nope"}))
    with pytest.raises(TransportError) as exc:
        await transport.send(MESSAGE)
    await transport.aclose()
    assert exc.value.permanent is False
    assert exc.value.suppress_address is None


async def test_brevo_treats_a_bad_request_as_permanent():
    transport = _brevo(
        lambda request: httpx.Response(400, json={"code": "missing_parameter", "message": "bad"})
    )
    with pytest.raises(TransportError) as exc:
        await transport.send(MESSAGE)
    await transport.aclose()
    assert exc.value.permanent is True
    assert exc.value.suppress_address is None


async def test_brevo_suppresses_the_address_when_the_provider_blames_it():
    transport = _brevo(
        lambda request: httpx.Response(400, json={"code": "blocked_contact", "message": "blocked"})
    )
    with pytest.raises(TransportError) as exc:
        await transport.send(MESSAGE)
    await transport.aclose()
    assert exc.value.permanent is True
    assert exc.value.suppress_address == "member@example.test"


async def test_brevo_timeouts_are_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    transport = _brevo(handler)
    with pytest.raises(TransportError) as exc:
        await transport.send(MESSAGE)
    await transport.aclose()
    assert exc.value.permanent is False


# ---- SMTP ------------------------------------------------------------------


def _smtp() -> SmtpTransport:
    return SmtpTransport(
        host="mailpit",
        port=1025,
        username="",
        password="",
        starttls=False,
        use_tls=False,
        from_address="no-reply@optimce.test",
        from_name="OptimCE",
        reply_to="contact@optimce.test",
        timeout_seconds=5,
    )


async def test_smtp_builds_a_multipart_message_with_html_last(monkeypatch):
    """`multipart/alternative` shows the LAST part, so HTML must come after text."""
    sent: dict = {}

    async def _fake_send(mime, **kwargs):
        sent["mime"] = mime
        sent["kwargs"] = kwargs
        return ({}, "ok")

    monkeypatch.setattr("aiosmtplib.send", _fake_send)
    assert await _smtp().send(MESSAGE) == "<abc@optimce.test>"

    mime = sent["mime"]
    assert mime["To"] == "Member User <member@example.test>"
    assert mime["From"] == "OptimCE <no-reply@optimce.test>"
    assert mime["Subject"] == MESSAGE.subject
    assert mime["Reply-To"] == "contact@optimce.test"
    assert mime["Message-ID"] == "<abc@optimce.test>"
    subtypes = [part.get_content_subtype() for part in mime.iter_parts()]
    assert subtypes == ["plain", "html"]
    assert sent["kwargs"]["hostname"] == "mailpit"
    assert sent["kwargs"]["port"] == 1025


@pytest.mark.parametrize(
    ("code", "permanent", "suppresses"),
    [
        (450, False, False),  # mailbox busy — retry
        (452, False, False),  # insufficient storage — retry
        (550, True, True),  # mailbox unavailable — the classic hard bounce
        (553, True, True),  # bad address
        (552, True, False),  # over quota: a soft failure dressed as a 5xx
    ],
)
async def test_smtp_classifies_by_reply_code(monkeypatch, code, permanent, suppresses):
    import aiosmtplib

    async def _fake_send(mime, **kwargs):
        raise aiosmtplib.SMTPResponseException(code, "reply")

    monkeypatch.setattr("aiosmtplib.send", _fake_send)
    with pytest.raises(TransportError) as exc:
        await _smtp().send(MESSAGE)
    assert exc.value.permanent is permanent
    assert (exc.value.suppress_address == "member@example.test") is suppresses


async def test_smtp_connection_failures_are_transient(monkeypatch):
    async def _fake_send(mime, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("aiosmtplib.send", _fake_send)
    with pytest.raises(TransportError) as exc:
        await _smtp().send(MESSAGE)
    assert exc.value.permanent is False
