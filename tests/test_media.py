"""Etapa de mídia com downloader falso (sem rede)."""

import pytest

from letssing.config import Settings
from letssing.paths import Workspace
from letssing.pipeline import media
from letssing.playlist import ItemCreate, ItemPatch
from letssing.sources.models import SearchResult
from letssing.sources.youtube import guess_artist_title

URL1 = "https://www.youtube.com/watch?v=aaa"
URL2 = "https://www.youtube.com/watch?v=bbb"


class FakeDownloader:
    def __init__(self):
        self.calls = []

    def __call__(self, req, progress=None):
        self.calls.append(req)
        b = req.dest / req.basename
        if req.want_audio:
            b.with_suffix(".mp3").write_bytes(b"audio:" + req.audio_url.encode())
            (req.dest / f"{req.basename}.pt.srt").write_text("1\n", encoding="utf-8")
        if req.want_cover:
            b.with_suffix(".jpg").write_bytes(b"jpg")
        if req.video_url:
            b.with_suffix(".mp4").write_bytes(b"video:" + req.video_url.encode())
        if progress:
            progress("audio", 1.0)


def fake_search(q):
    return [SearchResult(source="youtube", source_id="aaa", artist="x", title="y", audio_url=URL1, video_url=URL1)]


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path / "ws")
    w.ensure()
    return w


def run(item, ws, dl, settings=None):
    return media.ensure_media(item, settings or Settings(), ws, searcher=fake_search, downloader=dl)


def test_first_run_downloads_everything_and_searches(repo, ws):
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido"))
    dl = FakeDownloader()
    plan, chk = run(it, ws, dl)
    assert plan.audio_url == URL1  # veio da busca
    assert len(dl.calls) == 1 and dl.calls[0].want_audio and dl.calls[0].video_url == URL1
    folder = ws.input / "Legiao Urbana - Tempo Perdido"
    assert (folder / "Legiao Urbana - Tempo Perdido.mp3").exists()
    assert (folder / media.MANIFEST).exists()
    assert chk["audio"] and chk["cover"] and chk["video"] and chk["missing"] == ["txt"]


def test_second_run_downloads_nothing(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", audio_url=URL1))
    dl = FakeDownloader()
    run(it, ws, dl)
    run(it, ws, dl)
    assert len(dl.calls) == 1


def test_changing_audio_link_redownloads_audio_and_cover(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", audio_url=URL1, video_url=URL1))
    dl = FakeDownloader()
    run(it, ws, dl)
    it = repo.update(it.id, ItemPatch(audio_url=URL2))
    run(it, ws, dl)
    req = dl.calls[-1]
    assert req.want_audio and req.want_cover and req.audio_url == URL2
    assert req.video_url == ""  # vídeo explícito não mudou
    assert (ws.input / "A - B" / "A - B.mp3").read_bytes() == b"audio:" + URL2.encode()


def test_no_video_parks_and_restores_video(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", audio_url=URL1))
    dl = FakeDownloader()
    run(it, ws, dl)
    folder = ws.input / "A - B"
    it = repo.update(it.id, ItemPatch(no_video=True))
    _, chk = run(it, ws, dl)
    assert not chk["video"]
    assert (folder / media.PARKED_DIR / "A - B.mp4").exists()
    it = repo.update(it.id, ItemPatch(no_video=False))
    _, chk = run(it, ws, dl)
    assert chk["video"]
    assert len(dl.calls) == 1  # vídeo reaproveitado, sem baixar de novo


def test_cover_only_setting_skips_video(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", audio_url=URL1))
    dl = FakeDownloader()
    _, chk = run(it, ws, dl, Settings(cover_only=True))
    assert dl.calls[0].video_url == "" and not chk["video"]


def test_higher_quality_redownloads_video_only(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", audio_url=URL1))
    dl = FakeDownloader()
    run(it, ws, dl)
    run(it, ws, dl, Settings(video_max_height=1080))
    req = dl.calls[-1]
    assert req.video_url == URL1 and not req.want_audio and not req.want_cover


def test_manual_files_without_manifest_are_kept(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", audio_url=URL1, no_video=True))
    folder = ws.input / "A - B"
    folder.mkdir(parents=True)
    (folder / "A - B.ogg").write_bytes(b"meu audio")
    (folder / "A - B.png").write_bytes(b"minha capa")
    dl = FakeDownloader()
    run(it, ws, dl)
    assert dl.calls == []
    assert (folder / "A - B.ogg").read_bytes() == b"meu audio"


def test_download_without_audio_raises(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B", audio_url=URL1))
    with pytest.raises(RuntimeError, match="áudio"):
        run(it, ws, lambda req, progress=None: None)


def test_no_search_results_raises(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="B"))
    with pytest.raises(RuntimeError, match="Nenhum vídeo"):
        media.ensure_media(it, Settings(), ws, searcher=lambda q: [], downloader=FakeDownloader())


@pytest.mark.parametrize(
    ("video_title", "channel", "expected"),
    [
        ("Legião Urbana - Tempo Perdido (Letra)", "x", ("Legião Urbana", "Tempo Perdido")),
        ("Tempo Perdido", "Legião Urbana - Topic", ("Legião Urbana", "Tempo Perdido")),
        ("Queen – Bohemian Rhapsody (Official Video Remastered)", "Queen Official", ("Queen", "Bohemian Rhapsody")),
        ("Adele | Hello [HD]", "AdeleVEVO", ("Adele", "Hello")),
        ("Legião Urbana ― Tempo perdido", "Bille Cipriani", ("Legião Urbana", "Tempo perdido")),
        ("Legião Urbana • Tempo perdido", "x", ("Legião Urbana", "Tempo perdido")),
        ("Tempo Perdido (Ao Vivo Especial)", "Legião Urbana", ("Legião Urbana", "Tempo Perdido (Ao Vivo Especial)")),
    ],
)
def test_guess_artist_title(video_title, channel, expected):
    assert guess_artist_title(video_title, channel) == expected


def test_subtitle_failure_does_not_break_download(tmp_path, monkeypatch):
    from letssing.sources import youtube

    def fake_run(url, opts):
        if opts.get("writesubtitles"):
            raise RuntimeError("ERROR: Unable to download video subtitles for 'pt': HTTP Error 429")
        (tmp_path / "A - B.mp3").write_bytes(b"mp3")
        return {"duration": 10}

    monkeypatch.setattr(youtube, "_run", fake_run)
    req = youtube.DownloadRequest(dest=tmp_path, basename="A - B", audio_url="https://youtu.be/x",
                                  video_url="", cover_url="", max_height=720, ffmpeg_path="",
                                  want_audio=True, want_cover=False, want_subs=True)
    res = youtube.download(req)
    assert (tmp_path / "A - B.mp3").exists()
    assert res.warnings and "429" in res.warnings[0]


def test_auto_pick_prefers_same_duration():
    from letssing.playlist import Item
    from letssing.sources.models import SearchResult

    def search(q):
        return [SearchResult(source="youtube", source_id=i, artist="A", title="B", duration_s=d,
                             audio_url=f"https://www.youtube.com/watch?v={i}") for i, d in (("clipe", 260), ("audio", 239))]
    base = dict(id=1, artist="A", title="B", folder_name="A - B", dup_key="a|b",
                created_at="2026-11-22T00:00:00", updated_at="2026-11-22T00:00:00")
    assert media.resolve_audio_url(Item(**base, duration_s=238.5), search).endswith("audio")
    assert media.resolve_audio_url(Item(**base), search).endswith("clipe")
