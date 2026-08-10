# notification-dispatch — working notes for Claude

OptimCE's outbound message sender. Polls `outbound_message` in the **CRM**
database, renders a localised email, sends it through the configured transport,
and settles the row. Scaffolded from `document-generation` (the other worker-only
service); mirror its conventions. Read `README.md` for the domain.

## What this service is NOT

- **Not an API.** No `main.py`, no `api/`, no port, no KrakenD route, no
  `*-doc-gen` container. Sending is purely outbound; nothing about it requires
  listening. Adding an endpoint would make it the repo's first unauthenticated
  gateway route — think hard before that.
- **Not a subscribable annexe.** Transactional email is a channel, not a feature.
  A community that had not "activated" it could not onboard members. There is no
  `require_feature`, no `community_subscription` read, and no per-community
  routes to hang one on.
- **Not the owner of any table.** There is deliberately no
  `scripts/sql/schema.sql`. The delivery schema lives in
  `crm-backend/database_script/2026-08-03_notification_delivery.sql`, and that is
  load-bearing: producers enqueue inside their own transaction, so "the business
  write committed ⇒ the message is queued" is an invariant rather than a hope.
  news-board set the precedent for an annexe writing to the CRM schema.
- **Not a NATS consumer.** It polls. No `core/queue/`, no `streams.json`.
- **No `locales/` and no `core/i18n.py`.** Every other service has them; this one
  has no `ErrorException` surface, and its templates *are* its string catalogue.

## Layout
- `domain/` — pure. `message` (value objects + `OutboundStatus`), `transport`
  (the `EmailTransport` Protocol), `errors` (`TransportError.permanent`),
  `backoff`. Imports only stdlib + the shared channel encoding.
- `adapters/transports/` — `smtp`, `brevo`, `noop`, and `factory` (the only
  place that reads `EMAIL_TRANSPORT`).
- `adapters/templates/renderer.py` — Jinja2, one Environment, locale fallback.
- `infra/outbound_repository.py` — every SQL statement, written out.
- `worker/` — `dispatch.dispatch_once` (one pass, session + transport injected)
  and `main` (the poll loop, heartbeat and signal handling), plus `suppression`.
- `core/notifications/contract.py` — a **verbatim copy** of the annexes' file.

## The four things that are easy to get wrong

1. **`attempts` is incremented by the CLAIM, not by a caught failure.** If it
   moved on failure, a message that kills the worker mid-send would be reclaimed
   with the same count forever and never exhaust its budget.
2. **The claim commits before any send.** Holding the transaction across an
   SMTP/HTTP round trip pins a pooled connection and trips
   `idle_in_transaction_session_timeout`. The price is at-least-once delivery,
   mitigated by a stable `Message-ID` derived from `dedupe_key`.
3. **Every terminal update is guarded on `status = CLAIMED`.** That is what makes
   a reaped-then-reclaimed row safe: the original worker's late write finds a
   different status and changes nothing.
4. **The claim query cannot join.** Postgres rejects `FOR UPDATE` on the nullable
   side of an outer join, and there is no `id_user` column anyway — the
   recipient's address and name are denormalised onto the row at enqueue, so a
   later profile change never redirects a queued message. Community names come
   from a separate batched `SELECT`.

## Conventions
- **Failure classification is `TransportError.permanent`, and nothing else.**
  Permanent → FAILED, never retried. Transient → jittered exponential backoff
  until `DISPATCH_MAX_ATTEMPTS`. `TemplateError` is always permanent: a missing
  template or an undefined variable is a defect, not a hiccup.
- `suppress_address` is set only when the provider blamed the ADDRESS (hard
  bounce, invalid mailbox, blocked contact) — not the request or the connection.
- **`StrictUndefined` on purpose.** A renamed producer field must fail loudly,
  not send a mail with a blank where the invoice number goes.
  `tests/test_templates.py` turns that into a red test rather than a production
  dead-letter.
- **The subject is sanitised.** It is rendered from producer-controlled `data`
  and goes straight into a header, so `_sanitise_subject` collapses all
  whitespace. Never bypass it.
- **The preferences-link rule lives in the templates.** `category = INFORMATIONAL` renders a
  preferences link; `TRANSACTIONAL` must not. That is why `category` is a
  persisted column rather than something re-derived from `type` here.

## Gotchas
- **`select_autoescape(default=True)` would escape the `.txt` parts.**
  `document-generation` uses it because it renders arbitrary bundles of unknown
  extension. Here, `enabled_extensions=("html",)` with `default=False` is what
  keeps one Environment correct for both.
- **`Dockerfile.worker` must `COPY templates/`.** Without it the image ships a
  renderer and no templates, and every message dead-letters as `TemplateError`.
- **`EMAIL_TRANSPORT=NOOP` is refused outside local/test.** It marks everything
  SENT while no mail leaves — the one failure mode no dashboard would show.
- **`core/notifications/contract.py` is byte-identical with the three annexes'
  copies** and is covered by the monorepo's `scripts/check-notification-parity.sh`
  diff gate. Do not "clean it up": redefining
  `Channel.INAPP = 1 / EMAIL = 2` locally would create a second source of truth
  for an on-disk encoding.
- Brevo reports bounces by webhook only. With no inbound surface,
  `worker/suppression.py` polls `GET /v3/smtp/blockedContacts` instead — that is
  the only thing that keeps `email_suppression` current in production.
- `httpx` is a **production** dependency here and test-only everywhere else in
  the monorepo. Same pin (`0.28.1`) so there is one version in the tree.

## Verify
`cd notification-dispatch` then:
`ENV=test .venv/Scripts/python.exe -m pytest -q` (needs Docker Postgres on 5433) ·
`.venv/Scripts/python.exe -m ruff check .` · `... -m ruff format --check .` ·
`... -m mypy .`

On Windows, prefix test runs with `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` to
surface the real error instead of an INTERNALERROR.
