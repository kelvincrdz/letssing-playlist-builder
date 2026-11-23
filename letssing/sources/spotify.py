"""Spotify: login do usuário (PKCE) e leitura das playlists e das Músicas Curtidas.

Tudo conferido na documentação do Spotify em 2026-11-22:
- Redirect URI (concepts/redirect_uri): "localhost" não é aceito; para app local
  use o IP de loopback com http, ex.: http://127.0.0.1:8000/api/spotify/callback.
- PKCE (tutorials/code-pkce-flow): GET accounts.spotify.com/authorize com
  response_type=code, client_id, redirect_uri, code_challenge_method=S256,
  code_challenge, scope, state; POST accounts.spotify.com/api/token com
  grant_type=authorization_code, code, redirect_uri, client_id, code_verifier.
  Sem client secret. Renovação: grant_type=refresh_token, refresh_token,
  client_id; a resposta pode trazer um refresh_token novo (se não trouxer,
  continua valendo o antigo).
- Endpoints: GET /v1/me/playlists (playlist-read-private; colaborativas com
  playlist-read-collaborative), GET /v1/playlists/{id}/items (o antigo /tracks
  foi removido em fev/2026; só para playlists do usuário ou em que ele
  colabora, senão 403; cada entrada traz a faixa em `item`, `track` está
  obsoleto) e GET /v1/me/tracks (user-library-read). limit máx. 50, paginação
  por offset/next.

O refresh token fica no Cofre de Credenciais do Windows (keyring), nunca no
config.json nem nos logs. O access token só fica em memória.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from letssing.config import SecretStore
from letssing.sources.models import SearchResult

log = logging.getLogger("letssing.spotify")

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1/"
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"
CALLBACK_PATH = "/api/spotify/callback"
REFRESH_KEY = "spotify:refresh_token"
PAGE_LIMIT = 50  # máximo aceito pelos três endpoints
MAX_TRACKS = 1000
STATE_TTL_S = 600

# "Música - Remastered 2011" / "- 2011 Remaster": sufixo só do Spotify
_REMASTER = re.compile(r"\s+-\s+(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?(?:\s+version)?\s*$", re.IGNORECASE)


class SpotifyError(Exception):
    pass


class SpotifyAuthRequired(SpotifyError):
    pass


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


def clean_title(title: str) -> str:
    return _REMASTER.sub("", title).strip() or title


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]  # 43..128 caracteres
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class _Pending:
    verifier: str
    redirect_uri: str
    created: float


def track_result(track: dict[str, Any] | None) -> SearchResult | None:
    """Faixa do Spotify -> SearchResult (sem .txt e sem mídia: o áudio vem do YouTube)."""
    if not track or track.get("type", "track") != "track" or track.get("is_local") or not track.get("name"):
        return None
    artists = [a.get("name", "") for a in track.get("artists") or [] if a.get("name")]
    album = track.get("album") or {}
    images = album.get("images") or []
    ms = track.get("duration_ms")
    url = (track.get("external_urls") or {}).get("spotify", "")
    return SearchResult(
        source="spotify",
        source_id=track.get("id") or "",
        artist=", ".join(artists) or "Desconhecido",
        title=clean_title(track["name"]),
        has_txt=False,
        has_video=None,
        duration_s=ms / 1000 if ms else None,
        url=url,
        thumbnail=images[0]["url"] if images else "",
        extra=" · ".join(x for x in (album.get("name", ""), (album.get("release_date") or "")[:4]) if x),
    )


class SpotifyClient:
    def __init__(self, client_id: Callable[[], str], store: SecretStore,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._client_id = client_id
        self._store = store
        self._http = httpx.Client(timeout=20, transport=transport)
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._access: tuple[str, float] | None = None  # (token, expira em)

    # ---- login ----
    def _cid(self) -> str:
        cid = (self._client_id() or "").strip()
        if not cid:
            raise SpotifyAuthRequired("Preencha o Client ID do Spotify em Configurações")
        return cid

    def authorize_url(self, port: int) -> str:
        cid = self._cid()
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if now - v.created < STATE_TTL_S}
            self._pending[state] = _Pending(verifier, redirect_uri(port), now)
        return AUTHORIZE_URL + "?" + urlencode({
            "response_type": "code", "client_id": cid, "redirect_uri": redirect_uri(port),
            "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state,
        })

    def finish_login(self, code: str, state: str) -> None:
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or time.time() - pending.created > STATE_TTL_S:
            raise SpotifyError("Login do Spotify expirado ou inválido; tente de novo")
        self._token_request({
            "grant_type": "authorization_code", "code": code, "redirect_uri": pending.redirect_uri,
            "client_id": self._cid(), "code_verifier": pending.verifier,
        })
        log.info("spotify_login_ok")

    def connected(self) -> bool:
        return bool(self._store.get(REFRESH_KEY))

    def disconnect(self) -> None:
        self._store.delete(REFRESH_KEY)
        self._access = None

    def _token_request(self, data: dict[str, str]) -> str:
        r = self._http.post(TOKEN_URL, data=data)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200:
            if body.get("error") == "invalid_grant" and data["grant_type"] == "refresh_token":
                self.disconnect()  # refresh token vencido (6 meses) ou revogado
                raise SpotifyAuthRequired("A conexão com o Spotify expirou; conecte de novo")
            raise SpotifyError(f"Spotify recusou o login: {body.get('error_description') or body.get('error') or r.status_code}")
        if body.get("refresh_token"):
            self._store.set(REFRESH_KEY, body["refresh_token"])
        self._access = (body["access_token"], time.time() + int(body.get("expires_in", 3600)) - 60)
        return body["access_token"]

    def _token(self, force: bool = False) -> str:
        if not force and self._access and time.time() < self._access[1]:
            return self._access[0]
        refresh = self._store.get(REFRESH_KEY)
        if not refresh:
            raise SpotifyAuthRequired("Conecte sua conta do Spotify")
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh,
                                    "client_id": self._cid()})

    # ---- API ----
    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        full = url if url.startswith("https://") else API_URL + url
        for attempt in (1, 2):
            r = self._http.get(full, params=params, headers={"Authorization": f"Bearer {self._token(force=attempt == 2)}"})
            if r.status_code == 401 and attempt == 1:
                continue  # token expirou antes da hora: renova uma vez
            break
        if r.status_code == 403:
            raise SpotifyError("O Spotify negou o acesso (403). Em modo de desenvolvimento, sua conta precisa estar "
                               "em Users Management do app e o dono do app precisa ter Premium; playlists de outras "
                               "pessoas ou do próprio Spotify não podem ser lidas.")
        if r.status_code == 429:
            raise SpotifyError(f"Limite do Spotify atingido; tente em {r.headers.get('Retry-After', 'alguns')} s")
        if r.status_code != 200:
            raise SpotifyError(f"Erro do Spotify ({r.status_code}): {r.text[:200]}")
        return r.json()

    def _pages(self, url: str, limit_total: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page: dict[str, Any] | None = self._get(url, {"limit": PAGE_LIMIT})
        while page:
            out.extend(page.get("items") or [])
            if len(out) >= limit_total or not page.get("next"):
                break
            page = self._get(page["next"])
        return out[:limit_total]

    def me(self) -> dict[str, Any]:
        d = self._get("me")
        return {"id": d.get("id", ""), "name": d.get("display_name") or d.get("id", "")}

    def playlists(self) -> list[dict[str, Any]]:
        me = self.me()["id"]
        out = []
        for p in self._pages("me/playlists", 500):
            if not p:
                continue
            owner = p.get("owner") or {}
            images = p.get("images") or []
            total = (p.get("items") or p.get("tracks") or {}).get("total")
            out.append({
                "id": p["id"], "name": p.get("name", ""), "owner": owner.get("display_name") or owner.get("id", ""),
                "total": total, "image": images[-1]["url"] if images else "",
                # só dá para ler as faixas das próprias playlists e das colaborativas
                "readable": owner.get("id") == me or bool(p.get("collaborative")),
            })
        return out

    def playlist_tracks(self, playlist_id: str) -> list[SearchResult]:
        if not re.fullmatch(r"[A-Za-z0-9]{10,40}", playlist_id):
            raise SpotifyError("ID de playlist inválido")
        entries = self._pages(f"playlists/{playlist_id}/items", MAX_TRACKS)
        return [r for e in entries if e and (r := track_result(e.get("item") or e.get("track")))]

    def saved_tracks(self) -> list[SearchResult]:
        entries = self._pages("me/tracks", MAX_TRACKS)
        return [r for e in entries if e and (r := track_result(e.get("track")))]
