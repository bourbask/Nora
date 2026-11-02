# V2-B — Amortissement des prêts — Design

**Date:** 2026-07-09
**Milestone:** V2-B (déployer / enrichir) — feature « amortissement », non gatée par le matelas (utile en permanence)
**Statut:** auto-validé en mode autonome (décisions documentées dans le rapport de session) ; à relire.

## Contexte

L'utilisateur a plusieurs prêts (2 auto + prêt conso + prêt banque). V1 modélise la dette
comme `remaining_balance` manuel dans le ledger (kind=loan). L'amortissement calcule,
par prêt : la date de payoff, le solde mois par mois, et le split capital/intérêt —
en close-form depuis solde + mensualité + taux. Prérequis (passifs V1) présent.
Alimente un runway enrichi et l'arbitrage R18 (V3).

**État données :** aucun prêt daté n'est encore saisi dans `config/strategy.yaml`
(charges legacy non datées — gap connu). L'amortissement produira une sortie vide
tant que l'utilisateur n'a pas saisi ses prêts avec `remaining_balance` **et**
`rate`. Cette feature ajoute justement le champ `rate` au ledger pour le permettre.

Invariants : zéro LLM, déterministe.

## Composant 1 — cœur pur `web/api/amortization.py`

Aucune I/O.

- `schedule(balance, monthly_payment, annual_rate, max_months=600) -> dict`
  - Amortissement standard : chaque mois `interest = balance * annual_rate/12`,
    `principal = monthly_payment - interest`, `balance -= principal`.
  - S'arrête quand `balance <= 0` (dernier paiement tronqué au solde restant) ou
    à `max_months`.
  - **Garde-fou :** si `monthly_payment <= premier interest` (paiement ne couvre
    pas les intérêts → jamais remboursé), renvoie `{"payoff_months": None,
    "never_amortizes": True, ...}` sans boucler.
  - `annual_rate = 0` toléré (prêt sans intérêt : payoff = ceil(balance/payment)).
  - Retour : `{"payoff_months": int|None, "total_interest": float,
    "total_principal": float, "never_amortizes": bool,
    "schedule": [{"m": i, "interest", "principal", "balance"}]}` (schedule tronqué
    à payoff).
- `payoff_month(start_month, payoff_months) -> "YYYY-MM"` (via arithmétique idx,
  cf. `forecast.month_add`) — `None` si `payoff_months` None.

Self-check `web/api/test_amortization.py` (plain-assert, `make test-unit`) :
- prêt standard (ex. 10000 € à 5 %, 200 €/mois) → payoff fini, total_interest > 0.
- taux 0 → payoff = ceil(balance/payment), interest 0.
- paiement trop faible (< intérêt initial) → `never_amortizes=True`, pas de boucle infinie.
- dernier mois tronqué (balance final ≈ 0).

## Composant 2 — schéma `rate` + endpoint `/api/loans`

- **Modèle** `RecurringCharge` (`main.py`) : ajouter `rate: Optional[float] =
  Field(default=None, ge=0, le=1)` (taux annuel en fraction, ex. 0.045). Nullable
  → prêts existants inchangés.
- **Endpoint** `GET /api/loans` (lecture pure) : pour chaque charge `kind=="loan"`
  avec `remaining_balance` :
  - si `rate` présent et `amount` (mensualité) présent → `amortization.schedule(
    remaining_balance, amount, rate)` + `payoff_month(start, payoff_months)`.
  - sinon → sortie dégradée `{schedule vide, payoff_month:null, needs_rate:true}`.
  - renvoie `{"loans": [{name, balance, payment, rate, payoff_month,
    total_interest, never_amortizes, needs_rate}]}`.

## Composant 3 — UI

- **Ledger (`Strategy.tsx`)** : ajouter une colonne **taux** (input %, ex. 4.5)
  visible/pertinente pour les lignes `kind=loan` (input tout le temps, ignoré si
  vide). `Charge` gagne `rate: number | null` ; `toForm` lit `c.rate`, `toPayload`
  renvoie `c.rate`. Permet à l'utilisateur de saisir le taux → débloque l'amortissement.
- **Carte « Prêts » (Aujourd'hui / Dashboard)** : `useLoans()` → pour chaque prêt :
  nom · solde · mensualité · taux · **date de payoff** · intérêts restants total.
  Si `needs_rate` → « ajoute le taux dans Stratégies pour la date de remboursement ».
  Si `never_amortizes` → alerte « mensualité insuffisante ». Si aucun prêt → carte masquée.

## Tests

`test_amortization.py` (ci-dessus) dans `make test-unit`. Endpoint + UI vérifiés
avec des données synthétiques (pas de prêt réel saisi) — documenté.

## Hors scope

- Arbitrage dette-vs-investissement R18 (V3 ; l'amortissement en est un prérequis).
- Renégociation / taux variable / assurance emprunteur (close-form taux fixe suffit).
- Détection auto du taux (saisie manuelle).

## Invariants

- Zéro LLM ; déterministe ; cœur pur sans I/O ; garde-fou anti-boucle.
- Réutilise le ledger/`PUT strategy`, l'arithmétique mois de `forecast`.
- Commits `--no-gpg-sign`, sans attribution IA.
