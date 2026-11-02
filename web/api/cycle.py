"""Cycle de paie : bornes, charges restantes, rythme de dépense par catégorie,
disponible jusqu'à la prochaine paie. Fonctions pures, zéro I/O, zéro LLM.
ponytail: seules les charges mensuelles sont comptées dans le cycle ; les
cadences trimestrielles/annuelles ne sont pas projetées (à ajouter si besoin)."""
import calendar
from datetime import date, timedelta
from statistics import median

import forecast

PAY_WINDOW_DAYS = 45
CHARGE_TOLERANCE = 0.15
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


def cycle_bounds(today, last_pay):
    if last_pay is None:
        return today.replace(day=1), _eom(today)
    return last_pay, _eom(last_pay + timedelta(days=10))


def remaining_charges(charges, start, end, withdrawals):
    # Activité décidée sur le mois de fin de cycle (mois de la paie attendue).
    month = f"{end.year}-{end.month:02d}"
    out = []
    for c in charges:
        if c.get("freq", "monthly") != "monthly" or not forecast.active_obligations([c], month):
            continue
        needle = (c.get("match") or c["name"]).lower()
        lo, hi = c["amount"] * (1 - CHARGE_TOLERANCE), c["amount"] * (1 + CHARGE_TOLERANCE)
        paid = any(w["amount"] > 0 and start <= _d(w["date"]) <= end
                   and needle in w.get("payee", "").lower() and lo <= w["amount"] <= hi
                   for w in withdrawals)
        if not paid:
            out.append({"name": c["name"], "amount": c["amount"], "day": c.get("day")})
    return out


def daily_rates(cycles):
    cats = {k for c in cycles for k, v in c["by_category"].items() if v > 0}
    return {k: round(median(max(c["by_category"].get(k, 0), 0) / c["days"] for c in cycles), 2)
            for k in sorted(cats)}


def available(balance, charges_left, rates, days_left, margin):
    left = round(sum(c["amount"] for c in charges_left), 2)
    var = {k: round(r * days_left, 2) for k, r in rates.items()}
    return {"available": round(balance - left - sum(var.values()) - margin, 2),
            "balance": round(balance, 2), "charges_left_total": left,
            "variable_expected": var, "margin": round(margin, 2), "days_left": days_left}


def quality(complete_cycles, coverage, pay_found):
    ok = complete_cycles >= MIN_CYCLES and coverage >= MIN_COVERAGE and pay_found
    return "fiable" if ok else "incertaine"
