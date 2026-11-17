"""Jauge data-integrity : part de la dépense non catégorisée + plus grosses tx
non taggées ; nom et catégories probables d'un classement. Cœur pur, aucune I/O."""

import unicodedata

UNCATEGORIZED = "(sans catégorie)"


def coverage(expense_by_cat):
    total = round(sum(expense_by_cat.values()), 2)
    uncategorized = round(expense_by_cat.get(UNCATEGORIZED, 0.0), 2)
    ratio = round(uncategorized / total, 4) if total > 0 else 0.0
    return {"uncategorized": uncategorized, "total": total, "ratio": ratio}


def top_untagged(txs, n=5):
    untagged = [t for t in txs if not (t.get("category") or "")]
    untagged.sort(key=lambda t: t["amount"], reverse=True)
    return [{"date": t["date"], "amount": t["amount"], "description": t["description"]}
            for t in untagged[:n]]


NAME_MAX = 60


def clean_name(name):
    """Nom de catégorie saisi : contrôles et caractères invisibles retirés, espaces
    réduites ; None s'il est vide ou dépasse NAME_MAX caractères."""
    s = "".join(" " if unicodedata.category(c) == "Cc"
                else "" if unicodedata.category(c).startswith("C") else c
                for c in name or "")
    s = " ".join(s.split())
    return s if 1 <= len(s) <= NAME_MAX else None


def _norm(s):
    return " ".join((s or "").lower().split())


def _ranked(ops):
    """Catégories par nombre d'opérations décroissant, puis la plus récente d'abord."""
    stats = {}
    for o in ops:
        n, last = stats.get(o["category"], (0, ""))
        stats[o["category"]] = (n + 1, max(last, o["date"]))
    return sorted(stats, key=lambda c: stats[c], reverse=True)


def suggest(pattern, history, categories, n=4):
    """Catégories les plus probables pour un commerçant, et combien viennent de lui.

    D'abord celles de ses opérations déjà classées (motif contenu dans le libellé ou
    le tiers, comme le déclencheur de règle), puis les plus utilisées de tout
    l'historique, enfin les autres `categories` dans leur ordre. Seules les
    `categories` sont proposées."""
    allowed = set(categories)
    ops = [o for o in history if o["category"] in allowed]
    p = _norm(pattern)
    own = _ranked([o for o in ops if p and (p in _norm(o["description"])
                                             or p in _norm(o["payee"]))])[:n]
    out = list(own)
    for c in _ranked(ops) + list(categories):
        if len(out) >= n:
            break
        if c not in out:
            out.append(c)
    return out, len(own)
