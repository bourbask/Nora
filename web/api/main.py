"""
NORA backend proxy — thin FastAPI over Firefly III.

The Firefly Personal Access Token stays here (server-side); the browser only ever
receives aggregate JSON. All data logic lives in firefly_client.py.

Run (dev):  uvicorn main:app --reload --port 8068   (from web/api/)
"""

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

import alerts as alerts_mod
import amortization
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

app = FastAPI(title="NORA API")

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
