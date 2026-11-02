# V2-0 — Ingestion sans friction — Design

**Date:** 2026-07-08
**Milestone:** V2-0 (première brique de V2 ; automatisation d'ingestion, hors roadmap analytique)
**Statut:** validé, prêt pour plan d'implémentation

## Contexte

La douleur #1 exprimée est l'import bancaire manuel (banque + courtier). C'est
de l'ingestion, pas de l'analytique. V2-0 la réduit au minimum de friction
possible **sans jamais toucher au chemin de l'argent** (aucun LLM, tout montant
déterministe).

### État réel à l'entrée (vérifié, pas supposé)

3 des 4 items du plan d'origine sont déjà faits :

| Item | État |
|---|---|
| Timer systemd auto-sync | ✅ **installé et actif** (`firefly-autosync.timer`, prochain run planifié, a tourné le 2026-07-08). La note mémoire « réinstaller le timer » est périmée. |
| Dépôt banque → import auto | ✅ **fonctionne** — `providers/bank/convert.py` + `inbox/` + timer. Déjà réduit au glisser-déposer. |
| `auto_sync.log` | ✅ émis par `auto-sync.sh` en JSON-lines `{timestamp, source, event, transactions}`. |
| Scrape courtier | ❌ **à refaire** — source d'origine perdue (était un submodule git tiers, reverse-engineering WebSocket, disparu avec l'ancien projet). |
| Indicateur « dernier import » | ⬜ bonus, pas encore fait. |

**Périmètre réel de V2-0 = deux petits chantiers :** (1) wrapper de scrape courtier,
(2) indicateur « dernier import ». Le reste tourne déjà.

### Deux contraintes issues de l'audit de l'ancien projet

1. **2FA interactif par design courtier.** L'ancien `main.py` bloquait sur `input()`.
   L'automatisation cron était « illusoire ». Donc le scrape courtier ne peut jamais
   être 100 % non-interactif. Forme honnête : *l'humain lance le scrape et saisit
   le 2FA → le timer fait la moitié non-interactive (convert + import)*.
2. **Fuite de credentials.** L'ancien scraper stockait téléphone + PIN en clair
   dans un `config.ini` à l'intérieur du submodule. À ne pas reproduire.

## Composant 1 — `providers/broker/scrape.sh` (gitignoré)

**Job :** lancé à la demande par l'humain. Login interactif (téléphone + PIN +
prompt 2FA) → pilote le flux d'événements timeline de `brokerlib` → **aplatit** les
événements vers le CSV `;`-délimité que `convert.py` consomme déjà → écrit
`inbox/broker_transactions_<YYYY-MM-DD>.csv`. Ensuite le timer (ou un
`auto-sync.sh` manuel) convertit + importe.

**Contrat d'entrée de `convert.py` (à respecter exactement, fichier non modifié) :**
CSV `;`-délimité, colonnes `id;timestamp;title;subtitle;status;eventType;amount.value;cashAccountNumber`.
Le nom pointé `amount.value` révèle un aplatissement d'événements JSON courtier (amount
est un objet imbriqué `{value, currency}`).

**Décision : réutiliser `convert.py` tel quel.** Le seuil d'intégration est le CSV.
`brokerlib export_transactions` produit un CSV plus simple (sans `eventType` ni
`cashAccountNumber`) → inutilisable directement. Le wrapper doit donc utiliser le
flux d'événements **bruts** de la timeline brokerlib, pas son CSV.

**Dépendance :** ajouter `brokerlib` (communautaire, `communauté`, maintenu). Un
petit helper Python `providers/broker/scrape.py` fait le fetch timeline +
aplatissement ; `scrape.sh` est le CLI mince autour.

**Emplacement / confidentialité :** `providers/*` est déjà gitignoré en entier
(seuls `README.md` et `example/` sont suivis). Donc `scrape.sh`, `scrape.py` et le
`convert.py` courtier sont tous **locaux** par la règle existante — pas de nouvelle
règle gitignore, pas de risque de fuite.

**Secrets :** téléphone/PIN depuis `.env` (`BROKER_PHONE`, `BROKER_PIN`), jamais un ini en
clair dans un repo. brokerlib met en cache son propre cookie de session (`~/.brokerlib`),
donc le 2FA n'est pas redemandé à chaque run.

**Automatisation — honnêteté :** le 2FA reste interactif. Le scrape est
manuel/à-la-demande ; le README le dit clairement. Le timer ne fait que la moitié
non-interactive.

**Réutilisation :** `convert.py`, `auto-sync.sh`, `import-csv-firefly.sh` inchangés.

**Risque / spike :** confirmer que le schéma d'événement brokerlib porte bien
`eventType`, `cashAccountNumber` et `amount.value` imbriqué. Un login brokerlib jetable
+ dump vérifie le mapping d'aplatissement **avant** de s'engager dessus.

## Composant 2 — indicateur « dernier import » (Réglages)

**Job :** petite tuile dans Réglages : par source (`bank`, `broker`),
dernier événement — horodatage, ok/échec, nombre de transactions. Rassure « la
sync est vivante ».

**Données :** `data/auto_sync.log` existe déjà (JSON-lines). Aucune nouvelle
persistance.

**API :** un endpoint `GET /api/import-status` — lit le log, renvoie la dernière
ligne par source. Lecture pure, hors chemin de l'argent. Suit le pattern proxy
mince existant de `web/api`.

**UI :** tuile dans la page Réglages existante (où vit déjà le panneau de
cohérence). Temps relatif (« il y a 3 h »), pastille verte/rouge par source.
Réutilise les composants carte existants.

**Cas limites :** log absent/vide → « aucun import encore ». Dernier événement en
échec → rouge + nom de l'événement.

## Tests (self-checks plain-assert, dans `make test-unit`)

- Parser de log : dernière-ligne-par-source, log vide, ligne malformée.
- Aplatissement scrape : assert sur une petite fixture d'événement → colonnes du
  CSV attendues.

Pas de framework, pas de fixtures lourdes — même style que `test_forecast.py`.

## Hors scope V2-0 (explicite)

- **API bancaire PSD2** — reste en drop manuel, aucun code.
- **Auto-détection des récurrences** — c'est V2-A.
- **Dater les obligations dans `strategy.yaml`** — tâche manuelle 5 min séparée,
  aucun code (débloque la valeur du runway V1, mais indépendante de l'ingestion).
- **Toute narration LLM / valeur de marché** — invariants V1 maintenus.

## Invariants (repris de V1, jamais cassés)

- Zéro LLM dans le chemin de l'argent. Tout montant déterministe.
- Confidentialité : rien de perso dans le repo public → `.env` / gitignore
  (`scrape.sh` gitignoré, creds en `.env`).
- Réutiliser avant d'écrire.
- Commits `--no-gpg-sign`, sans attribution IA.
