from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from letssing.sources import youtube

router = APIRouter(prefix="/api", tags=["search"])
log = logging.getLogger("letssing.search")

PAGE_SIZE = 30  # resultados por fonte a cada "Carregar mais"
MAX_RESULTS = 150

SearchFn = Callable[[str, int], list]  # (busca, limite) -> resultados


def default_sources(usdb) -> dict[str, SearchFn]:
    # UltraStar-ES não tem busca automática (Cloudflare): importação manual na playlist
    return {"usdb": usdb.search, "youtube": youtube.search}


def _mark_duplicates(repo, results: list[dict]) -> list[dict]:
    for r in results:
        dup = repo.find_duplicate(r["artist"], r["title"])
        r["in_playlist"] = dup.id if dup else None
    return results


@router.get("/search")
def search(request: Request, q: str, source: str | None = None, offset: int = 0) -> dict[str, Any]:
    """Resultados agrupados por fonte; erro numa fonte não derruba as outras.

    `source` + `offset` = "Carregar mais" de uma fonte só. As fontes não têm
    paginação própria: pede-se um limite maior e devolve-se só a parte nova.
    """
    sources: dict[str, SearchFn] = request.app.state.search_sources
    if source is not None and source not in sources:
        raise HTTPException(404, f"Fonte desconhecida: {source}")
    offset = max(0, min(offset, MAX_RESULTS))
    out: dict[str, Any] = {}
    for name, fn in sources.items():
        if source is not None and name != source:
            continue
        want = min(offset + PAGE_SIZE, MAX_RESULTS)
        try:
            found = fn(q, want + 1)  # +1 para saber se ainda há mais
            page = [r.model_dump() for r in found[offset:want]]
            out[name] = {"results": _mark_duplicates(request.app.state.playlist, page),
                         "error": None, "offset": offset, "more": len(found) > want}
        except Exception as e:
            log.warning("search_failed", extra={"fields": {"source": name, "error": str(e)}})
            out[name] = {"results": [], "error": str(e) or e.__class__.__name__,
                         "offset": offset, "more": False}
    return out


def _youtube_cookies(request: Request) -> list | None:
    st = request.app.state.config.load()
    return request.app.state.youtube_cookies(st.youtube_cookies_browser, st.youtube_cookies_file)


@router.get("/youtube/playlist")
def youtube_playlist(request: Request, url: str) -> dict[str, Any]:
    """Músicas de uma playlist do YouTube, para escolher quais adicionar.
    Com o login do YouTube configurado, abre também as privadas e Curtidos/Assistir mais tarde."""
    warning = None
    try:
        pid = youtube.playlist_id(url)
        try:
            cookies = _youtube_cookies(request)
        except youtube.YoutubeLoginError as e:
            if pid in ("LL", "WL"):
                raise
            cookies, warning = None, f"Sem login do YouTube ({e}); só playlists públicas/não listadas."
        title, entries = youtube.playlist_entries(url, cookies=cookies)
    except (ValueError, youtube.YoutubeLoginError) as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        log.warning("playlist_failed", extra={"fields": {"error": str(e)}})
        raise HTTPException(502, f"Não consegui ler a playlist: {e}") from None
    results = [r.model_dump() for r in entries]
    return {"title": title, "warning": warning,
            "results": _mark_duplicates(request.app.state.playlist, results)}


@router.get("/youtube/my-playlists")
def youtube_my_playlists(request: Request) -> dict[str, Any]:
    """Playlists da conta do YouTube (precisa do login em Configurações)."""
    try:
        cookies = _youtube_cookies(request)
        if cookies is None:
            raise youtube.YoutubeLoginError(
                "Configure o login do YouTube em Configurações (navegador ou cookies.txt)")
        return {"playlists": request.app.state.youtube_my_playlists(cookies)}
    except youtube.YoutubeLoginError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        log.warning("my_playlists_failed", extra={"fields": {"error": str(e)}})
        raise HTTPException(502, f"Não consegui ler suas playlists: {e}") from None
