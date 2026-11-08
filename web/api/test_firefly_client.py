"""Requêtes envoyées à Firefly III par le client."""
import os

import httpx

os.environ.setdefault("FIREFLY_TOKEN", "test")

import firefly_client as fc  # noqa: E402


class _Recorder:
    """Remplace httpx.Client : garde la dernière requête POST."""

    calls = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kw):
        _Recorder.calls.append((url, kw))
        return httpx.Response(204, request=httpx.Request("POST", url))


def test_trigger_rule_sends_a_json_body(monkeypatch):
    # Firefly refuse (415) un POST sans corps JSON sur le déclenchement d'une règle.
    monkeypatch.setattr(fc.httpx, "Client", _Recorder)
    _Recorder.calls.clear()
    fc.trigger_rule(7)
    url, kw = _Recorder.calls[-1]
    assert url.endswith("/rules/7/trigger")
    assert kw["json"] is not None
    assert set(kw["params"]) == {"start", "end"}
