import sys
import threading
from pathlib import Path

import pytest

from letssing.config import Settings
from letssing.generators import ultrasinger
from letssing.jobs import Job
from letssing.paths import Workspace
from letssing.pipeline import media
from letssing.pipeline.runner import run_generate
from letssing.playlist import ItemCreate, Status, TxtSource
from letssing.sources.manual_import import ORIGINAL_TXT
from letssing.ultrastar.txt import parse_bytes

FAKE_DIR = Path(__file__).parent / "fake_ultrasinger"


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path / "ws")
    w.ensure()
    return w


@pytest.fixture
def settings(ws):
    return Settings(ultrasinger_dir=str(FAKE_DIR), ultrasinger_python=sys.executable, workspace_dir=str(ws.root))


def _song_folder(ws, item, audio=b"audio"):
    folder = ws.input / item.folder_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item.folder_name}.mp3").write_bytes(audio)
    (folder / f"{item.folder_name}.jpg").write_bytes(b"jpg")
    return folder


def test_language_code():
    assert ultrasinger.language_code("Portuguese") == "pt"
    assert ultrasinger.language_code("Spanish, English") == "es"
    assert ultrasinger.language_code("Klingon") is None
    assert ultrasinger.language_code("") is None


def test_build_args_cpu_and_format(tmp_path):
    s = Settings(ultrasinger_whisper_model="base")
    args = ultrasinger.build_args(tmp_path / "a.mp3", tmp_path / "out", s, "C:/ff/bin/ffmpeg.exe", "pt")
    assert args[args.index("--whisper") + 1] == "base"
    assert "--force_cpu" in args and "--disable_karaoke" in args
    assert args[args.index("--format_version") + 1] == "1.0.0"
    assert args[args.index("--language") + 1] == "pt"
    assert Path(args[args.index("--ffmpeg") + 1]) == Path("C:/ff/bin")


def test_whisper_model_validated():
    with pytest.raises(ValueError):
        Settings(ultrasinger_whisper_model="small.en")


def test_preflight(tmp_path):
    assert "não configurado" in ultrasinger.preflight(Settings(workspace_dir=str(tmp_path)))[0]
    probs = ultrasinger.preflight(Settings(ultrasinger_dir=str(tmp_path)))
    assert any("UltraSinger.py" in p for p in probs) and any("python.exe" in p for p in probs)


def test_generate_with_fake(repo, ws, settings):
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido", language="Portuguese"))
    folder = _song_folder(ws, it)
    lines = []
    txt = ultrasinger.generate(it, ws, settings, "", on_line=lines.append)
    assert txt == folder / ORIGINAL_TXT
    song, _ = parse_bytes(txt.read_bytes())
    assert song.get("ARTIST") == "Legião Urbana" and song.get("TITLE") == "Tempo Perdido"
    assert song.get("LANGUAGE") == "pt"
    assert song.get("YEAR") == "" and song.get("GENRE") == ""  # palpite do MusicBrainz descartado
    assert "Transcribing 100%" in lines  # só o último estado da barra
    assert not (ws.root / "ultrasinger" / it.folder_name).exists()
    assert not ultrasinger.needs_generation(it, ws)
    (folder / f"{it.folder_name}.mp3").write_bytes(b"outro audio maior")
    assert ultrasinger.needs_generation(it, ws)


def test_generate_failure_reports_last_line(repo, ws, settings):
    it = repo.add(ItemCreate(artist="A", title="Falha"))
    _song_folder(ws, it)
    with pytest.raises(ultrasinger.UltraSingerError, match="código 3.*não carregou"):
        ultrasinger.generate(it, ws, settings, "")
    assert not (ws.input / it.folder_name / ORIGINAL_TXT).exists()


def test_generate_cancel(repo, ws, settings):
    it = repo.add(ItemCreate(artist="A", title="B"))
    _song_folder(ws, it)
    cancel = threading.Event()
    cancel.set()

    def runner(cmd, cwd, on_line, c):
        return 1

    with pytest.raises(ultrasinger.UltraSingerError, match="cancelado"):
        ultrasinger.generate(it, ws, settings, "", cancel=cancel, runner=runner)


@pytest.fixture(autouse=True)
def _not_store_python(monkeypatch):
    # a checagem olha o Python que roda a app; os testes podem rodar em qualquer um
    monkeypatch.setattr(ultrasinger.sys, "base_prefix", r"C:\uv\python\cpython-3.12")


def _no_lyrics(*a, **k):
    return None


def _no_captions(url, language):
    return None


def _ensure_existing(item, settings, ws, progress=None):
    plan = media.MediaPlan(audio_url="https://www.youtube.com/watch?v=zzz", video_url="", cover_url="",
                           download_audio=False, download_cover=False, download_video=False, park_video=False)
    return plan, {}


def test_runner_generates_and_marks_ai(repo, ws, settings):
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido"))
    _song_folder(ws, it)
    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics, fetch_captions=_no_captions)
    got = repo.get(it.id)
    assert got.txt_source == TxtSource.ULTRASINGER and got.needs_review
    assert got.status == Status.PENDENTE and got.error_message == ""
    assert (ws.input / it.folder_name / f"{it.folder_name}.txt").exists()
    msgs = [e["message"] for e in job.events if e["type"] == "log"]
    assert any(".txt gerado (UltraSinger) em" in m for m in msgs)

    # 2ª execução: não gera de novo
    calls = []
    job2 = Job(id="j2", kind="generate", total=1)
    run_generate(job2, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics, fetch_captions=_no_captions,
                 generate_txt=lambda *a, **k: calls.append(a))
    assert calls == []


def test_runner_skips_when_not_configured(repo, ws):
    it = repo.add(ItemCreate(artist="A", title="Sem Txt"))
    _song_folder(ws, it)
    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, Settings(workspace_dir=str(ws.root)), ws,
                 ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics, fetch_captions=_no_captions)
    msg = repo.get(it.id).error_message
    assert msg.startswith("Pulada na conversão: falta txt") and "UltraSinger não configurado" in msg


def test_runner_ai_failure_isolated(repo, ws, settings):
    bad = repo.add(ItemCreate(artist="A", title="Falha"))
    good = repo.add(ItemCreate(artist="B", title="Boa"))
    _song_folder(ws, bad)
    _song_folder(ws, good)
    job = Job(id="j", kind="generate", total=2)
    run_generate(job, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics, fetch_captions=_no_captions)
    assert repo.get(bad.id).status == Status.ERRO
    assert "UltraSinger falhou" in repo.get(bad.id).error_message
    assert repo.get(good.id).txt_source == TxtSource.ULTRASINGER


def test_process_killed_if_reader_fails(repo, ws, settings):
    import time
    it = repo.add(ItemCreate(artist="A", title="Lento"))
    _song_folder(ws, it)

    def boom(line):
        raise RuntimeError("leitor quebrou")

    t0 = time.time()
    with pytest.raises(RuntimeError, match="leitor quebrou"):
        ultrasinger.generate(it, ws, settings, "", on_line=boom)
    assert time.time() - t0 < 20  # não esperou o sleep(60) do processo


def _auto_captions(n_words=30, lang="pt-orig"):
    from letssing.generators.captions import Track
    segs = [{"utf8": "palavra"}] + [{"utf8": f" w{i}", "tOffsetMs": 300 * i} for i in range(1, n_words)]
    data = {"events": [{"tStartMs": 5000, "dDurationMs": 300 * n_words, "segs": segs}]}
    return lambda url, language: (Track(lang, "auto", "u"), data)


def test_runner_uses_youtube_captions_then_repitch(repo, ws, settings):
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido", language="Portuguese"))
    _song_folder(ws, it)
    calls = []

    def fake_generate(item, ws_, settings_, ffmpeg, on_line=None, cancel=None, **kw):
        calls.append(kw)
        return ultrasinger.generate(item, ws_, settings_, ffmpeg, on_line=on_line, cancel=cancel, **kw)

    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics,
                 generate_txt=fake_generate, fetch_captions=_auto_captions())
    assert calls[0]["txt_ref"] == "captions:pt-orig" and calls[0]["draft"].startswith("#ARTIST:Legião Urbana")
    got = repo.get(it.id)
    assert got.txt_source == TxtSource.YOUTUBE_SUBS and got.needs_review
    assert media.load_manifest(ws.input / it.folder_name)["txt_ref"] == "captions:pt-orig"
    msgs = [e["message"] for e in job.events if e["type"] == "log"]
    assert any("legendas automáticas do YouTube (pt-orig, 30 palavras)" in m for m in msgs)


def test_runner_falls_back_when_captions_too_short(repo, ws, settings):
    it = repo.add(ItemCreate(artist="A", title="Curta"))
    _song_folder(ws, it)
    calls = []
    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics,
                 generate_txt=lambda *a, **kw: calls.append(kw), fetch_captions=_auto_captions(5))
    assert len(calls) == 1 and "draft" not in calls[0]  # UltraSinger completo, sem rascunho
    msgs = [e["message"] for e in job.events if e["type"] == "log"]
    assert any("só 5 palavras" in m for m in msgs)


def test_runner_falls_back_when_captions_fail(repo, ws, settings):
    it = repo.add(ItemCreate(artist="A", title="Erro"))
    _song_folder(ws, it)
    calls = []

    def boom(url, language):
        raise RuntimeError("HTTP 429")

    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics,
                 generate_txt=lambda *a, **kw: calls.append(kw), fetch_captions=boom)
    assert len(calls) == 1 and "draft" not in calls[0]
    assert repo.get(it.id).status != Status.ERRO


def test_captions_disabled_in_settings(repo, ws, settings):
    it = repo.add(ItemCreate(artist="A", title="Off"))
    _song_folder(ws, it)
    asked = []
    s = settings.model_copy(update={"use_youtube_captions": False})
    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, s, ws, ensure=_ensure_existing, convert=False, find_lyrics=_no_lyrics,
                 generate_txt=lambda *a, **kw: None, fetch_captions=lambda *a: asked.append(a))
    assert asked == []


def test_generate_repitch_mode_with_fake(repo, ws, settings):
    from letssing.generators.captions import parse_json3, to_draft_txt
    it = repo.add(ItemCreate(artist="Adele", title="Hello", language="English"))
    folder = _song_folder(ws, it)
    data = {"events": [{"tStartMs": 1000, "dDurationMs": 4000,
                        "segs": [{"utf8": "hello"}, {"utf8": " from", "tOffsetMs": 800}, {"utf8": " the", "tOffsetMs": 1600}]}]}
    draft = to_draft_txt(parse_json3(data), artist=it.artist, title=it.title, language="English", audio_file="x.mp3")
    txt = ultrasinger.generate(it, ws, settings, "", draft=draft, txt_ref="captions:en-orig")
    song, _ = parse_bytes(txt.read_bytes())
    notes = [l for l in song.to_text().splitlines() if l.startswith(":")]
    assert [n.split(" ", 4)[3] for n in notes] == ["64", "64", "64"]  # notas vieram do re-pitch
    assert [n.split(" ", 4)[4].strip() for n in notes] == ["hello", "from", "the"]  # letra mantida
    assert media.load_manifest(folder)["txt_ref"] == "captions:en-orig"


LRC = "\n".join(f"[00:{10 + 4 * i:02d}.50] linha numero {i} com cinco palavras" for i in range(6))


def test_runner_uses_lrclib_when_no_captions(repo, ws, settings):
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido", duration_s=302))
    _song_folder(ws, it)
    calls, asked = [], []

    def fake_generate(item, ws_, settings_, ffmpeg, on_line=None, cancel=None, **kw):
        calls.append(kw)
        return ultrasinger.generate(item, ws_, settings_, ffmpeg, on_line=on_line, cancel=cancel, **kw)

    def lyrics(artist, title, duration_s):
        asked.append((artist, title, duration_s))
        return {"id": 3023239, "artistName": artist, "trackName": title, "syncedLyrics": LRC}

    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False,
                 generate_txt=fake_generate, fetch_captions=_no_captions, find_lyrics=lyrics)
    assert asked == [("Legião Urbana", "Tempo Perdido", 302)]
    assert calls[0]["txt_ref"] == "lrclib:3023239" and ": 0 " in calls[0]["draft"]
    got = repo.get(it.id)
    assert got.txt_source == TxtSource.LRCLIB and got.needs_review
    msgs = [e["message"] for e in job.events if e["type"] == "log"]
    assert any("LRCLIB #3023239" in m for m in msgs)


def test_lrclib_disabled_in_settings(repo, ws, settings):
    it = repo.add(ItemCreate(artist="A", title="Off"))
    _song_folder(ws, it)
    asked, calls = [], []
    s = settings.model_copy(update={"use_lrclib": False})
    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, s, ws, ensure=_ensure_existing, convert=False,
                 generate_txt=lambda *a, **kw: calls.append(kw), fetch_captions=_no_captions,
                 find_lyrics=lambda *a: asked.append(a))
    assert asked == [] and len(calls) == 1 and "draft" not in calls[0]


def test_default_dir_in_workspace_tools(tmp_path):
    s = Settings(workspace_dir=str(tmp_path))
    assert ultrasinger.resolve_tool(s) is None  # nada instalado
    script = tmp_path / "tools" / "UltraSinger" / "src" / "UltraSinger.py"
    script.parent.mkdir(parents=True)
    script.write_text("")
    tool = ultrasinger.resolve_tool(s)
    assert tool.script_dir == script.parent
    assert tool.python == tmp_path / "tools" / "UltraSinger" / ".venv" / "Scripts" / "python.exe"


def test_store_python_detected(monkeypatch, settings):
    assert ultrasinger.running_on_store_python(
        r"C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0")
    assert not ultrasinger.running_on_store_python(r"C:\Users\x\AppData\Roaming\uv\python\cpython-3.12.16")
    monkeypatch.setattr(ultrasinger, "running_on_store_python", lambda: True)
    assert "uv sync" in ultrasinger.preflight(settings)[0]


def test_repitch_with_unsafe_title(repo, ws, settings):
    # "O Grilo // Contramão" e 'Sandra Rosa Madalena "A Cigana"' quebravam a pasta de saída
    from letssing.generators.captions import parse_json3, to_draft_txt
    it = repo.add(ItemCreate(artist="GriloTube", title='O Grilo // Contramão "ao vivo"'))
    _song_folder(ws, it)
    data = {"events": [{"tStartMs": 1000, "dDurationMs": 4000,
                        "segs": [{"utf8": "a"}, {"utf8": " b", "tOffsetMs": 800}]}]}
    draft = to_draft_txt(parse_json3(data), artist=it.artist, title=it.title, language="", audio_file="x.mp3")
    seen = {}

    def runner(cmd, cwd, on_line, cancel):
        txt_in = Path(cmd[cmd.index("-i") + 1])
        song, _ = parse_bytes(txt_in.read_bytes())
        seen["title"] = song.get("TITLE")
        return ultrasinger.run_process(cmd, cwd, on_line, cancel)

    txt = ultrasinger.generate(it, ws, settings, "", draft=draft, runner=runner)
    assert seen["title"] == "O Grilo Contramão ao vivo"
    assert parse_bytes(txt.read_bytes())[0].get("TITLE") == 'O Grilo // Contramão "ao vivo"'  # o real volta


def test_safe_name():
    assert ultrasinger.safe_name(r'a/b\c:d*e?f"g<h>i|j. ') == "a b c d e f g h i j"


def _captions_with(words, lang):
    from letssing.generators.captions import Track
    segs = [{"utf8": words[0]}] + [{"utf8": f" {w}", "tOffsetMs": 300 * i} for i, w in enumerate(words[1:], 1)]
    data = {"events": [{"tStartMs": 5000, "dDurationMs": 300 * len(words), "segs": segs}]}
    return lambda url, language: (Track(lang, "auto", "u"), data)


LYRICS_PT = ("todos os dias quando acordo não tenho mais o tempo que passou mas tenho muito tempo "
             "temos todo o tempo do mundo todos os dias antes de dormir lembro e esqueço como foi o dia").split()


def _lyrics(words):
    lrc = "\n".join(f"[00:{10 + 4 * i:02d}.00] {' '.join(words[i * 6:(i + 1) * 6])}" for i in range(len(words) // 6 + 1))
    return lambda a, t, d: {"id": 77, "artistName": a, "trackName": t, "syncedLyrics": lrc}


def _run_choice(repo, ws, settings, fetch_captions, find_lyrics):
    it = repo.add(ItemCreate(artist="Legião Urbana", title="Tempo Perdido"))
    _song_folder(ws, it)
    calls = []
    job = Job(id="j", kind="generate", total=1)
    run_generate(job, repo.list(), repo, settings, ws, ensure=_ensure_existing, convert=False,
                 generate_txt=lambda *a, **kw: calls.append(kw), fetch_captions=fetch_captions, find_lyrics=find_lyrics)
    return calls[0], [e["message"] for e in job.events if e["type"] == "log"]


def test_wrong_language_captions_replaced_by_lrclib(repo, ws, settings):
    garbage = ("todo los días cuando acuerdo no tengo más el tiempo que pasó pero tengo mucho tiempo "
               "tenemos todo el tiempo del mundo cada día antes de dormir recuerdo y olvido").split()
    call, msgs = _run_choice(repo, ws, settings, _captions_with(garbage, "es-orig"), _lyrics(LYRICS_PT))
    assert call["txt_ref"] == "lrclib:77"
    assert any("não bate com a letra do LRCLIB" in m and "es-orig" in m for m in msgs)


def test_matching_captions_kept_over_lrclib(repo, ws, settings):
    call, msgs = _run_choice(repo, ws, settings, _captions_with(LYRICS_PT, "pt-orig"), _lyrics(LYRICS_PT))
    assert call["txt_ref"] == "captions:pt-orig"


def test_lyrics_overlap():
    from letssing.pipeline.runner import lyrics_overlap
    assert lyrics_overlap(["Não", "TENHO", "mais", "o"], ["nao", "tenho", "mais"]) == 1.0
    assert lyrics_overlap(["tengo", "mucho"], ["tenho", "muito"]) == 0.0


def test_gpu_setting_drops_force_cpu(tmp_path):
    cpu = Settings(workspace_dir=str(tmp_path))
    gpu = cpu.model_copy(update={"ultrasinger_use_gpu": True})
    assert "--force_cpu" in ultrasinger.build_args(tmp_path / "a.mp3", tmp_path, cpu, "", None)
    assert "--force_cpu" not in ultrasinger.build_args(tmp_path / "a.mp3", tmp_path, gpu, "", None)
    assert "--force_cpu" in ultrasinger.build_repitch_args(tmp_path / "a.txt", tmp_path, "", cpu)
    assert "--force_cpu" not in ultrasinger.build_repitch_args(tmp_path / "a.txt", tmp_path, "", gpu)
