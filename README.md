<p align="center">
  <img src="docs/logo.svg" alt="OptimCE notification-dispatch logo" width="160">
</p>

# notification-dispatch

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/en/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![en](https://img.shields.io/badge/lang-en-43a047.svg)](README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](docs/README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](docs/README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](docs/README.nl.md)

The service that actually sends OptimCE's email.

Everything else in the platform *queues* messages; this worker delivers them. It
polls the `outbound_message` table, renders a localised subject and body, hands
the result to a configured transport (SMTP or Brevo), and records what happened.

## Why it exists

Until it did, OptimCE sent no email at all. Two consequences were live:

- inviting someone whose address had no account wrote a database row, logged an
  audit entry, and told nobody — there was no path by which that person could
  learn they had been invited;
- "send invoice" notified nobody: a member discovered they had an invoice by
  guessing to log in.

## How a message gets here

A producer calls `publish(type, data, target, category, channels)`. When `EMAIL`
is among the effective channels, the notification layer writes an
`outbound_message` row **inside the producer's own transaction**. That is the
whole design: if the invoice commits, the email is queued; if it rolls back, the
email is un-queued. There is no publish-then-hope.

An invitation to an address with no account has no in-app notification to
accompany it (`notification.id_user` is `NOT NULL`), so it is queued directly
with `id_notification = NULL`. That case is why this table exists.

## The loop

Every `DISPATCH_POLL_INTERVAL_SECONDS`:

1. **Reap** rows a worker claimed and then died holding.
2. **Claim** a batch — `FOR UPDATE SKIP LOCKED`, incrementing `attempts` in the
   same statement — and commit before sending anything.
3. **Drop** recipients on the suppression list.
4. **Render** the template for the message's type and locale.
5. **Send**, then mark `SENT`, reschedule with backoff, or mark `FAILED`.

Polling rather than `LISTEN/NOTIFY` is deliberate: `NOTIFY` is not durable, so a
sweep would be needed as a backstop regardless — and one is needed anyway for
retry backoff and for reaping. Given that, listening would buy only latency,
which email does not need.

## Transports

| `EMAIL_TRANSPORT` | Use |
|---|---|
| `BREVO` | production — `POST /v3/smtp/email`, plus a periodic pull of the provider's blocked-contact list into `email_suppression` |
| `SMTP` | self-hosting escape hatch, and Mailpit in the dev stack |
| `NOOP` | tests only; refused outside local/test |

Templates are rendered here, not in the provider: keeping four locales in one
system means they stay in version control and code review.

## Templates

```
templates/email/<type>/<locale>/{subject.txt,body.html,body.txt}
templates/email/_default/<locale>/…    # any type without its own
templates/email/_layout.html           # shared HTML chrome
```

Locale resolution is exact (`fr-BE`) → language (`fr`) → `en`; a message with no
locale uses `DEFAULT_LOCALE`. A type this service has no template for renders the
`_default` set rather than failing, so a new producer degrades instead of
dead-lettering.

## Running it

```bash
py -3.12 -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements/all.txt
cp .env.exemple .env.local           # then fill in CRM_DATABASE_URL and the transport
ENV=local .venv/Scripts/python.exe -m worker.main
```

In the dev stack it runs as its own container against Mailpit; open the caught
mail at <http://localhost:8007>.

## Tests

```bash
ENV=test .venv/Scripts/python.exe -m pytest -q     # needs Docker Postgres on 5433
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m mypy .
```

No test contacts a mail server: the loop takes its session and transport as
arguments, so it runs against a rolled-back transaction and a recording fake.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set
up a development environment, run the quality gates, and open a pull request. By
participating, you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Please report security vulnerabilities responsibly — see our
[security policy](SECURITY.md). Please **do not** open public issues for
vulnerabilities.

## License

Licensed under the [Apache License 2.0](LICENSE).
