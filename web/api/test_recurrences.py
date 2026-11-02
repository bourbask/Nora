from datetime import date

import recurrences as R

def _mk(payee, dates, amount):
    return [{"date": d, "amount": amount, "payee": payee} for d in dates]

def test_monthly_flat_amount_is_high_confidence():
    tx = _mk("STREAMING", ["2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05"], 15.99)
    out = R.detect_recurrences(tx, set(), date(2026, 4, 1))
    assert len(out) == 1
    c = out[0]
    assert c["name"] == "STREAMING" and c["freq"] == "monthly"
    assert c["amount"] == 15.99 and c["start"] == "2026-01"
    assert c["kind"] == "other" and c["end"] is None
    assert c["count"] == 4 and c["confidence"] == "high"

def test_drifting_amount_kept_as_medium():
    tx = (_mk("TELCO", ["2026-01-10"], 10.0) + _mk("TELCO", ["2026-02-10"], 13.0)
          + _mk("TELCO", ["2026-03-10"], 20.0) + _mk("TELCO", ["2026-04-10"], 25.0))
    out = R.detect_recurrences(tx, set(), date(2026, 4, 1))
    assert len(out) == 1 and out[0]["confidence"] == "medium"  # spread > 15%, mais gardé

def test_high_frequency_merchant_rejected():
    # 12 courses sur ~6 semaines, écarts de quelques jours → pas mensuel
    dates = [f"2026-01-{d:02d}" for d in (2, 4, 6, 9, 12, 15, 18, 21, 24, 27)]
    out = R.detect_recurrences(_mk("SUPERMARKET", dates, 40.0), set(), date(2026, 4, 1))
    assert out == []

def test_less_than_three_distinct_days_rejected():
    out = R.detect_recurrences(_mk("X", ["2026-01-05", "2026-02-05"], 9.0), set(), date(2026, 4, 1))
    assert out == []

def test_same_day_duplicates_collapsed():
    # 3 jours distincts mensuels + un doublon le même jour → pas d'écart de 0 j
    tx = _mk("SPOT", ["2026-01-05", "2026-01-05", "2026-02-05", "2026-03-05"], 9.99)
    out = R.detect_recurrences(tx, set(), date(2026, 4, 1))
    assert len(out) == 1 and out[0]["count"] == 3

def test_existing_name_excluded():
    tx = _mk("STREAMING", ["2026-01-05", "2026-02-05", "2026-03-05"], 15.99)
    assert R.detect_recurrences(tx, {"STREAMING"}, date(2026, 4, 1)) == []


def test_destinationless_bucket_excluded():
    # "?" = outflows without a destination_name, lumped together — not a real payee
    tx = _mk("?", ["2026-01-05", "2026-02-05", "2026-03-05"], 20.0)
    assert R.detect_recurrences(tx, set(), date(2026, 4, 1)) == []

def test_monthly_stale_dropped():
    # dernière occurrence = janvier, today = avril → 3 mois → périmée
    tx = _mk("OLD", ["2025-11-05", "2025-12-05", "2026-01-05"], 10.0)
    assert R.detect_recurrences(tx, set(), date(2026, 4, 1)) == []

def test_monthly_recent_kept():
    tx = _mk("CUR", ["2026-01-05", "2026-02-05", "2026-03-05"], 10.0)
    out = R.detect_recurrences(tx, set(), date(2026, 4, 1))
    assert len(out) == 1 and out[0]["freq"] == "monthly"

def test_quarterly_detected():
    tx = _mk("Q", ["2025-07-10", "2025-10-10", "2026-01-10", "2026-04-10"], 200.0)
    out = R.detect_recurrences(tx, set(), date(2026, 5, 1))
    assert len(out) == 1 and out[0]["freq"] == "quarterly"

def test_quarterly_stale_dropped():
    tx = _mk("Q", ["2024-07-10", "2024-10-10", "2025-01-10"], 200.0)
    assert R.detect_recurrences(tx, set(), date(2026, 5, 1)) == []

def test_yearly_same_month_detected():
    tx = _mk("Y", ["2024-03-15", "2025-03-15", "2026-03-15"], 90.0)
    out = R.detect_recurrences(tx, set(), date(2026, 6, 1))
    assert len(out) == 1 and out[0]["freq"] == "yearly"

def test_yearly_scattered_months_rejected():
    # ~annuel en cadence mais mois différents → dépense ponctuelle, pas annuelle
    tx = _mk("PONCT", ["2024-03-15", "2025-06-20", "2026-02-10"], 90.0)
    assert R.detect_recurrences(tx, set(), date(2026, 6, 1)) == []

def test_yearly_stale_dropped():
    tx = _mk("Y", ["2022-03-15", "2023-03-15", "2024-03-15"], 90.0)
    assert R.detect_recurrences(tx, set(), date(2026, 6, 1)) == []

def test_dismissed_excluded():
    tx = _mk("STREAMING", ["2026-01-05", "2026-02-05", "2026-03-05"], 15.99)
    assert R.detect_recurrences(tx, set(), date(2026, 4, 1), dismissed={"STREAMING"}) == []

if __name__ == "__main__":
    for n, fn in sorted(globals().items()):
        if n.startswith("test_"):
            fn()
    print("test_recurrences.py OK")
