<p align="center">
  <img src="logo.svg" alt="Logo notification-dispatch OptimCE" width="160">
</p>

# notification-dispatch

[![Site web](https://img.shields.io/badge/Site%20web-optimce.be-2e7d32.svg)](https://www.optimce.be)
[![Licence](https://img.shields.io/badge/Licence-Apache%202.0-blue.svg)](../LICENSE)
[![en](https://img.shields.io/badge/lang-en-lightgrey.svg)](../README.md)
[![fr](https://img.shields.io/badge/lang-fr-43a047.svg)](README.fr.md)
[![de](https://img.shields.io/badge/lang-de-lightgrey.svg)](README.de.md)
[![nl](https://img.shields.io/badge/lang-nl-lightgrey.svg)](README.nl.md)

Le service qui envoie réellement les e-mails d'OptimCE.

Tout le reste de la plateforme *met en file* des messages ; ce worker les
délivre. Il interroge la table `outbound_message`, effectue le rendu d'un objet
et d'un corps localisés, confie le résultat au transport configuré (SMTP ou
Brevo) et consigne ce qui s'est passé.

## Pourquoi il existe

Avant lui, OptimCE n'envoyait aucun e-mail. Deux conséquences étaient bien
réelles :

- inviter une personne dont l'adresse n'avait pas de compte écrivait une ligne
  en base, journalisait une entrée d'audit et n'avertissait personne — aucun
  chemin ne permettait à cette personne d'apprendre qu'elle avait été invitée ;
- « envoyer la facture » n'avertissait personne : un membre découvrait qu'il
  avait une facture en devinant qu'il fallait se connecter.

## Comment un message arrive ici

Un producteur appelle `publish(type, data, target, category, channels)`. Lorsque
`EMAIL` fait partie des canaux effectifs, la couche de notification écrit une
ligne `outbound_message` **à l'intérieur de la transaction du producteur**. C'est
là tout le principe : si la facture est validée, l'e-mail est en file ; si elle
est annulée, l'e-mail l'est aussi. Il n'y a pas de « publier puis espérer ».

Une invitation vers une adresse sans compte n'a aucune notification in-app pour
l'accompagner (`notification.id_user` est `NOT NULL`) ; elle est donc mise en
file directement avec `id_notification = NULL`. C'est ce cas qui justifie
l'existence de cette table.

## La boucle

Toutes les `DISPATCH_POLL_INTERVAL_SECONDS` :

1. **Récupérer** les lignes qu'un worker avait réservées avant de mourir.
2. **Réserver** un lot — `FOR UPDATE SKIP LOCKED`, en incrémentant `attempts`
   dans la même instruction — et valider avant tout envoi.
3. **Écarter** les destinataires figurant sur la liste de suppression.
4. **Effectuer le rendu** du modèle correspondant au type et à la langue du
   message.
5. **Envoyer**, puis marquer `SENT`, replanifier avec un délai croissant, ou
   marquer `FAILED`.

Interroger plutôt qu'utiliser `LISTEN/NOTIFY` est délibéré : `NOTIFY` n'est pas
durable, un balayage de secours serait donc nécessaire de toute façon — et il
l'est déjà pour les réessais et pour la récupération. Dans ces conditions,
l'écoute n'apporterait que de la latence en moins, dont l'e-mail n'a pas besoin.

## Transports

| `EMAIL_TRANSPORT` | Usage |
|---|---|
| `BREVO` | production — `POST /v3/smtp/email`, plus une récupération périodique de la liste des contacts bloqués du fournisseur vers `email_suppression` |
| `SMTP` | échappatoire pour l'auto-hébergement, et Mailpit dans la pile de développement |
| `NOOP` | tests uniquement ; refusé hors local/test |

Le rendu des modèles se fait ici, pas chez le fournisseur : garder quatre langues
dans un seul système, c'est les garder sous gestion de versions et en revue de
code.

## Modèles

```
templates/email/<type>/<locale>/{subject.txt,body.html,body.txt}
templates/email/_default/<locale>/…    # any type without its own
templates/email/_layout.html           # shared HTML chrome
```

La résolution de la langue est exacte (`fr-BE`) → langue (`fr`) → `en` ; un
message sans langue utilise `DEFAULT_LOCALE`. Un type pour lequel ce service n'a
pas de modèle utilise le jeu `_default` plutôt que d'échouer : un nouveau
producteur se dégrade au lieu de partir en file d'attente morte.

## Le lancer

```bash
py -3.12 -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements/all.txt
cp .env.exemple .env.local           # then fill in CRM_DATABASE_URL and the transport
ENV=local .venv/Scripts/python.exe -m worker.main
```

Dans la pile de développement, il tourne dans son propre conteneur face à
Mailpit ; les messages interceptés sont consultables sur
<http://localhost:8007>.

## Tests

```bash
ENV=test .venv/Scripts/python.exe -m pytest -q     # needs Docker Postgres on 5433
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m mypy .
```

Aucun test ne contacte de serveur de messagerie : la boucle reçoit sa session et
son transport en arguments, elle s'exécute donc sur une transaction annulée et
un faux enregistreur.

## Contribuer

Les contributions sont les bienvenues. Consultez
[CONTRIBUTING.md](../CONTRIBUTING.md) pour préparer un environnement de
développement, exécuter les contrôles qualité et ouvrir une pull request. En
participant, vous acceptez de respecter notre
[Code de conduite](../CODE_OF_CONDUCT.md).

## Sécurité

Merci de signaler les vulnérabilités de manière responsable — voir notre
[politique de sécurité](../SECURITY.md). N'ouvrez **pas** d'issue publique pour
une vulnérabilité.

## Licence

Distribué sous [licence Apache 2.0](../LICENSE).
