"""
NORA backend proxy — thin FastAPI over Firefly III.

The Firefly Personal Access Token stays here (server-side); the browser only ever
receives aggregate JSON. All data logic lives in firefly_client.py.

Run (dev):  uvicorn main:app --reload --port 8068   (from web/api/)
"""

import calendar
import math
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

import auth
import alerts as alerts_mod
import amortization
import budget
import cycle
import finance_alerts
import drift
import firefly_client as fc
import import_status
import recurrences
import scores
import categorization
import snapshots

# depth-safe: web/api/main.py -> repo root locally; falls back to parent when the
# module is mounted flat (e.g. web/api mounted at /app in a container). CONFIG/DATA
# dirs are overridable via NORA_CONFIG_DIR / NORA_DATA_DIR regardless.
_p = Path(__file__).resolve()
REPO_ROOT = _p.parents[2] if len(_p.parents) > 2 else _p.parent
CONFIG_DIR = Path(os.environ.get("NORA_CONFIG_DIR", REPO_ROOT / "config"))
STRATEGY_FILE = CONFIG_DIR / "strategy.yaml"
STRATEGY_EXAMPLE = CONFIG_DIR / "strategy.example.yaml"
DATA_DIR = Path(os.environ.get("NORA_DATA_DIR", REPO_ROOT / "data"))
AUTO_SYNC_LOG = DATA_DIR / "auto_sync.log"

app = FastAPI(title="NORA API", lifespan=auth.lifespan)

# Jeton d'accès ajouté AVANT CORS : CORS reste le plus externe (les 401 portent ses en-têtes).
app.middleware("http")(auth.require_token)

# Dev only: Vite dev server hits us cross-origin. In prod, nginx serves same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost:\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_cfg():
    path = STRATEGY_FILE if STRATEGY_FILE.exists() else STRATEGY_EXAMPLE
    if not path.exists():
        raise HTTPException(500, "no strategy config found")
    return yaml.safe_load(path.read_text())


def current_month():
    """Default reporting month = last COMPLETE month. The in-progress month has
    partial data (near-zero expenses, nonsensical savings rate), so it must not
    be the default view."""
    today = fc.date.today()
    y, m = today.year, today.month - 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y}-{m:02d}"


@app.get("/api/health")
def health():
    return {"ok": True, "firefly": fc.FIREFLY_BASE_URL,
            "reconcile": fc.reconcile_view(load_cfg())}


@app.get("/api/networth")
def api_networth():
    return fc.networth(load_cfg())


@app.get("/api/summary")
def api_summary(month: str | None = None):
    return fc.summary(month or current_month(), load_cfg())


@app.get("/api/categories/expense")
def api_expense(month: str | None = None, top: int | None = None):
    return {"month": month or current_month(),
            "categories": fc.expense_by_category(month or current_month(), load_cfg(), top)}


@app.get("/api/scores")
def api_scores(month: str | None = None):
    cfg = load_cfg()
    month = month or current_month()
    nw = fc.networth(cfg)
    s = fc.summary(month, cfg)
    typical_exp = fc.typical_monthly_expense(month, months=6)
    dormant = scores.dormant_health(
        dormant_cash=nw["dormant"],
        avg_monthly_expense=typical_exp,
        savings_rate=s["savings_rate"],
        cfg=cfg,
    )
    p = fc.portfolio(cfg)
    invested = scores.invested_health(
        instrument_cost=p["instrument_cost"],
        bucket_weights_actual=p["bucket_weights"],
        crypto_weight=p["crypto_weight"],
        cfg=cfg,
    )
    return {"month": month, "typical_monthly_expense": typical_exp,
            "dormant": dormant, "invested": invested}


@app.get("/api/runway")
def api_runway(horizon: int = 12):
    return fc.runway_view(load_cfg(), horizon)


@app.get("/api/guardrail")
def api_guardrail():
    return fc.guardrail_view(load_cfg())


@app.get("/api/remaining")
def api_remaining():
    return fc.remaining_view(load_cfg())


@app.get("/api/housing")
def api_housing():
    return fc.housing_view(load_cfg())


@app.get("/api/savings-trend")
def api_savings_trend():
    return fc.savings_trend_view(load_cfg())


@app.get("/api/import-status")
def api_import_status():
    text = AUTO_SYNC_LOG.read_text() if AUTO_SYNC_LOG.exists() else ""
    return {"sources": import_status.last_per_source(text)}


@app.get("/api/recurrences/detected")
def api_recurrences_detected():
    cfg = load_cfg()
    d = cfg.get("dormant", {})
    existing = {c.get("name") for c in d.get("recurring_charges", [])}
    dismissed = d.get("dismissed_recurrences", [])
    return {"candidates": recurrences.detect_recurrences(
        fc.withdrawals_since(12), existing, fc.date.today(), dismissed)}


@app.get("/api/categorization")
def api_categorization(month: str | None = None):
    month = month or current_month()
    first, last, _ = fc.month_bounds(month)
    # Exclude transfer categories like expense_by_category, so total = real
    # expense (not internal moves) and the ratio matches the rest of the app.
    exclude = {e.lower() for e in load_cfg().get("transfer_categories", [])}
    cats = {k: v for k, v in fc._insight_by_category("expense", first, last).items()
            if k.lower() not in exclude}
    cov = categorization.coverage(cats)
    top = categorization.top_untagged(fc.untagged_withdrawals(month), 5)
    return {"month": month, **cov, "top_untagged": top}


# ── cycle de paie, enveloppes, alertes de dérapage, classement ───────────────

STALE_AFTER = timedelta(hours=48)


def _today():
    return date.today()


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(ts):
    try:
        t = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


OK_EVENT = "import_completed"


def sync_state(sources=None):
    """(dernier import réussi, échec connu). Avec `sources` : réussite la plus ancienne
    parmi elles, et échec si l'une est absente ou en échec à son dernier événement.
    Sans : réussite la plus récente, échec si une source quelconque est en échec."""
    text = AUTO_SYNC_LOG.read_text() if AUTO_SYNC_LOG.exists() else ""
    latest = import_status.last_per_source(text)
    if sources:
        ok = import_status.last_per_source(text, OK_EVENT)
        failed = any(latest.get(s, {}).get("event") != OK_EVENT for s in sources)
        ts = [(_parse_ts(ok[s]["timestamp"]), ok[s]["timestamp"]) for s in sources if s in ok]
        if len(ts) < len(sources) or any(t is None for t, _ in ts):
            return None, True
        return min(ts)[1], failed
    failed = any(v.get("event") != OK_EVENT for v in latest.values())
    ok = [(t, v["timestamp"]) for v in latest.values()
          if v.get("event") == OK_EVENT and (t := _parse_ts(v.get("timestamp")))]
    return (max(ok)[1] if ok else None), failed


def _stale(ts):
    t = _parse_ts(ts)
    return t is None or _now() - t > STALE_AFTER


def _budget_cfg(cfg):
    b = cfg.get("budget")
    if not b:
        raise HTTPException(503, "Bloc « budget » absent de la configuration : "
                                 "cycle, enveloppes et alertes indisponibles.")
    return b


def _balance(name):
    bal = fc.account_balance(name)
    if bal is None:
        raise HTTPException(503, f"Compte « {name} » introuvable dans le registre.")
    return bal


def _current_cycle(b, today):
    """(paie, début, fin) ; une paie dont le cycle est déjà clos (paie en retard,
    synchro ancienne) est traitée comme non reconnue : cycle = mois calendaire."""
    window = cycle.PAY_WINDOW_DAYS + cycle.PAY_MIN_GAP_DAYS
    recent = [d for d in cycle.pay_dates(fc.incomes_since(window), b["pay"])
              if 0 <= (today - d).days <= cycle.PAY_WINDOW_DAYS]
    pay = max(recent, default=None)
    start, end = cycle.cycle_bounds(today, pay)
    if end < today:
        pay = None
        start, end = cycle.cycle_bounds(today, None)
    return pay, start, end


def _cycle_state(cfg):
    b = _budget_cfg(cfg)
    today = _today()
    # coussin et comptes investis jamais comptés dans le disponible
    cushion = b["cushion"]
    excluded = {cushion["account"], *cfg.get("invested", {}).get("accounts", [])}
    accounts = [a for a in b.get("accounts") or [] if a not in excluded]
    if not accounts:
        raise HTTPException(503, "Bloc budget.accounts absent ou vide : "
                                 "aucun compte courant pour calculer le disponible.")
    ex = {e.lower() for e in cfg.get("transfer_categories", [])}
    charges = cfg.get("dormant", {}).get("recurring_charges", [])
    postes = b.get("postes", [])

    pay, start, end = _current_cycle(b, today)
    ws = fc.withdrawals_between(start, today)
    # charges fixes exclues comme dans l'historique : déjà comptées dans les charges
    spending = [w for w in ws if w["category"].lower() not in ex
                and not cycle.is_charge(w, charges)]
    by_cat = {}
    for w in spending:
        if w["category"]:
            by_cat[w["category"]] = by_cat.get(w["category"], 0) + w["amount"]
    total = sum(w["amount"] for w in spending)
    coverage = round(sum(by_cat.values()) / total, 4) if total else 1.0

    history = fc.cycles_history(b.get("reference_cycles", 6), b["pay"], ex, charges)
    # les rythmes viennent de l'historique : sa couverture compte autant que celle du cycle
    hist = [(k, v) for c in history for k, v in c["by_category"].items()]
    hist_total = sum(v for _, v in hist)
    hist_coverage = (round(1 - sum(v for k, v in hist if k == "(sans catégorie)") / hist_total, 4)
                     if hist_total else 1.0)
    rates = cycle.daily_rates(history)
    balance = sum(_balance(a) for a in accounts)
    charges_left = cycle.remaining_charges(charges, start, end, ws)
    days_left = (end - today).days
    avail = cycle.available(balance, charges_left, rates, days_left, b.get("margin_eur", 0))

    cushion_bal = _balance(cushion["account"])
    m = budget.mode(cushion_bal, cushion["floor_eur"])
    env = budget.envelopes(rates, (end - start).days + 1, postes, m)
    spent = budget.spent_in_cycle(by_cat, postes)
    ts, sync_failed = sync_state(b.get("sync_sources"))
    consent = _consent(b)
    consent_expired = consent is not None and consent < today
    return {
        "cfg": b, "postes": postes, "today": today, "start": start, "end": end,
        "days_left": days_left, "mode": m, "rates": rates, "avail": avail,
        "charges_left": charges_left, "envelopes": env, "spent": spent,
        "cushion_balance": cushion_bal, "ws": ws, "charges": charges,
        "quality": cycle.quality(len(history), min(coverage, hist_coverage), pay is not None),
        "basis": {"pay_date": pay.isoformat() if pay else None,
                  "complete_cycles": len(history),
                  "reference_period": [_iso(history[0]["start"]), _iso(
                      history[-1]["start"] + timedelta(days=history[-1]["days"] - 1))]
                  if history else None,
                  "coverage": coverage, "history_coverage": hist_coverage},
        "last_sync": ts, "consent": consent, "consent_expired": consent_expired,
        "stale": _stale(ts) or sync_failed or consent_expired,
    }


def _consent(b):
    c = b.get("consent_expires")
    return date.fromisoformat(c) if isinstance(c, str) else c


def _iso(d):
    return d.isoformat() if d else None


def _meta(st):
    return {"quality": st["quality"], "stale": st["stale"], "last_sync": st["last_sync"],
            "consent_expired": st["consent_expired"], "basis": st["basis"]}


def _amount(amount):
    if not math.isfinite(amount) or amount <= 0:
        raise HTTPException(422, "Le montant doit être un nombre fini strictement positif.")
    return amount


def _poste(postes, poste):
    names = [p["name"] for p in postes]
    if poste not in names:
        raise HTTPException(422, f"Poste inconnu « {poste} ». Postes valides : {', '.join(names)}.")
    return next(p for p in postes if p["name"] == poste)


@app.get("/api/cycle")
def api_cycle():
    st = _cycle_state(load_cfg())
    return {
        "start": st["start"].isoformat(), "end": st["end"].isoformat(),
        "today": st["today"].isoformat(), "days_left": st["days_left"], "mode": st["mode"],
        **_meta(st), "available": st["avail"],
        "charges_left": [{**c, "due": _iso(c["due"])} for c in st["charges_left"]],
        "envelopes": st["envelopes"], "spent": st["spent"],
        "cushion": {"balance": st["cushion_balance"], "floor": st["cfg"]["cushion"]["floor_eur"]},
    }


@app.get("/api/cycle/afford")
def api_cycle_afford(amount: float, poste: str | None = None):
    _amount(amount)
    cfg = load_cfg()
    if poste is not None:
        _poste(_budget_cfg(cfg).get("postes", []), poste)
    st = _cycle_state(cfg)
    left = {k: round(v - st["spent"].get(k, 0), 2) for k, v in st["envelopes"].items()}
    # la dépense d'un poste puise d'abord dans ce que le disponible réserve déjà pour lui
    cat = next((p["category"] for p in st["postes"] if p["name"] == poste), None)
    used = round(min(amount, st["avail"]["variable_expected"].get(cat, 0)), 2)
    avail = {**st["avail"], "available": round(st["avail"]["available"] + used, 2)}
    return {**budget.can_afford(avail, amount, poste, st["mode"], left, st["postes"]),
            "available": st["avail"]["available"], "reserve_used": used, **_meta(st)}


@app.get("/api/cycle/save")
def api_cycle_save(amount: float):
    _amount(amount)
    st = _cycle_state(load_cfg())
    return {**budget.can_save(st["avail"], amount), "available": st["avail"]["available"],
            **_meta(st)}


@app.get("/api/envelopes")
def api_envelopes():
    st = _cycle_state(load_cfg())
    nxt = st["end"] + timedelta(days=1)
    days = calendar.monthrange(nxt.year, nxt.month)[1]
    return {"days": days, "mode": st["mode"],
            "envelopes": budget.envelopes(st["rates"], days, st["postes"], st["mode"]),
            **_meta(st)}


@app.get("/api/finance-alerts")
def api_finance_alerts():
    st = _cycle_state(load_cfg())
    b, today = st["cfg"], st["today"]
    cushion = b["cushion"]
    # épargne mensuelle du coussin estimée sur trois mois
    before = fc.account_balance(cushion["account"], today - timedelta(days=91))
    monthly = (st["cushion_balance"] - before) / 3 if before is not None else 0
    eta = budget.ceiling_eta(st["cushion_balance"], cushion["ceiling_eur"], monthly) \
        if cushion.get("ceiling_eur") else None
    unexpected = [w for w in st["ws"]
                  if not w["category"] and not cycle.is_charge(w, st["charges"])]
    return {"alerts": finance_alerts.build(
        st["start"], st["avail"], st["envelopes"], st["spent"], st["postes"], unexpected,
        st["consent"], today, eta, cushion.get("warn_months", 3),
        unexpected_min=b.get("unexpected_min_eur", 100.0)), **_meta(st)}


UNCLASSIFIED_MAX = 20


@app.get("/api/unclassified")
def api_unclassified():
    cfg = load_cfg()
    b = _budget_cfg(cfg)
    today = _today()
    _, start, _ = _current_cycle(b, today)
    charges = cfg.get("dormant", {}).get("recurring_charges", [])
    ws = [w for w in fc.withdrawals_between(start, today) if not w["category"]
          and w["amount"] > 0 and not cycle.is_charge(w, charges)]
    ws.sort(key=lambda w: w["date"], reverse=True)
    # le libellé est le champ que le déclencheur de règle inspecte
    return [{"id": w["id"], "date": w["date"], "payee": finance_alerts._clean(
                 " ".join(w["description"].split()) or w["payee"]),
             "amount": w["amount"]} for w in ws[:UNCLASSIFIED_MAX]]


class RuleBody(BaseModel):
    payee: str
    poste: str


@app.post("/api/rules")
def api_rules(body: RuleBody):
    payee = body.payee.strip()
    if not 3 <= len(payee) <= 100:
        raise HTTPException(422, "Le payee doit compter entre 3 et 100 caractères.")
    p = _poste(_budget_cfg(load_cfg()).get("postes", []), body.poste)
    rule_id = fc.find_rule(payee)  # une seule règle de l'assistant par payee
    created = rule_id is None
    if created:
        rule_id = fc.create_rule(payee, p["category"])
    else:
        # réécrite à chaque fois : catégorie à jour, strict + « sans catégorie » garantis
        fc.update_rule(rule_id, payee, p["category"])
    fc.trigger_rule(rule_id)
    return {"rule_id": rule_id, "created": created, "applied": True}


SNAPSHOTS_FILE = DATA_DIR / "snapshots.json"


@app.get("/api/snapshots")
def api_snapshots():
    return {"snapshots": snapshots.to_series(snapshots.load(SNAPSHOTS_FILE))}


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


@app.get("/api/portfolio")
def api_portfolio():
    return fc.portfolio(load_cfg())


@app.get("/api/flow")
def api_flow(month: str | None = None):
    return fc.flow(month or current_month(), load_cfg())


@app.get("/api/strategy")
def api_strategy_get():
    return load_cfg()


@app.get("/api/drift")
def api_drift():
    cfg = load_cfg()
    pf = fc.portfolio(cfg)
    inv = cfg.get("invested", {})
    return {"alerts": drift.drift_alerts(
        pf["bucket_weights"], inv.get("target_buckets", {}),
        pf["crypto_weight"], inv.get("crypto_cap", 0.10), inv.get("drift_threshold", 0.10))}


@app.get("/api/alerts")
def api_alerts():
    """Agrégateur : réutilise drift/guardrail/savings-trend/scores existants et
    renvoie le schéma unifié, filtré par l'enable-map de strategy.yaml."""
    cfg = load_cfg()
    pf = fc.portfolio(cfg)
    inv = cfg.get("invested", {})
    drift_result = drift.drift_alerts(
        pf["bucket_weights"], inv.get("target_buckets", {}),
        pf["crypto_weight"], inv.get("crypto_cap", 0.10), inv.get("drift_threshold", 0.10))
    return alerts_mod.build_alerts(
        cfg, drift_result, fc.guardrail_view(cfg), fc.savings_trend_view(cfg), api_scores())


@app.get("/api/loans")
def api_loans():
    cfg = load_cfg()
    out = []
    for c in cfg.get("dormant", {}).get("recurring_charges", []):
        if c.get("kind") != "loan" or not c.get("remaining_balance"):
            continue
        bal = c["remaining_balance"]
        pay = c.get("amount") or 0
        rate = c.get("rate")
        if rate is not None and pay > 0:
            s = amortization.schedule(bal, pay, rate)
            out.append({"name": c["name"], "balance": bal, "payment": pay, "rate": rate,
                        "payoff_month": amortization.payoff_month(c.get("start"), s["payoff_months"]),
                        "total_interest": s["total_interest"],
                        "never_amortizes": s["never_amortizes"], "needs_rate": False})
        else:
            out.append({"name": c["name"], "balance": bal, "payment": pay, "rate": rate,
                        "payoff_month": None, "total_interest": None,
                        "never_amortizes": False, "needs_rate": rate is None})
    return {"loans": out}


# ── editable strategy knobs (accounts / instrument_rules / weights preserved) ──

MONTH_RE = r"^\d{4}-\d{2}$"


class RecurringCharge(BaseModel):
    name: str
    amount: float = Field(ge=0)
    freq: Literal["monthly", "quarterly", "yearly"] = "monthly"
    start: str = Field(pattern=MONTH_RE)
    end: Optional[str] = Field(default=None, pattern=MONTH_RE)
    kind: Literal["loan", "tax", "insurance", "rent", "subscription", "other"] = "other"
    remaining_balance: Optional[float] = Field(default=None, ge=0)
    rate: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def order_ok(self):
        if self.end is not None and self.end < self.start:
            raise ValueError("end must be >= start")
        return self


class OneOff(BaseModel):
    name: str
    amount: float
    date: str = Field(pattern=MONTH_RE)
    kind: Literal["loan", "tax", "insurance", "rent", "subscription", "other"] = "other"


class DormantEdit(BaseModel):
    safety_cushion_months: float = Field(gt=0, le=60)
    checking_buffer_eur: float = Field(ge=0)
    target_savings_rate: float = Field(ge=0, le=1)
    recurring_charges: list[RecurringCharge] = []
    one_offs: list[OneOff] = []
    salary_override: Optional[float] = Field(default=None, ge=0)
    reconcile_tolerance_eur: float = Field(default=100, ge=0)
    savings_band_pts: float = Field(default=5, ge=0, le=100)


class Buckets(BaseModel):
    high: float = Field(ge=0, le=1)
    mid: float = Field(ge=0, le=1)
    low: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def sum_to_one(self):
        if abs((self.high + self.mid + self.low) - 1.0) > 0.01:
            raise ValueError("target_buckets must sum to 1.0")
        return self


class InvestedEdit(BaseModel):
    target_buckets: Buckets
    target_holdings: int = Field(gt=0, le=200)
    crypto_cap: float = Field(ge=0, le=1)
    drift_threshold: float = Field(default=0.10, ge=0, le=1)


class StrategyEdit(BaseModel):
    dormant: DormantEdit
    invested: InvestedEdit


@app.put("/api/strategy")
def api_strategy_put(edit: StrategyEdit):
    """Update the user-facing knobs and persist to config/strategy.yaml.
    Accounts, instrument_rules, score weights and transfer_categories are kept."""
    if not STRATEGY_FILE.exists() and STRATEGY_EXAMPLE.exists():
        cfg = yaml.safe_load(STRATEGY_EXAMPLE.read_text())
    else:
        cfg = load_cfg()

    d = cfg.setdefault("dormant", {})
    d["safety_cushion_months"] = edit.dormant.safety_cushion_months
    d["checking_buffer_eur"] = edit.dormant.checking_buffer_eur
    d["target_savings_rate"] = edit.dormant.target_savings_rate
    d["recurring_charges"] = [c.model_dump() for c in edit.dormant.recurring_charges]
    d["one_offs"] = [o.model_dump() for o in edit.dormant.one_offs]
    d["salary_override"] = edit.dormant.salary_override
    d["reconcile_tolerance_eur"] = edit.dormant.reconcile_tolerance_eur
    d["savings_band_pts"] = edit.dormant.savings_band_pts

    inv = cfg.setdefault("invested", {})
    inv["target_buckets"] = edit.invested.target_buckets.model_dump()
    inv["target_holdings"] = edit.invested.target_holdings
    inv["crypto_cap"] = edit.invested.crypto_cap
    inv["drift_threshold"] = edit.invested.drift_threshold

    STRATEGY_FILE.parent.mkdir(parents=True, exist_ok=True)
    STRATEGY_FILE.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return cfg
