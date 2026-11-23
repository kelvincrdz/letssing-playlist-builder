"""Execução do Let's Sing UltraStar Converter (ultrastar2singIt) em modo `--cli`.

Flags conferidas com `--help` da v1.0.2. Pontos importantes do código dele:
- no CLI, `--still-video`/`--max-video-size`/`--pitch-correction` só valem se
  `conversion_tweaks.enable: true` no `config.yml` (senão voltam ao padrão);
- o `config.yml` é lido da pasta do executável (ou do script, no código-fonte),
  por isso usamos uma CÓPIA do conversor em `workspace/tools/converter/`;
- a pasta de saída é APAGADA antes de cada execução: usamos só `workspace/output/`;
- para TitleIDs de DLC que ele conhece, o JSON name/TitleID do jogo do banco
  dele têm prioridade.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from letssing.config import Settings
from letssing.converter import dlc_catalog
from letssing.paths import Workspace

log = logging.getLogger("letssing.converter")

EXE_NAME = "LetsSingUltraStarConverter.exe"
SCRIPT_NAME = "LetsSingUltraStarConverter.py"
MAX_VIDEO_SIZE_MB = 50  # padrão do conversor

LineFn = Callable[[str], None]


class ConverterConfigError(Exception):
    """Configuração insuficiente para converter (erro do lote, não de uma música)."""


@dataclass
class ConverterTool:
    workdir: Path  # cópia em workspace/tools/converter
    command: list[str]  # executável ou [python, script]


@dataclass
class SongOutcome:
    name_id: str
    folder: str
    ok: bool
    message: str = ""


@dataclass
class RunResult:
    returncode: int
    outcomes: dict[str, SongOutcome] = field(default_factory=dict)  # por pasta
    cancelled: bool = False


def resolve_ffmpeg(settings: Settings) -> str:
    if settings.ffmpeg_path:
        return settings.ffmpeg_path
    found = shutil.which("ffmpeg")
    if not found:
        raise ConverterConfigError("FFmpeg não encontrado: configure o caminho em Configurações")
    return found


# Caminho padrão do instalador do RAD Video Tools; o próprio conversor
# (Gui.prefill_rad_path) pré-preenche exatamente este caminho.
DEFAULT_RAD_PATH = r"C:\Program Files (x86)\RADVideo\radvideo64.exe"


def resolve_rad(settings: Settings, default: str | None = None) -> str:
    """Caminho configurado, ou o padrão do instalador se existir; senão ''."""
    if settings.rad_path:
        return settings.rad_path
    default = DEFAULT_RAD_PATH if default is None else default
    return default if Path(default).is_file() else ""


RAD_TIMEOUT_S = 1800  # por vídeo; um RAD travado é encerrado depois disso


def rad_shim_path() -> Path:
    """letssing-rad.exe (console script de letssing.converter.radshim), ao lado do python do venv."""
    return Path(sys.executable).parent / "letssing-rad.exe"


def rad_for_converter(settings: Settings) -> str:
    """O que o conversor recebe como RAD: o intermediário que adapta os parâmetros."""
    if not resolve_rad(settings):
        return ""
    shim = rad_shim_path()
    return str(shim) if shim.is_file() else resolve_rad(settings)


def rad_log_path(ws: Workspace) -> Path:
    return ws.tools / "rad-shim.jsonl"


def _game_year(settings: Settings) -> int:
    try:
        return int(settings.game_version)
    except ValueError:
        return 2026


def output_format(settings: Settings) -> str:
    """JSON (vídeo .bk2) no 2024+; XML nas versões antigas."""
    return "json" if effective_json_name(settings) or _game_year(settings) >= 2024 else "xml"


def effective_json_name(settings: Settings) -> str:
    info = dlc_catalog.find(settings.dlc_title_id) if settings.dlc_title_id else None
    return info.dlc_json_name if info else settings.dlc_json_name


def effective_core_id(settings: Settings) -> str:
    info = dlc_catalog.find(settings.dlc_title_id) if settings.dlc_title_id else None
    return info.core_id if info else settings.core_title_id


def preflight(settings: Settings) -> list[str]:
    """Problemas que impedem a conversão do lote inteiro."""
    problems = []
    if not settings.dlc_title_id:
        problems.append("TitleID do DLC não configurado")
    if _game_year(settings) >= 2024 and not effective_json_name(settings):
        problems.append("DLC JSON Name não configurado (obrigatório no Let's Sing 2024+; veja romfs/name.txt no DBI)")
    if output_format(settings) == "json" and not effective_core_id(settings):
        problems.append("TitleID do jogo não configurado")
    try:
        ff = resolve_ffmpeg(settings)
        if not Path(ff).is_file():
            problems.append(f"FFmpeg não encontrado em {ff}")
    except ConverterConfigError as e:
        problems.append(str(e))
    if output_format(settings) == "json":
        rad = resolve_rad(settings)
        if not rad:
            problems.append("RAD Video Tools (radvideo64.exe) não configurado — obrigatório no 2024+ (vídeos .bk2)")
        elif not Path(rad).is_file():
            problems.append(f"radvideo64.exe não encontrado em {rad}")
        if not rad_shim_path().is_file():
            problems.append(f"{rad_shim_path().name} não encontrado: rode 'uv sync' na pasta do projeto")
    src = Path(settings.converter_path) if settings.converter_path else None
    if not src:
        problems.append("Caminho do ultrastar2singIt não configurado")
    elif src.is_dir():
        if not (src / SCRIPT_NAME).is_file():
            problems.append(f"{SCRIPT_NAME} não encontrado em {src}")
    elif not src.is_file():
        problems.append(f"Conversor não encontrado em {src}")
    return problems


def prepare_tool(settings: Settings, ws: Workspace) -> ConverterTool:
    """Copia o conversor para o workspace (só se mudou) e grava o config.yml."""
    src = Path(settings.converter_path)
    dest = ws.tools / "converter"
    dest.mkdir(parents=True, exist_ok=True)
    stamp_file = dest / ".source.json"
    if src.is_dir():
        stamp = {"src": str(src.resolve()), "mtime": max(
            (p.stat().st_mtime_ns for p in src.rglob("*.py")), default=0)}
    else:
        st = src.stat()
        stamp = {"src": str(src.resolve()), "size": st.st_size, "mtime": st.st_mtime_ns}
    try:
        old = json.loads(stamp_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        old = None
    if old != stamp:
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", "config.yml", "error.log"))
        else:
            shutil.copy2(src, dest / EXE_NAME)
        stamp_file.write_text(json.dumps(stamp), encoding="utf-8")
        log.info("converter_copied", extra={"fields": {"src": str(src)}})

    write_config_yml(settings, ws, dest / "config.yml")
    if src.is_dir():
        command = [sys.executable, "-X", "utf8", str(dest / SCRIPT_NAME)]
    else:
        command = [str(dest / EXE_NAME)]
    return ConverterTool(workdir=dest, command=command)


def write_config_yml(settings: Settings, ws: Workspace, path: Path) -> None:
    # JSON é YAML válido: evita depender de uma lib de YAML
    cfg = {
        "dlc": {"id": settings.dlc_title_id, "json_name": effective_json_name(settings) or None},
        "core": {"id": effective_core_id(settings) or None},
        "tools": {"ffmpeg_path": resolve_ffmpeg(settings), "rad_path": rad_for_converter(settings) or None},
        "folders": {"input": str(ws.stage), "output": str(ws.output)},
        "conversion_tweaks": {
            "enable": True,  # sem isso os ajustes abaixo são ignorados no CLI
            "dlc_songs": {"include": False, "name_txt_path": None, "songs_json_path": None,
                          "songs_dlc_tsv_path": None},
            "pitch_correction": "fast",
            "max_video_size": MAX_VIDEO_SIZE_MB,
            "no_medley": False,
            "still_video": bool(settings.cover_only),
        },
    }
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def build_args(settings: Settings, ws: Workspace) -> list[str]:
    args = ["--cli", "--dlc-id", settings.dlc_title_id,
            "--ffmpeg-path", resolve_ffmpeg(settings),
            "--input", str(ws.stage), "--output", str(ws.output),
            "--pitch-correction", "fast", "--max-video-size", str(MAX_VIDEO_SIZE_MB)]
    if effective_core_id(settings):
        args += ["--core-id", effective_core_id(settings)]
    if effective_json_name(settings):
        args += ["--dlc-json-name", effective_json_name(settings)]
    rad = rad_for_converter(settings)
    if rad:
        args += ["--rad-path", rad]
    if settings.cover_only:
        args.append("--still-video")
    return args


def prepare_output(ws: Workspace) -> None:
    """Garante uma saída que o conversor aceite sem desviar para '_Patch'."""
    out = ws.output
    if out.exists():
        ws.safe_rmtree(out)
    out.mkdir(parents=True)


_ERR_PATTERNS = (
    re.compile(r"^Error with directory (?P<dir>.+?)(?::\s*(?P<msg>.*))?$"),
    re.compile(r"^Skipping '(?P<dir>.+?)': (?P<msg>.*)$"),
    re.compile(r"^No ultrastar text file found for (?P<dir>.+?), skipping$"),
)


def run_converter(
    tool: ConverterTool,
    settings: Settings,
    ws: Workspace,
    songs: dict[str, str],  # pasta -> name_id
    on_line: LineFn | None = None,
    on_song: Callable[[str], None] | None = None,  # pasta que começou
    cancel: threading.Event | None = None,
) -> RunResult:
    prepare_output(ws)
    by_name_id = {nid: folder for folder, nid in songs.items()}
    errors: dict[str, str] = {}
    cmd = tool.command + build_args(settings, ws)
    log.info("converter_start", extra={"fields": {"songs": len(songs)}})
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "LETSSING_RAD_REAL": resolve_rad(settings), "LETSSING_RAD_LOG": str(rad_log_path(ws)),
           "LETSSING_RAD_TIMEOUT": str(RAD_TIMEOUT_S)}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd, cwd=tool.workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=env, creationflags=creationflags,
    )
    assert proc.stdout is not None

    if cancel is not None:
        def watch() -> None:
            while proc.poll() is None:
                if cancel.wait(0.5):
                    proc.terminate()
                    return
        threading.Thread(target=watch, daemon=True).start()

    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue
        if on_line:
            on_line(line)
        if line in by_name_id and on_song:
            on_song(by_name_id[line])
        for pat in _ERR_PATTERNS:
            m = pat.match(line)
            if m and m.group("dir") in songs:
                msg = (m.groupdict().get("msg") or line).strip()
                errors.setdefault(m.group("dir"), msg)
    code = proc.wait()
    result = RunResult(returncode=code, cancelled=bool(cancel and cancel.is_set()))
    fmt = output_format(settings)
    for folder, nid in songs.items():
        missing = missing_outputs(ws, settings.dlc_title_id, nid, fmt)
        if missing:
            msg = errors.get(folder) or ("arquivos não gerados: " + ", ".join(missing))
            if folder not in errors and fmt == "json" and f"{nid}.bk2" in missing:
                msg += f" (vídeo .bk2: veja {rad_log_path(ws)})"
            result.outcomes[folder] = SongOutcome(nid, folder, False, msg)
        else:
            result.outcomes[folder] = SongOutcome(nid, folder, True)
    return result


def expected_outputs(dlc_id: str, name_id: str, fmt: str) -> list[str]:
    video = f"{name_id}.bk2" if fmt == "json" else f"{name_id}.mp4"
    return [
        f"{dlc_id}/romfs/Songs/audio/{name_id}.ogg",
        f"{dlc_id}/romfs/Songs/audio_preview/{name_id}_preview.ogg",
        f"{dlc_id}/romfs/Songs/covers/{name_id}.png",
        f"{dlc_id}/romfs/Songs/vxla/{name_id}.vxla",
        f"{dlc_id}/romfs/Songs/videos/{video}",
    ]


def missing_outputs(ws: Workspace, dlc_id: str, name_id: str, fmt: str) -> list[str]:
    return [Path(p).name for p in expected_outputs(dlc_id, name_id, fmt)
            if not (ws.output / p).is_file()]
