"""Spotify: conectar a conta e escolher músicas das playlists/curtidas."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from letssing.api.routes_search import _mark_duplicates
from letssing.sources.spotify import SpotifyAuthRequired, SpotifyClient, SpotifyError, redirect_uri

router = APIRouter(prefix="/api/spotify", tags=["spotify"])
log = logging.getLogger("letssing.spotify")

DEFAULT_PORT = 8000


def _sp(request: Request) -> SpotifyClient:
    return request.app.state.spotify


def _port(request: Request) -> int:
    return request.url.port or DEFAULT_PORT


def _call(fn):
    try:
        return fn()
    except SpotifyAuthRequired as e:
        raise HTTPException(401, str(e)) from None
    except SpotifyError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        log.warning("spotify_failed", extra={"fields": {"error": str(e)}})
        raise HTTPException(502, f"Spotify inacessível: {e}") from None


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    sp = _sp(request)
    out: dict[str, Any] = {
        "configured": bool(request.app.state.config.load().spotify_client_id),
        "connected": sp.connected(),
        "redirect_uri": redirect_uri(_port(request)),
        "user": None, "error": None,
    }
    if out["configured"] and out["connected"]:
        try:
            out["user"] = sp.me()["name"]
        except SpotifyError as e:
            out["error"] = str(e)
            out["connected"] = sp.connected()
        except Exception as e:
            out["error"] = f"Spotify inacessível: {e}"
    return out


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    url = _call(lambda: _sp(request).authorize_url(_port(request)))
    return RedirectResponse(url, status_code=302)


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    # o Spotify volta em 127.0.0.1; a app abre de novo na aba Buscar
    if error:
        msg = "Login cancelado no Spotify" if error == "access_denied" else f"Spotify: {error}"
        return RedirectResponse(f"/?spotify_error={quote(msg)}", status_code=302)
    try:
        _sp(request).finish_login(code, state)
    except Exception as e:
        return RedirectResponse(f"/?spotify_error={quote(str(e)[:200])}", status_code=302)
    return RedirectResponse("/?spotify=ok", status_code=302)


@router.post("/disconnect")
def disconnect(request: Request) -> dict[str, Any]:
    _sp(request).disconnect()
    return {"ok": True}


@router.get("/playlists")
def playlists(request: Request) -> list[dict[str, Any]]:
    return _call(lambda: _sp(request).playlists())


def _tracks(request: Request, fn) -> dict[str, Any]:
    results = [r.model_dump() for r in _call(fn)]
    return {"results": _mark_duplicates(request.app.state.playlist, results)}


@router.get("/saved")
def saved(request: Request) -> dict[str, Any]:
    return _tracks(request, lambda: _sp(request).saved_tracks())


@router.get("/playlists/{playlist_id}")
def playlist_tracks(request: Request, playlist_id: str) -> dict[str, Any]:
    return _tracks(request, lambda: _sp(request).playlist_tracks(playlist_id))
