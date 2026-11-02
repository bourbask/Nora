# V2-A Auto-détection des récurrences — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dériver de l'historique des retraits les charges mensuelles récurrentes (carte + prélèvement) par régularité de cadence, et les proposer dans Stratégies pour amorcer le ledger daté en un clic — sans écrire sans confirmation.

**Architecture:** Cœur pur `recurrences.py` (aucune I/O, testable offline) ← fetcher paginé dans `firefly_client.py` + endpoint lecture-seule dans `main.py` ← section UI dans `Strategy.tsx` qui pré-remplit le ledger éditable existant. `convert.py`, `PUT /api/strategy`, le formulaire de charge V1 : inchangés.

**Tech Stack:** Python 3 stdlib (`statistics`, `datetime`), FastAPI, React + TS + @tanstack/react-query.

## Global Constraints

- Zéro LLM ; détection 100 % déterministe.
- `forecast.py` V1 ne consomme que `freq == "monthly"` → ne proposer QUE du mensuel.
- Détection sur **tous** les retraits, sans filtre par type de paiement ; le test de cadence trie.
- Montant **jamais** un critère de rejet (les abos dérivent) — médiane reportée, écart → `confidence`.
- Regroupement par `destination_name` exact (fragmentation des variantes acceptée en V1).
- Lecture pure : aucune écriture dans `strategy.yaml` hors clic utilisateur + sauvegarde via `PUT /api/strategy`.
- Tests = self-checks plain-assert (style `web/api/test_forecast.py`), dans `make test-unit`.
- Commits `git commit --no-gpg-sign`, sans attribution IA.

---

### Task 1: Cœur pur `detect_recurrences` (TDD)

**Files:**
- Create: `web/api/recurrences.py`
- Test: `web/api/test_recurrences.py`
- Modify: `Makefile` (cible `test-unit`)

**Interfaces:**
- Produces: `detect_recurrences(withdrawals, existing_names) -> list[dict]`
  - `withdrawals`: `list[{"date":"YYYY-MM-DD","amount":float>0,"payee":str}]`
  - `existing_names`: `set[str]`
  - retour trié (confidence high d'abord, puis amount desc) de
    `{"name","amount","freq":"monthly","start":"YYYY-MM","end":None,"kind":"other","count":int,"confidence":"high"|"medium"}`

- [ ] **Step 1: Écrire le test qui échoue**

```python
# web/api/test_recurrences.py
import recurrences as R

def _mk(payee, dates, amount):
    return [{"date": d, "amount": amount, "payee": payee} for d in dates]

def test_monthly_flat_amount_is_high_confidence():
    tx = _mk("STREAMING", ["2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05"], 15.99)
    out = R.detect_recurrences(tx, set())
    assert len(out) == 1
    c = out[0]
    assert c["name"] == "STREAMING" and c["freq"] == "monthly"
    assert c["amount"] == 15.99 and c["start"] == "2026-01"
    assert c["kind"] == "other" and c["end"] is None
    assert c["count"] == 4 and c["confidence"] == "high"

def test_drifting_amount_kept_as_medium():
    tx = (_mk("TELCO", ["2026-01-10"], 10.0) + _mk("TELCO", ["2026-02-10"], 13.0)
          + _mk("TELCO", ["2026-03-10"], 20.0) + _mk("TELCO", ["2026-04-10"], 25.0))
    out = R.detect_recurrences(tx, set())
    assert len(out) == 1 and out[0]["confidence"] == "medium"  # spread > 15%, mais gardé

def test_high_frequency_merchant_rejected():
    # 12 courses sur ~6 semaines, écarts de quelques jours → pas mensuel
    dates = [f"2026-01-{d:02d}" for d in (2, 4, 6, 9, 12, 15, 18, 21, 24, 27)]
    out = R.detect_recurrences(_mk("SUPERMARKET", dates, 40.0), set())
    assert out == []

def test_less_than_three_distinct_days_rejected():
    out = R.detect_recurrences(_mk("X", ["2026-01-05", "2026-02-05"], 9.0), set())
    assert out == []

def test_same_day_duplicates_collapsed():
    # 3 jours distincts mensuels + un doublon le même jour → pas d'écart de 0 j
    tx = _mk("SPOT", ["2026-01-05", "2026-01-05", "2026-02-05", "2026-03-05"], 9.99)
    out = R.detect_recurrences(tx, set())
    assert len(out) == 1 and out[0]["count"] == 3

def test_quarterly_rejected():
    tx = _mk("Q", ["2026-01-05", "2026-04-05", "2026-07-05", "2026-10-05"], 50.0)
    assert R.detect_recurrences(tx, set()) == []

def test_existing_name_excluded():
    tx = _mk("STREAMING", ["2026-01-05", "2026-02-05", "2026-03-05"], 15.99)
    assert R.detect_recurrences(tx, {"STREAMING"}) == []

if __name__ == "__main__":
    for n, fn in sorted(globals().items()):
        if n.startswith("test_"):
            fn()
    print("test_recurrences.py OK")
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd web/api && python3 test_recurrences.py`
Expected: FAIL — `ModuleNotFoundError: recurrences`.

- [ ] **Step 3: Implémenter le cœur pur**

```python
# web/api/recurrences.py
"""Détection déterministe de charges mensuelles récurrentes depuis l'historique
des retraits, par régularité de cadence (zéro LLM, aucune I/O réseau).

Discriminant = cadence, pas montant ni type de paiement : une charge récurrente
tombe ~1×/mois à ~30 j d'écart ; la dépense variable tombe plusieurs fois/mois à
écarts courts irréguliers. Le montant dérive (hausses, forfaits) → jamais un
critère de rejet, seulement la confidence."""
from datetime import date
from statistics import median

GAP_MED_LO, GAP_MED_HI = 26, 35      # médiane des écarts (jours) pour "mensuel"
GAP_BAND_LO, GAP_BAND_HI = 20, 40    # bande d'un écart "mensuel-ish"
BAND_FRAC_MIN = 0.60                 # part min des écarts dans la bande
DRIFT_HIGH = 0.15                    # écart montant max/médian pour confidence high


def _distinct_days(items):
    return sorted({date.fromisoformat(t["date"][:10]) for t in items})


def detect_recurrences(withdrawals, existing_names):
    groups = {}
    for t in withdrawals:
        groups.setdefault(t["payee"], []).append(t)

    out = []
    for payee, items in groups.items():
        if payee in existing_names:
            continue
        days = _distinct_days(items)
        if len(days) < 3:
            continue
        gaps = [(days[i + 1] - days[i]).days for i in range(len(days) - 1)]
        if not (GAP_MED_LO <= median(gaps) <= GAP_MED_HI):
            continue
        in_band = sum(1 for g in gaps if GAP_BAND_LO <= g <= GAP_BAND_HI)
        if in_band / len(gaps) < BAND_FRAC_MIN:
            continue

        amounts = [t["amount"] for t in items]
        amt = round(median(amounts), 2)
        drift = max(abs(a - amt) for a in amounts) / amt if amt else 1.0
        out.append({
            "name": payee, "amount": amt, "freq": "monthly",
            "start": days[0].strftime("%Y-%m"), "end": None, "kind": "other",
            "count": len(days),
            "confidence": "high" if drift <= DRIFT_HIGH else "medium",
        })

    out.sort(key=lambda c: (c["confidence"] != "high", -c["amount"]))
    return out
```

- [ ] **Step 4: Lancer, vérifier le succès**

Run: `cd web/api && python3 test_recurrences.py`
Expected: `test_recurrences.py OK`.

- [ ] **Step 5: Wire dans test-unit**

Dans `Makefile`, cible `test-unit`, après la ligne `python3 test_import_status.py` :
```makefile
	cd web/api && python3 test_recurrences.py
```

- [ ] **Step 6: Lancer make test-unit**

Run: `make test-unit`
Expected: toutes les lignes OK dont `test_recurrences.py OK`.

- [ ] **Step 7: Commit**

```bash
git add web/api/recurrences.py web/api/test_recurrences.py Makefile
git commit --no-gpg-sign -m "feat(api): deterministic recurrence detection (cadence-primary)"
```

---

### Task 2: Fetcher paginé + endpoint `/api/recurrences/detected`

**Files:**
- Modify: `web/api/firefly_client.py` (fetcher `withdrawals_since`)
- Modify: `web/api/main.py` (endpoint)

**Interfaces:**
- Consumes: `recurrences.detect_recurrences` (Task 1).
- Produces: `fc.withdrawals_since(window_months=12) -> list[{"date","amount","payee"}]` ;
  endpoint `GET /api/recurrences/detected -> {"candidates": [...]}`.

- [ ] **Step 1: Ajouter le fetcher dans firefly_client.py**

Modèle : `_all_transfers` (pagination `/transactions`). Ajouter :
```python
def withdrawals_since(window_months=12):
    """Toutes les sorties (paginées) depuis N mois. Aucun filtre par type de
    paiement — la détection de cadence trie. payee = destination_name."""
    start = _shift_month(_now_month(), window_months) + "-01"
    out, page = [], 1
    while True:
        data = api_get("/transactions",
                       {"type": "withdrawal", "limit": 200, "page": page, "start": start})
        for g in data.get("data", []):
            for t in g["attributes"]["transactions"]:
                amt = abs(float(t["amount"]))
                if amt == 0:
                    continue
                out.append({"date": t["date"][:10], "amount": amt,
                            "payee": t.get("destination_name") or "?"})
        pag = data.get("meta", {}).get("pagination", {})
        if page >= pag.get("total_pages", page):
            break
        page += 1
    return out
```
(`_shift_month` et `_now_month` existent déjà dans le fichier.)

- [ ] **Step 2: Vérifier le fetcher contre le stack réel**

Run:
```bash
cd web/api && set -a && source ../../.env && set +a && \
../../.venv/bin/python -c "import firefly_client as fc; w=fc.withdrawals_since(12); print(len(w),'withdrawals'); print(w[0])"
```
Expected: un nombre > 0 et un dict `{'date','amount','payee'}`. (Nécessite le stack Firefly up + `.venv` avec httpx/pyyaml.)

- [ ] **Step 3: Ajouter l'endpoint dans main.py**

Import en tête (près de `import scores`) : `import recurrences`.
Après l'endpoint `/api/import-status` :
```python
@app.get("/api/recurrences/detected")
def api_recurrences_detected():
    cfg = load_cfg()
    existing = {c.get("name") for c in cfg.get("dormant", {}).get("recurring_charges", [])}
    return {"candidates": recurrences.detect_recurrences(fc.withdrawals_since(12), existing)}
```

- [ ] **Step 4: Vérifier l'endpoint bout-en-bout**

Run:
```bash
cd web/api && set -a && source ../../.env && set +a && \
../../.venv/bin/python -c "import main; c=main.api_recurrences_detected()['candidates']; print(len(c),'candidats'); [print(x['confidence'],x['amount'],x['name']) for x in c[:15]]"
```
Expected: liste de candidats mensuels réels (streaming, logiciels, prêt banque, loyer, impôts…), les `high` d'abord. Pas d'exception.

- [ ] **Step 5: Commit**

```bash
git add web/api/firefly_client.py web/api/main.py
git commit --no-gpg-sign -m "feat(api): /api/recurrences/detected (paginated withdrawals + detection)"
```

---

### Task 3: UI — section « Récurrences détectées » (Stratégies)

**Files:**
- Modify: `web/ui/src/lib/api.ts` (type + hook)
- Modify: `web/ui/src/pages/Strategy.tsx` (section + bouton pré-remplissage)

**Interfaces:**
- Consumes: `GET /api/recurrences/detected` (Task 2).
- Produces: `useDetectedRecurrences()` ; type `DetectedRecurrence`.

- [ ] **Step 1: Type + hook dans api.ts**

Après `useUpdateStrategy` (~ligne 156) :
```typescript
export interface DetectedRecurrence {
  name: string; amount: number; freq: string; start: string;
  end: string | null; kind: string; count: number; confidence: "high" | "medium";
}
export const useDetectedRecurrences = () =>
  useQuery({
    queryKey: ["recurrences-detected"],
    queryFn: () => get<{ candidates: DetectedRecurrence[] }>("/api/recurrences/detected"),
  });
```

- [ ] **Step 2: Câbler le hook dans Strategy.tsx**

Import : ajouter `useDetectedRecurrences` à la ligne `import { useStrategy, ... } from "@/lib/api"`.
Après `const update = useUpdateStrategy();` :
```typescript
const detected = useDetectedRecurrences();
```
Ajouter un helper d'ajout (près de `set`) :
```typescript
const addDetected = (c: { name: string; amount: number; start: string }) =>
  set({ charges: [...f.charges, {
    name: c.name, amount: c.amount, start: c.start,
    end: null, kind: "other", remaining_balance: null,
  }] });
```

- [ ] **Step 3: Insérer la section détectée (avant le bouton « + charge », après la liste `f.charges.map`)**

Filtrer les candidats déjà présents dans le brouillon (nom déjà ajouté → disparaît aussitôt) :
```tsx
{detected.data && detected.data.candidates
  .filter((c) => !f.charges.some((ch) => ch.name === c.name)).length > 0 && (
  <div className="mt-4 space-y-1">
    <Label>Récurrences détectées (à confirmer)</Label>
    {detected.data.candidates
      .filter((c) => !f.charges.some((ch) => ch.name === c.name))
      .map((c) => (
        <div key={c.name} className="flex items-center justify-between rounded-md border px-3 py-1.5 text-sm">
          <span className="flex items-center gap-2">
            <span className={c.confidence === "high" ? "text-success" : "text-muted-foreground"}>●</span>
            {c.name}
          </span>
          <span className="flex items-center gap-3 text-muted-foreground">
            <span>{c.amount} € · vu {c.count}× depuis {c.start}</span>
            <Button type="button" onClick={() => addDetected(c)}>Ajouter</Button>
          </span>
        </div>
      ))}
  </div>
)}
```

- [ ] **Step 4: Vérifier le build TS**

Run: `cd web/ui && npx tsc --noEmit`
Expected: aucune erreur.

- [ ] **Step 5: Vérifier visuellement**

Run: `make dev` → onglet Stratégies.
Expected: sous le ledger, « Récurrences détectées » liste les candidats (pastille verte = high). Clic « Ajouter » → une charge pré-remplie apparaît dans le ledger et le candidat disparaît de la liste. Sauvegarder → persiste ; recharger → le candidat ajouté ne réapparaît pas (exclu backend).

- [ ] **Step 6: Commit**

```bash
git add web/ui/src/lib/api.ts web/ui/src/pages/Strategy.tsx
git commit --no-gpg-sign -m "feat(ui): detected-recurrences section prefills the ledger"
```

---

## Notes d'exécution

- Ordre strict 1 → 2 → 3 (endpoint consomme le cœur ; UI consomme l'endpoint).
- Task 1 entièrement offline/TDD. Tasks 2 & 4-steps de vérif nécessitent le stack Firefly up + `.venv` (httpx, pyyaml déjà installés cette session).
- Après tout : `make test` doit rester vert.
- Valeur débloquée : l'utilisateur confirme loyer/impôts/prêts/abos réels en quelques clics → runway V1 montre enfin le creux.
