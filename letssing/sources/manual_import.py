"""Importação manual de `.txt` ou `.zip` (ex.: baixado do UltraStar-ES no navegador).

O UltraStar-ES fica atrás de um desafio do Cloudflare, então não é acessado
automaticamente: o usuário baixa o arquivo e envia pela UI.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from letssing.naming import duplicate_key
from letssing.pipeline.validation import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from letssing.ultrastar.txt import UltraStarSong, parse_bytes

log = logging.getLogger("letssing.import")

MAX_UPLOAD = 300 * 1024 * 1024
MAX_UNZIPPED = 600 * 1024 * 1024
ORIGINAL_TXT = ".letssing.original"  # sem extensão .txt: o conversor pegaria qualquer *.txt


class ImportError_(Exception):
    pass


@dataclass
class ImportedPackage:
    song: UltraStarSong
    txt_raw: bytes
    encoding: str
    txt_name: str
    media: dict[str, tuple[str, bytes]] = field(default_factory=dict)  # tipo -> (nome, bytes)
    notes: list[str] = field(default_factory=list)


def _pick_txt(candidates: list[tuple[str, bytes]], artist: str, title: str) -> tuple[str, bytes]:
    parsed = []
    for name, raw in candidates:
        song, _ = parse_bytes(raw)
        if not song.problems():
            parsed.append((name, raw, song))
    if not parsed:
        raise ImportError_("Nenhum .txt UltraStar válido no arquivo")
    if artist and title:
        want = duplicate_key(artist, title)
        for name, raw, song in parsed:
            if duplicate_key(song.artist, song.title) == want:
                return name, raw
    # sem correspondência: prefere o que não é a versão [MULTI]/dueto separada
    parsed.sort(key=lambda t: ("[multi]" in t[0].lower(), t[0]))
    return parsed[0][0], parsed[0][1]


def read_package(filename: str, data: bytes, artist: str = "", title: str = "") -> ImportedPackage:
    if len(data) > MAX_UPLOAD:
        raise ImportError_("Arquivo grande demais")
    lower = filename.lower()
    if lower.endswith(".txt"):
        song, enc = parse_bytes(data)
        if song.problems():
            raise ImportError_("TXT inválido: " + "; ".join(song.problems()))
        return ImportedPackage(song=song, txt_raw=data, encoding=enc, txt_name=filename)
    if not lower.endswith(".zip"):
        raise ImportError_("Envie um arquivo .txt ou .zip")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_("ZIP corrompido") from None
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
        if sum(i.file_size for i in infos) > MAX_UNZIPPED:
            raise ImportError_("Conteúdo do ZIP grande demais")
        # nada é extraído por caminho: lemos só os bytes (sem risco de zip-slip)
        txts = [(Path(i.filename).name, zf.read(i)) for i in infos if i.filename.lower().endswith(".txt")]
        name, raw = _pick_txt(txts, artist, title)
        song, enc = parse_bytes(raw)
        pkg = ImportedPackage(song=song, txt_raw=raw, encoding=enc, txt_name=name)
        if len(txts) > 1:
            pkg.notes.append(f"{len(txts)} arquivos .txt no ZIP; usado: {name}")
        for kind, exts in (("audio", AUDIO_EXTENSIONS), ("video", VIDEO_EXTENSIONS), ("cover", IMAGE_EXTENSIONS)):
            matches = sorted((i for i in infos if Path(i.filename).suffix.lower() in exts),
                             key=lambda i: -i.file_size)
            if matches:
                pkg.media[kind] = (Path(matches[0].filename).name, zf.read(matches[0]))
    return pkg


def install_package(pkg: ImportedPackage, folder: Path, basename: str) -> list[str]:
    """Grava o .txt original e a mídia do pacote na pasta da música."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ORIGINAL_TXT).write_bytes(pkg.txt_raw)
    written = [pkg.txt_name]
    exts_by_kind = {"audio": AUDIO_EXTENSIONS, "video": VIDEO_EXTENSIONS, "cover": IMAGE_EXTENSIONS}
    for kind, (name, data) in pkg.media.items():
        ext = Path(name).suffix.lower()
        # remove a mídia anterior do mesmo tipo para não haver dois arquivos
        for old in folder.iterdir():
            if old.is_file() and old.stem == basename and old.suffix.lower() in exts_by_kind[kind]:
                old.unlink()
        (folder / f"{basename}{ext}").write_bytes(data)
        written.append(name)
    log.info("package_installed", extra={"fields": {"folder": basename, "files": written}})
    return written
