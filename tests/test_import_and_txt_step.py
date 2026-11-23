import io
import zipfile

import pytest

from letssing.paths import Workspace
from letssing.pipeline import txt_step
from letssing.playlist import ItemCreate, TxtSource
from letssing.sources.manual_import import ORIGINAL_TXT, ImportError_, install_package, read_package
from letssing.ultrastar.txt import parse

TXT = "#TITLE:Tempo Perdido\n#ARTIST:Legião Urbana\n#BPM:280\n#GAP:100\n#MP3:x.mp3\n: 0 4 60 To\nE\n"
TXT_OTHER = TXT.replace("Tempo Perdido", "Pais e Filhos")


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_read_plain_txt_cp1252():
    pkg = read_package("song.txt", TXT.encode("cp1252"))
    assert pkg.encoding == "cp1252" and pkg.song.artist == "Legião Urbana"


def test_read_invalid():
    with pytest.raises(ImportError_):
        read_package("song.txt", b"#TITLE:x\nE\n")
    with pytest.raises(ImportError_):
        read_package("song.rar", b"x")
    with pytest.raises(ImportError_):
        read_package("song.zip", b"not a zip")


def test_zip_picks_matching_txt_and_media():
    data = make_zip({
        "pack/Legiao - Pais.txt": TXT_OTHER.encode(),
        "pack/Legiao - Tempo.txt": TXT.encode(),
        "pack/song.mp3": b"a" * 10,
        "pack/cover.jpg": b"c",
        "pack/small.png": b"",
        "../../evil.ogg": b"e",  # caminho malicioso: só os bytes são lidos
    })
    pkg = read_package("pack.zip", data, "Legião Urbana", "Tempo Perdido")
    assert pkg.song.title == "Tempo Perdido"
    assert pkg.media["audio"][0] == "song.mp3"
    assert pkg.media["cover"][0] == "cover.jpg"  # maior imagem
    assert pkg.notes


def test_install_package(tmp_path):
    folder = tmp_path / "A - B"
    folder.mkdir()
    (folder / "A - B.mp3").write_bytes(b"youtube")
    data = make_zip({"x.txt": TXT.encode(), "y.ogg": b"zip audio"})
    install_package(read_package("x.zip", data), folder, "A - B")
    assert (folder / ORIGINAL_TXT).read_bytes() == TXT.encode()
    assert not (folder / "A - B.mp3").exists()  # substituído pelo do zip
    assert (folder / "A - B.ogg").read_bytes() == b"zip audio"
    assert not list(folder.glob("*.txt"))  # original não tem extensão .txt


class FakeUsdb:
    calls = 0

    def get_txt(self, song_id):
        FakeUsdb.calls += 1
        raw = (TXT.replace("#MP3:x.mp3", "#VIDEO:a=M4uG1MmZVl0,co=1.jpg")).encode()
        return parse(raw.decode()), raw

    @staticmethod
    def media_from_txt(song, song_id):
        from letssing.sources.usdb import UsdbClient
        return UsdbClient.media_from_txt(song, song_id)


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path / "ws")
    w.ensure()
    return w


def test_fetch_txt_from_usdb_fills_links_once(repo, ws):
    FakeUsdb.calls = 0
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido", txt_source=TxtSource.USDB, txt_ref="101"))
    it2, msg = txt_step.fetch_txt(it, ws, repo, FakeUsdb())
    assert it2.audio_url.endswith("M4uG1MmZVl0") and it2.video_url == it2.audio_url
    assert it2.cover_url == "https://assets.fanart.tv/fanart/1.jpg"
    assert "USDB" in msg
    txt_step.fetch_txt(it2, ws, repo, FakeUsdb())
    assert FakeUsdb.calls == 1  # cache


def test_fetch_txt_keeps_user_chosen_media(repo, ws):
    url = "https://www.youtube.com/watch?v=meuvideo123"
    it = repo.add(ItemCreate(artist="A", title="B", txt_source=TxtSource.USDB, txt_ref="1", audio_url=url))
    it, _ = txt_step.fetch_txt(it, ws, repo, FakeUsdb())
    assert it.audio_url == url


def test_fetch_txt_manual_requires_import(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", txt_source=TxtSource.ULTRASTAR_ES))
    with pytest.raises(RuntimeError, match="Importe"):
        txt_step.fetch_txt(it, ws)


def test_write_normalized_txt(repo, ws):
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido", gap_ms=250))
    folder = ws.input / it.folder_name
    folder.mkdir(parents=True)
    (folder / ORIGINAL_TXT).write_bytes(TXT.encode("cp1252"))
    for ext in (".mp3", ".jpg"):
        (folder / f"{it.folder_name}{ext}").write_bytes(b"x")
    (folder / "velho.txt").write_text("lixo")
    assert txt_step.write_normalized_txt(it, ws)
    out = (folder / f"{it.folder_name}.txt").read_bytes()
    text = out.decode("utf-8-sig")
    assert out.startswith(b"\xef\xbb\xbf")
    assert f"#MP3:{it.folder_name}.mp3" in text and f"#COVER:{it.folder_name}.jpg" in text
    assert "#GAP:250" in text and "#VIDEO" not in text and "Legião" in text
    assert not (folder / "velho.txt").exists()


def test_write_normalized_txt_without_original(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B"))
    assert txt_step.write_normalized_txt(it, ws) is False


# ---------------- API ----------------
def test_api_import_new_and_into_item(client, config, tmp_path):
    from letssing.config import Settings

    config.save(Settings(workspace_dir=str(tmp_path / "ws")))
    r = client.post("/api/import", params={"filename": "t.txt"}, content=TXT.encode())
    assert r.status_code == 201
    body = r.json()
    assert body["item"]["artist"] == "Legião Urbana" and body["item"]["txt_source"] == "ultrastar_es"
    assert (tmp_path / "ws" / "input" / "Legiao Urbana - Tempo Perdido" / ORIGINAL_TXT).exists()

    r = client.post("/api/import", params={"filename": "t.txt"}, content=TXT.encode())
    assert r.status_code == 409

    item = client.post("/api/playlist", json={"artist": "Legião Urbana", "title": "Pais e Filhos"}).json()
    r = client.post(f"/api/playlist/{item['id']}/txt", params={"filename": "p.zip"},
                    content=make_zip({"p.txt": TXT_OTHER.encode(), "p.mp3": b"a"}))
    assert r.status_code == 200
    assert r.json()["media"] == ["audio"] and r.json()["item"]["txt_ref"] == "p.zip"

    r = client.post(f"/api/playlist/{item['id']}/txt", params={"filename": "x.txt"}, content=b"lixo")
    assert r.status_code == 400
