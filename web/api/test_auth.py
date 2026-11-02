"""Jeton d'accès : exigé partout sauf /api/health ; l'API refuse de démarrer sans."""
import os

import pytest

pytest.importorskip("fastapi")
os.environ.setdefault("FIREFLY_TOKEN", "test")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

TOKEN = "t0k3n-de-test"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NORA_API_TOKEN", TOKEN)
    monkeypatch.setattr(main.fc, "reconcile_view", lambda cfg: {})
    monkeypatch.setattr(main, "load_cfg", lambda: {})
    return TestClient(main.app, raise_server_exceptions=False)


def test_sans_jeton_401(client):
    assert client.get("/api/remaining").status_code == 401


def test_mauvais_jeton_401(client):
    assert client.get("/api/remaining", headers={"Authorization": "Bearer faux"}).status_code == 401
    assert client.get("/api/remaining", headers={"Authorization": TOKEN}).status_code == 401


def test_bon_jeton_passe_l_auth(client):
    r = client.get("/api/remaining", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code != 401


def test_health_ouvert(client):
    assert client.get("/api/health").status_code == 200


def test_demarrage_sans_variable_refuse(monkeypatch):
    monkeypatch.delenv("NORA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="NORA_API_TOKEN"):
        with TestClient(main.app):
            pass
    monkeypatch.setenv("NORA_API_TOKEN", "")
    with pytest.raises(RuntimeError, match="NORA_API_TOKEN"):
        with TestClient(main.app):
            pass


@pytest.mark.parametrize("hdr", [f"Bearer  {TOKEN}", f"bearer {TOKEN}", f"Bearer {TOKEN} "])
def test_forme_stricte_401(client, hdr):
    assert client.get("/api/remaining", headers={"Authorization": hdr}).status_code == 401


def test_chemin_et_methode_stricts(client):
    assert client.get("/api/health/").status_code == 401
    assert client.post("/api/health").status_code == 401


def test_options_sans_donnees(client):
    r = client.options("/api/remaining")
    assert r.status_code != 200 or not r.content
