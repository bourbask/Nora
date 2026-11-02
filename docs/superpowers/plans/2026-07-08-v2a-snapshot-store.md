# V2-A Store de snapshots mensuels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capturer mois par mois une photo des métriques clés (valeur nette, cash, scores, poids) dans un store append-only, l'exposer, et tracer la valeur nette dans le temps.

**Architecture:** Cœur pur `snapshots.py` (JSON local, testable) ← script headless `capture-snapshot.py` (réutilise `firefly_client`/`scores`) déclenché par un timer systemd mensuel ← endpoint lecture + courbe UI.

**Tech Stack:** Python 3 stdlib (`json`, `datetime`), FastAPI, React + TS + ECharts.

## Global Constraints

- Zéro LLM ; déterministe ; le cœur pur ne lit pas l'horloge (`captured_at` injecté).
- `data/snapshots.json` gitignoré (déjà couvert par `data/` dans `.gitignore`).
- Backfill : seuls `net_worth` + `savings_rate` sont fiables en arrière ; scores/poids = `None`, `backfilled:True`.
- Réutiliser `firefly_client`, `scores`, `LineChart`, le pattern systemd (`firefly-autosync.*`).
- Tests = self-checks plain-assert (style `test_forecast.py`), dans `make test-unit`.
- Commits `git commit --no-gpg-sign`, sans attribution IA.

---

### Task 1: Cœur pur `snapshots.py` (TDD)

**Files:**
- Create: `web/api/snapshots.py`
- Test: `web/api/test_snapshots.py`
- Modify: `Makefile` (cible `test-unit`)

**Interfaces:**
- Produces: `build_snapshot(month, nw, summ, pf, dormant_score, invested_score, captured_at)`, `backfill_snapshot(month, net_worth, savings_rate, captured_at)`, `upsert(store, snap)`, `load(path)`, `dump(path, store)`, `to_series(store)`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# web/api/test_snapshots.py
import snapshots as S

NW = {"net_of_debt": 9864.88, "total": 12000.0, "debt": 2135.12, "dormant": 5000.0}
SUMM = {"savings_rate": 0.32}
PF = {"total_cost": 7000.0, "bucket_weights": {"high": 0.5, "mid": 0.3, "low": 0.2}, "crypto_weight": 0.05}


def test_build_snapshot_fields():
    s = S.build_snapshot("2026-06", NW, SUMM, PF, 78, 65, "2026-07-01T00:00:00Z")
    assert s["month"] == "2026-06"
    assert s["net_worth"] == 9864.88 and s["net_worth_gross"] == 12000.0 and s["debt"] == 2135.12
    assert s["dormant_cash"] == 5000.0 and s["invested_cost"] == 7000.0
    assert s["savings_rate"] == 0.32
    assert s["dormant_score"] == 78 and s["invested_score"] == 65
    assert s["bucket_weights"]["high"] == 0.5 and s["crypto_weight"] == 0.05
    assert s["backfilled"] is False and s["captured_at"] == "2026-07-01T00:00:00Z"


def test_build_snapshot_invested_none_ok():
    s = S.build_snapshot("2026-06", NW, SUMM, PF, 78, None, "t")
    assert s["invested_score"] is None


def test_backfill_only_reliable_fields():
    s = S.backfill_snapshot("2025-01", 8000.0, 0.25, "t")
    assert s["net_worth"] == 8000.0 and s["savings_rate"] == 0.25
    assert s["backfilled"] is True
    for k in ("net_worth_gross", "debt", "dormant_cash", "invested_cost",
              "dormant_score", "invested_score", "bucket_weights", "crypto_weight"):
        assert s[k] is None


def test_upsert_replaces_and_adds():
    store = {}
    S.upsert(store, S.build_snapshot("2026-06", NW, SUMM, PF, 1, 1, "t"))
    S.upsert(store, S.build_snapshot("2026-06", NW, SUMM, PF, 99, 99, "t"))  # same month
    S.upsert(store, S.build_snapshot("2026-07", NW, SUMM, PF, 2, 2, "t"))
    assert set(store) == {"2026-06", "2026-07"}
    assert store["2026-06"]["dormant_score"] == 99  # replaced


def test_to_series_sorted():
    store = {"2026-07": {"month": "2026-07"}, "2026-05": {"month": "2026-05"}}
    assert [s["month"] for s in S.to_series(store)] == ["2026-05", "2026-07"]


def test_load_missing_is_empty(tmp_path=None):
    assert S.load("/nonexistent/path/snapshots.json") == {}


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
    print("test_snapshots.py OK")
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd web/api && python3 test_snapshots.py`
Expected: FAIL — `ModuleNotFoundError: snapshots`.

- [ ] **Step 3: Implémenter snapshots.py**

```python
# web/api/snapshots.py
"""Store append-only de snapshots mensuels (métriques clés), persisté en JSON
local. Cœur pur : aucune I/O réseau, aucune horloge interne (captured_at injecté)."""
import json
from pathlib import Path


def build_snapshot(month, nw, summ, pf, dormant_score, invested_score, captured_at):
    return {
        "month": month,
        "net_worth": nw["net_of_debt"],
        "net_worth_gross": nw["total"],
        "debt": nw["debt"],
        "dormant_cash": nw["dormant"],
        "invested_cost": pf["total_cost"],
        "savings_rate": summ["savings_rate"],
        "dormant_score": dormant_score,
        "invested_score": invested_score,
        "bucket_weights": pf["bucket_weights"],
        "crypto_weight": pf["crypto_weight"],
        "captured_at": captured_at,
        "backfilled": False,
    }


def backfill_snapshot(month, net_worth, savings_rate, captured_at):
    return {
        "month": month, "net_worth": net_worth, "net_worth_gross": None,
        "debt": None, "dormant_cash": None, "invested_cost": None,
        "savings_rate": savings_rate, "dormant_score": None, "invested_score": None,
        "bucket_weights": None, "crypto_weight": None,
        "captured_at": captured_at, "backfilled": True,
    }


def upsert(store, snap):
    store[snap["month"]] = snap
    return store


def load(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def dump(path, store):
    Path(path).write_text(json.dumps(store, indent=2, ensure_ascii=False))


def to_series(store):
    return [store[m] for m in sorted(store)]
```

- [ ] **Step 4: Lancer, vérifier le succès**

Run: `cd web/api && python3 test_snapshots.py`
Expected: `test_snapshots.py OK`.

- [ ] **Step 5: Wire dans test-unit**

Dans `Makefile`, cible `test-unit`, après `python3 test_recurrences.py` :
```makefile
	cd web/api && python3 test_snapshots.py
```

- [ ] **Step 6: `make test-unit`** → tout vert.

- [ ] **Step 7: Commit**

```bash
git add web/api/snapshots.py web/api/test_snapshots.py Makefile
git commit --no-gpg-sign -m "feat(api): pure monthly snapshot store (build/upsert/load/dump)"
```

---

### Task 2: Script de capture `scripts/capture-snapshot.py`

**Files:**
- Create: `scripts/capture-snapshot.py`

**Interfaces:**
- Consumes: `snapshots` (Task 1), `firefly_client`, `scores`.
- Produces: écrit/upsert `data/snapshots.json` pour le dernier mois complet ; `--backfill N` remplit les N mois complets précédents absents.

- [ ] **Step 1: Écrire le script**

```python
#!/usr/bin/env python3
"""Capture le snapshot du dernier mois complet dans data/snapshots.json.
Headless : réutilise firefly_client + scores, ne dépend pas du web API tournant.

  python3 scripts/capture-snapshot.py              # dernier mois complet
  python3 scripts/capture-snapshot.py --backfill 12  # + 12 mois passés (champs fiables)
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "web" / "api"))

import yaml
import firefly_client as fc
import scores
import snapshots as S

DATA_DIR = REPO_ROOT / "data"
SNAP_FILE = DATA_DIR / "snapshots.json"
CONFIG = REPO_ROOT / "config" / "strategy.yaml"


def _cfg():
    return yaml.safe_load(CONFIG.read_text())


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _last_complete_month():
    return fc._shift_month(fc._now_month(), 1)


def capture(cfg, month, captured_at):
    nw = fc.networth(cfg)
    summ = fc.summary(month, cfg)
    pf = fc.portfolio(cfg)
    typical = fc.typical_monthly_expense(month, months=6)
    dormant = scores.dormant_health(dormant_cash=nw["dormant"], avg_monthly_expense=typical,
                                     savings_rate=summ["savings_rate"], cfg=cfg)
    invested = scores.invested_health(instrument_cost=pf["instrument_cost"],
                                      bucket_weights_actual=pf["bucket_weights"],
                                      crypto_weight=pf["crypto_weight"], cfg=cfg)
    return S.build_snapshot(month, nw, summ, pf, dormant["score"],
                            invested["score"] if invested else None, captured_at)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, metavar="N")
    args = ap.parse_args()

    cfg = _cfg()
    now = _now_iso()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store = S.load(SNAP_FILE)

    month = _last_complete_month()
    S.upsert(store, capture(cfg, month, now))
    print(f"→ snapshot {month} capturé")

    for k in range(1, args.backfill + 1):
        m = fc._shift_month(month, k)
        if m in store:
            continue  # ne pas écraser un mois déjà capturé en avant
        _, last_day, _ = fc.month_bounds(m)
        nw = fc.networth(cfg, on_date=last_day)["net_of_debt"]
        sr = fc.summary(m, cfg)["savings_rate"]
        S.upsert(store, S.backfill_snapshot(m, nw, sr, now))
        print(f"→ backfill {m}")

    S.dump(SNAP_FILE, store)
    print(f"→ {len(store)} mois dans {SNAP_FILE}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Vérifier la capture contre le stack réel**

```bash
cd <repo> && set -a && source .env && set +a && \
./.venv/bin/python scripts/capture-snapshot.py --backfill 6
cat data/snapshots.json | ./.venv/bin/python -m json.tool | head -40
```
Expected: le dernier mois complet capturé (tous champs) + jusqu'à 6 mois backfillés (net_worth + savings_rate, reste null). Pas d'exception. Coller la sortie dans le rapport.

- [ ] **Step 3: Vérifier l'idempotence**

```bash
./.venv/bin/python scripts/capture-snapshot.py && \
./.venv/bin/python -c "import json; d=json.load(open('data/snapshots.json')); print(len(d),'mois, pas de doublon')"
```
Expected: relancer n'ajoute pas de doublon (upsert), le mois courant est ré-capturé.

- [ ] **Step 4: Commit**

```bash
git add scripts/capture-snapshot.py
git commit --no-gpg-sign -m "feat(scripts): headless monthly snapshot capture + backfill"
```

---

### Task 3: Timer systemd mensuel

**Files:**
- Create: `scripts/systemd/nora-snapshot.service`
- Create: `scripts/systemd/nora-snapshot.timer`
- Modify: `scripts/systemd/README.md`

- [ ] **Step 1: Écrire le service**

```ini
# scripts/systemd/nora-snapshot.service
[Unit]
Description=Capture NORA monthly strategy snapshot

[Service]
Type=oneshot
WorkingDirectory=__PROJECT_DIR__
ExecStart=__PROJECT_DIR__/.venv/bin/python __PROJECT_DIR__/scripts/capture-snapshot.py
```

- [ ] **Step 2: Écrire le timer**

```ini
# scripts/systemd/nora-snapshot.timer
[Unit]
Description=Capture NORA snapshot monthly (1st of month)

[Timer]
OnCalendar=*-*-01 06:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Documenter l'install dans README.md**

Ajouter une section à `scripts/systemd/README.md` :
```markdown
## Monthly snapshot capture

Same install pattern as auto-sync, substituting the project dir:

```sh
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
sed "s#__PROJECT_DIR__#$DIR#" scripts/systemd/nora-snapshot.service \
    > ~/.config/systemd/user/nora-snapshot.service
cp scripts/systemd/nora-snapshot.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nora-snapshot.timer
```

First run, backfill history once: `./.venv/bin/python scripts/capture-snapshot.py --backfill 12`.
```

- [ ] **Step 4: Valider la syntaxe des unités**

Run: `systemd-analyze verify scripts/systemd/nora-snapshot.service 2>&1 | head` (les warnings `__PROJECT_DIR__` sont attendus car non substitué) ; sinon confirmer visuellement que les fichiers sont bien formés.

- [ ] **Step 5: Commit**

```bash
git add scripts/systemd/nora-snapshot.service scripts/systemd/nora-snapshot.timer scripts/systemd/README.md
git commit --no-gpg-sign -m "feat(systemd): monthly snapshot capture timer"
```

---

### Task 4: Endpoint + courbe UI valeur nette

**Files:**
- Modify: `web/api/main.py` (endpoint)
- Modify: `web/ui/src/lib/api.ts` (type + hook)
- Modify: `web/ui/src/components/charts.tsx` (NetWorthChart)
- Modify: `web/ui/src/pages/Dashboard.tsx` (carte)

**Interfaces:**
- Consumes: `snapshots.load/to_series` (Task 1), `data/snapshots.json` (Task 2).
- Produces: `GET /api/snapshots` ; `useSnapshots()` ; `NetWorthChart`.

- [ ] **Step 1: Endpoint dans main.py**

Import en tête (près de `import scores`) : `import snapshots`.
Après l'endpoint `/api/recurrences/detected` :
```python
SNAPSHOTS_FILE = DATA_DIR / "snapshots.json"


@app.get("/api/snapshots")
def api_snapshots():
    return {"snapshots": snapshots.to_series(snapshots.load(SNAPSHOTS_FILE))}
```

- [ ] **Step 2: Type + hook dans api.ts**

Après `useDetectedRecurrences`/`useDismissRecurrence` :
```typescript
export interface Snapshot {
  month: string; net_worth: number; net_worth_gross: number | null;
  debt: number | null; dormant_cash: number | null; invested_cost: number | null;
  savings_rate: number | null; dormant_score: number | null; invested_score: number | null;
  bucket_weights: Record<string, number> | null; crypto_weight: number | null;
  captured_at: string; backfilled: boolean;
}
export const useSnapshots = () =>
  useQuery({ queryKey: ["snapshots"], queryFn: () => get<{ snapshots: Snapshot[] }>("/api/snapshots") });
```

- [ ] **Step 3: NetWorthChart dans charts.tsx**

Ajouter (modèle = `SavingsTrendChart`, `Snapshot` importé du type `@/lib/api`) :
```tsx
export function NetWorthChart({ snapshots }: { snapshots: { month: string; net_worth: number }[] }) {
  const v = useViz();
  const option = {
    textStyle: { color: v.ink.secondary },
    tooltip: { trigger: "axis", valueFormatter: (x: number) => eur(x) },
    grid: { left: 60, right: 12, top: 24, bottom: 24 },
    xAxis: { type: "category", data: snapshots.map((s) => s.month.slice(2)), axisLabel: { color: v.ink.muted }, axisLine: { lineStyle: { color: v.ink.axis } } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: v.ink.grid } }, axisLabel: { color: v.ink.muted } },
    series: [{ type: "line", smooth: true, data: snapshots.map((s) => s.net_worth), itemStyle: { color: v.categorical[0] } }],
    color: v.categorical,
  };
  return <ReactECharts echarts={echarts} option={option} style={{ height: 220 }} notMerge />;
}
```
Vérifier que `eur` est importé dans `charts.tsx` ; sinon `import { eur } from "@/lib/utils";`.

- [ ] **Step 4: Carte dans Dashboard.tsx**

Importer : `NetWorthChart` depuis `@/components/charts`, `useSnapshots` depuis `@/lib/api`.
Câbler `const snaps = useSnapshots();` près de `const runway = useRunway();`.
Ajouter, sous la carte du RunwayChart :
```tsx
<Card>
  <CardContent className="pt-6">
    <CardTitle>Valeur nette dans le temps</CardTitle>
    {snaps.data && snaps.data.snapshots.length >= 2 ? (
      <NetWorthChart snapshots={snaps.data.snapshots} />
    ) : (
      <p className="mt-2 text-sm text-muted-foreground">Pas encore d'historique (premier snapshot au prochain run mensuel).</p>
    )}
  </CardContent>
</Card>
```

- [ ] **Step 5: Vérifier build TS**

Run: `cd web/ui && npx tsc --noEmit`
Expected: aucune erreur.

- [ ] **Step 6: Vérifier l'endpoint contre le stack**

```bash
cd web/api && set -a && source ../../.env && set +a && \
../../.venv/bin/python -c "import main; print(len(main.api_snapshots()['snapshots']), 'snapshots')"
```
Expected: un nombre ≥ 1 (Task 2 a peuplé le fichier). Pas d'exception.

- [ ] **Step 7: Commit**

```bash
git add web/api/main.py web/ui/src/lib/api.ts web/ui/src/components/charts.tsx web/ui/src/pages/Dashboard.tsx
git commit --no-gpg-sign -m "feat(ui): /api/snapshots + net-worth-over-time chart on Aujourd'hui"
```

---

## Notes d'exécution

- Ordre : 1 → 2 → 3 → 4. Task 1 offline/TDD. Tasks 2, 4-step6 nécessitent le stack Firefly up + `.venv`. Task 3 = fichiers de conf. Task 4 UI = tsc (clic visuel = humain).
- `capture-snapshot.py`, `nora-snapshot.*` : pas de secret dedans (creds via `.env`/token côté fc) ; `data/snapshots.json` gitignoré.
- Après tout : `make test` vert.
- Valeur : premier historique réel qui s'accumule en avant + courbe VN immédiate (backfill 12 mois au 1er run).
