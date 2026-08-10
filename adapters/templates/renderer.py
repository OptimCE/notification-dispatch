"""Render a queued message into a subject, an HTML body and a text body.

Layout:

    templates/email/<type>/<locale>/{subject.txt,body.html,body.txt}
    templates/email/_default/<locale>/…     # unknown type
    templates/email/_layout.html            # shared HTML chrome

Locale resolution is exact (``fr-BE``) -> language (``fr``) -> ``en``, with an
empty locale meaning "unknown" and falling back to the configured default first.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    select_autoescape,
)
from jinja2 import TemplateError as JinjaError

from core.notifications.contract import NotificationCategory
from domain.errors import TemplateError
from domain.message import DEFAULT_TEMPLATE, ClaimedMessage, EmailMessage

logger = logging.getLogger(__name__)

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent.parent / "templates" / "email"

#: Last resort of the locale chain. Every type must have this one.
FALLBACK_LOCALE = "en"

#: RFC 5322 recommends folding long headers; mail servers reject very long ones.
_MAX_SUBJECT_LENGTH = 255


def _build_environment() -> Environment:
    """One Environment, with per-file autoescaping.

    ``select_autoescape`` is evaluated per template NAME, so a single
    Environment escapes ``.html`` and leaves ``.txt`` alone. Two Environments
    would only buy two loaders and two filter registries to keep in sync.

    (``document-generation`` uses ``default=True`` because it renders arbitrary
    third-party bundles of unknown extension — a genuinely different problem.
    Do not copy that setting here: it would HTML-escape the plain-text parts.)
    """
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_ROOT)),
        autoescape=select_autoescape(
            enabled_extensions=("html",),
            default=False,
            default_for_string=False,
        ),
        # A producer renaming a `data` key must fail loudly at render, not send a
        # mail with a blank where the invoice number should be. The dispatch loop
        # classifies the resulting error as PERMANENT, so a broken template
        # fails once instead of retrying five times.
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        auto_reload=False,
    )


_env = _build_environment()


def resolve_locale_chain(locale: str, default_locale: str) -> list[str]:
    """Candidate directories for ``locale``, most specific first.

    ``''`` means the recipient never chose a language — every account predating
    the ``app_user.locale`` column — so the configured default leads the chain.
    """
    candidates: list[str] = []

    def _add(value: str) -> None:
        value = value.strip()
        if value and value not in candidates:
            candidates.append(value)

    _add(locale)
    if "-" in locale:
        _add(locale.split("-", 1)[0])
    if "_" in locale:
        _add(locale.split("_", 1)[0])
    _add(default_locale)
    if "-" in default_locale:
        _add(default_locale.split("-", 1)[0])
    _add(FALLBACK_LOCALE)
    return candidates


def template_dir_for(type: str) -> str:
    """The template directory a type renders from, or the shared fallback.

    An unknown type is not an error: this service cannot import the producers'
    ``types.py``, so a new EMAIL producer would otherwise dead-letter every
    message until someone noticed. ``_default`` renders a generic "you have a
    notification, sign in to see it" instead.
    """
    if (TEMPLATE_ROOT / type).is_dir():
        return type
    logger.warning(
        "No email template for type %s; falling back to %s",
        type,
        DEFAULT_TEMPLATE,
        extra={"operation": "dispatch:render", "type": type},
    )
    return DEFAULT_TEMPLATE


class EmailRenderer:
    def __init__(self, *, app_url: str, default_locale: str, message_id_domain: str) -> None:
        self._app_url = app_url.rstrip("/")
        self._default_locale = default_locale
        self._message_id_domain = message_id_domain

    def render(self, message: ClaimedMessage, *, community_name: str | None) -> EmailMessage:
        directory = template_dir_for(message.type)
        locale = self._pick_locale(directory, message.locale)
        context = self._context(message, community_name=community_name, locale=locale)

        try:
            subject = _sanitise_subject(
                _env.get_template(f"{directory}/{locale}/subject.txt").render(**context)
            )
            html = _env.get_template(f"{directory}/{locale}/body.html").render(**context)
            text = _env.get_template(f"{directory}/{locale}/body.txt").render(**context)
        except TemplateNotFound as exc:
            raise TemplateError(f"missing email template: {exc}") from exc
        except JinjaError as exc:
            raise TemplateError(f"email template render failed for {message.type}: {exc}") from exc

        return EmailMessage(
            to=message.recipient,
            to_name=message.recipient_name,
            subject=subject,
            html=html,
            text=text,
            message_id=self._message_id(message.dedupe_key),
        )

    def _pick_locale(self, directory: str, locale: str) -> str:
        for candidate in resolve_locale_chain(locale, self._default_locale):
            if (TEMPLATE_ROOT / directory / candidate).is_dir():
                return candidate
        raise TemplateError(
            f"no locale directory for {directory} (wanted {locale!r}, "
            f"default {self._default_locale!r}, fallback {FALLBACK_LOCALE!r})"
        )

    def _context(
        self, message: ClaimedMessage, *, community_name: str | None, locale: str
    ) -> dict[str, Any]:
        return {
            "data": message.data,
            "type": message.type,
            "locale": locale,
            "recipient": message.recipient,
            "recipient_name": message.recipient_name,
            "community_name": community_name,
            "app_url": self._app_url,
            # §1.6: an INFORMATIONAL mail must offer an honoured opt-out and a
            # TRANSACTIONAL one must NOT. The templates branch on this, which is
            # why `category` is a persisted column rather than something the
            # dispatcher re-derives from `type`.
            "is_informational": message.category == NotificationCategory.INFORMATIONAL,
            "preferences_url": f"{self._app_url}/users" if self._app_url else "",
        }

    def _message_id(self, dedupe_key: str) -> str | None:
        """A stable Message-ID derived from the idempotency key.

        A crash between "the provider accepted it" and "the row says SENT" causes
        an at-least-once redelivery. An identical Message-ID lets the recipient's
        client collapse the duplicate — free mitigation for a failure mode no
        single-phase transport can eliminate.
        """
        if not self._message_id_domain:
            return None
        digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:32]
        return f"<{digest}@{self._message_id_domain}>"


def _sanitise_subject(rendered: str) -> str:
    """Collapse whitespace and cap the length.

    Not cosmetic: the subject is rendered from producer-controlled ``data`` and
    goes straight into a mail header, so an embedded newline would split the
    header block and let whatever wrote the notification inject headers or extra
    recipients. Collapsing every run of whitespace to a single space removes the
    class entirely.
    """
    return " ".join(rendered.split())[:_MAX_SUBJECT_LENGTH]
