from datetime import date

import cycle as C

RULE = {"min_amount": 1000, "payee_contains": "employeur"}


def test_cycle_bounds():
    assert C.cycle_bounds(date(2026, 11, 2), date(2026, 10, 28)) == (date(2026, 10, 28), date(2026, 11, 30))
    assert C.cycle_bounds(date(2026, 11, 29), date(2026, 11, 27)) == (date(2026, 11, 27), date(2026, 12, 31))
    assert C.cycle_bounds(date(2027, 2, 3), date(2027, 1, 28))[1] == date(2027, 2, 28)
    assert C.cycle_bounds(date(2026, 11, 5), None) == (date(2026, 11, 1), date(2026, 11, 30))


def test_find_last_pay():
    today = date(2026, 11, 10)
    inc = [
        {"date": "2026-10-28", "amount": 2000, "payee": "EMPLOYEUR SA"},
        {"date": "2026-11-02", "amount": 50, "payee": "Employeur SA"},      # sous le minimum
        {"date": "2026-11-03", "amount": 2000, "payee": "Autre"},           # payee non correspondant
        {"date": "2026-09-28", "amount": 2000, "payee": "employeur sa"},    # plus ancien
    ]
    assert C.find_last_pay(inc, RULE, today) == date(2026, 10, 28)
    assert C.find_last_pay(inc, RULE, date(2026, 12, 20)) is None           # > 45 jours
    one = [{"date": "2026-10-01", "amount": 2000, "payee": "employeur"}]
    assert C.find_last_pay(one, RULE, date(2026, 11, 15)) == date(2026, 10, 1)   # 45 j
    assert C.find_last_pay(one, RULE, date(2026, 11, 16)) is None                # 46 j


def test_remaining_charges():
    start, end = date(2026, 10, 28), date(2026, 11, 30)
    charges = [
        {"name": "Loyer", "amount": 700, "freq": "monthly", "start": "2026-01", "end": None, "day": 3},
        {"name": "Assurance", "amount": 40, "freq": "monthly", "start": "2026-01", "end": None, "day": 15},
        {"name": "Ancien", "amount": 90, "freq": "monthly", "start": "2025-01", "end": "2026-09", "day": 12},
        {"name": "Trimestre", "amount": 300, "freq": "quarterly", "start": "2026-11", "end": None},
    ]
    w = [
        {"date": "2026-11-03", "amount": 712.0, "payee": "LOYER novembre"},
        {"date": "2026-11-04", "amount": -40.0, "payee": "assurance"},      # <= 0 ignorée
    ]
    assert C.remaining_charges(charges, start, end, w) == [
        {"name": "Assurance", "amount": 40, "day": 15, "due": date(2026, 11, 15)}]


def test_remaining_charges_match_and_tolerance():
    ch = [{"name": "Abo", "amount": 100, "freq": "monthly", "match": "streamco"}]
    s, e = date(2026, 11, 1), date(2026, 11, 30)
    ok = [{"date": "2026-11-05", "amount": 110.0, "payee": "StreamCo"}]
    far = [{"date": "2026-11-05", "amount": 130.0, "payee": "StreamCo"}]
    assert C.remaining_charges(ch, s, e, ok) == []
    assert len(C.remaining_charges(ch, s, e, far)) == 1


def test_daily_rates():
    cycles = [
        {"days": 30, "by_category": {"courses": 300}},
        {"days": 30, "by_category": {"courses": 330}},
        {"days": 31, "by_category": {"courses": 600}},
    ]
    assert C.daily_rates(cycles)["courses"] == 11.0
    mixed = cycles + [{"days": 30, "by_category": {"sorties": 60}}]
    r = C.daily_rates(mixed)
    assert r["courses"] == 10.5   # 0, 10, 11, 19.35 -> médiane
    assert r["sorties"] == 0.0   # 0,0,0,2 -> médiane 0


def test_daily_rates_ignores_non_positive():
    cycles = [{"days": 10, "by_category": {"x": -50, "y": 100}},
              {"days": 10, "by_category": {"x": -50, "y": 100}}]
    r = C.daily_rates(cycles)
    assert r.get("x", 0.0) == 0.0
    assert r["y"] == 10.0


def test_available():
    out = C.available(1000, [{"name": "a", "amount": 300, "day": 1}], {"courses": 10}, 10, 50)
    assert out["available"] == 550.0
    assert out["charges_left_total"] == 300.0
    assert out["variable_expected"] == {"courses": 100.0}
    assert out["days_left"] == 10 and out["margin"] == 50 and out["balance"] == 1000


def test_quality():
    assert C.quality(1, 0.9, True) == "incertaine"
    assert C.quality(2, 0.79, True) == "incertaine"
    assert C.quality(2, 0.8, False) == "incertaine"
    assert C.quality(2, 0.8, True) == "fiable"


S, E = date(2026, 10, 28), date(2026, 11, 30)


def _ch(name="Abo", amount=30, day=30, **kw):
    return {"name": name, "amount": amount, "freq": "monthly", "start": "2026-01", "end": None, "day": day, **kw}


def test_charge_due_twice_in_cycle():
    w = [{"date": "2026-10-30", "amount": 30.0, "payee": "abo"}]
    r = C.remaining_charges([_ch()], S, E, w)
    assert [x["due"] for x in r] == [date(2026, 11, 30)]


def test_day_5_only_november():
    r = C.remaining_charges([_ch(day=5)], S, E, [])
    assert [x["due"] for x in r] == [date(2026, 11, 5)]


def test_charge_ending_in_start_month_unpaid_counts():
    r = C.remaining_charges([_ch(end="2026-10")], S, E, [])
    assert [x["due"] for x in r] == [date(2026, 10, 30)]


def test_one_withdrawal_settles_one_charge():
    ch = [_ch("Assur A", 40, 10, match="assur"), _ch("Assur B", 45, 10, match="assur")]
    w = [{"date": "2026-11-10", "amount": 42.0, "payee": "ASSUR"}]
    assert len(C.remaining_charges(ch, S, E, w)) == 1


def test_undated_charge_one_occurrence():
    r = C.remaining_charges([_ch(day=None)], S, E, [])
    assert len(r) == 1 and r[0]["due"] is None


def test_available_clamps_days_left():
    assert C.available(1000, [], {"x": 10.0}, -5, 0)["available"] == 1000.0
