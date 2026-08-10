"""BrevoTransport — the production sender, over Brevo's transactional API.

``POST {base}/v3/smtp/email`` with an ``api-key`` header; ``201`` returns
``{"messageId": ...}``. Templates are rendered in-repo and sent as
``htmlContent``/``textContent`` rather than referencing a Brevo-hosted template:
server-side templates would split four locales across two systems and take them
out of version control and code review.

This is the first production HTTP client in the Python services, so it
establishes the pattern rather than following one: an explicit timeout (ruff
``S113`` requires it, and a stalled provider must not wedge the loop), a shared
``AsyncClient`` for connection reuse, and classification by status code.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from domain.errors import TransportError
from domain.message import EmailMessage

logger = logging.getLogger(__name__)

# 4xx that mean "this request/address will never work". 401/403 are excluded on
# purpose: a rotated or mistyped key is a configuration problem that WILL be
# fixed, and burning every queued message to FAILED in the meantime would turn a
# five-minute outage into a manual re-queue of everything.
_PERMANENT_STATUSES = frozenset({400, 404, 405, 409, 413, 415, 422})
# Brevo error codes that identify the ADDRESS as the problem, so it belongs on
# the suppression list rather than being retried or merely failed.
_ADDRESS_ERROR_CODES = frozenset({"invalid_parameter", "blocked_contact", "invalid_email"})


class BrevoTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        from_address: str,
        from_name: str,
        reply_to: str,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._from_address = from_address
        self._from_name = from_name
        self._reply_to = reply_to
        # Injectable so tests drive a real client over `httpx.MockTransport`
        # instead of monkeypatching the module — the house style is a hand-rolled
        # fake, and there is no `respx` anywhere in this monorepo.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "api-key": api_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
        )

    async def send(self, message: EmailMessage) -> str | None:
        payload = self._build_payload(message)
        try:
            response = await self._client.post(f"{self._base_url}/v3/smtp/email", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransportError(f"brevo transport error: {exc}", permanent=False) from exc

        if response.status_code in (200, 201, 202):
            return _message_id(response)

        detail, code = _error_detail(response)
        if response.status_code in _PERMANENT_STATUSES:
            suppress = message.to if code in _ADDRESS_ERROR_CODES else None
            raise TransportError(
                f"brevo {response.status_code}: {detail}",
                permanent=True,
                suppress_address=suppress,
            )
        # 401/403 (auth), 429 (rate limit), 5xx (provider) — all worth retrying.
        raise TransportError(f"brevo {response.status_code}: {detail}", permanent=False)

    def _build_payload(self, message: EmailMessage) -> dict[str, Any]:
        sender: dict[str, str] = {"email": self._from_address}
        if self._from_name:
            sender["name"] = self._from_name
        to: dict[str, str] = {"email": message.to}
        if message.to_name:
            to["name"] = message.to_name

        headers = dict(message.headers)
        if message.message_id:
            headers["Message-Id"] = message.message_id

        payload: dict[str, Any] = {
            "sender": sender,
            "to": [to],
            "subject": message.subject,
            "htmlContent": message.html,
            "textContent": message.text,
        }
        if self._reply_to:
            payload["replyTo"] = {"email": self._reply_to}
        if headers:
            payload["headers"] = headers
        return payload

    async def aclose(self) -> None:
        await self._client.aclose()


def _message_id(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("messageId") if isinstance(body, dict) else None


def _error_detail(response: httpx.Response) -> tuple[str, str | None]:
    """Brevo's error body is ``{"code": ..., "message": ...}``; degrade gracefully."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500], None
    if not isinstance(body, dict):
        return response.text[:500], None
    return str(body.get("message", ""))[:500], body.get("code")
