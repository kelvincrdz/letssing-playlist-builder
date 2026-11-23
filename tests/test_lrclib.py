import httpx
import pytest

from letssing.sources import lrclib

LRC = """[ar:Legião Urbana]
[00:20.32] Todos os dias quando acordo
[00:26.91] Não tenho mais o tempo que passou
[00:30.74]
[00:36.26][01:36.00] Temos todo o tempo do mundo
"""


def test_parse_lrc():
    got = lrclib.parse_lrc(LRC)
    assert got[0] == (20320, "Todos os dias quando acordo")
    assert (30740, "") in got  # pausa
    assert got[-1] == (96000, "Temos todo o tempo do mundo")  # carimbo repetido


def test_lines_from_lrc_word_times():
    lines = lrclib.lines_from_lrc(LRC, song_end_ms=100_000)
    assert [len(l.words) for l in lines] == [5, 7, 6, 6]
    first = lines[0].words
    assert first[0].start_ms == 20320 and first[0].text == "Todos"
    # linha de 5 palavras não ocupa os 6,6 s até a próxima: 5 x 650 + 400 ms
    assert first[-1].end_ms == pytest.approx(20320 + 5 * 650 + 400, abs=2)
    words = [w for l in lines for w in l.words]
    assert all(a.end_ms <= b.start_ms for a, b in zip(words, words[1:]))
    assert lines[-1].words[-1].end_ms <= 100_000


def _getter(routes):
    def get(path, params):
        status, body = routes[path](params)
        return httpx.Response(status, json=body, request=httpx.Request("GET", "https://lrclib.net/api/" + path))
    return get


REC = {"id": 1, "artistName": "Legião Urbana", "trackName": "Tempo Perdido", "duration": 302.0,
       "instrumental": False, "syncedLyrics": "[00:01.00] a"}


def test_find_synced_uses_get_with_duration():
    seen = []
    rec = lrclib.find_synced("Legião Urbana", "Tempo Perdido", 301.6, get=_getter({
        "get": lambda p: (seen.append(p) or (200, REC)),
    }))
    assert rec["id"] == 1 and seen[0]["duration"] == 302


def test_find_synced_search_filters_versions():
    live = {**REC, "id": 2, "duration": 237.0}
    plain = {**REC, "id": 3, "syncedLyrics": None}
    other = {**REC, "id": 4, "trackName": "Tempo Perdido (Ao Vivo)"}
    rec = lrclib.find_synced("Legiao Urbana", "Tempo Perdido", 302, get=_getter({
        "get": lambda p: (404, {"message": "not found"}),
        "search": lambda p: (200, [live, plain, other, {**REC, "id": 5}]),
    }))
    assert rec["id"] == 5  # acento ignorado; ao vivo, sem sincronia e outro título descartados


def test_find_synced_none():
    assert lrclib.find_synced("A", "B", get=_getter({"search": lambda p: (200, [])})) is None
