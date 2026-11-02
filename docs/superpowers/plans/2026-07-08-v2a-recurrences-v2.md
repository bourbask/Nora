# V2-A Récurrences iter2 (refuse + actualité + tri/annuel) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Étendre la détection de récurrences (actualité + trimestriel + annuel), ajouter un refus durable, et faire compter les charges non-mensuelles dans le runway.

**Architecture:** Deux cœurs purs (`recurrences.py`, `forecast.py`) étendus + testés offline ← câblage backend (modèle + endpoints, écriture yaml ciblée) ← UI (colonne freq dans le ledger + bouton Refuser).

**Tech Stack:** Python 3 stdlib (`datetime`, `statistics`), FastAPI + pydantic, React + TS + @tanstack/react-query.

## Global Constraints

- Zéro LLM ; tout déterministe.
- Détection pure (aucune I/O) ; `today` injecté en paramètre (testable).
- Montant jamais un critère de rejet (drift → confidence high si écart max/médian ≤ 15 %).
- Écriture yaml uniquement sur action explicite (dismiss) ; `PUT /api/strategy` ne doit pas effacer `dismissed_recurrences`.
- Tests = self-checks plain-assert (style `web/api/test_forecast.py`), dans `make test-unit`.
- Commits `git commit --no-gpg-sign`, sans attribution IA.

---

### Task 1: Détection étendue — freq + actualité (`recurrences.py`, TDD)

**Files:**
- Modify: `web/api/recurrences.py`
- Modify (append tests): `web/api/test_recurrences.py`

**Interfaces:**
- Produces: `detect_recurrences(withdrawals, existing_names, today, dismissed=()) -> list[dict]`
  - `today`: `datetime.date`. `dismissed`: itérable de noms.
  - candidat `{"name","amount","freq":"monthly"|"quarterly"|"yearly","start":"YYYY-MM","end":None,"kind":"other","count","confidence"}`.

- [ ] **Step 1: Écrire les tests (append à test_recurrences.py)**

Ajouter en haut : `from datetime import date`.

**(a) Mettre à jour chaque appel existant** `R.detect_recurrences(tx, set())` → `R.detect_recurrences(tx, set(), date(2026, 4, 1))` et `R.detect_recurrences(tx, {"STREAMING"})` → `R.detect_recurrences(tx, {"STREAMING"}, date(2026, 4, 1))` (cette date garde "récentes" les fixtures existantes, qui vont jusqu'à avril 2026).

**(b) SUPPRIMER `test_quarterly_rejected`** (lignes 38-40) : ce test asserte qu'une cadence ~90 j renvoie `[]`, ce qui n'est plus vrai — le trimestriel est désormais détecté. Il est remplacé par `test_quarterly_detected` / `test_quarterly_stale_dropped` ci-dessous.

**(c) Ajouter :**

```python
def test_monthly_stale_dropped():
    # dernière occurrence = janvier, today = avril → 3 mois → périmée
    tx = _mk("OLD", ["2025-11-05", "2025-12-05", "2026-01-05"], 10.0)
    assert R.detect_recurrences(tx, set(), date(2026, 4, 1)) == []

def test_monthly_recent_kept():
    tx = _mk("CUR", ["2026-01-05", "2026-02-05", "2026-03-05"], 10.0)
    out = R.detect_recurrences(tx, set(), date(2026, 4, 1))
    assert len(out) == 1 and out[0]["freq"] == "monthly"

def test_quarterly_detected():
    tx = _mk("Q", ["2025-07-10", "2025-10-10", "2026-01-10", "2026-04-10"], 200.0)
    out = R.detect_recurrences(tx, set(), date(2026, 5, 1))
    assert len(out) == 1 and out[0]["freq"] == "quarterly"

def test_quarterly_stale_dropped():
    tx = _mk("Q", ["2024-07-10", "2024-10-10", "2025-01-10"], 200.0)
    assert R.detect_recurrences(tx, set(), date(2026, 5, 1)) == []

def test_yearly_same_month_detected():
    tx = _mk("Y", ["2024-03-15", "2025-03-15", "2026-03-15"], 90.0)
    out = R.detect_recurrences(tx, set(), date(2026, 6, 1))
    assert len(out) == 1 and out[0]["freq"] == "yearly"

def test_yearly_scattered_months_rejected():
    # ~annuel en cadence mais mois différents → dépense ponctuelle, pas annuelle
    tx = _mk("PONCT", ["2024-03-15", "2025-06-20", "2026-02-10"], 90.0)
    assert R.detect_recurrences(tx, set(), date(2026, 6, 1)) == []

def test_yearly_stale_dropped():
    tx = _mk("Y", ["2022-03-15", "2023-03-15", "2024-03-15"], 90.0)
    assert R.detect_recurrences(tx, set(), date(2026, 6, 1)) == []

def test_dismissed_excluded():
    tx = _mk("STREAMING", ["2026-01-05", "2026-02-05", "2026-03-05"], 15.99)
    assert R.detect_recurrences(tx, set(), date(2026, 4, 1), dismissed={"STREAMING"}) == []
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd web/api && python3 test_recurrences.py`
Expected: FAIL (signature / freq / actualité pas encore là).

- [ ] **Step 3: Réécrire recurrences.py**

```python
"""Détection déterministe de charges récurrentes (mensuel / trimestriel / annuel)
depuis l'historique des retraits, par régularité de cadence + filtre d'actualité
(zéro LLM, aucune I/O réseau).

Discriminant = cadence, pas montant ni type de paiement. Le montant dérive
(hausses, forfaits) → jamais un critère de rejet, seulement la confidence. Le
filtre d'actualité compare la dernière occurrence à `today` : une récurrence dont
la dernière échéance est trop ancienne n'est plus proposée."""
from statistics import median

# freq -> médiane d'écart (jours), bande d'un écart, occ. min, ancienneté max (mois)
BANDS = {
    "monthly":   {"med": (26, 35),   "band": (20, 40),   "min_occ": 3, "stale": 2},
    "quarterly": {"med": (80, 100),  "band": (75, 105),  "min_occ": 3, "stale": 4},
    "yearly":    {"med": (330, 400), "band": (320, 410), "min_occ": 2, "stale": 14},
}
BAND_FRAC_MIN = 0.60
DRIFT_HIGH = 0.15


def _distinct_days(items):
    from datetime import date
    return sorted({date.fromisoformat(t["date"][:10]) for t in items})


def _months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def _eligible_freq(days, today):
    """freq ('monthly'|'quarterly'|'yearly') si le groupe est une récurrence
    encore d'actualité, sinon None."""
    if len(days) < 2:
        return None
    gaps = [(days[i + 1] - days[i]).days for i in range(len(days) - 1)]
    med = median(gaps)
    for freq, spec in BANDS.items():
        lo, hi = spec["med"]
        if lo <= med <= hi:
            blo, bhi = spec["band"]
            if sum(1 for g in gaps if blo <= g <= bhi) / len(gaps) < BAND_FRAC_MIN:
                return None
            if len(days) < spec["min_occ"]:
                return None
            if _months_between(days[-1], today) > spec["stale"]:
                return None
            if freq == "yearly" and len({d.month for d in days}) != 1:
                return None
            return freq
    return None


def detect_recurrences(withdrawals, existing_names, today, dismissed=()):
    excluded = set(existing_names) | set(dismissed)
    groups = {}
    for t in withdrawals:
        groups.setdefault(t["payee"], []).append(t)

    out = []
    for payee, items in groups.items():
        if payee in excluded or payee == "?":
            continue
        days = _distinct_days(items)
        freq = _eligible_freq(days, today)
        if freq is None:
            continue
        amounts = [t["amount"] for t in items]
        amt = round(median(amounts), 2)
        drift = max(abs(a - amt) for a in amounts) / amt if amt else 1.0
        out.append({
            "name": payee, "amount": amt, "freq": freq,
            "start": days[0].strftime("%Y-%m"), "end": None, "kind": "other",
            "count": len(days),
            "confidence": "high" if drift <= DRIFT_HIGH else "medium",
        })

    out.sort(key=lambda c: (c["confidence"] != "high", -c["amount"]))
    return out
```

- [ ] **Step 4: Lancer, vérifier le succès**

Run: `cd web/api && python3 test_recurrences.py`
Expected: `test_recurrences.py OK` (anciens + nouveaux).

- [ ] **Step 5: Commit**

```bash
git add web/api/recurrences.py web/api/test_recurrences.py
git commit --no-gpg-sign -m "feat(api): recurrence detection — actuality filter + quarterly/yearly"
```

---

### Task 2: Forecast non-mensuel (`forecast.py`, TDD)

**Files:**
- Modify: `web/api/forecast.py` (`_active` + helper `_month_idx`)
- Modify (append tests): `web/api/test_forecast.py`

**Interfaces:**
- Produces: `_active(charge, month)` gère `freq ∈ {monthly,quarterly,yearly}` ; imputation pleine sur les mois d'échéance (phase calée sur `start`), bornée par `[start,end]`. `active_obligations` inchangé (somme des actifs).

- [ ] **Step 1: Écrire les tests (append à test_forecast.py, avant le bloc `__main__`)**

```python
def test_active_quarterly():
    ch = [{"name": "Q", "amount": 200.0, "freq": "quarterly", "start": "2026-01"}]
    assert F.active_obligations(ch, "2026-01") == 200.0   # échéance
    assert F.active_obligations(ch, "2026-02") == 0.0     # hors phase
    assert F.active_obligations(ch, "2026-04") == 200.0   # +3 mois
    assert F.active_obligations(ch, "2026-07") == 200.0

def test_active_yearly():
    ch = [{"name": "Y", "amount": 90.0, "freq": "yearly", "start": "2026-03"}]
    assert F.active_obligations(ch, "2026-03") == 90.0
    assert F.active_obligations(ch, "2026-04") == 0.0
    assert F.active_obligations(ch, "2027-03") == 90.0

def test_active_nonmonthly_needs_start():
    ch = [{"name": "Q", "amount": 200.0, "freq": "quarterly"}]   # pas de start
    assert F.active_obligations(ch, "2026-05") == 0.0

def test_active_quarterly_respects_end():
    ch = [{"name": "Q", "amount": 200.0, "freq": "quarterly", "start": "2026-01", "end": "2026-03"}]
    assert F.active_obligations(ch, "2026-04") == 0.0    # après end, même si phase OK
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd web/api && python3 test_forecast.py`
Expected: FAIL sur les nouveaux (quarterly renvoie 0 partout car `_active` skippe non-monthly).

- [ ] **Step 3: Réécrire `_active` + ajouter `_month_idx` dans forecast.py**

Remplacer la fonction `_active` existante par :
```python
def _month_idx(month):
    y, m = (int(x) for x in month.split("-"))
    return y * 12 + (m - 1)


def _active(charge, month):
    # Legacy/partial config peut omettre start (charge déjà en historique) → un
    # start absent = "démarré depuis toujours" pour le mensuel ; les cadences
    # non-mensuelles ont besoin du start pour connaître leur phase.
    start = charge.get("start")
    end = charge.get("end")
    if start is not None and month < start:
        return False
    if end is not None and month > end:
        return False
    freq = charge.get("freq", "monthly")
    if freq == "monthly":
        return True
    if start is None:
        return False
    step = {"quarterly": 3, "yearly": 12}.get(freq)
    if step is None:
        return False
    return (_month_idx(month) - _month_idx(start)) % step == 0
```

- [ ] **Step 4: Lancer, vérifier le succès**

Run: `cd web/api && python3 test_forecast.py`
Expected: `test_forecast.py OK` (anciens tests monthly + legacy inchangés + nouveaux).

- [ ] **Step 5: Commit**

```bash
git add web/api/forecast.py web/api/test_forecast.py
git commit --no-gpg-sign -m "feat(api): forecast imputes quarterly/yearly obligations on due months"
```

---

### Task 3: Backend — validation freq + dismiss + endpoint étendu

**Files:**
- Modify: `web/api/main.py`

**Interfaces:**
- Consumes: `recurrences.detect_recurrences(..., today, dismissed=...)` (Task 1).
- Produces: `RecurringCharge.freq: Literal["monthly","quarterly","yearly"]` ; endpoint `POST /api/recurrences/dismiss` ; `/api/recurrences/detected` passe `today` + `dismissed`.

- [ ] **Step 1: Restreindre `freq` dans le modèle RecurringCharge**

Dans `main.py`, la classe `RecurringCharge`, remplacer :
```python
    freq: str = "monthly"
```
par :
```python
    freq: Literal["monthly", "quarterly", "yearly"] = "monthly"
```
(`Literal` est déjà importé.)

- [ ] **Step 2: Étendre l'endpoint detected + ajouter dismiss**

Remplacer l'endpoint `api_recurrences_detected` par :
```python
@app.get("/api/recurrences/detected")
def api_recurrences_detected():
    cfg = load_cfg()
    d = cfg.get("dormant", {})
    existing = {c.get("name") for c in d.get("recurring_charges", [])}
    dismissed = d.get("dismissed_recurrences", [])
    return {"candidates": recurrences.detect_recurrences(
        fc.withdrawals_since(12), existing, fc.date.today(), dismissed)}


class DismissBody(BaseModel):
    name: str


@app.post("/api/recurrences/dismiss")
def api_recurrences_dismiss(body: DismissBody):
    cfg = load_cfg()
    d = cfg.setdefault("dormant", {})
    dismissed = d.setdefault("dismissed_recurrences", [])
    if body.name not in dismissed:
        dismissed.append(body.name)
        STRATEGY_FILE.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return {"ok": True}
```
(`fc.date` est déjà exposé — `firefly_client` importe `date` et `main` l'utilise déjà via `fc.date.today()` dans `current_month`.)

- [ ] **Step 3: Vérifier que PUT /api/strategy préserve dismissed_recurrences**

Lire le corps de `api_strategy_put` : il réécrit `d["recurring_charges"]`, `d["one_offs"]`, etc., mais **ne touche pas** `d["dismissed_recurrences"]` (il part de `load_cfg()` puis n'écrase que ses propres clés). Confirmer par inspection qu'aucune ligne ne supprime la clé. Si une ligne remplace tout le dict `dormant`, la corriger pour préserver la clé. (Attendu : rien à changer.)

- [ ] **Step 4: Vérifier dismiss + detected contre le stack réel**

```bash
cd web/api && set -a && source ../../.env && set +a && ../../.venv/bin/python - <<'PY'
import main
before = main.api_recurrences_detected()["candidates"]
print("avant:", len(before), "candidats")
if before:
    name = before[0]["name"]
    main.api_recurrences_dismiss(main.DismissBody(name=name))
    after = main.api_recurrences_detected()["candidates"]
    print("après dismiss de", repr(name), ":", len(after), "candidats")
    assert name not in {c["name"] for c in after}
    # cleanup: retirer le nom du yaml pour ne pas polluer
    import yaml
    cfg = main.load_cfg(); cfg["dormant"]["dismissed_recurrences"].remove(name)
    main.STRATEGY_FILE.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    print("cleanup ok")
PY
```
Expected: `après` = `avant - 1`, le nom disparaît, cleanup ok. Coller la sortie dans le rapport.

- [ ] **Step 5: Commit**

```bash
git add web/api/main.py
git commit --no-gpg-sign -m "feat(api): dismiss endpoint + freq validation + actuality-aware detected"
```

---

### Task 4: UI — colonne freq dans le ledger + bouton Refuser

**Files:**
- Modify: `web/ui/src/lib/api.ts`
- Modify: `web/ui/src/pages/Strategy.tsx`

**Interfaces:**
- Consumes: `POST /api/recurrences/dismiss`, `DetectedRecurrence.freq` (déjà dans le type).
- Produces: `useDismissRecurrence()` mutation.

- [ ] **Step 1: Ajouter le hook dismiss dans api.ts**

Après `useDetectedRecurrences` :
```typescript
export const useDismissRecurrence = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => put<{ ok: boolean }>("/api/recurrences/dismiss", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurrences-detected"] }),
  });
};
```
NOTE : le helper `put` fait un PUT ; l'endpoint dismiss est un POST. Ajouter un helper `post` à côté de `put` (copie de `put` avec `method: "POST"`) et l'utiliser ici :
```typescript
async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}
```
et `mutationFn: (name) => post<{ ok: boolean }>("/api/recurrences/dismiss", { name })`.

- [ ] **Step 2: Ajouter freq à l'état Charge + toForm/toPayload/addDetected (Strategy.tsx)**

- Interface : `interface Charge { name: string; amount: number; freq: string; start: string; end: string | null; kind: string; remaining_balance: number | null }`
- `toForm` charges map : ajouter `freq: c.freq ?? "monthly",`
- `toPayload` charges map : remplacer `freq: "monthly",` par `freq: c.freq || "monthly",`
- `addDetected` : signature `(c: { name: string; amount: number; start: string; freq: string })` et l'objet poussé ajoute `freq: c.freq,` ; le champ "empty charge" du bouton "+ Ajouter" ajoute `freq: "monthly",`.

- [ ] **Step 3: Ajouter la colonne freq au ledger (Strategy.tsx)**

Dans le `grid` des charges, passer le template de
`grid-cols-[1fr_90px_110px_100px_100px_90px_36px]` à
`grid-cols-[1fr_90px_110px_110px_100px_100px_90px_36px]` et insérer, après le select `kind` :
```tsx
<select className="h-9 rounded-md border bg-background px-2 text-sm" value={c.freq}
  onChange={(e) => updCharge(i, { freq: e.target.value })}>
  {["monthly", "quarterly", "yearly"].map((fr) => <option key={fr} value={fr}>{fr}</option>)}
</select>
```

- [ ] **Step 4: Bouton Refuser + cadence dans la section détectée (Strategy.tsx)**

Câbler le hook près des autres : `const dismiss = useDismissRecurrence();`.
Dans la ligne candidat, remplacer le bloc de droite par :
```tsx
<span className="flex items-center gap-3 text-muted-foreground">
  <span>{c.amount} € · {c.freq} · vu {c.count}× depuis {c.start}</span>
  <Button type="button" onClick={() => addDetected(c)}>Ajouter</Button>
  <Button type="button" className="bg-secondary text-secondary-foreground"
    onClick={() => dismiss.mutate(c.name)}>Refuser</Button>
</span>
```

- [ ] **Step 5: Vérifier le build TS**

Run: `cd web/ui && npx tsc --noEmit`
Expected: aucune erreur.

- [ ] **Step 6: Commit**

```bash
git add web/ui/src/lib/api.ts web/ui/src/pages/Strategy.tsx
git commit --no-gpg-sign -m "feat(ui): freq column in ledger + Refuser button for detected recurrences"
```

---

## Notes d'exécution

- Ordre : 1 & 2 indépendants (purs) ; 3 dépend de 1 (signature) ; 4 dépend de 3 (endpoint dismiss) + 1 (freq en sortie).
- Tasks 1, 2 : offline/TDD. Task 3 : vérif contre le stack Firefly up + `.venv`. Task 4 : tsc (clic visuel = humain).
- Après tout : `make test` vert.
- Valeur : la liste ne montre plus que des récurrences vivantes (mensuel/tri/annuel), refusables d'un clic, et une charge non-mensuelle datée compte enfin dans le runway au bon mois.
