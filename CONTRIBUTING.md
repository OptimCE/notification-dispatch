# Contributing to OptimCE — notification-dispatch

Thank you for your interest in contributing! Issues and pull requests are
welcome from everyone. By participating in this project, you agree to abide by
our [Code of Conduct](CODE_OF_CONDUCT.md).

## Where to Contribute

This repository holds the **notification-dispatch** worker — the service that
delivers OptimCE's outbound email. It polls the `outbound_message` table,
renders a localised subject and body, hands the result to a configured transport
(SMTP or Brevo), and settles the row. It is one of several repositories under the
[OptimCE organization](https://github.com/OptimCE), and is included in the
[OptimCE monorepo](https://github.com/OptimCE/monorepo) as a git submodule.

- **Changes to the worker itself** (the dispatch loop, transports, the template
  renderer, the email templates, the repository layer, tests) belong here.
- **Changes to the delivery schema** belong in
  [crm-backend](https://github.com/OptimCE/crm-backend). This service
  deliberately owns no table: producers enqueue inside their own transaction, so
  "the business write committed ⇒ the message is queued" is an invariant rather
  than a hope.
- **Changes to the development environment and orchestration** — Docker Compose,
  the API gateway (KrakenD), authentication (Keycloak), shared reference data —
  belong in the [monorepo](https://github.com/OptimCE/monorepo) instead.

## Setting Up a Development Environment

The worker targets **Python 3.12**.

```bash
git clone https://github.com/OptimCE/notification-dispatch.git
cd notification-dispatch
py -3.12 -m venv .venv && . .venv/Scripts/activate   # bash: source .venv/bin/activate
pip install -r requirements/all.txt
cp .env.exemple .env.local                           # then fill in CRM_DATABASE_URL
```

The tests need a **PostgreSQL on host port 5433** — there is a compose file for
it, and the fixtures apply `tests/sql/crm_test_schema.sql` themselves:

```bash
docker compose -f tests/docker-compose.test.yml up -d
```

Nothing else is required: no NATS, no object storage, and no mail server. The
dispatch loop takes its session and its transport as arguments, so the tests run
against a rolled-back transaction and a recording fake.

Run the quality gates before opening a pull request:

```bash
ENV=test ruff check . && ruff format --check . && mypy . && pytest
```

On Windows, prefix test runs with `PYTHONIOENCODING=utf-8 PYTHONUTF8=1` so the
real error surfaces instead of an `INTERNALERROR`.

To run the worker itself against the dev stack — where it sends to Mailpit, and
you can read the caught mail at <http://localhost:8007>:

```bash
ENV=local python -m worker.main
```

See the [README](README.md) for the full architecture, the delivery loop, and the
template layout.

## Reporting Bugs and Suggesting Features

Open a [GitHub issue](https://github.com/OptimCE/notification-dispatch/issues).
For bugs, include what you did, what you expected, and what happened instead —
logs and reproduction steps help a lot.

For security vulnerabilities, **do not open a public issue**; follow the
[security policy](SECURITY.md) instead.

## Submitting Pull Requests

1. Fork the repository and create a feature branch from `main`.
2. Make your changes. Keep each pull request focused on a single topic.
3. Make sure the quality gates pass
   (`ENV=test ruff check . && ruff format --check . && mypy . && pytest`).
4. Open a pull request against `main`, describing **what** you changed and
   **why**.

Notes:

- **Templates render with `StrictUndefined`.** A renamed producer field must fail
  loudly rather than send a mail with a blank where the invoice number goes. If
  you add a template, add a test for it.
- **Adding a notification type does not require code here.** Drop a
  `templates/email/<type>/<locale>/` set in place; a type with no template of its
  own renders `_default` rather than dead-lettering.
- **`core/notifications/contract.py` is byte-identical across four OptimCE
  services** and is checked by a monorepo-level gate. It encodes
  `Channel.INAPP = 1 / EMAIL = 2`, an on-disk encoding — do not redefine it here.
  A change to it is a coordinated change to every copy.
- Small documentation fixes are welcome as direct pull requests; for larger
  changes, opening an issue first to discuss the approach can save you time.

## Commit Messages

Use short, imperative commit messages, preferably following the
[Conventional Commits](https://www.conventionalcommits.org/) style used in this
repository:

```
feat: add a German template for invoice.overdue
fix: guard the terminal update on status = CLAIMED
chore: bump aiosmtplib to 3.0.2
docs: document the locale fallback chain
```

## License

notification-dispatch is licensed under the [Apache License 2.0](LICENSE). By
contributing, you agree that your contributions will be licensed under the same
license.
