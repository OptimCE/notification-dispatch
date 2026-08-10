# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial release. Polls `outbound_message` in the CRM database and delivers
  queued email: reap, claim (`FOR UPDATE SKIP LOCKED`), suppression filter,
  render, send, settle.
- `EmailTransport` Protocol with `SMTP` (aiosmtplib), `BREVO`
  (`POST /v3/smtp/email`) and `NOOP` adapters, selected by `EMAIL_TRANSPORT`
  through a factory. `NOOP` is refused outside local/test.
- Jinja2 email templates for six notification types across four locales, plus a
  `_default` set so an unrecognised type degrades instead of dead-lettering.
- Periodic pull of Brevo's blocked-contact list into `email_suppression`. Brevo
  reports bounces by webhook only and this service has no inbound HTTP surface,
  so the sync runs outbound instead.

### Notes

- This service owns no tables. The delivery schema lives in
  `crm-backend/database_script/2026-08-03_notification_delivery.sql`, so that a
  producer's enqueue rides on its own transaction.
- First production consumer of `aiosmtplib`, which four other services have
  declared and never imported. First production HTTP client (`httpx`) in the
  Python services.
