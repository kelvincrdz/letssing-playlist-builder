import json
import time

import pytest
from fastapi.testclient import TestClient

from letssing.api.app import create_app
from letssing.pipeline.media import MediaPlan
from letssing.sources.models import SearchResult


def fake_ensure(item, settings, ws, progress=None):
    if item.title == "Quebra":
        raise RuntimeError("Video unavailable")
    if progress:
        progress("audio", 0.5)
    folder = ws.input / item.folder_name
    folder.mkdir(parents=True, exist_ok=True)
    for ext in (".mp3", ".jpg"):
        (folder / f"{item.folder_name}{ext}").write_bytes(b"x")
    plan = MediaPlan(audio_url="https://www.youtube.com/watch?v=zzz", video_url="", cover_url="",
                     download_audio=True, download_cover=True, download_video=False, park_video=False)
    return plan, {}


@pytest.fixture
def app_client(repo, config, tmp_path):
    from letssing.config import Settings

    config.save(Settings(workspace_dir=str(tmp_path / "ws")))
    sources = {
        "youtube": lambda q, limit: [SearchResult(source="youtube", source_id="1", artist="Legião Urbana",
                                                  title="Tempo Perdido", has_video=True)],
        "quebrada": lambda q, limit: (_ for _ in ()).throw(RuntimeError("fora do ar")),
        "muitos": lambda q, limit: [SearchResult(source="muitos", source_id=str(i), artist="A", title=f"M{i}")
                                    for i in range(min(limit, 75))],
    }
    app = create_app(config=config, playlist=repo, search_sources=sources, ensure_media=fake_ensure)
    return TestClient(app)


def wait_job(client, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job não terminou")


def test_search_groups_and_isolates_errors(app_client):
    app_client.post("/api/playlist", json={"artist": "Legiao Urbana", "title": "tempo perdido"})
    r = app_client.get("/api/search", params={"q": "legiao"}).json()
    assert r["youtube"]["error"] is None
    assert r["youtube"]["results"][0]["in_playlist"] is not None
    assert r["quebrada"]["results"] == [] and "fora do ar" in r["quebrada"]["error"]
    assert r["youtube"]["more"] is False


def test_search_load_more_pages_one_source(app_client):
    first = app_client.get("/api/search", params={"q": "x"}).json()["muitos"]
    assert [x["source_id"] for x in first["results"]] == [str(i) for i in range(30)] and first["more"]
    r = app_client.get("/api/search", params={"q": "x", "source": "muitos", "offset": 60}).json()
    assert list(r) == ["muitos"]  # só a fonte pedida
    assert [x["source_id"] for x in r["muitos"]["results"]] == [str(i) for i in range(60, 75)]
    assert r["muitos"]["more"] is False
    assert app_client.get("/api/search", params={"q": "x", "source": "nada"}).status_code == 404


def test_youtube_playlist_endpoint(app_client, monkeypatch):
    from letssing.sources import youtube

    def fake(url, cookies=None):
        youtube.playlist_id(url)
        return "Minha lista", [SearchResult(source="youtube", source_id="v1", artist="Legião Urbana",
                                            title="Tempo Perdido")]
    monkeypatch.setattr(youtube, "playlist_entries", fake)
    app_client.post("/api/playlist", json={"artist": "Legiao Urbana", "title": "tempo perdido"})
    r = app_client.get("/api/youtube/playlist", params={"url": "https://www.youtube.com/playlist?list=PLabcdefghij12"})
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Minha lista" and body["results"][0]["in_playlist"] is not None
    bad = app_client.get("/api/youtube/playlist", params={"url": "https://www.youtube.com/watch?v=abc"})
    assert bad.status_code == 400 and "list=" in bad.json()["detail"]


def test_generate_continues_after_failure(app_client):
    for t in ("Ok", "Quebra"):
        app_client.post("/api/playlist", json={"artist": "A", "title": t})
    txt = "#TITLE:ComTxt\n#ARTIST:A\n#BPM:120\n: 0 1 60 la\nE\n"
    assert app_client.post("/api/import", params={"filename": "c.txt"}, content=txt.encode()).status_code == 201
    job = app_client.post("/api/jobs/generate", json={}).json()
    final = wait_job(app_client, job["id"])
    assert final["state"] == "done" and final["done"] == 3

    by_title = {i["title"]: i for i in app_client.get("/api/playlist").json()}
    assert by_title["Ok"]["status"] == "pendente"  # mídia ok, falta .txt
    assert "falta txt" in by_title["Ok"]["error_message"]
    assert by_title["Ok"]["audio_url"].endswith("zzz")  # link encontrado foi salvo
    assert by_title["Quebra"]["status"] == "erro"
    assert "Video unavailable" in by_title["Quebra"]["error_message"]
    # pronta, mas a conversão foi bloqueada (sem TitleID/RAD/conversor configurados)
    assert by_title["ComTxt"]["status"] == "pendente"
    assert "conversão bloqueada" in by_title["ComTxt"]["error_message"]


def test_generate_full_with_fake_converter(repo, config, tmp_path):
    import sys
    from pathlib import Path

    from letssing.config import Settings
    from letssing.converter import singit

    for f in ("ffmpeg.exe", "rad.exe", "conv.exe"):
        (tmp_path / f).write_bytes(b"x")
    config.save(Settings(workspace_dir=str(tmp_path / "ws"), dlc_title_id="0100EE4020D19003",
                         ffmpeg_path=str(tmp_path / "ffmpeg.exe"), rad_path=str(tmp_path / "rad.exe"),
                         converter_path=str(tmp_path / "conv.exe")))
    fake = Path(__file__).with_name("fake_converter.py")

    def prepare(settings, ws):
        tool = singit.prepare_tool(settings, ws)
        return singit.ConverterTool(workdir=tool.workdir, command=[sys.executable, str(fake)])

    app = create_app(config=config, playlist=repo, search_sources={}, ensure_media=fake_ensure,
                     prepare_tool=prepare)
    c = TestClient(app)
    for title in ("Boa", "Falha"):
        txt = f"#TITLE:{title}\n#ARTIST:A\n#BPM:120\n: 0 1 60 la\nE\n"
        assert c.post("/api/import", params={"filename": "t.txt"}, content=txt.encode()).status_code == 201
    job = c.post("/api/jobs/generate", json={}).json()
    final = wait_job(c, job["id"], timeout=30)
    assert final["state"] == "done"

    by_title = {i["title"]: i for i in c.get("/api/playlist").json()}
    assert by_title["Boa"]["status"] == "pronto"
    assert by_title["Falha"]["status"] == "erro"
    assert "simulated failure" in by_title["Falha"]["error_message"]

    info = c.get("/api/jobs/dlc/latest").json()
    assert info["name"].startswith("letssing-dlc-")
    r = c.get("/api/jobs/dlc/latest.zip")
    assert r.status_code == 200 and r.content[:2] == b"PK"

    events = c.get(f"/api/jobs/{job['id']}/events").text
    assert '"zip": "letssing-dlc-' in events


def test_sse_stream_delivers_events(app_client):
    app_client.post("/api/playlist", json={"artist": "A", "title": "Ok"})
    job = app_client.post("/api/jobs/generate", json={}).json()
    wait_job(app_client, job["id"])
    with app_client.stream("GET", f"/api/jobs/{job['id']}/events") as r:
        body = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "state" and types[-1] == "state"
    assert "item" in types and "progress" in types and "summary" in types
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


def test_generate_empty_playlist(app_client):
    assert app_client.post("/api/jobs/generate", json={}).status_code == 400


def test_my_youtube_playlists_endpoint(app_client):
    st = app_client.app.state
    assert app_client.get("/api/youtube/my-playlists").status_code == 400  # sem login configurado
    st.youtube_cookies = lambda browser, path: ["cookie"]
    st.youtube_my_playlists = lambda cookies: [{"id": "LL", "title": "Vídeos curtidos", "count": None}]
    r = app_client.get("/api/youtube/my-playlists")
    assert r.status_code == 200 and r.json()["playlists"][0]["id"] == "LL"


def test_public_playlist_works_when_login_fails(app_client, monkeypatch):
    from letssing.sources import youtube

    def broken(browser, path):
        raise youtube.YoutubeLoginError("Chrome travado")
    app_client.app.state.youtube_cookies = broken
    got = {}

    def fake(url, cookies=None):
        got["cookies"] = cookies
        return "Pública", []
    monkeypatch.setattr(youtube, "playlist_entries", fake)
    r = app_client.get("/api/youtube/playlist", params={"url": "https://www.youtube.com/playlist?list=PLabcdefghij12"})
    assert r.status_code == 200 and got["cookies"] is None and "Chrome travado" in r.json()["warning"]
    r = app_client.get("/api/youtube/playlist", params={"url": "https://www.youtube.com/playlist?list=LL"})
    assert r.status_code == 400 and "Chrome travado" in r.json()["detail"]
