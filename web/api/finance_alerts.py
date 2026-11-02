"""Alertes de dérapage du cycle : fonction pure, zéro I/O, zéro LLM.
Chaque alerte porte un id stable par cycle, un titre, des détails et un remède."""
import re
from datetime import date

import budget

NEAR = 0.85
CONSENT_DAYS = 14
# contrôle, largeur nulle, séparateurs de ligne, marques et isolats bidirectionnels, BOM
_CTRL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")


def _e(x):
    return f"{round(x):d} €"


def _c(x):
    return f"{x:.2f} €".replace(".", ",")


def _clean(s):
    # donnée bancaire non fiable : sans caractères de contrôle, longueur bornée
    return _CTRL.sub(" ", str(s)).strip()[:60]


def build(cycle_start, avail, envelopes, spent, postes, unexpected, consent_expires,
          today, ceiling_months, warn_months, unexpected_min=100.0):
    out = []

    def add(kind, key, title, details, remedy):
        out.append({"id": f"{kind}:{cycle_start.isoformat()}:{key}", "kind": kind,
                    "title": title, "details": details, "remedy": remedy})

    if avail["available"] < 0:
        left = {p: envelopes[p] - spent.get(p, 0) for p in envelopes}
        parts, rest = [], 0
        for c in budget.cuts(-avail["available"], left, postes):
            if c["poste"] is None:
                rest = c["amount"]
            else:
                parts.append(f"{c['poste']} {_e(c['amount'])}")
        remedy = "Réduire : " + ", ".join(parts) if parts else "Aucune enveloppe à réduire"
        if rest:
            remedy += f" et il manque encore {_e(rest)}"
        add("negative", "disponible", f"Disponible négatif : {_e(avail['available'])}",
            f"Le disponible du cycle est de {_c(avail['available'])}.", remedy + ".")

    over = set()
    for name, env in envelopes.items():
        s = spent.get(name, 0)
        if s > env:
            over.add(name)
            add("envelope", name, f"Enveloppe {name} dépassée de {_e(s - env)}",
                f"Dépensé {_c(s)} pour une enveloppe de {_c(env)}.",
                f"Ne plus dépenser sur {name} pour ce cycle.")
    for name, env in envelopes.items():
        s = spent.get(name, 0)
        if name not in over and env > 0 and s >= NEAR * env:
            add("near_limit", name, f"Enveloppe {name} presque atteinte",
                f"Dépensé {_c(s)} sur {_c(env)} ({s / env:.0%}).",
                f"Garder {_c(env - s)} pour la fin du cycle.")

    for u in unexpected:
        if u["amount"] < unexpected_min:
            continue
        who = _clean(u["payee"])
        add("unexpected", f"{u['date']}:{who}:{u['amount']:.2f}",
            f"Dépense inattendue de {_e(u['amount'])}",
            f"Sortie de {_c(u['amount'])} le {u['date']} ({who}), non récurrente et non classée.",
            "Classer cette dépense ou vérifier qu'elle est légitime.")

    if consent_expires is not None:
        days = (consent_expires - today).days
        if days <= CONSENT_DAYS:
            iso = consent_expires.isoformat()
            if days < 0:
                title, det = "Consentement bancaire expiré", f"Le consentement a expiré le {iso}."
            else:
                title = f"Consentement bancaire : {days} jour(s) restant(s)"
                det = f"Le consentement expire le {iso}."
            add("consent", iso, title, det, "Renouveler le consentement bancaire.")

    if ceiling_months is not None and ceiling_months <= warn_months:
        add("ceiling", "livret", f"Plafond d'épargne dans {ceiling_months} mois",
            f"Au rythme actuel, le plafond sera atteint dans {ceiling_months} mois.",
            "Prévoir de réorienter l'épargne.")
    return out
