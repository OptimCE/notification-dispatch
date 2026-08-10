"""SmtpTransport — the self-hosting escape hatch, and Mailpit in dev.

``aiosmtplib`` has been a declared dependency of four services since the
template was written and imported by none of them; this is its first real
consumer.

Failure classification follows the protocol, which already draws the line for
us: SMTP replies in the 5xx range are permanent ("this will never work"), 4xx
are transient ("try later"). Connection and timeout errors are transient by
definition. A 5xx naming a recipient is the closest SMTP gets to a hard bounce,
so it also suppresses the address.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage as MimeMessage

import aiosmtplib

from domain.errors import TransportError
from domain.message import EmailMessage

logger = logging.getLogger(__name__)

# SMTP status codes that mean "the recipient is the problem" rather than "the
# server is". 550/551/553 are the classic hard-bounce replies; 552 is a mailbox
# over quota, which is a soft failure dressed as a 5xx, so it is excluded.
_ADDRESS_FAILURE_CODES = frozenset({550, 551, 553})


class SmtpTransport:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        starttls: bool,
        use_tls: bool,
        from_address: str,
        from_name: str,
        reply_to: str,
        timeout_seconds: int,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._starttls = starttls
        self._use_tls = use_tls
        self._from_address = from_address
        self._from_name = from_name
        self._reply_to = reply_to
        self._timeout_seconds = timeout_seconds

    async def send(self, message: EmailMessage) -> str | None:
        mime = self._build_mime(message)
        try:
            await aiosmtplib.send(
                mime,
                hostname=self._host,
                port=self._port,
                username=self._username or None,
                password=self._password or None,
                start_tls=self._starttls or None,
                use_tls=self._use_tls,
                timeout=self._timeout_seconds,
            )
        except aiosmtplib.SMTPResponseException as exc:
            permanent = 500 <= exc.code < 600
            suppress = message.to if exc.code in _ADDRESS_FAILURE_CODES else None
            raise TransportError(
                f"smtp {exc.code}: {exc.message}",
                permanent=permanent,
                suppress_address=suppress,
            ) from exc
        except aiosmtplib.SMTPRecipientsRefused as exc:
            # Every recipient rejected. There is exactly one per message here, so
            # this is unambiguous: the address is bad.
            raise TransportError(
                f"smtp recipient refused: {exc}", permanent=True, suppress_address=message.to
            ) from exc
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            # Connect failures, TLS failures, timeouts: the server, not the
            # address. Retrying is exactly the right response.
            raise TransportError(f"smtp transport error: {exc}", permanent=False) from exc
        return message.message_id

    def _build_mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = (
            f"{self._from_name} <{self._from_address}>" if self._from_name else self._from_address
        )
        mime["To"] = f"{message.to_name} <{message.to}>" if message.to_name else message.to
        mime["Subject"] = message.subject
        if self._reply_to:
            mime["Reply-To"] = self._reply_to
        if message.message_id:
            mime["Message-ID"] = message.message_id
        for key, value in message.headers.items():
            mime[key] = value
        # Text first, then HTML: `set_content` + `add_alternative` produces a
        # multipart/alternative whose LAST part is what a graphical client picks,
        # so this order is what makes the HTML the one shown.
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")
        return mime

    async def aclose(self) -> None:
        # `aiosmtplib.send` opens and closes a connection per call, so there is
        # nothing pooled to release.
        return None
