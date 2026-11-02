"""Cycle de paie : bornes, charges restantes, rythme de dépense par catégorie,
disponible jusqu'à la prochaine paie. Fonctions pures, zéro I/O, zéro LLM.
ponytail: seules les charges mensuelles sont comptées dans le cycle ; les
cadences trimestrielles/annuelles ne sont pas projetées (à ajouter si besoin)."""
import calendar
from datetime import date, timedelta
from statistics import median

import forecast

PAY_WINDOW_DAYS = 45
PAY_MIN_GAP_DAYS = 20
CHARGE_TOLERANCE = 0.15
MATCH_DAYS = 10
MIN_CYCLES = 2
MIN_COVERAGE = 0.80


def _d(x):
    return x if isinstance(x, date) else date.fromisoformat(x)


def _eom(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def find_last_pay(incomes, rule, today):
    needle = rule["payee_contains"].lower()
    found = [_d(i["date"]) for i in incomes
             if i["amount"] >= rule["min_amount"]
             and needle in i.get("payee", "").lower()
             and 0 <= (today - _d(i["date"])).days <= PAY_WINDOW_DAYS]
    return max(found) if found else None


def pay_dates(incomes, rule, min_gap=PAY_MIN_GAP_DAYS):
    """Dates de paie reconnues, triées ; un versement à moins de `min_gap` jours
    du précédent (prime, régularisation) n'ouvre pas de cycle."""
    needle = rule["payee_contains"].lower()
    out = []
    for d in sorted(_d(i["date"]) for i in incomes
                    if i["amount"] >= rule["min_amount"] and needle in i.get("payee", "").lower()):
        if not out or (d - out[-1]).days >= min_gap:
            out.append(d)
    return out


def cycle_bounds(today, last_pay):
    if last_pay is None:
        return today.replace(day=1), _eom(today)
    return last_pay, _eom(last_pay + timedelta(days=10))


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _occurrences(c, start, end):
    """Échéances de la charge dans [start, end] ; sans `day`, une seule échéance
    prudente (due=None) si la charge est active sur au moins un mois du cycle."""
    day = c.get("day")
    out = []
    for y, m in _months(start, end):
        if not forecast._active(c, f"{y}-{m:02d}"):
            continue
        if day is None:
            return [None]
        due = date(y, m, min(day, calendar.monthrange(y, m)[1]))
        if start <= due <= end:
            out.append(due)
    return out


def remaining_charges(charges, start, end, withdrawals):
    occs = []
    for c in charges:
        if c.get("freq", "monthly") != "monthly":
            continue
        occs += [(due, c) for due in _occurrences(c, start, end)]
    occs.sort(key=lambda o: (o[0] is None, o[0] or start, o[1]["name"]))
    pool = [(_d(w["date"]), w["amount"], w.get("payee", "").lower()) for w in withdrawals
            if w["amount"] > 0 and start <= _d(w["date"]) <= end]
    out = []
    for due, c in occs:
        needle = (c.get("match") or c["name"]).lower()
        lo, hi = c["amount"] * (1 - CHARGE_TOLERANCE), c["amount"] * (1 + CHARGE_TOLERANCE)
        best = None
        for i, (wd, amt, payee) in enumerate(pool):
            if needle not in payee or not lo <= amt <= hi:
                continue
            gap = abs((wd - due).days) if due else 0
            if due and gap > MATCH_DAYS:
                continue
            key = (gap, abs(amt - c["amount"]))
            if best is None or key < best[0]:
                best = (key, i)
        if best:
            pool.pop(best[1])  # une sortie ne solde qu'une échéance
        else:
            out.append({"name": c["name"], "amount": c["amount"], "day": c.get("day"), "due": due})
    return out


def is_charge(w, charges):
    """Sortie correspondant à une charge fixe (payee + montant à ±15 %) :
    exclue des rythmes variables, déjà comptée dans les charges."""
    payee = w.get("payee", "").lower()
    return any((c.get("match") or c["name"]).lower() in payee
               and abs(w["amount"] - c["amount"]) <= c["amount"] * CHARGE_TOLERANCE
               for c in charges)


def daily_rates(cycles):
    # Non arrondi : l'arrondi se fait sur les produits dans available().
    cats = {k for c in cycles for k, v in c["by_category"].items() if v > 0}
    return {k: median(max(c["by_category"].get(k, 0), 0) / c["days"] for c in cycles)
            for k in sorted(cats)}


def available(balance, charges_left, rates, days_left, margin):
    days_left = max(days_left, 0)
    left = round(sum(c["amount"] for c in charges_left), 2)
    var = {k: round(r * days_left, 2) for k, r in rates.items()}
    return {"available": round(balance - left - sum(var.values()) - margin, 2),
            "balance": round(balance, 2), "charges_left_total": left,
            "variable_expected": var, "margin": round(margin, 2), "days_left": days_left}


def quality(complete_cycles, coverage, pay_found):
    ok = complete_cycles >= MIN_CYCLES and coverage >= MIN_COVERAGE and pay_found
    return "fiable" if ok else "incertaine"
