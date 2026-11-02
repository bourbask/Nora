"""Agrégateur d'alertes — pur, zéro I/O. Assemble le schéma unifié /api/alerts à
partir des vues déjà calculées (drift, guardrail, savings_trend, scores) ; ne
recalcule rien. main.py fournit les vues (I/O Firefly), ici on ne fait que
mapper + filtrer via l'enable-map de strategy.yaml.

Run the self-check:  python alerts.py"""

from datetime import datetime, timezone


def _enabled(cfg, key):
    # DEFAULT tout activé si le bloc alerts est absent.
    return (cfg.get("alerts") or {}).get(key, True)


def build_alerts(cfg, drift_result, guardrail, savings_trend, scores):
    """cfg: strategy dict. drift_result: liste de drift.drift_alerts. guardrail:
    fc.guardrail_view. savings_trend: fc.savings_trend_view. scores: la vue
    /api/scores ({dormant, invested, ...}). Retourne le dict {generated_at, alerts}."""
    en = cfg.get("alerts") or {}
    out = []

    if _enabled(cfg, "drift"):
        for a in drift_result:
            out.append({"type": "drift", "rule_id": a["rule_id"], "severity": "warn",
                        "computed_value": a["computed_value"], "threshold": a["threshold"],
                        "message": a["message"]})

    if _enabled(cfg, "cushion"):
        cov = guardrail.get("coverage_months")
        target = cfg.get("dormant", {}).get("safety_cushion_months", 6)
        if cov is not None and cov < target:
            out.append({"type": "cushion", "rule_id": "cushion_below", "severity": "critical",
                        "computed_value": cov, "threshold": target,
                        "message": f"Matelas {cov} mois < cible {target} mois"})

    if _enabled(cfg, "savings") and savings_trend.get("verdict") != "within":
        pts = savings_trend.get("points") or []
        last = pts[-1]["rate"] if pts else 0.0
        tgt = savings_trend.get("target", 0.0)
        band = savings_trend.get("band", 0.0)
        below = savings_trend["verdict"] == "below"
        thr = round(tgt - band, 4) if below else round(tgt + band, 4)
        out.append({"type": "savings", "rule_id": "savings_out_of_band",
                    "severity": "warn" if below else "info",
                    "computed_value": last, "threshold": thr,
                    "message": f"Taux d'épargne {last:.0%} "
                               f"{'<' if below else '>'} bande {thr:.0%} (cible {tgt:.0%})"})

    if _enabled(cfg, "scores"):
        floor = en.get("score_floor", 50)
        for key in ("dormant", "invested"):
            sc = (scores.get(key) or {}).get("score")
            if sc is not None and sc < floor:
                out.append({"type": "score", "rule_id": "score_below", "severity": "warn",
                            "computed_value": sc, "threshold": floor,
                            "message": f"Score {key} {sc} < plancher {floor}"})

    return {"generated_at": datetime.now(timezone.utc).isoformat(), "alerts": out}


def demo():
    cfg = {"dormant": {"safety_cushion_months": 6}}
    drift_r = [{"rule_id": "crypto_over_cap", "computed_value": 0.2, "threshold": 0.1,
                "message": "crypto over"}]
    guard = {"coverage_months": 3.0}
    savings = {"verdict": "below", "points": [{"rate": 0.05}], "target": 0.20, "band": 0.05}
    scores = {"dormant": {"score": 40.0}, "invested": {"score": 80.0}}

    r = build_alerts(cfg, drift_r, guard, savings, scores)
    kinds = {a["rule_id"]: a for a in r["alerts"]}
    assert set(kinds) == {"crypto_over_cap", "cushion_below", "savings_out_of_band", "score_below"}, kinds
    assert kinds["cushion_below"]["severity"] == "critical", kinds
    assert kinds["savings_out_of_band"]["severity"] == "warn", kinds
    assert kinds["crypto_over_cap"]["type"] == "drift", kinds

    # enable-map filtre : tout coupé sauf drift
    cfg2 = {"alerts": {"drift": True, "cushion": False, "savings": False, "scores": False}}
    r2 = build_alerts(cfg2, drift_r, guard, savings, scores)
    assert {a["rule_id"] for a in r2["alerts"]} == {"crypto_over_cap"}, r2
    print("alerts.py self-check OK")


if __name__ == "__main__":
    demo()
