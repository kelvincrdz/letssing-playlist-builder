"""Cliente do Spotify contra um servidor falso (httpx.MockTransport)."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from letssing.config import MemoryStore
from letssing.sources import spotify
from letssing.sources.spotify import REFRESH_KEY, SpotifyAuthRequired, SpotifyClient, SpotifyError


def track(i, name="Love Story - Taylor's Version", ms=235000):
    return {"type": "track", "id": f"t{i}", "name": name, "duration_ms": ms, "is_local": False,
            "artists": [{"name": "Taylor Swift"}], "external_urls": {"spotify": f"https://open.spotify.com/track/t{i}"},
            "album": {"name": "Fearless", "release_date": "2021-04-09", "images": [{"url": "big"}, {"url": "small"}]}}


class FakeSpotify:
    def __init__(self, n_tracks=60):
        self.n = n_tracks
        self.tokens = 0
        self.token_bodies = []
        self.expire_next = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url == spotify.TOKEN_URL:
            body = parse_qs(req.content.decode())
            self.token_bodies.append(body)
            if body["grant_type"] == ["refresh_token"] and body["refresh_token"] == ["revogado"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.tokens += 1
            out = {"access_token": f"at{self.tokens}", "token_type": "Bearer", "expires_in": 3600}
            if body["grant_type"] == ["authorization_code"]:
                out["refresh_token"] = "rt1"
            return httpx.Response(200, json=out)
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401, json={"error": {"status": 401}})
        path = req.url.path.removeprefix("/v1/")
        q = dict(req.url.params)
        if path == "me":
            return httpx.Response(200, json={"id": "kel", "display_name": "Kelvin"})
        if path == "me/playlists":
            return httpx.Response(200, json={"items": [
                {"id": "p1", "name": "Minha", "owner": {"id": "kel"}, "items": {"total": 3}, "images": []},
                {"id": "p2", "name": "Top Brasil", "owner": {"id": "spotify"}, "tracks": {"total": 50}, "images": []},
            ], "next": None, "total": 2})
        if path == "playlists/p2xxxxxxxxxx/items":
            return httpx.Response(403, json={"error": {"status": 403}})
        if path in ("playlists/p1aaaaaaaaaa/items", "me/tracks"):
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 20))
            key = "item" if path.startswith("playlists") else "track"
            items = [{key: track(i)} for i in range(off, min(off + lim, self.n))]
            if off == 0:
                items.append({key: {"type": "episode", "name": "podcast"}})
            nxt = f"{spotify.API_URL}{path}?offset={off + lim}&limit={lim}" if off + lim < self.n else None
            return httpx.Response(200, json={"items": items, "next": nxt, "total": self.n})
        return httpx.Response(404)


def make(fake, cid="abc123", store=None):
    store = store or MemoryStore()
    return SpotifyClient(lambda: cid, store, transport=httpx.MockTransport(fake)), store


def login(client):
    url = client.authorize_url(8000)
    q = parse_qs(urlparse(url).query)
    client.finish_login("code1", q["state"][0])
    return q


def test_authorize_url_uses_pkce_and_loopback():
    c, _ = make(FakeSpotify())
    q = parse_qs(urlparse(c.authorize_url(8000)).query)
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/spotify/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["abc123"]
    assert "user-library-read" in q["scope"][0]


def test_login_stores_refresh_token_without_secret():
    fake = FakeSpotify()
    c, store = make(fake)
    q = login(c)
    body = fake.token_bodies[0]
    assert body["code_verifier"][0] and "client_secret" not in body
    assert body["redirect_uri"] == q["redirect_uri"]
    assert store.get(REFRESH_KEY) == "rt1" and c.connected()


def test_bad_state_rejected():
    c, _ = make(FakeSpotify())
    c.authorize_url(8000)
    with pytest.raises(SpotifyError, match="expirado"):
        c.finish_login("code1", "forjado")


def test_missing_client_id():
    c, _ = make(FakeSpotify(), cid="")
    with pytest.raises(SpotifyAuthRequired, match="Client ID"):
        c.authorize_url(8000)


def test_saved_tracks_paginates_and_cleans():
    c, _ = make(FakeSpotify(n_tracks=60))
    login(c)
    tracks = c.saved_tracks()
    assert len(tracks) == 60  # 50 + 10, sem o episódio de podcast
    t = tracks[0]
    assert (t.artist, t.title, t.duration_s, t.thumbnail) == ("Taylor Swift", "Love Story - Taylor's Version", 235, "big")
    assert t.source == "spotify" and not t.has_txt and t.audio_url == ""


def test_playlists_and_readable_flag():
    c, _ = make(FakeSpotify())
    login(c)
    pls = c.playlists()
    assert [(p["name"], p["total"], p["readable"]) for p in pls] == [("Minha", 3, True), ("Top Brasil", 50, False)]


def test_playlist_items_403_explained():
    c, _ = make(FakeSpotify())
    login(c)
    with pytest.raises(SpotifyError, match="Users Management"):
        c.playlist_tracks("p2xxxxxxxxxx")


def test_refresh_after_401_and_revoked_token():
    fake = FakeSpotify()
    c, store = make(fake)
    login(c)
    fake.expire_next = True
    assert c.me()["name"] == "Kelvin"
    assert fake.token_bodies[-1]["grant_type"] == ["refresh_token"]
    store.set(REFRESH_KEY, "revogado")
    c._access = None
    with pytest.raises(SpotifyAuthRequired, match="expirou"):
        c.me()
    assert not c.connected()


def test_clean_title():
    assert spotify.clean_title("Here Comes the Sun - Remastered 2009") == "Here Comes the Sun"
    assert spotify.clean_title("Help! - 2015 Remaster") == "Help!"
    assert spotify.clean_title("Love Story (Taylor's Version)") == "Love Story (Taylor's Version)"


def test_api_status_and_callback(client):
    st = client.get("/api/spotify/status").json()
    assert st == {"configured": False, "connected": False, "user": None, "error": None,
                  "redirect_uri": "http://127.0.0.1:8000/api/spotify/callback"}
    r = client.get("/api/spotify/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert r.status_code == 302 and "spotify_error=" in r.headers["location"]
    assert client.get("/api/spotify/saved").status_code == 401


def test_api_login_redirects_to_spotify(client):
    client.put("/api/config", json={"spotify_client_id": "abc123"})
    r = client.get("/api/spotify/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(spotify.AUTHORIZE_URL) and "client_id=abc123" in loc
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A" in loc
