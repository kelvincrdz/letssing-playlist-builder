"""Cliente mínimo do USDB (https://usdb.animux.de), seguindo o usdb_syncer.

Sem API oficial: login por formulário, lista de músicas por POST e .txt pela
página `gettxt`. Busca e download exigem conta (gratuita).
"""

from __future__ import annotations

import html
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Callable

import httpx

from letssing.sources.models import SearchResult
from letssing.ultrastar.txt import UltraStarSong, parse_bytes, parse_meta_tags

log = logging.getLogger("letssing.usdb")

BASE_URL = "https://usdb.animux.de/"
COVER_URL = BASE_URL + "data/cover/{id}.jpg"
DETAIL_URL = BASE_URL + "?link=detail&id={id}"
NOT_LOGGED_IN = "You are not logged in. Login to use this function."
LOGIN_INVALID = "Login or Password invalid, please try again."
DATASET_NOT_FOUND = "Datensatz nicht gefunden"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LetsSingPlaylistBuilder/0.1"

# Mesma estrutura de linha que o usdb_syncer espera (usdb_scraper.py)
SONG_LIST_ROW_REGEX = re.compile(
    r'<tr class="list_tr\d"\s+data-songid="(?P<song_id>\d+)"\s+'
    r'data-lastchange="(?P<lastchange>\d+)"[^>]*?>\s*'
    r'<td(?:.*?<source src="(?P<sample_url>.*?)".*?)?></td>'
    r'<td[^>]*?><img src="(?P<cover_url>.*?)".*?></td>'
    r"<td[^>]*?>(?P<artist>.*?)</td>\n"
    r"<td[^>]*?><a href=.*?>(?P<title>.*?)</td>\n"
    r"<td[^>]*?>(?P<genre>.*?)</td>\n"
    r"<td[^>]*?>(?P<year>.*?)</td>\n"
    r"<td[^>]*?>(?P<edition>.*?)</td>\n"
    r"<td[^>]*?>(?P<golden_notes>.*?)</td>\n"
    r"<td[^>]*?>(?P<language>.*?)</td>\n"
    r"<td[^>]*?>(?P<creator>.*?)</td>\n"
    r"<td[^>]*?>(?P<rating>.*?)</td>\n"
    r"<td[^>]*?>(?P<views>.*?)</td>"
)
_TEXTAREA = re.compile(r"<textarea[^>]*>(.*?)</textarea>", re.DOTALL | re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")

Credentials = Callable[[], tuple[str, str | None]]  # (usuário, senha)


class UsdbError(Exception):
    pass


class UsdbLoginRequired(UsdbError):
    pass


@dataclass
class UsdbSong:
    song_id: str
    artist: str
    title: str
    language: str
    edition: str
    year: str
    cover_url: str
    rating: str
    views: str


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", s)).split())


def parse_song_list(page: str) -> list[UsdbSong]:
    out = []
    for m in SONG_LIST_ROW_REGEX.finditer(page):
        cover = html.unescape(m["cover_url"] or "")
        if cover and not cover.startswith("http"):
            cover = BASE_URL + cover.lstrip("/")
        out.append(UsdbSong(
            song_id=m["song_id"],
            artist=_clean(m["artist"]),
            title=_clean(m["title"]),
            language=_clean(m["language"]),
            edition=_clean(m["edition"]),
            year=_clean(m["year"]),
            cover_url=cover,
            rating=_clean(m["rating"]),
            views=_clean(m["views"]),
        ))
    return out


def parse_txt_page(page: str) -> str:
    m = _TEXTAREA.search(page)
    if not m:
        raise UsdbError("Página do USDB sem o .txt (layout mudou?)")
    return html.unescape(m.group(1))


def _fold(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w]+", " ", s.casefold()).strip()


def query_variants(query: str, max_splits: int = 4) -> list[dict[str, str]]:
    """Combinações de (artista, título) a tentar para uma busca de texto livre."""
    words = query.split()
    variants: list[dict[str, str]] = [{"interpret": query, "title": ""}, {"interpret": "", "title": query}]
    for k in range(1, min(len(words), max_splits + 1)):
        variants.append({"interpret": " ".join(words[:k]), "title": " ".join(words[k:])})
    seen, unique = set(), []
    for v in variants:
        key = (v["interpret"], v["title"])
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique


def score(song: UsdbSong, query: str) -> float:
    q = set(_fold(query).split())
    if not q:
        return 0.0
    hay = set(_fold(f"{song.artist} {song.title}").split())
    return len(q & hay) / len(q)


class UsdbClient:
    def __init__(self, credentials: Credentials, transport: httpx.BaseTransport | None = None) -> None:
        self._credentials = credentials
        self._client = httpx.Client(
            base_url=BASE_URL, timeout=20, follow_redirects=True,
            headers={"User-Agent": USER_AGENT}, transport=transport,
        )
        self._lock = threading.Lock()
        self._logged_in = False

    # ---- sessão ----
    def login(self) -> None:
        user, password = self._credentials()
        if not user or not password:
            raise UsdbLoginRequired("Configure usuário e senha do USDB em Configurações")
        r = self._client.post("", data={"user": user, "pass": password, "login": "Login"})
        r.raise_for_status()
        if LOGIN_INVALID in r.text:
            self._logged_in = False
            raise UsdbLoginRequired("Usuário ou senha do USDB inválidos")
        self._logged_in = True
        log.info("usdb_login_ok", extra={"fields": {"user": user}})

    def check_login(self) -> str:
        """Faz login e devolve o usuário (para o botão 'Testar login')."""
        with self._lock:
            self._logged_in = False
            self._client.cookies.clear()
            self.login()
        return self._credentials()[0]

    def _page(self, method: str, url: str, **kw) -> str:
        with self._lock:
            if not self._logged_in:
                self.login()
            for attempt in (1, 2):
                r = self._client.request(method, url, **kw)
                r.raise_for_status()
                r.encoding = "utf-8"
                text = r.text
                if NOT_LOGGED_IN in text and attempt == 1:
                    self.login()  # sessão expirou
                    continue
                if NOT_LOGGED_IN in text:
                    raise UsdbLoginRequired("O USDB recusou a sessão; confira usuário e senha")
                if DATASET_NOT_FOUND in text:
                    raise UsdbError("Música não encontrada no USDB")
                return text
        raise UsdbError("falha inesperada")

    # ---- operações ----
    def list_songs(self, interpret: str = "", title: str = "", limit: int = 30) -> list[UsdbSong]:
        payload = {"interpret": interpret, "title": title, "details": "1", "order": "rating",
                   "ud": "desc", "limit": str(limit), "start": "0"}
        page = self._page("POST", "index.php", params={"link": "list"}, data=payload)
        songs = parse_song_list(page)
        if not songs and "data-songid" in page:
            raise UsdbError("Não consegui ler a lista do USDB (o layout do site mudou?)")
        return songs

    def search(self, query: str, limit: int = 15) -> list[SearchResult]:
        query = " ".join(query.split())
        if not query:
            return []
        found: dict[str, UsdbSong] = {}
        for v in query_variants(query):
            for s in self.list_songs(**v, limit=max(30, limit)):
                found.setdefault(s.song_id, s)
            if sum(1 for s in found.values() if score(s, query) == 1.0) >= limit:
                break
        ranked = sorted(found.values(), key=lambda s: (score(s, query), _int(s.views)), reverse=True)
        ranked = [s for s in ranked if score(s, query) >= 0.5][:limit]
        log.info("usdb_search", extra={"fields": {"query": query, "results": len(ranked)}})
        return [
            SearchResult(
                source="usdb",
                source_id=s.song_id,
                artist=s.artist,
                title=s.title,
                language=s.language,
                has_txt=True,
                has_video=None,
                url=DETAIL_URL.format(id=s.song_id),
                thumbnail=s.cover_url or COVER_URL.format(id=s.song_id),
                extra=" · ".join(x for x in (s.edition, s.year, f"nota {s.rating}" if s.rating else "",
                                              f"{s.views} views" if s.views else "") if x),
            )
            for s in ranked
        ]

    def get_txt(self, song_id: str) -> tuple[UltraStarSong, bytes]:
        page = self._page("POST", "index.php", params={"link": "gettxt", "id": str(int(song_id))},
                          data={"wd": "1"})
        raw = parse_txt_page(page).encode("utf-8")
        song, _ = parse_bytes(raw)
        if song.problems():
            raise UsdbError("TXT do USDB inválido: " + "; ".join(song.problems()))
        return song, raw

    @staticmethod
    def media_from_txt(song: UltraStarSong, song_id: str) -> dict[str, str]:
        """Links de mídia que o USDB registra na tag #VIDEO (a=, v=, co=)."""
        tags = parse_meta_tags(song.get("VIDEO"))
        audio = tags.audio or tags.video
        return {
            "audio_url": audio,
            "video_url": tags.video or audio,
            "cover_url": tags.cover or COVER_URL.format(id=song_id),
        }


def _int(s: str) -> int:
    try:
        return int(re.sub(r"\D", "", s) or 0)
    except ValueError:
        return 0


# ---- procurar uma música da playlist no USDB (ideia do usdx_scraper) ----
# "Tempo Perdido - Ao Vivo", "Malandragem (Acústico MTV)" -> título-base para comparar
_VERSION_SUFFIX = re.compile(r"\s*(\([^)]*\)|\[[^\]]*\]|\s-\s.*)$")


def strip_version(title: str) -> str:
    """Título sem os sufixos de versão, com acentos (para buscar)."""
    t = title.strip()
    while True:
        new = _VERSION_SUFFIX.sub("", t).strip()
        if new == t or not new:
            return t
        t = new


def base_title(title: str) -> str:
    """Título-base normalizado (para comparar)."""
    return _fold(strip_version(title))


def _artist_tokens(artist: str) -> set[str]:
    return {w for w in _fold(artist).split() if len(w) >= 2}


def find_match(search: Callable[..., list[SearchResult]], artist: str, title: str) -> SearchResult | None:
    """Melhor resultado do USDB com o mesmo título-base e artista em comum.
    A busca já vem ordenada por relevância e visualizações."""
    want_title, want_artist = base_title(title), _artist_tokens(artist)
    for r in search(f"{artist} {strip_version(title)}", 10):
        if base_title(r.title) != want_title:
            continue
        if want_artist and not (want_artist & _artist_tokens(r.artist)):
            continue
        return r
    return None
