<p align="center">
  <img src="logo.svg" alt="OptimCE notification-dispatch Logo" width="160">
</p>

# notification-dispatch

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/de/)
[![Lizenz](https://img.shields.io/badge/Lizenz-Apache%202.0-blue.svg)](../LICENSE)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-43a047.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](README.nl.md)

Der Dienst, der die E-Mails von OptimCE tatsächlich versendet.

Alles andere auf der Plattform *stellt Nachrichten in eine Warteschlange*; dieser
Worker stellt sie zu. Er fragt die Tabelle `outbound_message` ab, rendert Betreff
und Text in der passenden Sprache, übergibt das Ergebnis an den konfigurierten
Transport (SMTP oder Brevo) und hält fest, was passiert ist.

## Warum es ihn gibt

Bis dahin verschickte OptimCE überhaupt keine E-Mails. Zwei Folgen waren real:

- Wer jemanden einlud, dessen Adresse kein Konto hatte, schrieb eine Zeile in die
  Datenbank, protokollierte einen Audit-Eintrag und benachrichtigte niemanden —
  es gab keinen Weg, auf dem diese Person von ihrer Einladung hätte erfahren
  können;
- „Rechnung senden“ benachrichtigte niemanden: Ein Mitglied erfuhr von seiner
  Rechnung nur, indem es auf gut Glück auf die Idee kam, sich anzumelden.

## Wie eine Nachricht hierher gelangt

Ein Produzent ruft `publish(type, data, target, category, channels)` auf. Ist
`EMAIL` unter den wirksamen Kanälen, schreibt die Benachrichtigungsschicht eine
`outbound_message`-Zeile **innerhalb der Transaktion des Produzenten**. Genau
darin besteht der Entwurf: Wird die Rechnung committet, ist die E-Mail in der
Warteschlange; wird sie zurückgerollt, ist die E-Mail es ebenfalls. Es gibt kein
Veröffentlichen-und-Hoffen.

Eine Einladung an eine Adresse ohne Konto hat keine begleitende In-App-Benach-
richtigung (`notification.id_user` ist `NOT NULL`) und wird deshalb direkt mit
`id_notification = NULL` eingereiht. Dieser Fall ist der Grund, warum es diese
Tabelle gibt.

## Die Schleife

Alle `DISPATCH_POLL_INTERVAL_SECONDS`:

1. **Einsammeln**, was ein Worker beansprucht hatte und dann liegen ließ.
2. **Beanspruchen** eines Stapels — `FOR UPDATE SKIP LOCKED`, mit Erhöhung von
   `attempts` in derselben Anweisung — und committen, bevor irgendetwas gesendet
   wird.
3. **Verwerfen** von Empfängern auf der Sperrliste.
4. **Rendern** der Vorlage für Typ und Sprache der Nachricht.
5. **Senden**, dann als `SENT` markieren, mit Backoff neu einplanen oder als
   `FAILED` markieren.

Abfragen statt `LISTEN/NOTIFY` ist Absicht: `NOTIFY` ist nicht dauerhaft, ein
Auffang-Durchlauf wäre also ohnehin nötig — und er wird für Retry-Backoff und
das Einsammeln ohnehin gebraucht. Damit brächte Zuhören nur geringere Latenz,
die E-Mail nicht benötigt.

## Transporte

| `EMAIL_TRANSPORT` | Verwendung |
|---|---|
| `BREVO` | Produktion — `POST /v3/smtp/email`, dazu ein periodischer Abruf der Liste blockierter Kontakte des Anbieters nach `email_suppression` |
| `SMTP` | Ausweg fürs Self-Hosting, und Mailpit im Entwicklungs-Stack |
| `NOOP` | nur für Tests; außerhalb von local/test abgelehnt |

Vorlagen werden hier gerendert, nicht beim Anbieter: Vier Sprachen in einem
System zu halten bedeutet, sie unter Versionskontrolle und im Code-Review zu
halten.

## Vorlagen

```
templates/email/<type>/<locale>/{subject.txt,body.html,body.txt}
templates/email/_default/<locale>/…    # any type without its own
templates/email/_layout.html           # shared HTML chrome
```

Die Sprachauflösung erfolgt exakt (`fr-BE`) → Sprache (`fr`) → `en`; eine
Nachricht ohne Sprache verwendet `DEFAULT_LOCALE`. Ein Typ, für den dieser Dienst
keine Vorlage hat, rendert den `_default`-Satz, statt fehlzuschlagen — ein neuer
Produzent verschlechtert sich also, statt im Dead-Letter zu landen.

## Ausführen

```bash
py -3.12 -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements/all.txt
cp .env.exemple .env.local           # then fill in CRM_DATABASE_URL and the transport
ENV=local .venv/Scripts/python.exe -m worker.main
```

Im Entwicklungs-Stack läuft er als eigener Container gegen Mailpit; die
abgefangenen Nachrichten lassen sich unter <http://localhost:8007> öffnen.

## Tests

```bash
ENV=test .venv/Scripts/python.exe -m pytest -q     # needs Docker Postgres on 5433
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m mypy .
```

Kein Test kontaktiert einen Mailserver: Die Schleife erhält Session und Transport
als Argumente und läuft daher gegen eine zurückgerollte Transaktion und ein
aufzeichnendes Fake.

## Mitwirken

Beiträge sind willkommen. In [CONTRIBUTING.md](../CONTRIBUTING.md) steht, wie Sie
eine Entwicklungsumgebung einrichten, die Qualitätsprüfungen ausführen und einen
Pull Request eröffnen. Mit Ihrer Teilnahme erklären Sie sich mit unserem
[Verhaltenskodex](../CODE_OF_CONDUCT.md) einverstanden.

## Sicherheit

Bitte melden Sie Sicherheitslücken verantwortungsvoll — siehe unsere
[Sicherheitsrichtlinie](../SECURITY.md). Bitte öffnen Sie **kein** öffentliches
Issue für Sicherheitslücken.

## Lizenz

Lizenziert unter der [Apache-Lizenz 2.0](../LICENSE).
