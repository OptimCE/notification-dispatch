# Security Policy

## Supported Versions

notification-dispatch does not publish tagged releases yet. Security fixes are
applied to the `main` branch.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Instead, use one of these channels:

1. **GitHub private vulnerability reporting** (preferred): go to the
   **Security** tab of this repository and click **"Report a vulnerability"**.
2. **Email**: [contact@optimce.be](mailto:contact@optimce.be).

Please include as much of the following as you can:

- The type of issue (e.g. injection, header injection, privilege escalation,
  information disclosure, delivery to an unintended recipient)
- The affected file(s), notification type, or component
- Step-by-step instructions to reproduce the issue, or a proof of concept
- The impact you believe the issue has, and how an attacker might exploit it

## What to Expect

OptimCE is maintained by a small team. We aim to acknowledge your report within
a few business days, keep you informed while we investigate, and credit you in
the fix (unless you prefer to remain anonymous). Please give us a reasonable
amount of time to address the issue before any public disclosure.

## Scope

This repository contains the **notification-dispatch** worker, one service of the
[OptimCE platform](https://github.com/OptimCE). Vulnerabilities in this service
— for example in email template rendering, header injection, the SMTP and Brevo
transports, the suppression list, or the worker's database access — belong here.
If the issue affects a different OptimCE service, you
can report it in that service's repository; if you are unsure, reporting it here
(or by email) is fine — we will route it to the right place.
