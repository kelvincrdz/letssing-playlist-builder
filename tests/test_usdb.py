"""Cliente USDB contra um servidor falso (httpx.MockTransport)."""

from urllib.parse import parse_qs

import httpx
import pytest

from letssing.sources.usdb import (
    NOT_LOGGED_IN,
    LOGIN_INVALID,
    UsdbClient,
    UsdbLoginRequired,
    parse_song_list,
    query_variants,
)


def row(song_id, artist, title, lang="Portuguese", views="100"):
    return (
        f'<tr class="list_tr1" data-songid="{song_id}" data-lastchange="1700000000" onclick="x">\n'
        f'<td></td><td class="list_td"><img src="data/cover/{song_id}.jpg" alt=""></td>'
        f"<td>{artist}</td>\n"
        f'<td><a href="?link=detail&id={song_id}">{title}</a></td>\n'
        f"<td>Rock</td>\n<td>1986</td>\n<td>Dois</td>\n<td>Yes</td>\n<td>{lang}</td>\n"
        f"<td>fulano</td>\n<td>4</td>\n<td>{views}</td>"
    )


TXT = "#TITLE:Tempo Perdido\n#ARTIST:Legião Urbana\n#BPM:280\n#GAP:100\n#VIDEO:a=M4uG1MmZVl0,co=1-c.jpg\n: 0 4 60 To\nE"
DB = [
    ("101", "Legião Urbana", "Tempo Perdido", "900"),
    ("102", "Legião Urbana", "Pais e Filhos", "800"),
    ("103", "Outro", "Tempo Perdido (cover)", "5"),
]


class FakeUsdb:
    def __init__(self, user="kel", password="ok"):
        self.user, self.password = user, password
        self.sessions: set[str] = set()
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        form = parse_qs(request.content.decode()) if request.content else {}
        if "login" in form:
            if form.get("user") == [self.user] and form.get("pass") == [self.password]:
                return httpx.Response(200, text="Welcome kel", headers={"set-cookie": "PHPSESSID=abc; Path=/"})
            return httpx.Response(200, text=LOGIN_INVALID)
        if "PHPSESSID=abc" not in request.headers.get("cookie", ""):
            return httpx.Response(200, text=NOT_LOGGED_IN)
        link = request.url.params.get("link")
        if link == "list":
            artist = (form.get("interpret") or [""])[0].lower()
            title = (form.get("title") or [""])[0].lower()
            rows = [row(i, a, t, views=v) for i, a, t, v in DB
                    if artist in a.lower() and title in t.lower()]
            return httpx.Response(200, text="<table>" + "\n".join(rows) + "</table>")
        if link == "gettxt":
            return httpx.Response(200, text=f"<form><textarea name='txt'>{TXT.replace('&', '&amp;')}</textarea></form>")
        return httpx.Response(404)


def client(fake, user="kel", password="ok"):
    return UsdbClient(lambda: (user, password), transport=httpx.MockTransport(fake))


def test_parse_song_list():
    songs = parse_song_list("\n".join(row(i, a, t) for i, a, t, _ in DB))
    assert [s.song_id for s in songs] == ["101", "102", "103"]
    assert songs[0].artist == "Legião Urbana" and songs[0].title == "Tempo Perdido"
    assert songs[0].cover_url == "https://usdb.animux.de/data/cover/101.jpg"


def test_query_variants():
    v = query_variants("Legião Urbana Tempo Perdido")
    assert {"interpret": "Legião Urbana", "title": "Tempo Perdido"} in v
    assert v[0] == {"interpret": "Legião Urbana Tempo Perdido", "title": ""}


def test_search_logs_in_and_ranks():
    fake = FakeUsdb()
    results = client(fake).search("Legião Urbana Tempo Perdido")
    assert results[0].source_id == "101"
    assert results[0].has_txt and results[0].has_video is None
    assert all(r.source_id != "102" for r in results)  # 'Pais e Filhos' não bate
    assert sum(1 for r in fake.requests if b"login" in (r.content or b"")) == 1


def test_missing_credentials():
    with pytest.raises(UsdbLoginRequired, match="Configure"):
        client(FakeUsdb(), password=None).search("x")


def test_invalid_credentials():
    with pytest.raises(UsdbLoginRequired, match="inválidos"):
        client(FakeUsdb(), password="errada").search("x")


def test_session_expired_relogs():
    fake = FakeUsdb()
    c = client(fake)
    c.search("Tempo")
    c._client.cookies.clear()  # sessão expirou no servidor
    assert c.search("Tempo")
    assert sum(1 for r in fake.requests if b"login" in (r.content or b"")) == 2


def test_get_txt_and_media_links():
    c = client(FakeUsdb())
    song, raw = c.get_txt("101")
    assert song.artist == "Legião Urbana"
    assert raw.decode("utf-8").startswith("#TITLE")
    links = c.media_from_txt(song, "101")
    assert links["audio_url"].endswith("M4uG1MmZVl0")
    assert links["video_url"] == links["audio_url"]  # sem v=, usa o áudio
    assert links["cover_url"] == "https://assets.fanart.tv/fanart/1-c.jpg"


def test_check_login():
    assert client(FakeUsdb()).check_login() == "kel"


def _res(i, artist, title):
    from letssing.sources.models import SearchResult
    return SearchResult(source="usdb", source_id=str(i), artist=artist, title=title, has_txt=True)


def test_find_match_ignores_version_suffix_and_accents():
    from letssing.sources.usdb import find_match
    asked = []

    def search(q, limit):
        asked.append(q)
        return [_res(1, "Outro", "Malandragem"), _res(2, "Cassia Eller", "Malandragem"),
                _res(3, "Cássia Eller", "Malandragem (Ao Vivo)")]
    m = find_match(search, "Cássia Eller", "Malandragem (Acústico MTV)")
    assert m.source_id == "2" and asked == ["Cássia Eller Malandragem"]


def test_find_match_none():
    from letssing.sources.usdb import find_match
    assert find_match(lambda q, n: [_res(1, "Legião Urbana", "Pais e Filhos")], "Legião Urbana", "Tempo Perdido") is None


def test_api_usdb_match_switches_item(client, repo):
    from letssing.playlist import ItemCreate, TxtSource
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido - Ao Vivo",
                             audio_url="https://www.youtube.com/watch?v=meuvideo123"))
    client.app.state.usdb = type("U", (), {"search": staticmethod(
        lambda q, n: [_res(101, "Legião Urbana", "Tempo Perdido")])})()
    r = client.post(f"/api/playlist/{it.id}/usdb-match").json()
    assert r["matched"] and r["item"]["txt_source"] == TxtSource.USDB and r["item"]["txt_ref"] == "101"
    assert r["item"]["audio_url"] == ""  # passa a usar o áudio indicado pelo USDB
    assert client.post(f"/api/playlist/{it.id}/usdb-match").json()["already"]


def test_api_usdb_match_needs_login(client, repo):
    from letssing.playlist import ItemCreate
    it = repo.add(ItemCreate(artist="A", title="B"))
    r = client.post(f"/api/playlist/{it.id}/usdb-match")
    assert r.status_code == 401 and "Configure" in r.json()["detail"]
