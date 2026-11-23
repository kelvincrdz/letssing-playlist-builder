"""YouTube via yt-dlp: busca (`ytsearchN:`), playlists e download de áudio, vídeo, capa e legendas.

O YouTube exige um runtime JavaScript; usamos o `deno` instalado pelo pacote pip
`deno` (sem instalação global).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp

from letssing.sources.models import SearchResult

log = logging.getLogger("letssing.youtube")

SUB_LANGS = ["pt.*", "en.*"]
AUDIO_EXT = ".mp3"
VIDEO_EXT = ".mp4"
COVER_EXT = ".jpg"

ProgressFn = Callable[[str, float | None], None]  # (fase, 0..1 ou None)

# hífen, traços Unicode (‐ ‑ ‒ – — ― −), barra vertical, bullets e til
_SEPARATORS = re.compile(r"\s+[\-‐-―−|•·~]\s+")
_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(official|oficial|video|vídeo|clipe|clip|lyric|lyrics|letra|legendado|"
    r"audio|áudio|hd|hq|4k|visualizer|remaster(ed)?|karaok[eê])\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_CHANNEL_NOISE = re.compile(r"\s*(-\s*topic|vevo|oficial|official)$", re.IGNORECASE)


def guess_artist_title(video_title: str, channel: str) -> tuple[str, str]:
    """'Artista - Música (Official Video)' -> ('Artista', 'Música')."""
    t = _NOISE.sub("", video_title).strip()
    parts = _SEPARATORS.split(t, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    artist = _CHANNEL_NOISE.sub("", channel or "").strip()
    return artist or "Desconhecido", t or video_title


def _js_runtimes() -> dict[str, dict[str, str]]:
    try:
        import deno

        return {"deno": {"path": str(deno.find_deno_bin())}}
    except Exception:  # sem o pacote: yt-dlp tenta o deno do PATH
        return {"deno": {}}


def _base_opts(ffmpeg_path: str = "") -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "js_runtimes": _js_runtimes(),
        "windowsfilenames": True,
    }
    if ffmpeg_path:
        opts["ffmpeg_location"] = str(Path(ffmpeg_path).parent if Path(ffmpeg_path).is_file() else ffmpeg_path)
    return opts


def search(query: str, limit: int = 5) -> list[SearchResult]:
    query = " ".join(query.split())
    if not query:
        return []
    opts = {**_base_opts(), "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = [r for e in (info or {}).get("entries") or [] if (r := _entry_result(e))]
    log.info("youtube_search", extra={"fields": {"query": query, "results": len(results)}})
    return results


def _entry_result(e: dict[str, Any] | None) -> SearchResult | None:
    """Entrada "flat" do yt-dlp (busca ou playlist) -> SearchResult."""
    if not e or not e.get("id"):
        return None
    artist, title = guess_artist_title(e.get("title") or "", e.get("channel") or e.get("uploader") or "")
    url = f"https://www.youtube.com/watch?v={e['id']}"
    thumbs = e.get("thumbnails") or []
    views = e.get("view_count")
    return SearchResult(
        source="youtube",
        source_id=e["id"],
        artist=artist,
        title=title,
        has_txt=False,
        has_video=True,
        duration_s=e.get("duration"),
        url=url,
        audio_url=url,
        video_url=url,
        thumbnail=thumbs[-1]["url"] if thumbs else "",
        extra=" · ".join(x for x in [
            e.get("title") or "",
            e.get("channel") or e.get("uploader") or "",
            f"{views:,} views".replace(",", ".") if views else "",
        ] if x),
    )


# ---- login do YouTube (sessão do navegador ou cookies.txt) ----
# Só para LER playlists da conta (privadas, Curtidos, Assistir mais tarde);
# downloads continuam sem login. Dos cookies carregados, só os de youtube.com
# ficam na sessão do yt-dlp, e o arquivo cookies.txt nunca é regravado.
COOKIE_DOMAIN = "youtube.com"
AUTH_COOKIES = ("SAPISID", "__Secure-3PAPISID")  # mesmos que o extrator do yt-dlp usa
SPECIAL_PLAYLISTS = (("LL", "Vídeos curtidos"), ("WL", "Assistir mais tarde"))


class YoutubeLoginError(RuntimeError):
    pass


def _cookie_source(browser: str = "", cookies_file: str = "") -> str:
    return "cookies.txt" if cookies_file else browser


def load_youtube_cookies(browser: str = "", cookies_file: str = "") -> list | None:
    """Cookies de youtube.com da sessão do usuário, ou None se não configurado."""
    from http.cookiejar import LoadError

    from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser

    if not browser and not cookies_file:
        return None
    try:
        if cookies_file:
            jar = YoutubeDLCookieJar(cookies_file)
            jar.load()
        else:
            jar = extract_cookies_from_browser(browser)
    except (OSError, LoadError) as e:
        raise YoutubeLoginError(f"Não consegui ler o cookies.txt: {e}") from None
    except Exception as e:
        hint = ""
        if browser in ("chrome", "edge", "brave", "opera", "vivaldi", "chromium"):
            hint = (" O Chrome/Edge atuais criptografam os cookies de um jeito que o yt-dlp não lê (e travam o arquivo"
                    " com o navegador aberto). Use o Firefox ou exporte um cookies.txt (veja Configurações).")
        raise YoutubeLoginError(f"Não consegui ler a sessão do {browser}: {str(e)[:200]}.{hint}") from None
    cookies = [c for c in jar if c.domain.lstrip(".").endswith(COOKIE_DOMAIN)]
    if not any(c.name in AUTH_COOKIES for c in cookies):
        where = "nesse cookies.txt" if cookies_file else f"no {browser}"
        raise YoutubeLoginError(f"Não achei login do YouTube {where}. Entre no youtube.com"
                                + (" e exporte de novo." if cookies_file else " nesse navegador e tente de novo."))
    return cookies


def _ydl(opts: dict[str, Any], cookies: list | None) -> yt_dlp.YoutubeDL:
    ydl = yt_dlp.YoutubeDL(opts)
    if cookies is not None:
        jar = ydl.cookiejar  # vazio: nenhum cookiefile/cookiesfrombrowser nas opções
        jar.clear()
        for c in cookies:
            jar.set_cookie(c)
    return ydl


def my_playlists(cookies: list) -> list[dict[str, Any]]:
    """Playlists da conta (feed/playlists) + Curtidos e Assistir mais tarde."""
    opts = {**_base_opts(), "extract_flat": True, "skip_download": True, "ignoreerrors": True}
    with _ydl(opts, cookies) as ydl:
        info = ydl.extract_info("https://www.youtube.com/feed/playlists", download=False)
    out = [{"id": pid, "title": title, "count": None} for pid, title in SPECIAL_PLAYLISTS]
    seen = {pid for pid, _ in SPECIAL_PLAYLISTS}
    for e in (info or {}).get("entries") or []:
        if not e:
            continue
        # só playlists (url .../playlist?list=ID); vídeos soltos no feed ficam de fora
        pid = (parse_qs(urlparse(e.get("url") or "").query).get("list") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", pid or "") or pid in seen:
            continue
        seen.add(pid)
        out.append({"id": pid, "title": e.get("title") or pid, "count": e.get("playlist_count")})
    log.info("youtube_my_playlists", extra={"fields": {"count": len(out)}})
    return out


PLAYLIST_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com")
MAX_PLAYLIST_ITEMS = 200


def playlist_id(url: str) -> str:
    """ID da playlist (parâmetro `list=`) de um link do YouTube; ValueError se não for."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or parsed.hostname not in PLAYLIST_HOSTS:
        raise ValueError("Cole um link de playlist do YouTube (youtube.com/playlist?list=…)")
    ids = parse_qs(parsed.query).get("list") or []
    if not ids or not (re.fullmatch(r"[A-Za-z0-9_-]{10,64}", ids[0]) or ids[0] in ("LL", "WL")):
        raise ValueError("O link não tem uma playlist (falta o parâmetro list=)")
    return ids[0]


def playlist_entries(url: str, limit: int = MAX_PLAYLIST_ITEMS,
                     cookies: list | None = None) -> tuple[str, list[SearchResult]]:
    """(título da playlist, músicas). Vídeos privados/removidos ficam de fora.
    Com `cookies` (login do YouTube), lê também playlists privadas, LL e WL."""
    pid = playlist_id(url)
    if pid in ("LL", "WL") and cookies is None:
        raise YoutubeLoginError("Curtidos e Assistir mais tarde precisam do login do YouTube (Configurações)")
    opts = {**_base_opts(), "extract_flat": "in_playlist", "skip_download": True,
            "playlistend": limit, "ignoreerrors": True}
    with _ydl(opts, cookies) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/playlist?list={pid}", download=False)
    entries = [e for e in (info or {}).get("entries") or []
               if e and e.get("title") not in ("[Private video]", "[Deleted video]")]
    results = [r for e in entries if (r := _entry_result(e))]
    log.info("youtube_playlist", extra={"fields": {"playlist": pid, "results": len(results)}})
    return (info or {}).get("title") or "", results


@dataclass
class DownloadRequest:
    dest: Path  # pasta da música
    basename: str  # "Artista - Titulo"
    audio_url: str
    video_url: str = ""  # vazio = não baixar vídeo
    cover_url: str = ""  # vazio = thumbnail do áudio
    max_height: int = 720
    ffmpeg_path: str = ""
    want_subs: bool = True
    want_audio: bool = True
    want_cover: bool = True


@dataclass
class DownloadResult:
    files: list[Path] = field(default_factory=list)
    duration_s: float | None = None
    warnings: list[str] = field(default_factory=list)


def _hook(progress: ProgressFn | None, phase: str) -> Callable[[dict], None]:
    def h(d: dict) -> None:
        if not progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            progress(phase, (d.get("downloaded_bytes", 0) / total) if total else None)
        elif d.get("status") == "finished":
            progress(phase, 1.0)
    return h


DOWNLOAD_ATTEMPTS = 3
RETRY_WAIT_S = 5


def _is_transient(e: Exception) -> bool:
    # 403 em "video data" = link do formato recusado/expirado; uma extração nova costuma passar
    text = str(e)
    return "HTTP Error 403" in text or "HTTP Error 429" in text or "timed out" in text.lower()


def _run(url: str, opts: dict[str, Any], sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as e:
            if attempt == DOWNLOAD_ATTEMPTS or not _is_transient(e):
                raise
            log.warning("download_retry", extra={"fields": {"url": url, "attempt": attempt, "error": str(e)[:200]}})
            sleep(RETRY_WAIT_S * attempt)
    if not info:
        raise RuntimeError(f"yt-dlp não retornou informações para {url}")
    return info


def download(req: DownloadRequest, progress: ProgressFn | None = None) -> DownloadResult:
    """Baixa os arquivos pedidos em `req.dest`. Levanta exceção em falha."""
    req.dest.mkdir(parents=True, exist_ok=True)
    outtmpl = str(req.dest / f"{req.basename}.%(ext)s")
    base = _base_opts(req.ffmpeg_path)
    result = DownloadResult()

    thumb_cover = req.want_cover and not req.cover_url
    if req.want_cover and req.cover_url:
        try:
            _download_cover(req.cover_url, req.dest / f"{req.basename}{COVER_EXT}")
        except Exception as e:  # capa externa falhou: usa a thumbnail do vídeo
            log.warning("cover_failed", extra={"fields": {"url": req.cover_url, "error": str(e)}})
            thumb_cover = True

    if req.want_audio or thumb_cover:
        opts: dict[str, Any] = {
            **base,
            "outtmpl": {"default": outtmpl, "thumbnail": outtmpl},
            "postprocessors": [],
            "progress_hooks": [_hook(progress, "audio")],
        }
        if req.want_audio:
            opts["format"] = "ba/b"
            opts["postprocessors"].append(
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}
            )
        else:
            opts["skip_download"] = True
        if thumb_cover:
            opts["writethumbnail"] = True
            opts["postprocessors"].append({"key": "FFmpegThumbnailsConvertor", "format": "jpg"})
        info = _run(req.audio_url, opts)
        result.duration_s = info.get("duration")

    if req.want_subs:
        # Legendas são um extra: falha (ex.: HTTP 429 do YouTube) não derruba o download.
        # Só as manuais: com as automáticas, "pt.*"/"en.*" pega dezenas de traduções
        # automáticas (pt-BR, pt-PT, en-GB...) e o YouTube corta com 429. A letra
        # para gerar o .txt vem de fetch_captions (uma única legenda json3).
        opts = {**base, "skip_download": True, "outtmpl": {"default": outtmpl, "subtitle": outtmpl},
                "writesubtitles": True, "subtitleslangs": SUB_LANGS,
                "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}]}
        try:
            _run(req.audio_url, opts)
        except Exception as e:
            result.warnings.append(f"legendas não baixadas: {str(e).removeprefix('ERROR: ')[:200]}")
            log.warning("subs_failed", extra={"fields": {"url": req.audio_url, "error": str(e)}})

    if req.video_url:
        h = int(req.max_height)
        opts = {
            **base,
            "outtmpl": {"default": outtmpl},
            # H.264 primeiro: o conversor re-encoda com FFmpeg, e AV1/VP9 são
            # bem mais lentos de decodificar em CPUs modestas
            "format": (
                f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/"
                f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b"
            ),
            "merge_output_format": "mp4",
            "progress_hooks": [_hook(progress, "video")],
        }
        info = _run(req.video_url, opts)
        result.duration_s = result.duration_s or info.get("duration")

    result.files = sorted(p for p in req.dest.iterdir() if p.is_file())
    return result


def _download_cover(url: str, target: Path) -> None:
    """Capa por link direto (imagem) ou por página do YouTube (thumbnail)."""
    import httpx

    if "youtube.com" in url or "youtu.be" in url:
        tmp_tmpl = str(target.with_suffix("")) + ".%(ext)s"
        opts = {**_base_opts(), "skip_download": True, "writethumbnail": True,
                "outtmpl": {"default": tmp_tmpl, "thumbnail": tmp_tmpl},
                "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}]}
        _run(url, opts)
        return
    with httpx.Client(follow_redirects=True, timeout=30) as c:
        r = c.get(url)
        r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if not ctype.startswith("image/"):
        raise RuntimeError(f"Link da capa não é uma imagem ({ctype or 'tipo desconhecido'})")
    ext = ".png" if "png" in ctype else COVER_EXT
    target.with_suffix(ext).write_bytes(r.content)


def fetch_captions(url: str, language: str | None) -> tuple[Any, dict[str, Any]] | None:
    """Legenda json3 do vídeo para gerar o .txt: (Track, json) ou None se não houver.

    `language`: código ISO da música (ex.: "pt"), se conhecido.
    """
    import json

    from letssing.generators.captions import choose_track

    with yt_dlp.YoutubeDL({**_base_opts(), "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        track = choose_track(info, language)
        if track is None:
            return None
        with ydl.urlopen(track.url) as resp:  # mesma sessão/cookies do yt-dlp
            data = json.loads(resp.read().decode("utf-8"))
    log.info("captions_fetched", extra={"fields": {"lang": track.lang, "kind": track.kind}})
    return track, data
