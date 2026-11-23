"""Área de staging: a pasta de entrada que o ultrastar2singIt de fato enxerga.

Por que não usar `workspace/input/` direto:
- o conversor grava o cache dele (`<name_id>.ogg`, `.png`, `.mp4`, `_cover.mp4`,
  `.bk2`…) dentro da pasta da música, e várias dessas extensões também são
  aceitas como ENTRADA; ele usa o primeiro arquivo de cada tipo. As entradas
  aqui se chamam `00_input.*` para virem antes do cache na listagem;
- ele não percebe quando uma entrada muda: guardamos uma impressão digital
  (tamanho + mtime) de cada entrada e apagamos só o cache afetado;
- ele converte TODA subpasta da entrada; pastas fora do lote recebem prefixo
  `_` (ignoradas por ele) e voltam quando forem selecionadas de novo.

O cache dele (principalmente o `.bk2`, que é lento) sobrevive entre execuções.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from letssing.naming import converter_name_id
from letssing.paths import Workspace
from letssing.pipeline.validation import check_folder

log = logging.getLogger("letssing.staging")

INPUT_STEM = "00_input"
MANIFEST = ".stage.json"
PARK_PREFIX = "_off_"

# cache do conversor afetado por cada entrada (sufixos após o name_id)
_CACHE_BY_INPUT = {
    "audio": ("_preview.ogg", ".ogg"),
    "video": (".bk2", ".mp4", "_cover.mp4"),
    "cover": (".png", "_long.png", "_InGameLoading.png", "_Result.png", "_cover.mp4"),
    # #GAP/#VIDEOGAP/#MEDLEY mudam o áudio cortado e a prévia
    "txt": ("_preview.ogg", ".ogg", "_cover.mp4"),
}


@dataclass
class StagedSong:
    item_id: int
    folder: Path
    name_id: str
    rebuilt: list[str] = field(default_factory=list)  # entradas que mudaram


@dataclass
class StageResult:
    songs: list[StagedSong] = field(default_factory=list)
    conflicts: dict[int, str] = field(default_factory=dict)  # item_id -> motivo


def _fingerprint(p: Path) -> list[int]:
    st = p.stat()
    return [st.st_size, st.st_mtime_ns]


def _place(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)  # hardlink: instantâneo e sem ocupar espaço
    except OSError:
        shutil.copy2(src, dst)


def _purge(folder: Path, name_id: str, suffixes: tuple[str, ...]) -> list[str]:
    removed = []
    for suf in suffixes:
        f = folder / f"{name_id}{suf}"
        if f.exists():
            f.unlink()
            removed.append(f.name)
    return removed


def stage_song(item_id: int, source: Path, ws: Workspace) -> StagedSong:
    """Sincroniza `workspace/input/<pasta>` -> `workspace/stage/<pasta>`."""
    name = source.name
    name_id = converter_name_id(name)
    target = ws.stage / name
    parked = ws.stage / f"{PARK_PREFIX}{name}"
    if not target.exists() and parked.exists():
        parked.rename(target)
    target.mkdir(parents=True, exist_ok=True)

    chk = check_folder(source)
    inputs = {
        "txt": chk.txt[-1] if chk.txt else None,
        "audio": chk.audio[0] if chk.audio else None,
        "cover": chk.cover[0] if chk.cover else None,
        "video": chk.video[0] if chk.video else None,
    }
    try:
        man = json.loads((target / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        man = {}

    song = StagedSong(item_id=item_id, folder=target, name_id=name_id)
    new_man: dict[str, object] = {}
    for kind, src in inputs.items():
        old = man.get(kind)
        # remove a entrada antiga deste tipo (a extensão pode ter mudado)
        current = [f for f in target.iterdir() if f.is_file() and f.stem == INPUT_STEM
                   and _kind_of(f) == kind]
        if src is None:
            if current or old:
                for f in current:
                    f.unlink()
                _purge(target, name_id, _CACHE_BY_INPUT[kind])
                song.rebuilt.append(kind)
            continue
        fp = {"name": src.name, "fp": _fingerprint(src)}
        dst = target / f"{INPUT_STEM}{src.suffix.lower()}"
        if old != fp or not dst.exists() or len(current) != 1:
            for f in current:
                f.unlink()
            _place(src, dst)
            if old is not None:
                _purge(target, name_id, _CACHE_BY_INPUT[kind])
            song.rebuilt.append(kind)
        new_man[kind] = fp
    (target / MANIFEST).write_text(json.dumps(new_man, indent=2), encoding="utf-8")
    return song


def _kind_of(f: Path) -> str:
    from letssing.pipeline.validation import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

    ext = f.suffix.lower()
    if ext == ".txt":
        return "txt"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in IMAGE_EXTENSIONS:
        return "cover"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return ""


def stage_batch(selected: list[tuple[int, Path]], ws: Workspace) -> StageResult:
    """Deixa em `stage/` exatamente as músicas selecionadas (as outras ficam estacionadas)."""
    ws.stage.mkdir(parents=True, exist_ok=True)
    result = StageResult()
    seen_ids: dict[str, int] = {}
    wanted: set[str] = set()
    for item_id, source in selected:
        nid = converter_name_id(source.name)
        if nid in seen_ids:
            # o conversor usaria o mesmo nome de arquivo para as duas
            result.conflicts[item_id] = (
                f"conflito de nome interno '{nid}' com outra música do lote; "
                "mude levemente o título de uma delas"
            )
            continue
        seen_ids[nid] = item_id
        wanted.add(source.name)
        result.songs.append(stage_song(item_id, source, ws))

    for d in ws.stage.iterdir():
        if d.is_dir() and not d.name.startswith(("_", ".")) and d.name not in wanted:
            parked = ws.stage / f"{PARK_PREFIX}{d.name}"
            if parked.exists():
                shutil.rmtree(parked)  # dentro do workspace
            d.rename(parked)
            log.info("stage_parked", extra={"fields": {"folder": d.name}})
    return result
