"""
Shared Firefly III transport + aggregate helpers.

Extracted from mcp/firefly/server.py so auth/token resolution lives in one place.
Pure httpx (no FastAPI) so it is importable and testable on its own against a
live Firefly instance.

Env:
  FIREFLY_BASE_URL   (default http://localhost:8066 ; http://firefly-iii:8080 in container)
  FIREFLY_TOKEN or FIREFLY_III_ACCESS_TOKEN  (Personal Access Token)
"""

import os
import re
import calendar
from datetime import date, timedelta
from pathlib import Path

import httpx

import cycle
import forecast as F

FIREFLY_BASE_URL = os.environ.get("FIREFLY_BASE_URL", "http://localhost:8066")


def _resolve_token():
    tok = os.environ.get("FIREFLY_TOKEN") or os.environ.get("FIREFLY_III_ACCESS_TOKEN")
    if tok:
        return tok
    # Fallback: scrape repo-root .env (web/api/firefly_client.py -> parents[2] = repo root)
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        m = re.search(r"^FIREFLY_III_ACCESS_TOKEN=(.+)$", env_path.read_text(), re.MULTILINE)
        if m:
            return m.group(1).strip()
    raise RuntimeError("FIREFLY_TOKEN not set and not found in .env")


FIREFLY_TOKEN = _resolve_token()


def _headers():
    return {"Authorization": f"Bearer {FIREFLY_TOKEN}", "Accept": "application/json"}


def api_get(path, params=None):
    url = f"{FIREFLY_BASE_URL}/api/v1{path}"
    with httpx.Client() as c:
        r = c.get(url, headers=_headers(), params=params, timeout=30)
        r.raise_for_status()
        return r.json()


def api_post(path, data, params=None):
    url = f"{FIREFLY_BASE_URL}/api/v1{path}"
    with httpx.Client() as c:
        r = c.post(url, headers=_headers(), json=data, params=params, timeout=30)
        r.raise_for_status()
        return r.json() if r.content else {}


def api_put(path, data):
    url = f"{FIREFLY_BASE_URL}/api/v1{path}"
    with httpx.Client() as c:
        r = c.put(url, headers=_headers(), json=data, timeout=30)
        r.raise_for_status()
        return r.json()


# ── date helpers ──────────────────────────────────────────────────────────────

def month_bounds(month):
    """'YYYY-MM' -> (first_day, last_day, last_day_prev_month) as date objects."""
    y, m = (int(x) for x in month.split("-"))
    last = calendar.monthrange(y, m)[1]
    first_day = date(y, m, 1)
    last_day = date(y, m, last)
    last_day_prev = first_day - timedelta(days=1)
    return first_day, last_day, last_day_prev


# ── aggregate helpers ───────────────────────────────────────────────────────

def asset_accounts(on_date=None):
    """List of {name, balance} for asset accounts, optionally as-of a date."""
    params = {"type": "asset", "limit": 100}
    if on_date is not None:
        params["date"] = on_date.isoformat()
    data = api_get("/accounts", params)
    out = []
    for a in data.get("data", []):
        attr = a["attributes"]
        out.append({"name": attr["name"], "balance": float(attr.get("current_balance") or 0)})
    return out


def _classify(name, cfg):
    if name in cfg["dormant"]["accounts"]:
        return "dormant"
    if name in cfg["invested"]["accounts"]:
        return "invested"
    return "unclassified"


def networth(cfg, on_date=None):
    """Total net worth split dormant/invested, plus per-account detail."""
    accts = asset_accounts(on_date)
    dormant = invested = other = 0.0
    detail = []
    for a in accts:
        cls = _classify(a["name"], cfg)
        if cls == "dormant":
            dormant += a["balance"]
        elif cls == "invested":
            invested += a["balance"]
        else:
            other += a["balance"]
        detail.append({**a, "cls": cls})
    ledger = (cfg.get("dormant") or {}).get("recurring_charges", [])
    debt = round(sum(c["remaining_balance"] for c in ledger
                     if c.get("remaining_balance")), 2)
    loans = [{"name": c["name"], "remaining_balance": c["remaining_balance"]}
             for c in ledger if c.get("remaining_balance")]
    assets = round(dormant + invested + other, 2)
    return {
        "total": assets,                       # assets only (kept for existing callers)
        "net_of_debt": round(assets - debt, 2),
        "debt": debt,
        "loans": loans,
        "dormant": round(dormant, 2),
        "invested": round(invested, 2),
        "unclassified": round(other, 2),
        "accounts": detail,
    }


def _networth_total(cfg, on_date):
    return networth(cfg, on_date)["total"]


def _insight_by_category(kind, first_day, last_day):
    """kind in {'income','expense'} -> {category_name: abs_amount}."""
    data = api_get(f"/insight/{kind}/category", {
        "start": first_day.isoformat(), "end": last_day.isoformat(),
    })
    out = {}
    for row in data:
        name = row.get("name") or "(sans catégorie)"
        out[name] = abs(float(row.get("difference_float") or row.get("difference") or 0))
    return out


def _sum_excluding(cat_map, exclude):
    ex = {e.lower() for e in exclude}
    return round(sum(v for k, v in cat_map.items() if k.lower() not in ex), 2)


def _insight_total(kind, first_day, last_day):
    """Type-based external flow total (deposits for income, withdrawals for
    expense). Internal transfers are excluded by construction — this is the
    correct income/expense source (category-based sums leak the salary into the
    'Virements' bucket). Verified: income - expense == ΔNW."""
    data = api_get(f"/insight/{kind}/total", {
        "start": first_day.isoformat(), "end": last_day.isoformat(),
    })
    return round(abs(sum(float(r.get("difference_float") or 0) for r in data)), 2)


def summary(month, cfg):
    """Monthly external income/expense (type-based), savings capacity (ΔNW),
    savings rate.

    savings_capacity (ΔNW) is the robust headline metric. savings_rate uses gross
    external income and can be distorted by lumpy one-off inflows (e.g. an
    investment round-trip); treat it as indicative. TODO(phase1): approximate
    recurring salary via a trailing median for a stabler rate."""
    first_day, last_day, last_day_prev = month_bounds(month)

    real_income = _insight_total("income", first_day, last_day)
    real_expense = _insight_total("expense", first_day, last_day)

    nw_end = _networth_total(cfg, last_day)
    nw_start = _networth_total(cfg, last_day_prev)
    capacity = round(nw_end - nw_start, 2)

    rate = round(capacity / real_income, 4) if real_income > 0 else None
    return {
        "month": month,
        "real_income": real_income,
        "real_expense": real_expense,
        "savings_capacity": capacity,
        "savings_rate": rate,
        "networth_start": nw_start,
        "networth_end": nw_end,
    }


def _shift_month(month, back):
    """'YYYY-MM' shifted `back` months earlier."""
    y, m = (int(x) for x in month.split("-"))
    idx = y * 12 + (m - 1) - back
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def typical_monthly_expense(ref_month, months=6):
    """Median type-based expense over the last `months` months ending at ref_month.
    Median (not mean) so lumpy one-off outflows — big purchases, investment
    round-trips booked as withdrawals — don't inflate the 'typical' figure that
    the safety-cushion coverage is measured against."""
    totals = []
    for i in range(months):
        m = _shift_month(ref_month, i)
        fd, ld, _ = month_bounds(m)
        totals.append(_insight_total("expense", fd, ld))
    totals.sort()
    n = len(totals)
    if n == 0:
        return 0.0
    mid = n // 2
    med = totals[mid] if n % 2 else (totals[mid - 1] + totals[mid]) / 2
    return round(med, 2)


def monthly_income_series(ref_month, months=6):
    """Type-based external income for each of the last `months` months."""
    out = []
    for i in range(months):
        m = _shift_month(ref_month, i)
        fd, ld, _ = month_bounds(m)
        out.append(_insight_total("income", fd, ld))
    return out


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


def untagged_withdrawals(month):
    """Retraits du mois SANS catégorie (paginés). category vide → non taggé."""
    first, last, _ = month_bounds(month)
    out, page = [], 1
    while True:
        data = api_get("/transactions", {
            "type": "withdrawal", "limit": 200, "page": page,
            "start": first.isoformat(), "end": last.isoformat(),
        })
        for g in data.get("data", []):
            for t in g["attributes"]["transactions"]:
                if (t.get("category_name") or ""):
                    continue  # déjà taggé
                amt = abs(float(t["amount"]))
                if amt == 0:
                    continue
                out.append({"date": t["date"][:10], "amount": amt,
                            "description": t.get("description") or "", "category": ""})
        pag = data.get("meta", {}).get("pagination", {})
        if page >= pag.get("total_pages", page):
            break
        page += 1
    return out


def _pages(path, params=None):
    """Éléments de toutes les pages d'une liste paginée du registre."""
    page = 1
    while True:
        data = api_get(path, {**(params or {}), "limit": 200, "page": page})
        yield from data.get("data", [])
        pag = data.get("meta", {}).get("pagination", {})
        if page >= pag.get("total_pages", page):
            return
        page += 1


def incomes_since(days):
    """Entrées d'argent des `days` derniers jours. payee = source_name."""
    start = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for g in _pages("/transactions", {"type": "deposit", "start": start}):
        for t in g["attributes"]["transactions"]:
            amt = abs(float(t["amount"]))
            if amt:
                out.append({"date": t["date"][:10], "amount": amt,
                            "payee": t.get("source_name") or "?"})
    return out


def withdrawals_between(start, end):
    """Sorties de [start, end] avec catégorie ('' si non classée) et id du groupe."""
    out = []
    for g in _pages("/transactions", {"type": "withdrawal", "start": start.isoformat(),
                                      "end": end.isoformat()}):
        for t in g["attributes"]["transactions"]:
            amt = abs(float(t["amount"]))
            if amt:
                out.append({"id": g["id"], "date": t["date"][:10], "amount": amt,
                            "payee": t.get("destination_name") or "?",
                            "category": t.get("category_name") or "",
                            "description": t.get("description") or ""})
    return out


def cycles_history(n, pay_rule, exclude=(), charges=()):
    """Les `n` derniers cycles complets [paie, paie suivante) : dépense par
    catégorie (catégories de virement et charges fixes exclues), longueur en jours."""
    pays = cycle.pay_dates(incomes_since(31 * (n + 1) + 31), pay_rule)
    bounds = list(zip(pays, pays[1:]))[-n:] if n > 0 else []
    if not bounds:
        return []
    ex = {e.lower() for e in exclude}
    ws = withdrawals_between(bounds[0][0], bounds[-1][1] - timedelta(days=1))
    out = []
    for s, e in bounds:
        by_cat = {}
        for w in ws:
            d = date.fromisoformat(w["date"])
            if not s <= d < e or w["category"].lower() in ex or cycle.is_charge(w, charges):
                continue
            cat = w["category"] or "(sans catégorie)"
            by_cat[cat] = round(by_cat.get(cat, 0) + w["amount"], 2)
        out.append({"start": s, "days": (e - s).days, "by_category": by_cat})
    return out


def account_balance(name, on_date=None):
    """Solde d'un compte d'actif, None si le compte est inconnu."""
    return next((a["balance"] for a in asset_accounts(on_date) if a["name"] == name), None)


RULE_GROUP = "Classement assistant"
# Le payee exposé est le compte de destination : la règle teste ce même champ.
RULE_TRIGGER = "destination_account_contains"


def _rule_group_id():
    for g in _pages("/rule-groups"):
        if g["attributes"].get("title") == RULE_GROUP:
            return g["id"]
    return api_post("/rule-groups", {"title": RULE_GROUP})["data"]["id"]


def find_rule(payee):
    """Règle existante pour ce payee : {id, category}, sinon None."""
    for r in _pages("/rules"):
        a = r["attributes"]
        if any(t.get("type") == RULE_TRIGGER and (t.get("value") or "").lower() == payee.lower()
               for t in a.get("triggers", [])):
            cat = next((x.get("value") for x in a.get("actions", [])
                        if x.get("type") == "set_category"), None)
            return {"id": r["id"], "category": cat}
    return None


def _rule_parts(payee, category):
    # strict : seules les opérations encore sans catégorie sont touchées
    return {
        "strict": True,
        "triggers": [{"type": RULE_TRIGGER, "value": payee, "stop_processing": False,
                      "active": True},
                     {"type": "has_no_category", "value": "true", "stop_processing": False,
                      "active": True}],
        "actions": [{"type": "set_category", "value": category,
                     "stop_processing": False, "active": True}],
    }


def create_rule(payee, category):
    return api_post("/rules", {
        "title": f"Classement : {payee}"[:255],
        "rule_group_id": _rule_group_id(),
        "trigger": "store-journal", "active": True, "stop_processing": False,
        **_rule_parts(payee, category),
    })["data"]["id"]


def update_rule(rule_id, payee, category):
    api_put(f"/rules/{rule_id}", _rule_parts(payee, category))


def trigger_rule(rule_id, days=400):
    """Applique la règle aux opérations des `days` derniers jours."""
    today = date.today()
    api_post(f"/rules/{rule_id}/trigger", None, params={
        "start": (today - timedelta(days=days)).isoformat(), "end": today.isoformat()})


def _month_summary_row(month, cfg):
    s = summary(month, cfg)
    return {"month": month, "income": s["real_income"], "expense": s["real_expense"],
            "capacity": s["savings_capacity"], "dnw": s["savings_capacity"]}


def expense_by_category(month, cfg, top=None):
    """Top cost centers for the month, transfer categories excluded, desc."""
    first_day, last_day, _ = month_bounds(month)
    exclude = {e.lower() for e in cfg.get("transfer_categories", [])}
    cats = _insight_by_category("expense", first_day, last_day)
    rows = [{"category": k, "amount": round(v, 2)}
            for k, v in cats.items() if k.lower() not in exclude and v > 0]
    rows.sort(key=lambda r: r["amount"], reverse=True)
    return rows[:top] if top else rows


# ── portfolio (cost basis, derived from transfer transactions) ───────────────

def _all_transfers():
    """All transfer transactions (paginated). Each split has description,
    amount, source_name, destination_name."""
    out = []
    page = 1
    while True:
        data = api_get("/transactions", {"type": "transfer", "limit": 200, "page": page})
        groups = data.get("data", [])
        if not groups:
            break
        for g in groups:
            for t in g["attributes"]["transactions"]:
                out.append(t)
        pag = data.get("meta", {}).get("pagination", {})
        if page >= pag.get("total_pages", page):
            break
        page += 1
    return out


def _match_rule(instrument, rules):
    low = instrument.lower()
    for r in rules:
        for kw in r.get("match", []):
            if kw == "*" or kw.lower() in low:
                return r
    return {"bucket": "mid", "class": "stock"}


def portfolio(cfg):
    """Per-instrument cost basis from 'Achat:'/'Vente:' transfers into invested
    accounts. Buy adds, sell subtracts. Aggregated by risk bucket and asset class.
    Reconciles against invested account balances (booked at cost)."""
    inv_accounts = set(cfg["invested"]["accounts"])
    rules = cfg["invested"].get("instrument_rules", [])

    instrument_cost = {}
    for t in _all_transfers():
        src = t.get("source_name") or ""
        dest = t.get("destination_name") or ""
        amount = abs(float(t.get("amount") or 0))
        # Buy = cash -> invested account (+cost). Sell = invested -> cash (-cost).
        # The account side is authoritative (sells are booked with dest=cash, so
        # filtering on destination alone would miss them and inflate the total).
        if dest in inv_accounts and src not in inv_accounts:
            sign = 1
        elif src in inv_accounts and dest not in inv_accounts:
            sign = -1
        else:
            continue  # internal reshuffle or unrelated transfer
        desc = t.get("description") or ""
        name = desc.split(":", 1)[-1].strip() if ":" in desc else desc.strip()
        if not name:
            continue
        instrument_cost[name] = instrument_cost.get(name, 0.0) + sign * amount

    # drop fully-exited (≈0 or negative) instruments from allocation
    instrument_cost = {k: round(v, 2) for k, v in instrument_cost.items() if v > 0.01}
    total = round(sum(instrument_cost.values()), 2)

    by_bucket = {"high": 0.0, "mid": 0.0, "low": 0.0}
    by_class = {}
    instruments = []
    for name, cost in sorted(instrument_cost.items(), key=lambda x: -x[1]):
        rule = _match_rule(name, rules)
        by_bucket[rule["bucket"]] = by_bucket.get(rule["bucket"], 0.0) + cost
        by_class[rule["class"]] = by_class.get(rule["class"], 0.0) + cost
        instruments.append({"name": name, "cost": cost,
                            "bucket": rule["bucket"], "class": rule["class"],
                            "weight": round(cost / total, 4) if total else 0})

    crypto_cost = by_class.get("crypto", 0.0)
    return {
        "total_cost": total,
        "instrument_cost": instrument_cost,
        "instruments": instruments,
        "by_bucket": {k: round(v, 2) for k, v in by_bucket.items()},
        "by_class": {k: round(v, 2) for k, v in by_class.items()},
        "bucket_weights": {k: round(v / total, 4) if total else 0 for k, v in by_bucket.items()},
        "crypto_weight": round(crypto_cost / total, 4) if total else 0,
    }


def flow(month, cfg):
    """Conservative monthly Sankey: a single 'Flux du mois' source splits into
    expense categories (transfers excluded) plus net savings. Conservative by
    construction (source == sum of sinks), so it always balances. This is a
    where-did-it-go view, not a gross income statement."""
    cats = expense_by_category(month, cfg)
    s = summary(month, cfg)
    savings = max(s["savings_capacity"], 0)

    ROOT = "Flux du mois"
    links = [{"source": ROOT, "target": c["category"], "value": c["amount"]}
             for c in cats if c["amount"] > 0]
    if savings > 0:
        links.append({"source": ROOT, "target": "Épargne", "value": round(savings, 2)})
    names = [ROOT] + [l["target"] for l in links]
    return {"month": month, "nodes": [{"name": n} for n in names], "links": links,
            "savings_capacity": s["savings_capacity"]}


# ── forecast fetch-wrappers (assemble inputs, delegate math to forecast.py) ───

def _now_month():
    t = date.today()
    return f"{t.year}-{t.month:02d}"


def _forecast_inputs(cfg, ref_month):
    d = cfg.get("dormant", {})
    charges = d.get("recurring_charges", [])
    one_offs = d.get("one_offs", [])
    salary = F.expected_salary(monthly_income_series(ref_month, 6), d.get("salary_override"))
    typical = typical_monthly_expense(ref_month, 6)
    variable = F.variable_typical(typical, charges, ref_month)
    dormant_cash = networth(cfg)["dormant"]
    return d, charges, one_offs, salary, variable, dormant_cash


def runway_view(cfg, horizon=12):
    ref = _now_month()
    _, charges, one_offs, salary, variable, cash = _forecast_inputs(cfg, ref)
    r = F.build_runway(salary, charges, variable, one_offs, cash, ref, horizon)
    return {"ref_month": ref, "salary": salary, "variable_typical": variable, **r}


def guardrail_view(cfg):
    ref = _now_month()
    d, charges, one_offs, salary, variable, cash = _forecast_inputs(cfg, ref)
    r = F.build_runway(salary, charges, variable, one_offs, cash, ref, 12)
    monthly_cost = F.active_obligations(charges, ref) + variable
    return F.guardrail(cash, monthly_cost, d.get("safety_cushion_months", 6), r)


def remaining_view(cfg):
    ref = _now_month()
    d, charges, one_offs, salary, variable, cash = _forecast_inputs(cfg, ref)
    g = guardrail_view(cfg)
    return {"ref_month": ref,
            **F.remaining(salary, charges, variable, ref,
                          d.get("checking_buffer_eur", 0),
                          d.get("target_savings_rate", 0.20), g["invest_ok"])}


def housing_view(cfg):
    ref = _now_month()
    d, charges, _, salary, _, _ = _forecast_inputs(cfg, ref)
    return F.housing_ratio(charges, salary, ref)


def savings_trend_view(cfg, months=6):
    ref = _now_month()
    series = [_month_summary_row(_shift_month(ref, i), cfg) for i in range(months)][::-1]
    d = cfg.get("dormant", {})
    return F.savings_trend(series, d.get("target_savings_rate", 0.20), d.get("savings_band_pts", 5))


def reconcile_view(cfg, months=6):
    ref = _now_month()
    rows = [_month_summary_row(_shift_month(ref, i), cfg) for i in range(months)][::-1]
    d = cfg.get("dormant", {})
    res = F.reconcile(rows, d.get("reconcile_tolerance_eur", 100))
    unclassified = [a["name"] for a in networth(cfg)["accounts"] if a["cls"] == "unclassified"]
    return {**res, "unclassified_accounts": unclassified}
