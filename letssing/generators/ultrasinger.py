"""Gera um .txt UltraStar com o UltraSinger (IA) quando a música não tem .txt.

Opções usadas — todas lidas em src/UltraSinger.py (init_settings/arg_options):
    -i <áudio>               arquivo de entrada
    -o <pasta>               saída; o UltraSinger cria "<Artista> - <Título>[ (n)]"
                             dentro dela, com nomes corrigidos pelo MusicBrainz
    --whisper <modelo>       modelo do Whisper (padrão dele: large-v2)
    --language <código>      código ISO do Whisper (ex.: pt); sem ele, detecta sozinho
    --force_cpu              sem GPU NVIDIA (padrão; Configurações → "Usar GPU NVIDIA" tira)
    --format_version 1.0.0   sem #INSTRUMENTAL/#VOCALS (só >= 1.1.0 os cria)
    --disable_karaoke        sem o "[Karaoke].txt" extra
    --ffmpeg <pasta>         pasta do ffmpeg (é acrescentada ao PATH dele)

A app roda tudo numa pasta própria em workspace/ultrasinger/ e copia só o .txt
para a pasta da música (como `.letssing.original`), que depois passa pela mesma
normalização dos outros .txt. A música fica marcada como "gerado por IA".
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from letssing.config import Settings
from letssing.paths import Workspace
from letssing.pipeline import media
from letssing.pipeline.validation import check_folder
from letssing.playlist import Item
from letssing.sources.manual_import import ORIGINAL_TXT
from letssing.ultrastar.txt import parse_bytes

log = logging.getLogger("letssing.ultrasinger")

SCRIPT = Path("src") / "UltraSinger.py"
MANIFEST_KEY = "ultrasinger_audio"  # áudio usado na última geração (nome:tamanho)
FORMAT_VERSION = "1.0.0"
MB_GUESSED_TAGS = ("YEAR", "GENRE")

# Nomes de idioma (como vêm do #LANGUAGE) -> código do Whisper
_LANG_CODES = {
    "portuguese": "pt", "português": "pt", "portugues": "pt", "pt": "pt", "pt-br": "pt",
    "english": "en", "inglês": "en", "ingles": "en", "en": "en",
    "spanish": "es", "español": "es", "espanol": "es", "espanhol": "es", "es": "es",
    "french": "fr", "français": "fr", "francais": "fr", "francês": "fr", "frances": "fr", "fr": "fr",
    "german": "de", "deutsch": "de", "alemão": "de", "alemao": "de", "de": "de",
    "italian": "it", "italiano": "it", "it": "it",
}

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

LineFn = Callable[[str], None]


class UltraSingerError(RuntimeError):
    pass


@dataclass
class Tool:
    python: Path
    script_dir: Path  # pasta src/ (cwd do processo)


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_name(text: str) -> str:
    """Texto que pode virar nome de pasta no Windows."""
    return " ".join(_UNSAFE.sub(" ", text or "").split()).rstrip(". ")


def language_code(language: str) -> str | None:
    """Primeiro idioma reconhecido ("Portuguese, English" -> "pt"); None = detectar."""
    for part in re.split(r"[,;/]", language or ""):
        code = _LANG_CODES.get(part.strip().lower())
        if code:
            return code
    return None


def default_dir(settings: Settings) -> Path:
    """Onde o README manda instalar: <workspace>/tools/UltraSinger."""
    return Path(settings.workspace_dir) / "tools" / "UltraSinger"


def ultrasinger_root(settings: Settings) -> Path | None:
    """Pasta configurada ou, se vazia, a padrão quando ela tem o UltraSinger."""
    if settings.ultrasinger_dir:
        return Path(settings.ultrasinger_dir)
    d = default_dir(settings)
    return d if (d / SCRIPT).is_file() else None


def resolve_tool(settings: Settings) -> Tool | None:
    root = ultrasinger_root(settings)
    if root is None:
        return None
    python = Path(settings.ultrasinger_python) if settings.ultrasinger_python \
        else root / ".venv" / "Scripts" / "python.exe"
    return Tool(python=python, script_dir=(root / SCRIPT).parent)


STORE_PYTHON_MSG = ("A app está rodando no Python da Microsoft Store, que esconde a pasta AppData\\Roaming "
                    "dos programas que ela abre, e o UltraSinger não acha o Python dele. Pare a app (Ctrl+C), "
                    "rode \"uv sync\" na pasta do projeto e inicie de novo.")


def running_on_store_python(base_prefix: str | None = None) -> bool:
    return "PythonSoftwareFoundation.Python" in (sys.base_prefix if base_prefix is None else base_prefix)


def preflight(settings: Settings) -> list[str]:
    tool = resolve_tool(settings)
    if tool is None:
        return ["UltraSinger não configurado (Configurações → pasta do UltraSinger)"]
    if running_on_store_python():
        return [STORE_PYTHON_MSG]
    problems = []
    if not (tool.script_dir / SCRIPT.name).is_file():
        problems.append(f"{SCRIPT.as_posix()} não encontrado em {ultrasinger_root(settings)}")
    if not tool.python.is_file():
        problems.append(f"python.exe do UltraSinger não encontrado em {tool.python}")
    return problems


def audio_fingerprint(audio: Path) -> str:
    return f"{audio.name}:{audio.stat().st_size}"


def needs_generation(item: Item, ws: Workspace) -> bool:
    """True se não há .txt gerado ou se o áudio mudou desde a última geração."""
    folder = ws.input / item.folder_name
    if not (folder / ORIGINAL_TXT).exists():
        return True
    audio = check_folder(folder).audio
    if not audio:
        return False  # sem áudio não há o que gerar; a checagem da pasta acusa
    return media.load_manifest(folder).get(MANIFEST_KEY) != audio_fingerprint(audio[0])


def _device_args(settings: Settings | None) -> list[str]:
    """Sem --force_cpu o UltraSinger usa a GPU NVIDIA (CUDA) se o PyTorch dele tiver suporte."""
    return [] if settings is not None and settings.ultrasinger_use_gpu else ["--force_cpu"]


def build_args(audio_in: Path, out_dir: Path, settings: Settings, ffmpeg: str, language: str | None) -> list[str]:
    args = ["-i", str(audio_in), "-o", str(out_dir),
            "--whisper", settings.ultrasinger_whisper_model,
            *_device_args(settings),
            "--format_version", FORMAT_VERSION,
            "--disable_karaoke"]
    if language:
        args += ["--language", language]
    if ffmpeg:
        args += ["--ffmpeg", str(Path(ffmpeg).parent)]
    return args


def find_output_txt(out_dir: Path) -> Path | None:
    txts = [p for p in out_dir.rglob("*.txt") if "[Karaoke]" not in p.name and "cache" not in p.parts]
    return max(txts, key=lambda p: p.stat().st_mtime) if txts else None


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def run_process(cmd: list[str], cwd: Path, on_line: LineFn | None, cancel: threading.Event | None) -> int:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"}
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.stdout is not None
    if cancel is not None:
        def watch() -> None:
            while proc.poll() is None:
                if cancel.wait(0.5):
                    _kill_tree(proc)
                    return
        threading.Thread(target=watch, daemon=True).start()
    try:
        for raw in proc.stdout:
            # barras de progresso (tqdm) reescrevem a linha com CR: fica só o último estado
            text = _ANSI.sub("", raw.decode("utf-8", errors="replace"))
            parts = [p.strip() for p in text.split("\r") if p.strip()]
            line = parts[-1] if parts else ""
            if line and on_line:
                on_line(line)
    except BaseException:
        _kill_tree(proc)  # nunca deixar o UltraSinger rodando sozinho
        proc.wait()
        raise
    return proc.wait()


def build_repitch_args(txt_in: Path, out_dir: Path, ffmpeg: str, settings: Settings | None = None) -> list[str]:
    """Entrada .txt = modo "re-pitch": mantém letra/tempo e calcula só as notas."""
    args = ["-i", str(txt_in), "-o", str(out_dir), *_device_args(settings),
            "--format_version", FORMAT_VERSION, "--disable_karaoke"]
    if ffmpeg:
        args += ["--ffmpeg", str(Path(ffmpeg).parent)]
    return args


def generate(
    item: Item,
    ws: Workspace,
    settings: Settings,
    ffmpeg: str,
    on_line: LineFn | None = None,
    cancel: threading.Event | None = None,
    runner: Callable[..., int] = run_process,
    draft: str | None = None,
    txt_ref: str = "ultrasinger",
) -> Path:
    """Roda o UltraSinger e grava o .txt original da música. Devolve o caminho.

    Sem `draft`: modo completo (Whisper transcreve a letra a partir do áudio).
    Com `draft` (texto de um .txt UltraStar com letra e tempo, ex.: das legendas
    do YouTube): modo re-pitch, que só calcula as notas.
    """
    problems = preflight(settings)
    if problems:
        raise UltraSingerError(problems[0])
    tool = resolve_tool(settings)
    assert tool is not None
    folder = ws.input / item.folder_name
    audio = check_folder(folder).audio
    if not audio:
        raise UltraSingerError("Sem áudio para o UltraSinger analisar")

    work = ws.root / "ultrasinger" / item.folder_name
    if work.exists():
        ws.safe_rmtree(work)
    (work / "in").mkdir(parents=True)
    # "Artista - Título.ext": o UltraSinger usa o nome para buscar no MusicBrainz
    audio_in = work / "in" / f"{item.folder_name}{audio[0].suffix}"
    shutil.copy2(audio[0], audio_in)
    out_dir = work / "out"

    if draft is None:
        args = build_args(audio_in, out_dir, settings, ffmpeg, language_code(item.language))
    else:
        song, _ = parse_bytes(draft.encode("utf-8"))
        song.set("MP3", audio_in.name)  # o re-pitch acha o áudio pelo #MP3, na mesma pasta
        # O re-pitch cria a pasta de saída "#ARTIST - #TITLE": caracteres proibidos no
        # Windows (/, ", : …) quebrariam a pasta. Artista/título reais voltam no fim.
        song.set("ARTIST", safe_name(song.get("ARTIST")) or "Artista")
        song.set("TITLE", safe_name(song.get("TITLE")) or "Musica")
        txt_in = work / "in" / f"{item.folder_name}.txt"
        txt_in.write_bytes(song.to_text().encode("utf-8"))  # sem BOM: o UltraSinger lê como utf-8
        args = build_repitch_args(txt_in, out_dir, ffmpeg, settings)
    cmd = [str(tool.python), SCRIPT.name, *args]
    log.info("ultrasinger_start", extra={"fields": {"folder": item.folder_name, "repitch": draft is not None,
                                                    "model": settings.ultrasinger_whisper_model}})
    tail: list[str] = []

    def collect(line: str) -> None:
        tail.append(line)
        del tail[:-15]
        if on_line:
            on_line(line)

    code = runner(cmd, tool.script_dir, collect, cancel)
    if cancel is not None and cancel.is_set():
        raise UltraSingerError("UltraSinger cancelado")
    txt = find_output_txt(out_dir)
    if code != 0 or txt is None:
        last = next((l for l in reversed(tail) if l.strip()), "")
        raise UltraSingerError(f"UltraSinger falhou (código {code})" + (f": {last[:300]}" if last else ""))

    song, _ = parse_bytes(txt.read_bytes())
    # Mantém artista/título da playlist (o UltraSinger troca pelos do MusicBrainz)
    song.set("ARTIST", item.artist)
    song.set("TITLE", item.title)
    # Ano e gênero vêm do mesmo palpite do MusicBrainz, que pode ser de outra
    # música (ex.: "Legiao Urbana - Tempo Perdido" -> "Theme From Juan", 2011)
    for tag in MB_GUESSED_TAGS:
        song.remove(tag)
    target = folder / ORIGINAL_TXT
    target.write_bytes(song.to_bytes())
    media.save_manifest(folder, {**media.load_manifest(folder), MANIFEST_KEY: audio_fingerprint(audio[0]),
                                 "txt_ref": txt_ref})
    ws.safe_rmtree(work)
    log.info("ultrasinger_done", extra={"fields": {"folder": item.folder_name}})
    return target
