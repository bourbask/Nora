from datetime import date

import finance_alerts as fa

POSTES = [
    {"name": "restos", "category": "R", "compressible": True, "floor_eur": 20},
    {"name": "loisirs", "category": "L", "compressible": True, "floor_eur": 10},
    {"name": "courses", "category": "C", "compressible": False, "floor_eur": 0},
]
C = date(2026, 11, 1)
T = date(2026, 11, 10)
AV = {"available": 100.0}
ENV = {"restos": 100, "loisirs": 100, "courses": 300}


def run(**kw):
    a = dict(cycle_start=C, avail=AV, envelopes=ENV, spent={}, postes=POSTES,
             unexpected=[], consent_expires=None, today=T, ceiling_months=None, warn_months=3)
    a.update(kw)
    return fa.build(**a)


def kinds(al):
    return [x["kind"] for x in al]


def test_rien():
    assert run() == []


def test_negative_remede_ordonne():
    al = run(avail={"available": -100.0}, spent={"restos": 70, "loisirs": 50})
    n = [x for x in al if x["kind"] == "negative"][0]
    assert n["remedy"] == "Réduire : restos 30 €, loisirs 50 €, courses 20 €."
    assert n["id"] == "negative:2026-11-01:disponible"


def test_negative_reste_non_couvrable():
    al = run(avail={"available": -500.0}, envelopes={"restos": 30}, postes=POSTES[:1])
    assert "et il manque encore 470 €" in al[0]["remedy"]


def test_envelope_et_near_limit_exclusifs():
    al = run(spent={"restos": 120, "loisirs": 90, "courses": 10})
    assert kinds(al) == ["envelope", "near_limit"]
    assert al[0]["id"].endswith(":restos") and al[1]["id"].endswith(":loisirs")


def test_near_limit_seuil():
    assert run(spent={"restos": 84}) == []
    assert kinds(run(spent={"restos": 85})) == ["near_limit"]
    assert kinds(run(spent={"restos": 100})) == ["near_limit"]


def test_unexpected():
    u = [{"date": "2026-11-03", "payee": "Shop\x00\n" + "x" * 100, "amount": 150.0},
         {"date": "2026-11-04", "payee": "petit", "amount": 99.0}]
    al = run(unexpected=u)
    assert kinds(al) == ["unexpected"]
    assert "\x00" not in al[0]["title"] + al[0]["details"] and "x" * 61 not in al[0]["title"]
    assert al[0]["id"].startswith("unexpected:2026-11-01:2026-11-03:")
    assert kinds(run(unexpected=u, unexpected_min=50.0)) == ["unexpected"] * 2


def test_unexpected_strips_invisible_and_bidi():
    bad = "".join(map(chr, [*range(0x200B, 0x2010), 0x2028, 0x2029, *range(0x202A, 0x202F),
                            *range(0x2066, 0x206A), 0xFEFF]))
    u = [{"date": "2026-11-03", "payee": f"Sh{bad}op", "amount": 150.0}]
    det = run(unexpected=u)[0]["details"]
    assert not set(bad) & set(det)


def test_consent():
    assert run(consent_expires=date(2026, 11, 25)) == []
    assert kinds(run(consent_expires=date(2026, 11, 24))) == ["consent"]
    assert kinds(run(consent_expires=date(2026, 11, 20))) == ["consent"]
    exp = run(consent_expires=date(2026, 11, 1))
    assert "expiré" in exp[0]["title"].lower()
    assert exp[0]["id"] == "consent:2026-11-01:2026-11-01"


def test_ceiling():
    assert run(ceiling_months=4) == []
    assert run(ceiling_months=None) == []
    al = run(ceiling_months=3)
    assert kinds(al) == ["ceiling"] and al[0]["id"].endswith(":livret")


def test_ids_stables_et_par_cycle():
    kw = dict(avail={"available": -5.0}, spent={"restos": 120})
    assert run(**kw) == run(**kw)
    other = run(cycle_start=date(2026, 12, 1), **kw)
    assert not {x["id"] for x in run(**kw)} & {x["id"] for x in other}
