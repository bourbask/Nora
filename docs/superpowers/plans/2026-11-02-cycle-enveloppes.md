# Cycle de paie, disponible et enveloppes — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter à l'API de Nora les calculs déterministes du module finances de l'assistant : position dans le cycle de paie, disponible justifié, réponses « je peux payer / je mets de côté », enveloppes par poste et réductions ordonnées, modes du coussin, alertes de dérapage, opérations à classer et création de règles, le tout derrière un jeton.

**Architecture:** Nouveaux modules purs testés (`cycle.py`, `budget.py`, `finance_alerts.py`) à côté de `forecast.py`, sans en modifier les sorties ; l'I/O registre reste dans `firefly_client.py` ; routes ajoutées dans `main.py` sous `/api/cycle*`, `/api/envelopes`, `/api/finance-alerts`, `/api/unclassified`, `/api/rules`. Middleware jeton sur toutes les routes ; le proxy du tableau de bord l'ajoute côté serveur.

**Tech Stack:** Python 3, FastAPI, httpx, pyyaml, pytest (tests `web/api/test_*.py`, lancés depuis `web/api` : `python -m pytest -q`).

**Spec:** spec « Module finances » de l'assistant (dépôt de l'assistant, `docs/superpowers/specs/2026-11-02-module-finances-design.md`) — résumée ici dans les contraintes.

## Global Constraints

- Calcul déterministe, zéro LLM ; chaque réponse porte sa **justification** (entrées, période de référence) et la **date de dernière synchronisation**.
- Fin de cycle = **dernier jour du mois** de la paie attendue ; jamais compter sur une paie anticipée.
- Le compte coussin et tout compte d'investissement **ne comptent jamais** dans le disponible.
- Données insuffisantes ⇒ `quality: "incertaine"` : moins de **2** cycles complets, part classée **< 0.80**, ou paie du cycle non reconnue.
- Synchro de plus de **48 h** ⇒ `stale: true`.
- Mode **reconstitution** si solde du coussin < seuil ; sinon **serein**. En reconstitution, aucune dépense d'un poste compressible au-delà de son enveloppe restante n'est validée.
- Toutes les valeurs personnelles dans `config/strategy.yaml` (gitignoré) ; `strategy.example.yaml` avec placeholders neutres.
- Dépôt public : aucun nom de banque, d'agrégateur, de montant réel dans le code, les tests, les commentaires, les commits.
- Les sorties existantes (`/api/remaining`, `/api/runway`, tableau de bord) ne changent pas.

## Review Focus

1. Paie reçue le 26–31 → le cycle courant commence ce jour-là et finit au dernier jour du **mois suivant** ; paie non trouvée → cycle = mois calendaire courant, qualité incertaine.
2. Charge fixe déjà prélevée avec un montant qui a dérivé (±15 %) → reconnue comme passée, pas recomptée.
3. Mois de 28/29/30/31 jours → `days_left` exact, aucun décalage d'un jour.
4. Opération remboursée / montant négatif ou nul dans les sorties → ignorée, jamais une dépense négative qui gonfle le disponible.
5. Création de règle répétée pour le même payee → une seule règle (idempotent), pas de doublon dans le registre.

---

## Structure

```
web/api/cycle.py            cycle, paie, charges restantes, taux variables, disponible, qualité
web/api/budget.py           modes du coussin, enveloppes, réductions, verdicts, échéance du plafond
web/api/finance_alerts.py   dérapages → alertes à identifiant stable
web/api/auth.py             middleware jeton
web/api/firefly_client.py   + entrées I/O (revenus, sorties catégorisées par cycle, non classées, règles)
web/api/main.py             + routes
config/strategy.example.yaml + bloc `budget`
deploy/nora-ui.nginx.conf   + en-tête jeton vers l'API
```

Bloc de configuration ajouté (example, valeurs neutres) :

```yaml
budget:
  pay: {payee_contains: "EMPLOYEUR", min_amount: 1000}
  margin_eur: 50
  cushion: {account: "Bank - Savings", floor_eur: 3000, ceiling_eur: 22950, warn_months: 3}
  postes:                              # ordre de la liste = ordre de réduction (premier rogné d'abord)
    - {name: restos,  category: "Restaurants", compressible: true,  floor_eur: 0}
    - {name: loisirs, category: "Loisirs",     compressible: true,  floor_eur: 0}
    - {name: courses, category: "Courses",     compressible: false, floor_eur: 0}
    - {name: essence, category: "Carburant",   compressible: false, floor_eur: 0}
  reference_cycles: 6
  consent_expires: null                # "AAAA-MM-JJ", saisi au consentement bancaire
```

`dormant.recurring_charges[]` gagne deux champs optionnels : `match` (sous-chaîne du payee, défaut = `name`) et `day` (jour habituel du prélèvement).

---

### Task 1: Cycle de paie et charges restantes (`cycle.py`)

**Files:** Create `web/api/cycle.py`, `web/api/test_cycle.py`

**Interfaces — Produces:**
- `find_last_pay(incomes: list[dict], rule: dict, today: date) -> date | None` — `incomes` = `{date, amount, payee}` ; dernière entrée `amount >= rule.min_amount` et `rule.payee_contains` (insensible à la casse) dans le payee, au plus 45 jours avant `today`.
- `cycle_bounds(today: date, last_pay: date | None) -> tuple[date, date]` — `last_pay` présent : `(last_pay, dernier jour du mois contenant last_pay + 10 jours)` ; absent : `(1er du mois de today, dernier jour du mois de today)`.
- `remaining_charges(charges: list[dict], start: date, end: date, withdrawals: list[dict]) -> list[dict]` — charges mensuelles actives (réutiliser `forecast.active_obligations` pour l'activité par mois) sans sortie correspondante dans `[start, end]` (payee contient `match` et montant à ±15 %) ; `{name, amount, day}`.
- `daily_rates(cycles: list[dict]) -> dict[str, float]` — `cycles` = `[{days: int, by_category: {cat: montant}}]` (cycles complets) ; médiane par catégorie de `montant/days`.
- `available(balance: float, charges_left: list[dict], rates: dict, days_left: int, margin: float) -> dict` — `{available, balance, charges_left_total, variable_expected: {cat: montant}, margin, days_left}` arrondis au centime.
- `quality(complete_cycles: int, coverage: float, pay_found: bool) -> str` — `"fiable"` ou `"incertaine"`.

- [ ] **Step 1: Tests** `test_cycle.py` :
  - `cycle_bounds(date(2026,11,2), date(2026,10,28)) == (date(2026,10,28), date(2026,11,30))` ; `cycle_bounds(date(2026,11,29), date(2026,11,27)) == (date(2026,11,27), date(2026,12,31))` ; février : `cycle_bounds(date(2027,2,3), date(2027,1,28))[1] == date(2027,2,28)` ; `cycle_bounds(date(2026,11,5), None) == (date(2026,11,1), date(2026,11,30))`.
  - `find_last_pay` : ignore un revenu sous `min_amount`, ignore un payee non correspondant, rend le plus récent, `None` au-delà de 45 jours.
  - `remaining_charges` : loyer prélevé le 3 à 712 € pour une charge de 700 → passée ; assurance non prélevée → restante ; charge dont `end` est passé → absente.
  - `daily_rates` : trois cycles `{courses: 300/30j, 330/30j, 600/31j}` → médiane `11.0` ; catégorie absente d'un cycle compte 0.
  - `available` : `balance 1000`, charges 300, `rates {courses: 10}`, `days_left 10`, `margin 50` → `available == 550.0`.
  - Review Focus 4 : une sortie de montant ≤ 0 n'entre ni dans les taux ni dans les charges passées.
  - `quality(1, 0.9, True) == "incertaine"`, `quality(2, 0.79, True) == "incertaine"`, `quality(2, 0.8, False) == "incertaine"`, `quality(2, 0.8, True) == "fiable"`.
- [ ] **Step 2:** `cd web/api && python -m pytest -q test_cycle.py` → échec (module absent).
- [ ] **Step 3:** implémenter `cycle.py` (fonctions pures, `datetime.date`, `calendar.monthrange`).
- [ ] **Step 4:** vert ; suite complète `python -m pytest -q` verte.
- [ ] **Step 5:** commit `feat(api): cycle de paie et disponible`.

### Task 2: Coussin, enveloppes, réductions, verdicts (`budget.py`)

**Files:** Create `web/api/budget.py`, `web/api/test_budget.py`

**Interfaces:**
- Consumes: sorties de Task 1 (`available()` dict, `daily_rates`).
- Produces:
  - `mode(cushion_balance: float, floor: float) -> str` — `"reconstitution"` si `<`, sinon `"serein"`.
  - `envelopes(rates: dict, days: int, postes: list[dict], mode: str) -> dict[str, float]` — par poste : `rates[category]*days` ; en reconstitution, poste compressible → `floor_eur`.
  - `spent_in_cycle(by_category: dict, postes) -> dict[str, float]`.
  - `cuts(shortfall: float, envelopes_left: dict, postes: list[dict]) -> list[dict]` — parcours dans l'ordre de `postes`, prend sur chaque enveloppe restante (≥ 0) jusqu'à combler ; `[{poste, amount}]` ; reste non comblé → dernier élément `{poste: None, amount: reste}`.
  - `can_afford(avail: dict, amount: float, poste: str | None, mode: str, envelopes_left: dict, postes) -> dict` — `{ok: bool, after: float, reason: str, cuts: list}` ; `ok` faux si `after < 0` (avec `cuts`) ou si reconstitution + poste compressible + `amount > envelopes_left[poste]` (`reason = "reconstitution"`).
  - `can_save(avail: dict, amount: float) -> dict` — `{ok, after}`.
  - `ceiling_eta(balance: float, ceiling: float, monthly_saving: float) -> int | None` — mois avant plafond, `None` si épargne ≤ 0.
- [ ] **Step 1: Tests** `test_budget.py` : `mode(2999, 3000) == "reconstitution"`, `mode(3000, 3000) == "serein"` ; enveloppes reconstitution → restos/loisirs à `floor_eur`, courses inchangées ; `cuts(100, {restos: 30, loisirs: 50, courses: 200, essence: 80}, postes)` → `[{restos,30},{loisirs,50},{courses,20}]` ; `cuts` sur enveloppes insuffisantes → dernier `{poste: None, amount: reste}` ; `can_afford` : disponible 60, essence 70 → `ok False`, `after -10`, `cuts` non vides ; reconstitution, restos 25 > enveloppe 10 → `ok False`, `reason "reconstitution"` ; serein même cas mais disponible suffisant → `ok True` ; `can_save(avail 300, 300).ok is True`, `(…, 301).ok is False` ; `ceiling_eta(20000, 22950, 500) == 6`, `ceiling_eta(…, 0) is None`.
- [ ] **Step 2–4:** rouge, implémentation, vert (suite complète).
- [ ] **Step 5:** commit `feat(api): enveloppes, réductions et modes du coussin`.

### Task 3: Alertes de dérapage (`finance_alerts.py`)

**Files:** Create `web/api/finance_alerts.py`, `web/api/test_finance_alerts.py`

**Interfaces — Produces:**
- `build(cycle_start: date, avail: dict, envelopes: dict, spent: dict, unexpected: list[dict], consent_expires: date | None, today: date, ceiling_months: int | None, warn_months: int) -> list[dict]` — chaque alerte `{id, kind, title, details, remedy}` ; `id` stable par cycle : `f"{kind}:{cycle_start.isoformat()}:{clé}"`.
  - `kind` : `"negative"` (disponible < 0 ; `remedy` = réductions `cuts`), `"envelope"` (dépensé > enveloppe d'un poste), `"near_limit"` (dépensé ≥ 85 % d'une enveloppe — conseil, pas dérapage), `"unexpected"` (sortie ≥ 100 € non récurrente et non catégorisée dans le cycle — seuil `unexpected_min_eur` en config, défaut 100), `"consent"` (≤ 14 jours avant `consent_expires`, ou expiré), `"ceiling"` (`ceiling_months is not None and ≤ warn_months`).
  - Textes en français, chiffres arrondis à l'euro dans `title`, au centime dans `details`.
- [ ] **Step 1: Tests** : chaque `kind` déclenché par son cas et absent hors cas ; même entrée deux fois → mêmes `id` ; nouveau cycle → `id` différents ; `remedy` du `negative` liste les postes dans l'ordre de réduction.
- [ ] **Step 2–4:** rouge, implémentation, vert.
- [ ] **Step 5:** commit `feat(api): alertes de dérapage`.

### Task 4: Entrées registre et routes

**Files:** Modify `web/api/firefly_client.py`, `web/api/main.py`, `config/strategy.example.yaml`; Create `web/api/test_cycle_routes.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces (routes, JSON) :
  - `GET /api/cycle` → `{start, end, today, days_left, mode, quality, stale, last_sync, available: <dict Task 1>, charges_left: [...], envelopes: {...}, spent: {...}, cushion: {balance, floor}}`.
  - `GET /api/cycle/afford?amount=<float>&poste=<str?>` → `can_afford` + `quality`, `stale`.
  - `GET /api/cycle/save?amount=<float>` → `can_save` + `quality`, `stale`.
  - `GET /api/envelopes` → enveloppes du **cycle suivant** (`days` = longueur du mois suivant), `mode`.
  - `GET /api/finance-alerts` → `{alerts: [...]}`.
  - `GET /api/unclassified` → opérations sans catégorie du cycle : `[{id, date, payee, amount}]` (au plus 20, montants > 0).
  - `POST /api/rules` `{payee: str, poste: str}` → crée (ou réutilise si une règle de même déclencheur existe) une règle du registre « description/destination contient `payee` ⇒ catégorie du poste », la déclenche sur les opérations passées ; `{rule_id, created: bool, applied: true}` ; `poste` inconnu → 422 ; `payee` vide ou > 100 caractères → 422.
- I/O ajoutée dans `firefly_client.py` : `incomes_since(days)`, `withdrawals_between(start, end)` (avec catégorie), `cycles_history(n, pay_rule)` (n cycles complets précédents), `account_balance(name)`, `find_rule(payee)`, `create_rule(payee, category)`, `trigger_rule(rule_id)` (API REST du registre : `/rules`, `/rules/{id}/trigger`) ; `last_sync()` depuis le journal d'import existant (`import_status.last_per_source`, horodatage le plus récent).
- [ ] **Step 1: Tests** `test_cycle_routes.py` avec `fastapi.testclient.TestClient` et `firefly_client` monkeypatché (aucun réseau) : `/api/cycle` rend `days_left` exact pour un 2 novembre (28), `stale` vrai si dernière synchro > 48 h ; `/api/cycle/afford?amount=70&poste=essence` reflète `can_afford` ; `POST /api/rules` deux fois même payee → second `created False` (Review Focus 5) ; poste inconnu → 422 ; `/api/remaining` inchangé (snapshot de sa réponse avant/après sur les mêmes données monkeypatchées).
- [ ] **Step 2–4:** rouge, implémentation, vert (suite complète).
- [ ] **Step 5:** commit `feat(api): routes du cycle, des enveloppes, des alertes et des règles`.

### Task 5: Jeton d'accès à l'API

**Files:** Create `web/api/auth.py`, `web/api/test_auth.py`; Modify `web/api/main.py`, `deploy/nora-ui.nginx.conf`, compose de déploiement (variable `NORA_API_TOKEN`), README (section déploiement).

**Interfaces — Produces:** middleware : en-tête `Authorization: Bearer <NORA_API_TOKEN>` exigé sur toutes les routes sauf `GET /api/health` ; comparaison à temps constant (`hmac.compare_digest`) ; `NORA_API_TOKEN` absent ou vide ⇒ l'API refuse de démarrer (message nommant la variable). Le proxy nginx du tableau de bord ajoute l'en-tête côté serveur (le navigateur ne voit jamais le jeton).
- [ ] **Step 1: Tests** : sans jeton → 401 ; mauvais jeton → 401 ; bon jeton → 200 ; `/api/health` sans jeton → 200 ; démarrage sans variable → erreur nommant `NORA_API_TOKEN`.
- [ ] **Step 2–4:** rouge, implémentation, vert ; `docker compose config -q` sur le compose de déploiement.
- [ ] **Step 5:** commit `feat(api): jeton d'accès`.

## Après fusion (opérateur)

1. `config/strategy.yaml` : bloc `budget` réel, `match`/`day` sur les charges fixes.
2. `NORA_API_TOKEN` généré (`openssl rand -hex 32`) dans l'environnement de l'API et du proxy.
3. Redéployer ; vérifier le tableau de bord (inchangé) et `GET /api/cycle` avec le jeton.
