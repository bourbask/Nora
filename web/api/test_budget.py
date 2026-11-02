import budget

POSTES = [
    {"name": "restos", "category": "Restos", "compressible": True, "floor_eur": 20},
    {"name": "loisirs", "category": "Loisirs", "compressible": True, "floor_eur": 10},
    {"name": "courses", "category": "Courses", "compressible": False, "floor_eur": 0},
    {"name": "essence", "category": "Essence", "compressible": False, "floor_eur": 0},
]
LEFT = {"restos": 30, "loisirs": 50, "courses": 200, "essence": 80}


def test_mode():
    assert budget.mode(2999, 3000) == "reconstitution"
    assert budget.mode(3000, 3000) == "serein"


def test_envelopes():
    rates = {"Restos": 3.0, "Loisirs": 2.0, "Courses": 10.0, "Essence": 5.0}
    s = budget.envelopes(rates, 10, POSTES, "serein")
    assert s == {"restos": 30, "loisirs": 20, "courses": 100, "essence": 50}
    r = budget.envelopes(rates, 10, POSTES, "reconstitution")
    assert r == {"restos": 20, "loisirs": 10, "courses": 100, "essence": 50}


def test_spent():
    assert budget.spent_in_cycle({"Restos": 12.345, "Courses": 5}, POSTES) == {
        "restos": 12.35, "loisirs": 0, "courses": 5, "essence": 0}


def test_cuts():
    assert budget.cuts(100, LEFT, POSTES) == [
        {"poste": "restos", "amount": 30}, {"poste": "loisirs", "amount": 50},
        {"poste": "courses", "amount": 20}]
    out = budget.cuts(400, LEFT, POSTES)
    assert out[-1] == {"poste": None, "amount": 40}
    assert budget.cuts(10, {**LEFT, "restos": -5}, POSTES) == [{"poste": "loisirs", "amount": 10}]


def test_can_afford():
    r = budget.can_afford({"available": 60}, 70, "essence", "serein", LEFT, POSTES)
    assert r["ok"] is False and r["after"] == -10 and r["cuts"]
    left = {**LEFT, "restos": 10}
    r = budget.can_afford({"available": 500}, 25, "restos", "reconstitution", left, POSTES)
    assert r["ok"] is False and r["reason"] == "reconstitution"
    r = budget.can_afford({"available": 500}, 25, "restos", "serein", left, POSTES)
    assert r["ok"] is True and r["after"] == 475 and r["cuts"] == []


def test_can_save():
    assert budget.can_save({"available": 300}, 300)["ok"] is True
    assert budget.can_save({"available": 300}, 301)["ok"] is False


def test_ceiling_eta():
    assert budget.ceiling_eta(20000, 22950, 500) == 6
    assert budget.ceiling_eta(20000, 22950, 0) is None
    assert budget.ceiling_eta(23000, 22950, 500) == 0
