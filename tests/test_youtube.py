import pytest

from letssing.sources import youtube


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PLxA687tYuMWgeHrs69WUDoU0PZxYdEzew",
    "https://www.youtube.com/watch?v=ko70cExuzZM&list=PLxA687tYuMWgeHrs69WUDoU0PZxYdEzew&index=2",
    "https://music.youtube.com/playlist?list=PLxA687tYuMWgeHrs69WUDoU0PZxYdEzew",
])
def test_playlist_id(url):
    assert youtube.playlist_id(url) == "PLxA687tYuMWgeHrs69WUDoU0PZxYdEzew"


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=ko70cExuzZM",
    "https://evil.example/playlist?list=PLxA687tYuMWgeHrs69WUDoU0PZxYdEzew",
    "file:///c:/x?list=PLxA687tYuMWgeHrs69WUDoU0PZxYdEzew",
    "nem é link",
])
def test_playlist_id_rejects(url):
    with pytest.raises(ValueError):
        youtube.playlist_id(url)


def test_entry_result_from_playlist_entry():
    r = youtube._entry_result({"id": "ko70cExuzZM", "title": "Taylor Swift - The Fate of Ophelia (Official Music Video)",
                              "channel": "Taylor Swift", "duration": 239.0,
                              "thumbnails": [{"url": "a"}, {"url": "b"}]})
    assert (r.artist, r.title) == ("Taylor Swift", "The Fate of Ophelia")
    assert r.audio_url == r.video_url == "https://www.youtube.com/watch?v=ko70cExuzZM"
    assert r.thumbnail == "b" and r.duration_s == 239.0
    assert youtube._entry_result({"title": "sem id"}) is None


COOKIES_TXT = """# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tabc
.youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\txyz
.google.com\tTRUE\t/\tTRUE\t2147483647\tSID\tnao-usar
.banco.example\tTRUE\t/\tTRUE\t2147483647\tsessao\tsegredo
"""


def test_cookies_file_keeps_only_youtube(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(COOKIES_TXT, encoding="utf-8")
    before = f.read_bytes()
    cookies = youtube.load_youtube_cookies(cookies_file=str(f))
    assert sorted(c.name for c in cookies) == ["LOGIN_INFO", "SAPISID"]
    ydl = youtube._ydl({"quiet": True}, cookies)
    assert sorted(c.name for c in ydl.cookiejar) == ["LOGIN_INFO", "SAPISID"]
    ydl.close()
    assert f.read_bytes() == before  # nunca regravado


def test_cookies_without_login(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(COOKIES_TXT.replace("SAPISID", "PREF"), encoding="utf-8")
    with pytest.raises(youtube.YoutubeLoginError, match="Não achei login"):
        youtube.load_youtube_cookies(cookies_file=str(f))


def test_cookies_not_configured_and_bad_file(tmp_path):
    assert youtube.load_youtube_cookies() is None
    with pytest.raises(youtube.YoutubeLoginError, match="cookies.txt"):
        youtube.load_youtube_cookies(cookies_file=str(tmp_path / "nao-existe.txt"))


def test_special_playlists_need_login():
    assert youtube.playlist_id("https://www.youtube.com/playlist?list=LL") == "LL"
    with pytest.raises(youtube.YoutubeLoginError, match="login"):
        youtube.playlist_entries("https://www.youtube.com/playlist?list=WL")


def test_my_playlists_parses_feed(monkeypatch):
    class FakeYDL:
        def __init__(self, opts): self.cookiejar = __import__("http.cookiejar").cookiejar.CookieJar()
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download):
            assert url == "https://www.youtube.com/feed/playlists"
            return {"entries": [
                {"id": "PLminha1234567", "url": "https://www.youtube.com/playlist?list=PLminha1234567", "title": "Karaokê"},
                {"id": "dQw4w9WgXcQ", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "vídeo solto"},
                {"id": "LL", "url": "https://www.youtube.com/playlist?list=LL", "title": "Liked"},
            ]}
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeYDL)
    got = youtube.my_playlists([])
    assert [p["id"] for p in got] == ["LL", "WL", "PLminha1234567"]
    assert got[2]["title"] == "Karaokê"


def test_download_retries_transient_403(monkeypatch):
    import yt_dlp
    calls, waits = [], []

    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download):
            calls.append(url)
            if len(calls) < 3:
                raise yt_dlp.utils.DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            return {"id": "x"}
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeYDL)
    assert youtube._run("u", {}, sleep=waits.append) == {"id": "x"}
    assert len(calls) == 3 and waits == [5, 10]


def test_download_does_not_retry_other_errors(monkeypatch):
    import yt_dlp

    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download):
            raise yt_dlp.utils.DownloadError("ERROR: Video unavailable")
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(yt_dlp.utils.DownloadError, match="unavailable"):
        youtube._run("u", {}, sleep=lambda s: None)


def test_friendly_strips_colors():
    from letssing.pipeline.runner import _friendly
    assert _friendly(RuntimeError("\x1b[0;31mERROR:\x1b[0m unable: HTTP Error 403")) == "unable: HTTP Error 403"
    assert _friendly(RuntimeError("[0;31mERROR:[0m unable")) == "unable"
