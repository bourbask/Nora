import alerts as A

# Fixtures = vues déjà calculées (offline, aucune API live).
DRIFT = [
    {"rule_id": "crypto_over_cap", "computed_value": 0.20, "threshold": 0.10, "message": "crypto over"},
    {"rule_id": "bucket_drift", "computed_value": 0.15, "threshold": 0.10, "message": "bucket drift"},
]
GUARDRAIL_BREACH = {"coverage_months": 3.0}          # < safety_cushion_months (6)
GUARDRAIL_OK = {"coverage_months": 8.0}
SAVINGS_BELOW = {"verdict": "below", "points": [{"rate": 0.05}], "target": 0.20, "band": 0.05}
SAVINGS_ABOVE = {"verdict": "above", "points": [{"rate": 0.40}], "target": 0.20, "band": 0.05}
SAVINGS_WITHIN = {"verdict": "within", "points": [{"rate": 0.20}], "target": 0.20, "band": 0.05}
SCORES_LOW = {"dormant": {"score": 40.0}, "invested": {"score": 80.0}}
CFG = {"dormant": {"safety_cushion_months": 6}, "alerts": {"score_floor": 50}}


def _by_rule(resp):
    return {a["rule_id"]: a for a in resp["alerts"]}


def test_full_set_and_severities():
    r = A.build_alerts(CFG, DRIFT, GUARDRAIL_BREACH, SAVINGS_BELOW, SCORES_LOW)
    m = _by_rule(r)
    assert set(m) == {"crypto_over_cap", "bucket_drift", "cushion_below",
                      "savings_out_of_band", "score_below"}, m
    assert m["crypto_over_cap"]["type"] == "drift" and m["crypto_over_cap"]["severity"] == "warn"
    assert m["cushion_below"]["type"] == "cushion" and m["cushion_below"]["severity"] == "critical"
    assert m["savings_out_of_band"]["severity"] == "warn"          # sous la bande
    assert m["score_below"]["type"] == "score" and m["score_below"]["computed_value"] == 40.0
    assert "generated_at" in r


def test_savings_above_is_info():
    r = A.build_alerts(CFG, [], GUARDRAIL_OK, SAVINGS_ABOVE, {})
    assert _by_rule(r)["savings_out_of_band"]["severity"] == "info"


def test_no_alerts_when_all_healthy():
    healthy = {"dormant": {"score": 90.0}, "invested": {"score": 90.0}}
    r = A.build_alerts(CFG, [], GUARDRAIL_OK, SAVINGS_WITHIN, healthy)
    assert r["alerts"] == []


def test_enable_map_filters_types():
    cfg = {"dormant": {"safety_cushion_months": 6},
           "alerts": {"drift": True, "cushion": False, "savings": False, "scores": False}}
    r = A.build_alerts(cfg, DRIFT, GUARDRAIL_BREACH, SAVINGS_BELOW, SCORES_LOW)
    assert {a["rule_id"] for a in r["alerts"]} == {"crypto_over_cap", "bucket_drift"}


def test_absent_block_defaults_all_on():
    r = A.build_alerts({"dormant": {"safety_cushion_months": 6}},
                       DRIFT, GUARDRAIL_BREACH, SAVINGS_BELOW, SCORES_LOW)
    # score_floor par défaut = 50 -> dormant 40 déclenche
    assert "score_below" in _by_rule(r)
    assert len(r["alerts"]) == 5


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
    print("test_alerts.py OK")
