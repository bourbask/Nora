# V2-A (feature 1) — Auto-détection des récurrences — Design

**Date:** 2026-07-08
**Milestone:** V2-A (réduire la saisie + fiabiliser les chiffres) — 1ʳᵉ des 3 features
**Statut:** validé, prêt pour plan d'implémentation

## Contexte

La friction #1 restante après V1 : `config/strategy.yaml` a des obligations non
datées → le runway ne montre pas le creux tant que l'utilisateur ne saisit pas ses
charges datées à la main. Cette feature est la **moitié « auto » du modèle
hybride** : dériver les récurrences de l'historique et les **proposer à
confirmer**, pour amorcer le ledger sans saisie intégrale.

Zéro LLM (invariant). Détection 100 % déterministe.

### Donnée réelle observée (historique importé 2026-07-08)

Les paiements par carte sont la **principale source de données du quotidien** —
les exclure raterait des abonnements réels facturés sur carte (streaming, logiciels,
services cloud, IA, hébergeur…). Décision : **détecter sur TOUS les retraits**,
sans filtre par type de paiement.

Le discriminant n'est ni le type de paiement ni la stabilité du montant (les abos
dérivent : hausses de prix, changements de forfait), mais la **régularité de
cadence**. Vérifié sur les données réelles : une charge récurrente se déclenche
~1×/mois à ~30 j d'écart ; une dépense variable (courses, essence, resto) se
déclenche plusieurs fois/mois à écarts courts et irréguliers. Le test de cadence
sépare proprement les deux et fait ressortir prêts (prêt banque, prêt conso), loyer
(montant fictif), impôts (montant fictif), prélèvements (assureur, énergie, opérateurs) ET
abos carte — tout en rejetant supermarchés/stations essence.

**Limite connue (déférée) :** le regroupement par `destination_name` exact
fragmente un même abo en plusieurs variantes de libellé (Un logiciel apparaît sous 5
chaînes différentes). L'utilisateur voit des doublons et confirme la bonne ligne.
Normalisation floue des libellés marchands = knob futur, hors V1.

## Composant 1 — cœur pur `detect_recurrences`

**Fichier :** nouveau `web/api/recurrences.py` (pur, aucune I/O réseau).

**Signature :**
```python
def detect_recurrences(withdrawals, existing_names):
    # withdrawals: list[dict] déjà fetchés+fenêtrés par l'appelant, chaque item =
    #   {"date": "YYYY-MM-DD", "amount": float>0, "payee": str}
    # existing_names: set[str] — noms déjà dans recurring_charges (à exclure)
    # -> list[dict] candidats triés par confidence desc puis amount desc :
    #   {"name","amount","freq","start","end","kind","count","confidence"}
    #   count = nb de jours d'occurrence distincts ; start = mois de la 1ʳᵉ.
```

**Règles de détection (cadence-primaire) :**
1. Grouper par `payee` (`destination_name` exact).
2. **Collapser les occurrences du même jour** (une charge/jour max par payee),
   puis calculer les écarts (jours) entre jours consécutifs.
3. Candidat si **≥ 3 jours d'occurrence distincts** dans la fenêtre.
4. **Cadence mensuelle régulière** (le filtre principal) :
   - médiane des écarts ∈ **26–35 j**, ET
   - **≥ 60 %** des écarts ∈ **20–40 j** (rejette les marchands haute fréquence
     dont la médiane tomberait par hasard près de 30).
   - sinon → **rejeter**. `freq = "monthly"` pour tous les gardés. `forecast.py`
     V1 ne consomme QUE `freq == "monthly"` (ligne 17 : `active_obligations`
     skippe le reste) ; non-mensuel déféré au support non-mensuel du forecast (V2+).
5. Montant : `amount` = **médiane** des montants du groupe. **PAS un critère de
   rejet** — les abos dérivent (hausses, forfaits). Reporté tel quel.
6. `start` = mois (`YYYY-MM`) de la 1ʳᵉ occurrence ; `end = null` ;
   `kind = "other"` (l'utilisateur choisit à la confirmation).
7. Exclure tout candidat dont `name` ∈ `existing_names`.
8. `confidence` : `"high"` si écart max au médian du montant ≤ **15 %** ;
   sinon `"medium"`. (Signale la dérive, ne rejette pas.)

**Valeurs sorties compatibles** avec le modèle `RecurringCharge`
(`main.py`) : `name:str`, `amount:float≥0`, `freq:str`, `start:YYYY-MM`,
`end:null`, `kind∈{loan,tax,insurance,rent,subscription,other}`.

## Composant 2 — endpoint `GET /api/recurrences/detected`

**Fichier :** `web/api/main.py` (nouvel endpoint) + fetcher dans
`web/api/firefly_client.py`.

- Fetcher `withdrawals_since(window_months=12)` dans `firefly_client.py` :
  withdrawals paginés (réutilise le pattern de `_all_transfers` :
  `/transactions?type=withdrawal&limit=200&page=`), fenêtre bornée par date.
  **Aucun filtre par type de paiement** — le test de cadence trie. Retourne
  `[{"date":"YYYY-MM-DD","amount":float>0,"payee":destination_name}]`.
- Endpoint : lit `recurring_charges` du config (→ `existing_names`), fetch, appelle
  `recurrences.detect_recurrences(...)`, renvoie `{"candidates": [...]}`.
- **Lecture pure. Aucune écriture** dans strategy.yaml.

## Composant 3 — UI (Stratégies)

**Fichiers :** `web/ui/src/lib/api.ts` (type + hook), page Stratégies (section).

- `useDetectedRecurrences()` → `GET /api/recurrences/detected`.
- Section « Récurrences détectées » sous le ledger existant. Une ligne par
  candidat : payee · montant · cadence · « vu N× depuis {start} » · pastille
  confidence · bouton **« Ajouter »**.
- Clic « Ajouter » → **pré-remplit une nouvelle ligne dans le ledger éditable
  existant** (name/amount/freq/start remplis ; kind par défaut `other` à ajuster ;
  end/remaining_balance vides). L'utilisateur ajuste puis **sauvegarde via le
  `PUT /api/strategy` existant**.
- **Rien n'est écrit sans le clic + la sauvegarde.** Un candidat déjà présent
  dans le ledger (par name) n'apparaît pas (exclu côté backend).

**Réutilisation :** le formulaire d'édition de charge du ledger V1 existe déjà —
le bouton ne fait que le pré-remplir, aucun nouveau form.

## Tests (self-checks plain-assert, `make test-unit`)

`web/api/test_recurrences.py` :
- groupe mensuel régulier, montant plat → 1 candidat `monthly`, `high`.
- montant qui dérive (> 15 %) mais cadence mensuelle → **gardé**, `confidence="medium"`.
- marchand haute fréquence (plusieurs/mois, écarts de quelques jours) → rejeté
  (< 60 % des écarts en 20–40 j).
- < 3 jours d'occurrence distincts → rejeté.
- plusieurs occurrences le même jour → collapsées (pas d'écart de 0 j parasite).
- cadence trimestrielle (~90 j) → rejetée (non-mensuel non consommé par le runway V1).
- `name` ∈ existing_names → exclu.

Pas de framework. Style `test_forecast.py`.

## Hors scope (explicite)

- Normalisation floue des libellés marchands (dédup des variantes d'un même abo) —
  regroupement par `destination_name` exact en V1 ; fragmentation acceptée.
- Devinette de `kind` par mots-clés (loan/tax/rent) — l'utilisateur choisit,
  même si loyer/impôts/prêts ressortent nettement dans les données.
- Devinette de `end` / `remaining_balance` — saisie manuelle (prêts bornés).
- Écriture auto dans strategy.yaml — le modèle est *proposer à confirmer*.
- Détection des revenus récurrents (salaire) — hors périmètre (ledger = charges).
- Store de snapshots, jauge de catégorisation — autres features V2-A, specs à part.

## Invariants

- Zéro LLM ; détection déterministe.
- Confidentialité : payees/montants restent locaux (data), rien dans le repo.
- Réutiliser avant d'écrire (`_all_transfers` pattern, ledger form, `PUT /strategy`).
- Commits `--no-gpg-sign`, sans attribution IA.
