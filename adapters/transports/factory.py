"""Pick the email transport from configuration.

Mirrors crm-backend's ``src/container/factory/cache.factory.ts`` deliberately:
read the configured name, upper-case it, switch, validate THAT adapter's
settings, return it, and raise on anything unknown. Adding a provider is a new
``case`` plus its settings — no caller changes.

One deviation from `cache.factory.ts`, and it is the important one: there is no
"not configured → silently disabled" branch. Caching is optional; transactional
email is not. A stack that cannot send is the bug this whole workstream exists
to fix, so a missing or misspelled transport fails at boot, loudly.
"""

from __future__ import annotations

import logging

from adapters.transports.brevo import BrevoTransport
from adapters.transports.noop import NoopTransport
from adapters.transports.smtp import SmtpTransport
from core.config import Settings
from domain.transport import EmailTransport

logger = logging.getLogger(__name__)


def build_transport(settings: Settings) -> EmailTransport:
    name = settings.EMAIL_TRANSPORT.strip().upper()
    match name:
        case "SMTP":
            if not settings.SMTP_HOST.strip():
                raise ValueError("SMTP_HOST is required when EMAIL_TRANSPORT=SMTP")
            if not settings.EMAIL_FROM_ADDRESS.strip():
                raise ValueError("EMAIL_FROM_ADDRESS is required when EMAIL_TRANSPORT=SMTP")
            logger.info("Email transport: SMTP (%s:%s)", settings.SMTP_HOST, settings.SMTP_PORT)
            return SmtpTransport(
                host=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USERNAME,
                password=settings.SMTP_PASSWORD,
                starttls=settings.SMTP_STARTTLS,
                use_tls=settings.SMTP_TLS,
                from_address=settings.EMAIL_FROM_ADDRESS,
                from_name=settings.EMAIL_FROM_NAME,
                reply_to=settings.EMAIL_REPLY_TO,
                timeout_seconds=settings.SMTP_TIMEOUT_SECONDS,
            )
        case "BREVO":
            if not settings.BREVO_API_KEY.strip():
                raise ValueError("BREVO_API_KEY is required when EMAIL_TRANSPORT=BREVO")
            if not settings.EMAIL_FROM_ADDRESS.strip():
                raise ValueError("EMAIL_FROM_ADDRESS is required when EMAIL_TRANSPORT=BREVO")
            logger.info("Email transport: BREVO (%s)", settings.BREVO_BASE_URL)
            return BrevoTransport(
                api_key=settings.BREVO_API_KEY,
                base_url=settings.BREVO_BASE_URL,
                from_address=settings.EMAIL_FROM_ADDRESS,
                from_name=settings.EMAIL_FROM_NAME,
                reply_to=settings.EMAIL_REPLY_TO,
                timeout_seconds=settings.BREVO_TIMEOUT_SECONDS,
            )
        case "NOOP":
            logger.warning("Email transport: NOOP — messages are logged, not sent")
            return NoopTransport()
        case _:
            raise ValueError(f"Unknown email transport: {settings.EMAIL_TRANSPORT!r}")
