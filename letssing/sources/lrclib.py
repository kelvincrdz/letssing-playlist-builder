"""Letras sincronizadas do LRCLIB (https://lrclib.net) -> rascunho de .txt UltraStar.

API lida em https://lrclib.net/docs.md (2026-11-22):
- sem chave; pede um User-Agent com nome, versão e link do app;
- GET /api/get?artist_name&track_name[&album_name][&duration]: um registro
  (com `duration`, casa com diferença de até ~2 s); 404 se não achar;
- GET /api/search?track_name&artist_name: até 20 registros;
- campos: trackName, artistName, duration (s), instrumental, plainLyrics,
  syncedLyrics (LRC: "[mm:ss.xx] linha"), hasWordSync.

O LRC dá só o início de cada linha. O tempo de cada palavra é estimado pelo
tamanho dela dentro da linha; as notas vêm depois pelo re-pitch do UltraSinger.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Callable

import httpx

from letssing import __version__
from letssing.generators.captions import Line, Word, _fix_overlaps

log = logging.getLogger("letssing.lrclib")

BASE_URL = "https://lrclib.net/api/"
USER_AGENT = f"LetsSingPlaylistBuilder/{__version__} (app local; https://github.com/ratnapatka/ultrastar2singIt)"
DURATION_TOLERANCE_S = 3  # a doc fala em ±2 s; um pouco de folga para o áudio do YouTube
MS_PER_WORD = 650  # linha cantada costuma ocupar menos que o intervalo até a próxima
LINE_PAD_MS = 400
LAST_LINE_MAX_MS = 6_000

_TIME = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")

Getter = Callable[..., Any]


def _fold(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]+", " ", s.casefold()).strip()


def parse_lrc(text: str) -> list[tuple[int, str]]:
    """'[00:20.32] Todos os dias' -> [(20320, 'Todos os dias')]. Linhas vazias
    (só tempo) marcam pausas e entram como texto vazio."""
    out: list[tuple[int, str]] = []
    for raw in (text or "").splitlines():
        stamps = list(_TIME.finditer(raw))
        if not stamps:
            continue  # metadados ([ar:], [ti:]…) ou lixo
        words = raw[stamps[-1].end():].strip()
        for m in stamps:  # "[00:10.00][01:20.00] refrão" repete a linha
            frac = (m.group(3) or "0").ljust(3, "0")[:3]
            ms = (int(m.group(1)) * 60 + int(m.group(2))) * 1000 + int(frac)
            out.append((ms, words))
    out.sort(key=lambda x: x[0])
    return out


def lines_from_lrc(text: str, song_end_ms: int | None = None) -> list[Line]:
    timed = parse_lrc(text)
    lines: list[Line] = []
    for i, (start, words_text) in enumerate(timed):
        words = words_text.split()
        if not words:
            continue
        nxt = timed[i + 1][0] if i + 1 < len(timed) else (song_end_ms or start + LAST_LINE_MAX_MS)
        span = max(len(words) * 50, min(nxt - start, len(words) * MS_PER_WORD + LINE_PAD_MS))
        total = sum(len(w) for w in words) or 1
        pos = float(start)
        line = Line()
        for w in words:
            d = span * len(w) / total
            line.words.append(Word(int(pos), int(pos + d), w))
            pos += d
        lines.append(line)
    _fix_overlaps(lines)
    return lines


def _http_get(path: str, params: dict[str, Any]) -> httpx.Response:
    with httpx.Client(base_url=BASE_URL, timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        return c.get(path, params=params)


def find_synced(artist: str, title: str, duration_s: float | None = None,
                get: Getter = _http_get) -> dict[str, Any] | None:
    """Registro do LRCLIB com syncedLyrics para a música, ou None."""
    params: dict[str, Any] = {"artist_name": artist, "track_name": title}
    if duration_s:
        r = get("get", {**params, "duration": int(round(duration_s))})
        if r.status_code == 200 and (rec := r.json()).get("syncedLyrics"):
            return rec
        if r.status_code not in (200, 404):
            r.raise_for_status()
    r = get("search", params)
    r.raise_for_status()
    want_artist, want_title = _fold(artist), _fold(title)
    best = None
    for rec in r.json() or []:
        if rec.get("instrumental") or not rec.get("syncedLyrics"):
            continue
        if _fold(rec.get("trackName") or "") != want_title:
            continue
        got_artist = _fold(rec.get("artistName") or "")
        if want_artist not in got_artist and got_artist not in want_artist:
            continue
        if duration_s and rec.get("duration") and abs(rec["duration"] - duration_s) > DURATION_TOLERANCE_S:
            continue  # outra versão (ao vivo, remix…): o tempo não bateria com o áudio
        best = rec
        break
    log.info("lrclib_lookup", extra={"fields": {"artist": artist, "title": title, "found": bool(best)}})
    return best
