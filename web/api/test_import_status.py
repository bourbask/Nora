import import_status as I

LOG = """\
{"timestamp":"2026-07-07T12:26:06Z","source":"broker","event":"import_failed","transactions":null}
{"timestamp":"2026-07-07T12:27:58Z","source":"broker","event":"import_completed","transactions":325}
{"timestamp":"2026-07-08T06:00:00Z","source":"bank","event":"import_completed","transactions":42}
garbage not json
"""


def test_last_per_source_takes_latest_line():
    r = I.last_per_source(LOG)
    assert r["broker"]["event"] == "import_completed"
    assert r["broker"]["transactions"] == 325
    assert r["bank"]["transactions"] == 42


def test_empty_log_is_empty_dict():
    assert I.last_per_source("") == {}


def test_malformed_lines_ignored():
    r = I.last_per_source('garbage\n{"timestamp":"t","source":"bank","event":"x","transactions":1}\n')
    assert set(r) == {"bank"}


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f()
    print("test_import_status.py OK")
