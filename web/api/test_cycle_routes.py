"""Routes du cycle : registre simulé en mémoire, aucun réseau."""
import os
from datetime import date, datetime, timezone

import pytest

pytest.importorskip("fastapi")
os.environ.setdefault("FIREFLY_TOKEN", "test")

from fastapi.testclient import TestClient  # noqa: E402

import budget  # noqa: E402
import cycle  # noqa: E402
import firefly_client as fc  # noqa: E402
import main  # noqa: E402

TODAY = date(2026, 11, 2)

CFG = {
    "dormant": {
        "accounts": ["Compte A", "Compte B"],
        "checking_buffer_eur": 100, "target_savings_rate": 0.2, "safety_cushion_months": 6,
        "recurring_charges": [
            {"name": "Loyer", "match": "LOYER", "amount": 700, "day": 1,
             "freq": "monthly", "start": "2024-01", "end": None, "kind": "rent"},
            {"name": "Assurance", "match": "ASSUR", "amount": 50, "day": 15,
             "freq": "monthly", "start": "2024-01", "end": None, "kind": "insurance"},
        ],
        "one_offs": [],
    },
    "invested": {"accounts": ["Compte C"]},
    "transfer_categories": ["Virements"],
    "budget": {
        "pay": {"payee_contains": "EMPLOYEUR", "min_amount": 1000},
        "accounts": ["Compte A"],
        "margin_eur": 50,
        "cushion": {"account": "Compte B", "floor_eur": 3000, "ceiling_eur": 20000,
                    "warn_months": 3},
        "postes": [
            {"name": "restos", "category": "Restaurants", "compressible": True, "floor_eur": 0},
            {"name": "courses", "category": "Courses", "compressible": False, "floor_eur": 0},
            {"name": "essence", "category": "Carburant", "compressible": False, "floor_eur": 0},
        ],
        "reference_cycles": 6,
        "consent_expires": None,
    },
}

INCOMES = [{"date": f"2026-{m:02d}-28", "amount": 2000.0, "payee": "EMPLOYEUR SA"}
           for m in (7, 8, 9, 10)] + [
    {"date": "2026-10-30", "amount": 50.0, "payee": "REMBOURSEMENT"}]


def _w(id_, d, amount, payee, category=""):
    return {"id": id_, "date": d, "amount": amount, "payee": payee, "category": category,
            "description": payee}


WITHDRAWALS = []
for i, (s, e) in enumerate((("2026-07", "2026-08"), ("2026-08", "2026-09"),
                            ("2026-09", "2026-10"))):
    WITHDRAWALS += [_w(f"h{i}a", f"{s}-29", 300.0, "SUPERMARCHE", "Courses"),
                    _w(f"h{i}b", f"{s}-30", 62.0, "STATION", "Carburant"),
                    _w(f"h{i}c", f"{e}-01", 700.0, "LOYER", "Logement"),
                    _w(f"h{i}d", f"{e}-05", 500.0, "VERS EPARGNE", "Virements")]
WITHDRAWALS += [_w("c1", "2026-10-30", 40.0, "SUPERMARCHE", "Courses"),
                _w("c2", "2026-10-31", 150.0, "MAGASIN"),
                _w("c3", "2026-11-01", 705.0, "LOYER", "Logement"),
                _w("c4", "2026-11-01", 20.0, "STATION", "Carburant"),
                _w("c5", "2026-11-02", 12.5, "BOULANGERIE"),
                _w("c6", "2026-11-02", 700.0, "PHARMACIE", "Santé")]

BALANCES = {"Compte A": 500.0, "Compte B": 5000.0, "Compte C": 9000.0}


class FakeRegistry:
    """Règles et groupes de règles du registre, en mémoire."""

    def __init__(self):
        self.groups, self.rules, self.triggered, self.puts = [], [], [], []

    def get(self, path, params=None):
        # pas de liste globale des règles : seul le groupe dédié est lisible
        if path == "/rule-groups":
            items = self.groups
        else:
            gid = path.split("/")[2]
            assert path == f"/rule-groups/{gid}/rules"
            items = [r for r in self.rules if r["attributes"].get("rule_group_id") == gid]
        return {"data": items, "meta": {"pagination": {"total_pages": 1}}}

    def post(self, path, data, params=None):
        if path == "/rule-groups":
            g = {"id": str(len(self.groups) + 1), "attributes": {"title": data["title"]}}
            self.groups.append(g)
            return {"data": g}
        if path == "/rules":
            r = {"id": str(len(self.rules) + 1), "attributes": data}
            self.rules.append(r)
            return {"data": r}
        self.triggered.append((path, params))
        return {}

    def put(self, path, data):
        rid = path.rsplit("/", 1)[1]
        r = next(r for r in self.rules if r["id"] == rid)
        self.puts.append(rid)
        r["attributes"].update(data)
        return {"data": r}


@pytest.fixture
def client(monkeypatch, tmp_path):
    log = tmp_path / "auto_sync.log"
    log.write_text('{"timestamp":"2026-11-01T06:00:00Z","source":"banque","event":"import_completed","transactions":3}\n')
    reg = FakeRegistry()
    monkeypatch.setattr(main, "load_cfg", lambda: CFG)
    monkeypatch.setattr(main, "AUTO_SYNC_LOG", log)
    monkeypatch.setattr(main, "_today", lambda: TODAY)
    monkeypatch.setattr(main, "_now", lambda: datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(fc, "incomes_since", lambda days: INCOMES)
    monkeypatch.setattr(fc, "withdrawals_between", lambda s, e: [
        w for w in WITHDRAWALS if s <= date.fromisoformat(w["date"]) <= e])
    monkeypatch.setattr(fc, "account_balance", lambda name, on_date=None: BALANCES.get(name))
    monkeypatch.setattr(fc, "api_get", reg.get)
    monkeypatch.setattr(fc, "api_post", reg.post)
    monkeypatch.setattr(fc, "api_put", reg.put)
    monkeypatch.setenv("NORA_API_TOKEN", "t")
    c = TestClient(main.app, headers={"Authorization": "Bearer t"})
    c.registry, c.log = reg, log
    return c


def test_cycle_days_left_and_quality(client):
    r = client.get("/api/cycle")
    assert r.status_code == 200
    j = r.json()
    assert (j["start"], j["end"], j["today"]) == ("2026-10-28", "2026-11-30", "2026-11-02")
    assert j["days_left"] == 28
    assert j["mode"] == "serein"
    assert j["stale"] is False and j["last_sync"] == "2026-11-01T06:00:00Z"
    assert j["quality"] == "fiable"
    assert j["basis"]["complete_cycles"] == 3
    assert j["basis"]["reference_period"] == ["2026-07-28", "2026-10-27"]
    # loyer déjà prélevé (705 ≈ 700) ; assurance restante, échéance sérialisée
    assert j["charges_left"] == [{"name": "Assurance", "amount": 50, "day": 15,
                                  "due": "2026-11-15"}]
    # courses médiane 300/31 j, carburant 62/31 j ; loyer et virements exclus des rythmes
    a = j["available"]
    assert set(a["variable_expected"]) == {"Courses", "Carburant"}
    assert a["variable_expected"]["Carburant"] == 56.0
    assert a["available"] == round(500 - 50 - a["variable_expected"]["Courses"] - 56 - 50, 2)
    assert j["spent"] == {"restos": 0, "courses": 40.0, "essence": 20.0}
    assert j["cushion"] == {"balance": 5000.0, "floor": 3000}


def test_late_pay_falls_back_to_calendar_month(client, monkeypatch):
    # paie d'octobre absente : celle de septembre clôt son cycle le 31 octobre
    monkeypatch.setattr(fc, "incomes_since", lambda days: [
        i for i in INCOMES if i["date"] != "2026-10-28"])
    j = client.get("/api/cycle").json()
    assert (j["start"], j["end"], j["days_left"]) == ("2026-11-01", "2026-11-30", 28)
    assert j["quality"] == "incertaine" and j["basis"]["pay_date"] is None
    assert j["charges_left"][0]["name"] == "Assurance"


def test_bonus_does_not_open_cycle(client, monkeypatch):
    monkeypatch.setattr(fc, "incomes_since", lambda days: INCOMES + [
        {"date": "2026-10-31", "amount": 1500.0, "payee": "EMPLOYEUR SA"}])
    assert client.get("/api/cycle").json()["start"] == "2026-10-28"
    assert [u["id"] for u in client.get("/api/unclassified").json()] == ["c5", "c2"]


@pytest.mark.parametrize("accounts", [None, [], ["Compte B"], ["Compte C"]])
def test_missing_accounts_is_503(client, monkeypatch, accounts):
    b = {k: v for k, v in CFG["budget"].items() if k != "accounts"}
    if accounts is not None:
        b["accounts"] = accounts
    monkeypatch.setattr(main, "load_cfg", lambda: {**CFG, "budget": b})
    for path in ("/api/cycle", "/api/cycle/afford?amount=10", "/api/cycle/save?amount=10",
                 "/api/envelopes", "/api/finance-alerts"):
        r = client.get(path)
        assert r.status_code == 503 and "budget.accounts" in r.json()["detail"]


def test_last_event_failure_is_stale(client):
    client.log.write_text(
        '{"timestamp":"2026-11-01T06:00:00Z","source":"banque","event":"import_completed","transactions":3}\n'
        '{"timestamp":"2026-11-02T06:00:00Z","source":"banque","event":"import_failed","transactions":null}\n')
    j = client.get("/api/cycle").json()
    assert j["stale"] is True and j["last_sync"] is None


def test_last_sync_compares_parsed_timestamps(client):
    # 09:00+05:00 = 04:00Z, antérieur à 06:00Z : la comparaison porte sur l'instant, pas sur le texte
    client.log.write_text(
        '{"timestamp":"2026-11-01T06:00:00Z","source":"a","event":"import_completed","transactions":1}\n'
        '{"timestamp":"2026-11-01T09:00:00+05:00","source":"b","event":"import_completed","transactions":1}\n')
    assert client.get("/api/cycle").json()["last_sync"] == "2026-11-01T06:00:00Z"


def test_cycle_stale_after_48h(client):
    client.log.write_text('{"timestamp":"2026-10-31T07:00:00Z","source":"banque","event":"import_completed","transactions":1}\n')
    assert client.get("/api/cycle").json()["stale"] is True
    client.log.write_text("")
    j = client.get("/api/cycle").json()
    assert j["stale"] is True and j["last_sync"] is None


def _with(monkeypatch, extra, drop=()):
    ws = [w for w in WITHDRAWALS if w["id"] not in drop] + extra
    monkeypatch.setattr(fc, "withdrawals_between", lambda s, e: [
        w for w in ws if s <= date.fromisoformat(w["date"]) <= e])


def test_fixed_charges_excluded_from_cycle_spending(client, monkeypatch):
    base = client.get("/api/cycle").json()
    # loyer non classé, assurance classée dans un poste : ni couverture, ni dépensé, ni à classer
    _with(monkeypatch, [_w("c3", "2026-11-01", 705.0, "LOYER"),
                        _w("c7", "2026-11-02", 50.0, "ASSUR", "Courses")], drop={"c3"})
    j = client.get("/api/cycle").json()
    assert j["basis"]["coverage"] == base["basis"]["coverage"]
    assert j["spent"] == base["spent"]
    assert [u["id"] for u in client.get("/api/unclassified").json()] == ["c5", "c2"]


def test_reconstitution_refuses_purchase_without_poste(client, monkeypatch):
    monkeypatch.setitem(BALANCES, "Compte B", 100.0)
    no = client.get("/api/cycle/afford?amount=5").json()
    assert (no["ok"], no["reason"]) == (False, "reconstitution")
    assert client.get("/api/cycle/afford?amount=5&poste=courses").json()["ok"] is True


def test_sync_sources_min_of_latest_success(client, monkeypatch):
    b = {**CFG["budget"], "sync_sources": ["a", "b"]}
    monkeypatch.setattr(main, "load_cfg", lambda: {**CFG, "budget": b})
    client.log.write_text(
        '{"timestamp":"2026-11-02T06:00:00Z","source":"a","event":"import_completed","transactions":1}\n'
        '{"timestamp":"2026-11-01T06:00:00Z","source":"b","event":"import_completed","transactions":1}\n'
        '{"timestamp":"2026-11-02T07:00:00Z","source":"autre","event":"import_failed","transactions":null}\n')
    j = client.get("/api/cycle").json()
    assert (j["last_sync"], j["stale"]) == ("2026-11-01T06:00:00Z", False)
    # dernier événement de b en échec : dernière réussite conservée, mais périmé
    with client.log.open("a") as f:
        f.write('{"timestamp":"2026-11-02T07:30:00Z","source":"b","event":"import_failed","transactions":null}\n')
    j = client.get("/api/cycle").json()
    assert (j["last_sync"], j["stale"]) == ("2026-11-01T06:00:00Z", True)
    # source configurée absente du journal : périmé
    b["sync_sources"] = ["a", "c"]
    j = client.get("/api/cycle").json()
    assert (j["last_sync"], j["stale"]) == (None, True)


def test_any_latest_failure_is_stale_without_sync_sources(client):
    client.log.write_text(
        '{"timestamp":"2026-11-02T06:00:00Z","source":"a","event":"import_completed","transactions":1}\n'
        '{"timestamp":"2026-11-02T07:00:00Z","source":"b","event":"import_failed","transactions":null}\n')
    j = client.get("/api/cycle").json()
    assert (j["last_sync"], j["stale"]) == ("2026-11-02T06:00:00Z", True)


@pytest.mark.parametrize("consent,expired", [("2026-11-01", True), ("2026-11-02", False), (None, False)])
def test_consent_expired_flagged_everywhere(client, monkeypatch, consent, expired):
    b = {**CFG["budget"], "consent_expires": consent}
    monkeypatch.setattr(main, "load_cfg", lambda: {**CFG, "budget": b})
    for path in ("/api/cycle", "/api/cycle/afford?amount=1", "/api/cycle/save?amount=1",
                 "/api/envelopes", "/api/finance-alerts"):
        j = client.get(path).json()
        assert j["consent_expired"] is expired and j["stale"] is expired


def test_afford_uses_poste_reserve(client):
    # disponible 73.03 dont 56 déjà réservés à l'essence pour les 28 jours restants
    assert client.get("/api/cycle").json()["available"]["available"] == 73.03
    r = client.get("/api/cycle/afford?amount=80&poste=essence")
    assert r.status_code == 200
    j = r.json()
    assert (j["ok"], j["after"], j["reserve_used"], j["available"]) == (True, 49.03, 56.0, 73.03)
    assert j["quality"] == "fiable" and j["stale"] is False and j["basis"]["complete_cycles"] == 3
    no = client.get("/api/cycle/afford?amount=200&poste=essence").json()
    assert no["ok"] is False and no["after"] == -70.97 and no["cuts"]
    # sans poste : aucune réserve mobilisée
    free = client.get("/api/cycle/afford?amount=80").json()
    assert (free["ok"], free["after"], free["reserve_used"]) == (False, -6.97, 0)


def test_afford_matches_can_afford(client):
    j = client.get("/api/cycle").json()
    left = {k: round(j["envelopes"][k] - j["spent"][k], 2) for k in j["envelopes"]}
    avail = {**j["available"], "available": round(j["available"]["available"] + 30, 2)}
    got = client.get("/api/cycle/afford?amount=30&poste=essence").json()
    want = budget.can_afford(avail, 30, "essence", "serein", left, CFG["budget"]["postes"])
    assert {k: got[k] for k in want} == want


@pytest.mark.parametrize("q", ["amount=0", "amount=-5", "amount=nan", "amount=inf",
                               "amount=10&poste=inconnu"])
def test_afford_rejects_bad_input(client, q):
    r = client.get(f"/api/cycle/afford?{q}")
    assert r.status_code == 422
    if "poste" in q:
        assert "essence" in r.json()["detail"]


def test_save(client):
    avail = client.get("/api/cycle").json()["available"]["available"]
    assert client.get(f"/api/cycle/save?amount={avail}").json()["ok"] is True
    j = client.get(f"/api/cycle/save?amount={avail + 1}").json()
    assert j["ok"] is False and "quality" in j and "stale" in j
    assert client.get("/api/cycle/save?amount=0").status_code == 422


def test_envelopes_next_cycle(client):
    j = client.get("/api/envelopes").json()
    assert j["days"] == 31 and j["mode"] == "serein"  # décembre
    assert j["envelopes"]["essence"] == 62.0


def test_missing_budget_is_503(client, monkeypatch):
    monkeypatch.setattr(main, "load_cfg", lambda: {k: v for k, v in CFG.items() if k != "budget"})
    for path in ("/api/cycle", "/api/envelopes", "/api/finance-alerts", "/api/unclassified"):
        r = client.get(path)
        assert r.status_code == 503 and "budget" in r.json()["detail"]
    r = client.post("/api/rules", json={"payee": "MAGASIN", "poste": "courses"})
    assert r.status_code == 503 and client.registry.rules == []


def test_finance_alerts_unexpected(client):
    alerts = client.get("/api/finance-alerts").json()["alerts"]
    kinds = [a["kind"] for a in alerts]
    assert "unexpected" in kinds and "negative" not in kinds
    un = [a for a in alerts if a["kind"] == "unexpected"]
    assert len(un) == 1 and "MAGASIN" in un[0]["details"]


def test_unclassified(client):
    j = client.get("/api/unclassified").json()
    assert j == [{"id": "c5", "date": "2026-11-02", "payee": "BOULANGERIE", "amount": 12.5},
                 {"id": "c2", "date": "2026-10-31", "payee": "MAGASIN", "amount": 150.0}]


def test_rules_idempotent(client):
    r1 = client.post("/api/rules", json={"payee": "  MAGASIN ", "poste": "courses"})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["created"] is True and j1["applied"] is True
    r2 = client.post("/api/rules", json={"payee": "MAGASIN", "poste": "courses"}).json()
    assert r2 == {"rule_id": j1["rule_id"], "created": False, "applied": True}
    reg = client.registry
    assert len(reg.rules) == 1 and len(reg.groups) == 1
    rule = reg.rules[0]["attributes"]
    assert rule["strict"] is True
    assert [(t["type"], t["value"]) for t in rule["triggers"]] == [
        ("description_contains", "MAGASIN"), ("has_no_category", "true")]
    assert rule["actions"][0] == {"type": "set_category", "value": "Courses",
                                  "stop_processing": False, "active": True}
    path, params = reg.triggered[-1]
    assert path == f"/rules/{j1['rule_id']}/trigger"
    assert date.fromisoformat(params["start"]) <= date.fromisoformat(params["end"]).replace(
        year=date.fromisoformat(params["end"]).year - 1)


def test_rule_same_payee_other_category_is_updated(client):
    j1 = client.post("/api/rules", json={"payee": "MAGASIN", "poste": "courses"}).json()
    j2 = client.post("/api/rules", json={"payee": "magasin", "poste": "restos"}).json()
    assert j2 == {"rule_id": j1["rule_id"], "created": False, "applied": True}
    reg = client.registry
    assert len(reg.rules) == 1
    assert reg.rules[0]["attributes"]["actions"][0]["value"] == "Restaurants"
    assert reg.triggered[-1][0] == f"/rules/{j1['rule_id']}/trigger"


def test_rule_outside_assistant_group_is_untouched(client):
    reg = client.registry
    user_rule = {"title": "Règle manuelle", "rule_group_id": "9", "strict": False,
                 "triggers": [{"type": "destination_account_contains", "value": "MAGASIN"}],
                 "actions": [{"type": "set_category", "value": "Loisirs"}]}
    reg.groups.append({"id": "9", "attributes": {"title": "Mes règles"}})
    reg.rules.append({"id": "50", "attributes": dict(user_rule)})
    j = client.post("/api/rules", json={"payee": "MAGASIN", "poste": "courses"}).json()
    assert j["created"] is True and j["rule_id"] != "50"
    assert reg.rules[0]["attributes"] == user_rule and "50" not in reg.puts
    assert ("/rules/50/trigger", ) not in [(t[0],) for t in reg.triggered]


def test_legacy_assistant_rule_is_repaired_before_trigger(client):
    reg = client.registry
    reg.groups.append({"id": "1", "attributes": {"title": "Classement assistant"}})
    reg.rules.append({"id": "7", "attributes": {
        "rule_group_id": "1", "strict": False,
        "triggers": [{"type": "destination_account_contains", "value": "MAGASIN"}],
        "actions": [{"type": "set_category", "value": "Courses"}]}})
    j = client.post("/api/rules", json={"payee": "MAGASIN", "poste": "courses"}).json()
    assert j == {"rule_id": "7", "created": False, "applied": True}
    a = reg.rules[0]["attributes"]
    assert reg.puts == ["7"] and a["strict"] is True
    assert [t["type"] for t in a["triggers"]] == ["description_contains", "has_no_category"]
    assert reg.triggered[-1][0] == "/rules/7/trigger"
    assert len(reg.rules) == 1


@pytest.mark.parametrize("body", [{"payee": "MAGASIN", "poste": "inconnu"},
                                  {"payee": "   ", "poste": "courses"},
                                  {"payee": " ab ", "poste": "courses"},
                                  {"payee": "x" * 101, "poste": "courses"}])
def test_rules_rejects_bad_input(client, body):
    assert client.post("/api/rules", json=body).status_code == 422
    assert client.registry.rules == []


def test_cycles_history(monkeypatch):
    monkeypatch.setattr(fc, "incomes_since", lambda days: INCOMES + [
        {"date": "2026-10-02", "amount": 1500.0, "payee": "EMPLOYEUR SA"}])  # prime proche
    monkeypatch.setattr(fc, "withdrawals_between", lambda s, e: [
        w for w in WITHDRAWALS if s <= date.fromisoformat(w["date"]) <= e])
    h = fc.cycles_history(2, CFG["budget"]["pay"], exclude=["virements"],
                          charges=CFG["dormant"]["recurring_charges"])
    assert [c["days"] for c in h] == [31, 30]
    assert h[-1]["by_category"] == {"Courses": 300.0, "Carburant": 62.0}


# capturé sur le code antérieur aux routes du cycle
REMAINING_SNAPSHOT = {"ref_month": "2026-11", "reste_a_vivre": 400.0, "reste_a_investir": 0.0}


def test_remaining_unchanged(monkeypatch):
    monkeypatch.setattr(main, "load_cfg", lambda: CFG)
    monkeypatch.setattr(fc, "_now_month", lambda: "2026-11")
    monkeypatch.setattr(fc, "monthly_income_series", lambda ref, n=6: [2000.0] * 6)
    monkeypatch.setattr(fc, "typical_monthly_expense", lambda ref, months=6: 1600.0)
    monkeypatch.setattr(fc, "asset_accounts", lambda on_date=None: [
        {"name": k, "balance": v} for k, v in BALANCES.items()])
    monkeypatch.setenv("NORA_API_TOKEN", "t")
    r = TestClient(main.app, headers={"Authorization": "Bearer t"}).get("/api/remaining")
    assert r.status_code == 200
    assert r.json() == REMAINING_SNAPSHOT


def _tx(kind, name, desc):
    return {"data": [{"id": "1", "attributes": {"transactions": [
        {"date": "2026-11-01", "amount": "10", "description": desc,
         "destination_name": name if kind == "withdrawal" else "Compte B",
         "source_name": name if kind == "deposit" else "Compte B"}]}}], "meta": {}}


def test_payee_from_description_when_counterparty_unknown(monkeypatch):
    monkeypatch.setattr(fc, "api_get", lambda p, params=None: _tx(
        "withdrawal", "(unknown destination)", "PRLV  ASSUREUR EXEMPLE AUTO\n10/2026-123 "))
    w = fc.withdrawals_between(date(2026, 11, 1), date(2026, 11, 2))
    assert w[0]["payee"] == "PRLV ASSUREUR EXEMPLE AUTO 10/2026-123"


def test_payee_keeps_real_destination(monkeypatch):
    monkeypatch.setattr(fc, "api_get", lambda p, params=None: _tx("withdrawal", "Magasin", "CB X"))
    assert fc.withdrawals_since(1)[0]["payee"] == "Magasin"


def test_unknown_source_deposit_matches_pay_rule(monkeypatch):
    monkeypatch.setattr(fc, "api_get", lambda p, params=None: _tx(
        "deposit", "(Unknown source)", "VIR EMPLOYEUR SA VIREMENT"))
    inc = fc.incomes_since(30)
    assert inc[0]["payee"] == "VIR EMPLOYEUR SA VIREMENT"
    assert cycle.pay_dates(inc, {"payee_contains": "EMPLOYEUR", "min_amount": 0}) \
        == [date(2026, 11, 1)]


def test_unclassified_payee_is_description(client):
    WITHDRAWALS.append({**_w("c9", "2026-11-02", 5.0, "ENSEIGNE A"),
                        "description": "CARTE 03/07  ENSEIGNE B VILLE"})
    try:
        j = client.get("/api/unclassified").json()
    finally:
        WITHDRAWALS.pop()
    assert [u["payee"] for u in j if u["id"] == "c9"] == ["CARTE 03/07 ENSEIGNE B VILLE"]


def test_uncategorized_history_makes_quality_uncertain(client, monkeypatch):
    # cycle en cours classé, mais l'historique qui fournit les rythmes ne l'est pas
    _with(monkeypatch, [_w("h9", "2026-08-10", 2000.0, "MAGASIN")])
    j = client.get("/api/cycle").json()
    assert j["basis"]["coverage"] >= cycle.MIN_COVERAGE
    assert j["basis"]["history_coverage"] == round(1 - 2000 / 3086, 4)
    assert j["quality"] == "incertaine"
