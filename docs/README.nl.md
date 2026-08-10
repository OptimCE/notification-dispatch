<p align="center">
  <img src="logo.svg" alt="OptimCE notification-dispatch-logo" width="160">
</p>

# notification-dispatch

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/nl/)
[![Licentie](https://img.shields.io/badge/Licentie-Apache%202.0-blue.svg)](../LICENSE)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-lightgrey.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-43a047.svg)](README.nl.md)

De dienst die de e-mail van OptimCE daadwerkelijk verstuurt.

Al het andere in het platform *plaatst berichten in de wachtrij*; deze worker
bezorgt ze. Hij bevraagt de tabel `outbound_message`, rendert een gelokaliseerd
onderwerp en bericht, geeft het resultaat door aan het geconfigureerde transport
(SMTP of Brevo) en legt vast wat er gebeurd is.

## Waarom hij bestaat

Tot dan verstuurde OptimCE helemaal geen e-mail. Twee gevolgen waren reëel:

- iemand uitnodigen van wie het adres geen account had, schreef een rij naar de
  database, legde een auditregel vast en verwittigde niemand — er was geen enkele
  weg waarlangs die persoon kon vernemen dat hij was uitgenodigd;
- "factuur versturen" verwittigde niemand: een lid ontdekte dat er een factuur
  was door te gokken dat het moest inloggen.

## Hoe een bericht hier terechtkomt

Een producent roept `publish(type, data, target, category, channels)` aan. Zit
`EMAIL` bij de effectieve kanalen, dan schrijft de notificatielaag een
`outbound_message`-rij **binnen de transactie van de producent zelf**. Dat is het
hele ontwerp: committeert de factuur, dan staat de e-mail in de wachtrij; rolt ze
terug, dan staat de e-mail er niet meer. Er is geen publiceren-en-hopen.

Een uitnodiging naar een adres zonder account heeft geen in-app-notificatie om
mee te reizen (`notification.id_user` is `NOT NULL`) en wordt daarom rechtstreeks
in de wachtrij gezet met `id_notification = NULL`. Dat geval is de reden waarom
deze tabel bestaat.

## De lus

Elke `DISPATCH_POLL_INTERVAL_SECONDS`:

1. **Terughalen** van rijen die een worker had geclaimd en vervolgens liet
   liggen.
2. **Claimen** van een batch — `FOR UPDATE SKIP LOCKED`, met verhoging van
   `attempts` in dezelfde instructie — en committen vóór er iets verstuurd wordt.
3. **Weglaten** van ontvangers op de onderdrukkingslijst.
4. **Renderen** van de template voor het type en de taal van het bericht.
5. **Versturen**, en dan `SENT` markeren, opnieuw inplannen met backoff, of
   `FAILED` markeren.

Bevragen in plaats van `LISTEN/NOTIFY` is bewust: `NOTIFY` is niet duurzaam, dus
een veegronde als vangnet zou hoe dan ook nodig zijn — en die is er sowieso al
voor retry-backoff en voor het terughalen. Daarmee zou luisteren alleen latentie
opleveren, en die heeft e-mail niet nodig.

## Transporten

| `EMAIL_TRANSPORT` | Gebruik |
|---|---|
| `BREVO` | productie — `POST /v3/smtp/email`, plus een periodieke ophaling van de lijst geblokkeerde contacten van de provider naar `email_suppression` |
| `SMTP` | uitweg voor self-hosting, en Mailpit in de ontwikkelstack |
| `NOOP` | alleen tests; geweigerd buiten local/test |

Templates worden hier gerenderd, niet bij de provider: vier talen in één systeem
houden betekent dat ze onder versiebeheer en in code review blijven.

## Templates

```
templates/email/<type>/<locale>/{subject.txt,body.html,body.txt}
templates/email/_default/<locale>/…    # any type without its own
templates/email/_layout.html           # shared HTML chrome
```

Taalresolutie verloopt exact (`fr-BE`) → taal (`fr`) → `en`; een bericht zonder
taal gebruikt `DEFAULT_LOCALE`. Een type waarvoor deze dienst geen template heeft,
rendert de `_default`-set in plaats van te falen, zodat een nieuwe producent
degradeert in plaats van in de dead letter te belanden.

## Draaien

```bash
py -3.12 -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements/all.txt
cp .env.exemple .env.local           # then fill in CRM_DATABASE_URL and the transport
ENV=local .venv/Scripts/python.exe -m worker.main
```

In de ontwikkelstack draait hij als eigen container tegen Mailpit; de opgevangen
mail is te lezen op <http://localhost:8007>.

## Tests

```bash
ENV=test .venv/Scripts/python.exe -m pytest -q     # needs Docker Postgres on 5433
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m mypy .
```

Geen enkele test benadert een mailserver: de lus krijgt zijn sessie en transport
als argumenten mee en draait dus tegen een teruggerolde transactie en een
registrerende fake.

## Bijdragen

Bijdragen zijn welkom. Zie [CONTRIBUTING.md](../CONTRIBUTING.md) voor het
opzetten van een ontwikkelomgeving, het uitvoeren van de kwaliteitscontroles en
het openen van een pull request. Door deel te nemen gaat u akkoord met onze
[Gedragscode](../CODE_OF_CONDUCT.md).

## Beveiliging

Meld beveiligingslekken op een verantwoorde manier — zie ons
[beveiligingsbeleid](../SECURITY.md). Open **geen** publieke issue voor een
kwetsbaarheid.

## Licentie

In licentie gegeven onder de [Apache License 2.0](../LICENSE).
