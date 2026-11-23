"""Etapa de mídia: garante áudio, capa, vídeo (opcional) e legendas na pasta da música.

Retomável: um manifesto `.letssing.json` na pasta guarda de quais links cada
arquivo veio. Só baixa o que falta ou o que teve o link trocado.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from letssing.config import Settings
from letssing.paths import Workspace
from letssing.pipeline.validation import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    check_folder,
)
from letssing.playlist import Item
from letssing.sources import youtube

log = logging.getLogger("letssing.media")

MANIFEST = ".letssing.json"
PARKED_DIR = "_sem_video"  # vídeo guardado quando o item está "sem vídeo"

ProgressFn = Callable[[str, float | None], None]
Searcher = Callable[[str], list]
Downloader = Callable[..., Any]


@dataclass
class MediaPlan:
    audio_url: str
    video_url: str  # "" = sem vídeo
    cover_url: str
    download_audio: bool
    download_cover: bool
    download_video: bool
    park_video: bool  # tirar o vídeo existente da pasta (item sem vídeo)


def load_manifest(folder: Path) -> dict[str, Any]:
    p = folder / MANIFEST
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_manifest(folder: Path, data: dict[str, Any]) -> None:
    (folder / MANIFEST).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _own(folder: Path, basename: str, exts: tuple[str, ...]) -> list[Path]:
    """Arquivos de mídia gerados por nós (mesmo basename da pasta)."""
    if not folder.is_dir():
        return []
    return [f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in exts and f.stem == basename]


def plan_media(item: Item, folder: Path, settings: Settings, audio_url: str) -> MediaPlan:
    man = load_manifest(folder)
    chk = check_folder(folder) if folder.exists() else None
    want_video = not item.no_video and not settings.cover_only
    video_url = (item.video_url or audio_url) if want_video else ""

    has_audio = bool(chk and chk.audio)
    has_cover = bool(chk and chk.cover)
    has_video = bool(chk and chk.video)

    audio_changed = bool(man) and man.get("audio_url") != audio_url
    download_audio = not has_audio or audio_changed

    explicit_cover = item.cover_url
    cover_changed = man.get("cover_url", "") != explicit_cover or (not explicit_cover and audio_changed)
    download_cover = not has_cover or (bool(man) and cover_changed)

    video_changed = bool(man) and (
        man.get("video_url") != video_url or int(man.get("max_height", 0)) != settings.video_max_height
    )
    download_video = want_video and (not has_video or video_changed)

    return MediaPlan(
        audio_url=audio_url,
        video_url=video_url,
        cover_url=explicit_cover,
        download_audio=download_audio,
        download_cover=download_cover,
        download_video=download_video,
        park_video=not want_video and has_video,
    )


# vídeo com duração até 5 s diferente da faixa = mesma versão (clipes têm intro/final extras)
DURATION_MATCH_S = 5


def resolve_audio_url(item: Item, searcher: Searcher) -> str:
    if item.audio_url:
        return item.audio_url
    results = searcher(f"{item.artist} {item.title}")
    if not results:
        raise RuntimeError("Nenhum vídeo encontrado no YouTube para esta música")
    if item.duration_s:  # duração conhecida (ex.: do Spotify): prefere a mesma versão
        same = [r for r in results if r.duration_s and abs(r.duration_s - item.duration_s) <= DURATION_MATCH_S]
        if same:
            return same[0].audio_url
    return results[0].audio_url


def _remove(files: list[Path], ws: Workspace) -> None:
    root = ws.root.resolve()
    for f in files:
        if root in f.resolve().parents:
            f.unlink(missing_ok=True)


def _park_video(folder: Path, basename: str, ws: Workspace) -> None:
    parked = folder / PARKED_DIR
    parked.mkdir(exist_ok=True)
    for f in _own(folder, basename, VIDEO_EXTENSIONS):
        shutil.move(str(f), parked / f.name)


def _unpark_video(folder: Path, basename: str) -> bool:
    parked = folder / PARKED_DIR
    for f in _own(parked, basename, VIDEO_EXTENSIONS):
        shutil.move(str(f), folder / f.name)
        return True
    return False


def ensure_media(
    item: Item,
    settings: Settings,
    ws: Workspace,
    *,
    searcher: Searcher = youtube.search,
    downloader: Downloader = youtube.download,
    progress: ProgressFn | None = None,
) -> tuple[MediaPlan, dict]:
    """Baixa o que falta para `item`. Devolve (plano, checklist da pasta)."""
    folder = ws.input / item.folder_name
    folder.mkdir(parents=True, exist_ok=True)
    basename = item.folder_name

    audio_url = resolve_audio_url(item, searcher)

    # item voltou a ter vídeo: reaproveita o que estava guardado
    man = load_manifest(folder)
    if not item.no_video and not settings.cover_only and not check_folder(folder).video:
        if man.get("video_url") == (item.video_url or audio_url) and _unpark_video(folder, basename):
            log.info("video_unparked", extra={"fields": {"folder": basename}})

    plan = plan_media(item, folder, settings, audio_url)

    if plan.park_video:
        _park_video(folder, basename, ws)
    if plan.download_audio:
        _remove(_own(folder, basename, AUDIO_EXTENSIONS) + list(folder.glob(f"{glob_escape(basename)}*.srt")), ws)
    if plan.download_cover:
        _remove(_own(folder, basename, IMAGE_EXTENSIONS), ws)
    if plan.download_video:
        _remove(_own(folder, basename, VIDEO_EXTENSIONS), ws)

    if plan.download_audio or plan.download_cover or plan.download_video:
        req = youtube.DownloadRequest(
            dest=folder,
            basename=basename,
            audio_url=audio_url,
            video_url=plan.video_url if plan.download_video else "",
            cover_url=plan.cover_url,
            max_height=settings.video_max_height,
            ffmpeg_path=settings.ffmpeg_path,
            want_audio=plan.download_audio,
            want_cover=plan.download_cover,
            want_subs=plan.download_audio,
        )
        res = downloader(req, progress)
        download_warnings = list(getattr(res, "warnings", []) or [])
    else:
        download_warnings = []

    new_man = {
        **load_manifest(folder),
        "audio_url": audio_url,
        "cover_url": plan.cover_url,
        "max_height": settings.video_max_height,
    }
    if plan.video_url:
        new_man["video_url"] = plan.video_url
    save_manifest(folder, new_man)

    chk = check_folder(folder)
    if not chk.audio:
        raise RuntimeError("Download terminou sem arquivo de áudio")
    if not chk.cover:
        raise RuntimeError("Download terminou sem capa")
    if plan.video_url and not chk.video:
        raise RuntimeError("Download terminou sem vídeo")
    return plan, {**chk.summary(), "download_warnings": download_warnings}


def glob_escape(s: str) -> str:
    return "".join(f"[{c}]" if c in "[]*?" else c for c in s)
