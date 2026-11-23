import json
import os
import sys
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from letssing.config import Settings
from letssing.converter import package, singit, staging
from letssing.paths import Workspace

FAKE = Path(__file__).with_name("fake_converter.py")
DLC = "0100EE4020D19003"


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path / "ws")
    w.ensure()
    return w


@pytest.fixture
def tools(tmp_path):
    ff = tmp_path / "ffmpeg.exe"
    rad = tmp_path / "radvideo64.exe"
    conv = tmp_path / "conv.exe"
    for f in (ff, rad, conv):
        f.write_bytes(b"x")
    return Settings(ffmpeg_path=str(ff), rad_path=str(rad), converter_path=str(conv),
                    dlc_title_id=DLC, dlc_json_name="songs_int", core_title_id="0100EE4020D18000")


def song_folder(ws, name, video=True, audio=b"audio"):
    d = ws.input / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.txt").write_text("#TITLE:x\n#ARTIST:y\n#BPM:100\n: 0 1 1 a\nE\n", encoding="utf-8")
    (d / f"{name}.mp3").write_bytes(audio)
    (d / f"{name}.jpg").write_bytes(b"jpg")
    if video:
        (d / f"{name}.mp4").write_bytes(b"mp4")
    return d


# ---------------- staging ----------------
def test_stage_links_inputs_with_sortable_names(ws):
    src = song_folder(ws, "Legiao Urbana - Tempo Perdido")
    res = staging.stage_batch([(1, src)], ws)
    st = res.songs[0].folder
    names = sorted(f.name for f in st.iterdir() if not f.name.startswith("."))
    assert names == ["00_input.jpg", "00_input.mp3", "00_input.mp4", "00_input.txt"]
    assert res.songs[0].name_id == "LUtempoperdido"
    assert set(res.songs[0].rebuilt) == {"txt", "audio", "cover", "video"}
    # entradas vêm antes do cache do conversor na listagem do diretório
    (st / "LUtempoperdido.ogg").write_bytes(b"cache")
    listing = [e.name for e in os.scandir(st) if e.name.endswith((".mp3", ".ogg"))]
    assert listing[0] == "00_input.mp3"


def test_restage_without_changes_keeps_cache(ws):
    src = song_folder(ws, "A - Song")
    staging.stage_batch([(1, src)], ws)
    st = ws.stage / "A - Song"
    for f in ("Asong.ogg", "Asong_preview.ogg", "Asong.bk2", "Asong.png"):
        (st / f).write_bytes(b"cache")
    res = staging.stage_batch([(1, src)], ws)
    assert res.songs[0].rebuilt == []
    assert (st / "Asong.bk2").exists() and (st / "Asong.ogg").exists()


def test_changed_audio_purges_only_audio_cache(ws):
    src = song_folder(ws, "A - Song")
    staging.stage_batch([(1, src)], ws)
    st = ws.stage / "A - Song"
    for f in ("Asong.ogg", "Asong_preview.ogg", "Asong.bk2", "Asong.png"):
        (st / f).write_bytes(b"cache")
    time.sleep(0.01)
    (src / "A - Song.mp3").unlink()
    (src / "A - Song.mp3").write_bytes(b"new audio!")
    res = staging.stage_batch([(1, src)], ws)
    assert res.songs[0].rebuilt == ["audio"]
    assert not (st / "Asong.ogg").exists() and not (st / "Asong_preview.ogg").exists()
    assert (st / "Asong.bk2").exists() and (st / "Asong.png").exists()
    assert (st / "00_input.mp3").read_bytes() == b"new audio!"


def test_removed_video_purges_video_cache(ws):
    src = song_folder(ws, "A - Song")
    staging.stage_batch([(1, src)], ws)
    st = ws.stage / "A - Song"
    (st / "Asong.bk2").write_bytes(b"cache")
    (src / "A - Song.mp4").unlink()
    res = staging.stage_batch([(1, src)], ws)
    assert "video" in res.songs[0].rebuilt
    assert not (st / "00_input.mp4").exists() and not (st / "Asong.bk2").exists()


def test_unselected_songs_are_parked_and_restored(ws):
    a, b = song_folder(ws, "A - One"), song_folder(ws, "B - Two")
    staging.stage_batch([(1, a), (2, b)], ws)
    (ws.stage / "B - Two" / "BTwo.bk2").write_bytes(b"cache")
    staging.stage_batch([(1, a)], ws)
    assert not (ws.stage / "B - Two").exists()
    assert (ws.stage / "_off_B - Two" / "BTwo.bk2").exists()
    staging.stage_batch([(1, a), (2, b)], ws)
    assert (ws.stage / "B - Two" / "BTwo.bk2").exists()  # cache voltou junto


def test_name_id_conflict(ws):
    a = song_folder(ws, "Ana Banana - Hello")
    b = song_folder(ws, "A B - Hello")  # ambos viram 'ABhello'
    res = staging.stage_batch([(1, a), (2, b)], ws)
    assert 2 in res.conflicts and len(res.songs) == 1


# ---------------- converter ----------------
def test_preflight_lists_missing_config(monkeypatch):
    monkeypatch.setattr(singit, "DEFAULT_RAD_PATH", "Z:/nao/existe/radvideo64.exe")
    problems = singit.preflight(Settings(ffmpeg_path="C:/nao/existe/ffmpeg.exe"))
    text = " ".join(problems)
    assert "TitleID do DLC" in text and "JSON Name" in text and "RAD" in text and "ultrastar2singIt" in text


def test_preflight_ok(tools):
    assert singit.preflight(tools) == []


def test_known_dlc_uses_converter_db_values(tools):
    s = tools.model_copy(update={"dlc_json_name": "", "core_title_id": ""})
    assert singit.effective_json_name(s) == "songs_int"
    assert singit.effective_core_id(s) == "0100EE4020D18000"
    assert singit.preflight(s) == []


def test_config_yml_and_args(tools, ws):
    path = ws.tools / "config.yml"
    singit.write_config_yml(tools.model_copy(update={"cover_only": True}), ws, path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg["conversion_tweaks"]["enable"] is True
    assert cfg["conversion_tweaks"]["still_video"] is True
    assert cfg["folders"] == {"input": str(ws.stage), "output": str(ws.output)}
    args = singit.build_args(tools, ws)
    assert args[0] == "--cli" and "--dlc-json-name" in args and "--rad-path" in args
    assert args[args.index("--output") + 1] == str(ws.output)


def test_prepare_tool_copies_exe_once(tools, ws):
    t1 = singit.prepare_tool(tools, ws)
    exe = t1.workdir / singit.EXE_NAME
    assert exe.exists() and (t1.workdir / "config.yml").exists()
    mtime = exe.stat().st_mtime_ns
    singit.prepare_tool(tools, ws)
    assert exe.stat().st_mtime_ns == mtime


def fake_tool(ws):
    workdir = ws.tools / "converter"
    workdir.mkdir(parents=True, exist_ok=True)
    return singit.ConverterTool(workdir=workdir, command=[sys.executable, str(FAKE)])


def test_run_converter_reports_per_song(tools, ws):
    ok, bad = song_folder(ws, "A - Song"), song_folder(ws, "B - Falha")
    res = staging.stage_batch([(1, ok), (2, bad)], ws)
    songs = {s.folder.name: s.name_id for s in res.songs}
    tool = fake_tool(ws)
    singit.write_config_yml(tools, ws, tool.workdir / "config.yml")
    started, lines = [], []
    (ws.output / "lixo.txt").write_text("x")  # saída é limpa antes
    result = singit.run_converter(tool, tools, ws, songs, on_line=lines.append, on_song=started.append)
    assert result.returncode == 0
    assert started == ["A - Song", "B - Falha"]
    assert result.outcomes["A - Song"].ok
    assert not result.outcomes["B - Falha"].ok
    assert result.outcomes["B - Falha"].message == "simulated failure"
    assert "done" in lines


def test_run_converter_cancel(tools, ws, tmp_path):
    slow = tmp_path / "slow.py"
    slow.write_text("import time\nprint('start', flush=True)\ntime.sleep(30)\n")
    tool = singit.ConverterTool(workdir=ws.tools, command=[sys.executable, str(slow)])
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    t0 = time.time()
    result = singit.run_converter(tool, tools, ws, {"A - Song": "Asong"}, cancel=cancel)
    assert result.cancelled and time.time() - t0 < 10
    assert not result.outcomes["A - Song"].ok


# ---------------- zip ----------------
def test_build_zip_layout(ws):
    base = ws.output / DLC / "romfs"
    (base / "Songs" / "videos").mkdir(parents=True)
    (base / "Songs" / "videos" / "X.bk2").write_bytes(b"v" * 100)
    (base / "songs_int.json").write_text("[]")
    (ws.output / "_Patch_lixo").mkdir()
    z = package.build_zip(ws.output, ws.dist, now=datetime(2026, 11, 22, 10, 0, 0))
    assert z.name == "letssing-dlc-20261122-100000.zip"
    with zipfile.ZipFile(z) as zf:
        names = sorted(zf.namelist())
        assert names == [f"atmosphere/contents/{DLC}/romfs/Songs/videos/X.bk2",
                         f"atmosphere/contents/{DLC}/romfs/songs_int.json"]
        assert zf.getinfo(names[0]).compress_type == zipfile.ZIP_STORED
    assert package.latest_zip(ws.dist) == z


def test_build_zip_empty_output(ws):
    with pytest.raises(RuntimeError):
        package.build_zip(ws.output, ws.dist)


def test_resolve_rad_prefers_configured_then_default(tmp_path):
    default = tmp_path / "radvideo64.exe"
    assert singit.resolve_rad(Settings(), default=str(default)) == ""
    default.write_bytes(b"")
    assert singit.resolve_rad(Settings(), default=str(default)) == str(default)
    assert singit.resolve_rad(Settings(rad_path="X:/meu/rad.exe"), default=str(default)) == "X:/meu/rad.exe"


def test_rad_required_in_json_even_with_cover_only(monkeypatch):
    monkeypatch.setattr(singit, "DEFAULT_RAD_PATH", "Z:/nao/existe/radvideo64.exe")
    s = Settings(game_version="2026", cover_only=True)
    assert any("RAD" in p for p in singit.preflight(s))
    from letssing.api.routes_config import _rad_status
    assert _rad_status(s)["ok"] is False
