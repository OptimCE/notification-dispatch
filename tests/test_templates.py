"""Every email template renders, in every locale, from the real payloads.

The parametrised cross-product mirrors the annexes' ``tests/test_locales.py``:
adding a type or a locale surfaces here as a missing file rather than as a
silently dead-lettered message in production.
"""

import re

import pytest
from jinja2 import meta

from adapters.templates.renderer import (
    FALLBACK_LOCALE,
    TEMPLATE_ROOT,
    EmailRenderer,
    _env,
    resolve_locale_chain,
    template_dir_for,
)
from core.notifications.contract import NotificationCategory
from domain.errors import TemplateError
from domain.message import EMAIL_TYPES, ClaimedMessage
from tests.fixtures.payloads import PAYLOADS

LOCALES = ("en", "fr", "nl", "de")
ALL_DIRECTORIES = (*EMAIL_TYPES, "_default")

RENDERER = EmailRenderer(
    app_url="https://app.optimce.test", default_locale="fr", message_id_domain="optimce.test"
)


def _message(type_: str, *, locale: str = "", category=NotificationCategory.TRANSACTIONAL):
    return ClaimedMessage(
        id=1,
        type=type_,
        category=category,
        recipient="member@example.test",
        recipient_name="Member User",
        locale=locale,
        data=PAYLOADS[type_],
        dedupe_key=f"2:{type_}:u4:deadbeef",
        attempts=1,
        id_community=1,
        id_notification=99,
    )


@pytest.mark.parametrize("type_", ALL_DIRECTORIES)
@pytest.mark.parametrize("locale", LOCALES)
def test_every_type_renders_in_every_locale(type_: str, locale: str):
    email = RENDERER.render(_message(type_, locale=locale), community_name="Test Community")

    assert email.subject.strip(), f"empty subject for {type_}/{locale}"
    assert email.html.strip(), f"empty HTML body for {type_}/{locale}"
    assert email.text.strip(), f"empty text body for {type_}/{locale}"
    # An unrendered delimiter means a stray literal brace, not a template bug
    # Jinja would have raised on.
    for part in (email.subject, email.html, email.text):
        assert "{{" not in part and "{%" not in part, f"unrendered Jinja in {type_}/{locale}"


@pytest.mark.parametrize("type_", ALL_DIRECTORIES)
@pytest.mark.parametrize("locale", LOCALES)
def test_subject_is_single_line_and_bounded(type_: str, locale: str):
    """A newline in a subject splits the mail header block.

    The subject is rendered from producer-controlled `data`, so this is a
    security control, not tidiness — see `_sanitise_subject`.
    """
    subject = RENDERER.render(_message(type_, locale=locale), community_name="C").subject
    assert "\n" not in subject and "\r" not in subject
    assert 0 < len(subject) <= 255


@pytest.mark.parametrize("type_", ALL_DIRECTORIES)
@pytest.mark.parametrize("locale", LOCALES)
def test_templates_reference_no_key_the_producer_does_not_send(type_: str, locale: str):
    """The drift check that `StrictUndefined` alone cannot give us.

    `StrictUndefined` turns a renamed field into a runtime failure; this turns it
    into a red test. It walks each template's AST for `data.<key>` accesses and
    asserts every one is present in the canonical payload.
    """
    known = set(PAYLOADS[type_])
    for filename in ("subject.txt", "body.html", "body.txt"):
        source = (TEMPLATE_ROOT / template_dir_for(type_) / locale / filename).read_text("utf-8")
        # `data` reaches the template as a plain dict, so accesses are literal
        # `data.<key>` in the source; the AST reports `data` as one variable.
        referenced = set(re.findall(r"\bdata\.([a-zA-Z_][a-zA-Z0-9_]*)", source))
        unknown = referenced - known
        assert not unknown, f"{type_}/{locale}/{filename} references unknown data keys: {unknown}"


@pytest.mark.parametrize("type_", ALL_DIRECTORIES)
@pytest.mark.parametrize("locale", LOCALES)
def test_templates_declare_no_unexpected_context_variable(type_: str, locale: str):
    """Anything a template reads must be something the renderer supplies."""
    supplied = {
        "data",
        "type",
        "locale",
        "recipient",
        "recipient_name",
        "community_name",
        "app_url",
        "is_informational",
        "preferences_url",
    }
    for filename in ("subject.txt", "body.html", "body.txt"):
        source = (TEMPLATE_ROOT / template_dir_for(type_) / locale / filename).read_text("utf-8")
        declared = meta.find_undeclared_variables(_env.parse(source))
        assert declared <= supplied, f"{type_}/{locale}/{filename} wants {declared - supplied}"


def test_informational_offers_an_opt_out_and_transactional_does_not():
    """§1.6, enforced.

    An INFORMATIONAL message needs an opt-out that is honoured; a TRANSACTIONAL
    one must NOT offer one — an invoice is not something a recipient may
    unsubscribe from, and offering the choice would be a lie.
    """
    informational = RENDERER.render(
        _message(
            "admin_deadline.due_soon",
            locale="en",
            category=NotificationCategory.INFORMATIONAL,
        ),
        community_name="C",
    )
    transactional = RENDERER.render(
        _message("invoice.issued", locale="en", category=NotificationCategory.TRANSACTIONAL),
        community_name="C",
    )
    assert "/users" in informational.html
    assert "/users" in informational.text
    assert "/users" not in transactional.html
    assert "/users" not in transactional.text


def test_locale_falls_back_from_region_to_language_then_to_the_default():
    assert resolve_locale_chain("fr-BE", "fr")[:2] == ["fr-BE", "fr"]
    # An unknown language exhausts the chain down to English.
    assert resolve_locale_chain("pt", "fr")[-1] == FALLBACK_LOCALE
    # '' means the recipient never chose one, so the configured default leads.
    assert resolve_locale_chain("", "nl")[0] == "nl"


@pytest.mark.parametrize("locale", ["fr-BE", "fr_BE", "pt", "", "nl"])
def test_every_locale_spelling_resolves_to_a_real_template(locale: str):
    email = RENDERER.render(_message("invoice.issued", locale=locale), community_name="C")
    assert email.subject.strip()


def test_unknown_type_degrades_to_the_default_template():
    """Drift between this service's EMAIL_TYPES and a producer must not dead-letter."""
    assert template_dir_for("something.brandnew") == "_default"
    message = ClaimedMessage(
        id=1,
        type="something.brandnew",
        category=NotificationCategory.TRANSACTIONAL,
        recipient="a@b.test",
        recipient_name=None,
        locale="fr",
        data={"anything": 1},
        dedupe_key="2:something.brandnew:u1:abc",
        attempts=1,
        id_community=None,
        id_notification=None,
    )
    email = RENDERER.render(message, community_name=None)
    assert email.subject.strip()


def test_a_missing_data_key_fails_loudly_rather_than_rendering_a_blank():
    """`StrictUndefined` is the point: a blank where the invoice number goes is worse."""
    message = _message("invoice.issued", locale="en")
    broken = ClaimedMessage(
        id=message.id,
        type=message.type,
        category=message.category,
        recipient=message.recipient,
        recipient_name=message.recipient_name,
        locale=message.locale,
        data={"invoice_id": 1},  # number/total/currency/due_date all missing
        dedupe_key=message.dedupe_key,
        attempts=message.attempts,
        id_community=message.id_community,
        id_notification=message.id_notification,
    )
    with pytest.raises(TemplateError):
        RENDERER.render(broken, community_name="C")


def test_message_id_is_stable_and_derived_from_the_dedupe_key():
    """A redelivered message must carry the same Message-ID so clients collapse it."""
    first = RENDERER.render(_message("invoice.issued", locale="en"), community_name="C")
    second = RENDERER.render(_message("invoice.issued", locale="en"), community_name="C")
    assert first.message_id == second.message_id
    assert first.message_id and first.message_id.endswith("@optimce.test>")


def test_community_name_is_optional_everywhere():
    """A join that found nothing must not render the word 'None' at the user."""
    for type_ in ALL_DIRECTORIES:
        email = RENDERER.render(_message(type_, locale="en"), community_name=None)
        assert "None" not in email.subject
        assert ">None<" not in email.html
