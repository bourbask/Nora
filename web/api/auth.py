"""Jeton d'accès : toute route exige `Authorization: Bearer <NORA_API_TOKEN>` sauf la sonde de santé."""
import hmac
import os
from contextlib import asynccontextmanager

from fastapi import Request
from fastapi.responses import JSONResponse

OPEN = {("GET", "/api/health")}


def _token() -> str:
    return os.environ.get("NORA_API_TOKEN", "").strip()


@asynccontextmanager
async def lifespan(app):
    if not _token():
        raise RuntimeError("NORA_API_TOKEN absent ou vide : l'API refuse de démarrer.")
    yield


async def require_token(request: Request, call_next):
    if request.method == "OPTIONS" or (request.method, request.url.path) in OPEN:
        return await call_next(request)
    given = request.headers.get("authorization", "")
    expected = f"Bearer {_token()}"
    # token vide => jamais d'accès (le démarrage l'interdit déjà)
    if not _token() or not hmac.compare_digest(given.encode(), expected.encode()):
        return JSONResponse({"detail": "Jeton d'accès requis."}, status_code=401)
    return await call_next(request)
