"""Checklist de uma pasta de música, igual ao que o conversor exige.

Obrigatórios: .txt UltraStar, áudio, capa. Vídeo é opcional (sem vídeo o
conversor gera um vídeo estático com a capa).

Os arquivos de cache que o próprio conversor grava na pasta (`<name_id>.ogg`,
`<name_id>.png`, `_preview.ogg`…) não contam como entrada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from letssing.naming import SEPARATOR, converter_name_id

# Mesmas extensões do SupportedFormats.py do ultrastar2singIt v1.0.2
VIDEO_EXTENSIONS = (".mp4", ".mpeg", ".avi", ".flv", ".divx", ".mkv", ".webm")
AUDIO_EXTENSIONS = (".mp3", ".ogg", ".aac", ".wav", ".flac", ".m4a", ".opus")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp")
TXT_EXTENSION = ".txt"


@dataclass
class FolderCheck:
    folder: Path
    txt: list[Path] = field(default_factory=list)
    audio: list[Path] = field(default_factory=list)
    cover: list[Path] = field(default_factory=list)
    video: list[Path] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        out = []
        if not self.txt:
            out.append("txt")
        if not self.audio:
            out.append("áudio")
        if not self.cover:
            out.append("capa")
        return out

    @property
    def ok(self) -> bool:
        return not self.missing and not self.problems

    def summary(self) -> dict:
        return {
            "txt": bool(self.txt),
            "audio": bool(self.audio),
            "cover": bool(self.cover),
            "video": bool(self.video),
            "missing": self.missing,
            "problems": self.problems,
            "warnings": self.warnings,
            "ok": self.ok,
        }


def _converter_cache_names(folder_name: str) -> set[str]:
    try:
        nid = converter_name_id(folder_name)
    except IndexError:
        return set()
    return {f"{nid}{s}".lower() for s in (
        ".ogg", "_preview.ogg", ".png", "_long.png", "_InGameLoading.png", "_Result.png",
        ".mp4", ".bk2", ".vxla", "_meta.xml",
    )}


def check_folder(folder: Path) -> FolderCheck:
    chk = FolderCheck(folder=folder)
    if not folder.is_dir():
        chk.problems.append("pasta não existe")
        return chk
    if SEPARATOR not in folder.name:
        chk.problems.append("nome da pasta precisa ter ' - ' (Artista - Titulo)")
    cache = _converter_cache_names(folder.name)
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.name.lower() in cache:
            continue
        ext = f.suffix.lower()
        if ext == TXT_EXTENSION:
            chk.txt.append(f)
        elif ext in AUDIO_EXTENSIONS:
            chk.audio.append(f)
        elif ext in IMAGE_EXTENSIONS:
            chk.cover.append(f)
        elif ext in VIDEO_EXTENSIONS:
            chk.video.append(f)
    if len(chk.audio) > 1:
        chk.warnings.append("mais de um arquivo de áudio; o conversor usa o primeiro: " + chk.audio[0].name)
    if len(chk.video) > 1:
        chk.warnings.append("mais de um vídeo; o conversor usa o primeiro: " + chk.video[0].name)
    if len(chk.txt) > 1:
        chk.warnings.append("mais de um .txt; o conversor usa o último: " + chk.txt[-1].name)
    for f in chk.audio + chk.cover + chk.video + chk.txt:
        if f.stat().st_size == 0:
            chk.problems.append(f"arquivo vazio: {f.name}")
    return chk
