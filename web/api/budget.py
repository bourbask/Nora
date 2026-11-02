"""Coussin, enveloppes par poste, réductions ordonnées et verdicts.
Fonctions pures, zéro I/O, zéro LLM. L'ordre de `postes` est l'ordre de réduction."""
import math


def mode(cushion_balance, floor):
    return "reconstitution" if cushion_balance < floor else "serein"


def envelopes(rates, days, postes, mode):
    out = {}
    for p in postes:
        if mode == "reconstitution" and p["compressible"]:
            out[p["name"]] = round(p["floor_eur"], 2)
        else:
            out[p["name"]] = round(rates.get(p["category"], 0) * days, 2)
    return out


def spent_in_cycle(by_category, postes):
    return {p["name"]: round(max(by_category.get(p["category"], 0), 0), 2) for p in postes}


def cuts(shortfall, envelopes_left, postes):
    out, rest = [], round(shortfall, 2)
    for p in postes:
        if rest <= 0:
            break
        take = round(min(max(envelopes_left.get(p["name"], 0), 0), rest), 2)
        if take > 0:
            out.append({"poste": p["name"], "amount": take})
            rest = round(rest - take, 2)
    if rest > 0:
        out.append({"poste": None, "amount": rest})
    return out


def can_afford(avail, amount, poste, mode, envelopes_left, postes):
    after = round(avail["available"] - amount, 2)
    compressible = any(p["name"] == poste and p["compressible"] for p in postes)
    blocked = (mode == "reconstitution" and compressible
               and amount > envelopes_left.get(poste, 0))
    c = cuts(-after, envelopes_left, postes) if after < 0 else []
    if blocked:
        reason = "reconstitution"
    elif after < 0:
        reason = "disponible insuffisant"
    else:
        reason = "ok"
    return {"ok": not blocked and after >= 0, "after": after, "reason": reason, "cuts": c}


def can_save(avail, amount):
    after = round(avail["available"] - amount, 2)
    return {"ok": after >= 0, "after": after}


def ceiling_eta(balance, ceiling, monthly_saving):
    if monthly_saving <= 0:
        return None
    return max(0, math.ceil((ceiling - balance) / monthly_saving))
