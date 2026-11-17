import categorization as C


def test_coverage_ratio():
    cov = C.coverage({"Courses": 600.0, "(sans catégorie)": 200.0, "Loyer": 200.0})
    assert cov["uncategorized"] == 200.0
    assert cov["total"] == 1000.0
    assert cov["ratio"] == 0.2


def test_coverage_none_uncategorized():
    cov = C.coverage({"Courses": 500.0})
    assert cov["uncategorized"] == 0.0 and cov["ratio"] == 0.0


def test_coverage_zero_total_no_div():
    cov = C.coverage({})
    assert cov["total"] == 0.0 and cov["ratio"] == 0.0


def test_top_untagged_sorts_and_limits():
    txs = [
        {"date": "2026-06-02", "amount": 10.0, "description": "a", "category": ""},
        {"date": "2026-06-03", "amount": 90.0, "description": "b", "category": ""},
        {"date": "2026-06-04", "amount": 50.0, "description": "c", "category": "Courses"},
        {"date": "2026-06-05", "amount": 30.0, "description": "d", "category": None},
    ]
    top = C.top_untagged(txs, 2)
    assert [t["amount"] for t in top] == [90.0, 30.0]      # tagged 'c' exclu, trié desc, limité
    assert top[0] == {"date": "2026-06-03", "amount": 90.0, "description": "b"}


def test_top_untagged_empty():
    assert C.top_untagged([], 5) == []


def _op(cat, date, desc="", payee=""):
    return {"category": cat, "date": date, "description": desc, "payee": payee}


def test_clean_name():
    assert C.clean_name("  Santé \n et​ bien-être ") == "Santé et bien-être"
    assert C.clean_name(" \t ") is None and C.clean_name(None) is None
    assert C.clean_name("x" * 61) is None and C.clean_name("x" * 60) == "x" * 60


def test_suggest_merchant_history_first_then_most_used():
    hist = [_op("Loisirs", "2026-01-02", "CARTE COMMERCE A VILLE"),
            _op("Cadeaux", "2026-03-01", "carte  commerce a"),
            _op("Cadeaux", "2026-02-01", payee="Commerce A"),
            _op("Courses", "2026-01-01", "AUTRE"), _op("Courses", "2026-01-03", "AUTRE"),
            _op("Courses", "2026-01-04", "AUTRE"), _op("Santé", "2026-01-05", "AUTRE"),
            _op("Hors liste", "2026-01-06", "COMMERCE A")]
    cats = ["Courses", "Loisirs", "Cadeaux", "Santé", "Divers"]
    assert C.suggest("COMMERCE A", hist, cats) == (["Cadeaux", "Loisirs", "Courses", "Santé"], 2)


def test_suggest_tie_goes_to_most_recent_and_fills_from_categories():
    hist = [_op("Loisirs", "2026-01-01", "COMMERCE B"), _op("Santé", "2026-02-01", "COMMERCE B")]
    assert C.suggest("commerce b", hist, ["Courses", "Loisirs", "Santé"], n=4) == (
        ["Santé", "Loisirs", "Courses"], 2)
    assert C.suggest("INCONNU", [], ["Courses", "Loisirs"], n=1) == (["Courses"], 0)


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
    print("test_categorization.py OK")
