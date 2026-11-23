"""Etapa do .txt UltraStar.

1. `fetch_txt` (antes da mídia): baixa o .txt do USDB (com os links de mídia da
   tag #VIDEO) ou confere o importado. Sem .txt, o runner gera um depois da mídia.
2. `write_normalized_txt` (depois da mídia): grava `<pasta>.txt` em UTF-8 com
   as tags #MP3/#AUDIO/#VIDEO/#COVER apontando para os arquivos locais e o
   #GAP manual, se houver. Refeito a cada execução (é barato).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from letssing.paths import Workspace
from letssing.pipeline import media
from letssing.pipeline.validation import check_folder
from letssing.playlist import Item, PlaylistRepository, TxtSource
from letssing.playlist.models import GENERATED_SOURCES
from letssing.sources.manual_import import ORIGINAL_TXT
from letssing.ultrastar.txt import normalize, parse_bytes

log = logging.getLogger("letssing.txt")


class TxtProvider(Protocol):
    def get_txt(self, song_id: str) -> tuple: ...

    @staticmethod
    def media_from_txt(song, song_id: str) -> dict[str, str]: ...


def original_path(ws: Workspace, item: Item) -> Path:
    return ws.input / item.folder_name / ORIGINAL_TXT


def fetch_txt(item: Item, ws: Workspace, repo: PlaylistRepository | None = None,
              usdb: TxtProvider | None = None) -> tuple[Item, str]:
    """Devolve (item atualizado, descrição do que foi feito)."""
    folder = ws.input / item.folder_name
    folder.mkdir(parents=True, exist_ok=True)
    orig = folder / ORIGINAL_TXT
    man = media.load_manifest(folder)

    if item.txt_source == TxtSource.USDB:
        if not item.txt_ref:
            raise RuntimeError("Item do USDB sem ID da música")
        if orig.exists() and man.get("txt_ref") == f"usdb:{item.txt_ref}":
            return item, "txt do USDB já baixado"
        if usdb is None:
            raise RuntimeError("Cliente USDB indisponível")
        song, raw = usdb.get_txt(item.txt_ref)
        orig.write_bytes(raw)
        media.save_manifest(folder, {**man, "txt_ref": f"usdb:{item.txt_ref}"})
        links = usdb.media_from_txt(song, item.txt_ref)
        changes = {k: v for k, v in links.items() if v and not getattr(item, k)}
        extra = {}
        if song.is_duet and not item.is_duet:
            extra["is_duet"] = True
        if song.language and not item.language:
            extra["language"] = song.language
        if (changes or extra) and repo is not None:
            item = repo.set_fields(item.id, **changes, **extra) or item
        got = ", ".join(k.removesuffix("_url") for k in changes) or "nenhum (usa busca no YouTube)"
        return item, f"txt baixado do USDB #{item.txt_ref}; links de mídia do USDB: {got}"

    if item.txt_source in (TxtSource.ULTRASTAR_ES, TxtSource.MANUAL):
        if not orig.exists():
            raise RuntimeError("Importe o .txt (botão 'Importar .txt/.zip' na playlist)")
        return item, "txt importado manualmente"

    # NONE / gerados: o runner gera depois da mídia (legendas do YouTube ou UltraSinger)
    ref = str(man.get("txt_ref", ""))
    if item.txt_source in GENERATED_SOURCES and orig.exists() and ref.startswith(("ultrasinger", "captions:", "lrclib:")):
        return item, "txt gerado anteriormente já existe"
    return item, "sem .txt: será gerado depois da mídia"


def write_normalized_txt(item: Item, ws: Workspace) -> bool:
    """Grava o .txt final. Devolve False se não há .txt original."""
    folder = ws.input / item.folder_name
    orig = folder / ORIGINAL_TXT
    if not orig.exists():
        return False
    song, enc = parse_bytes(orig.read_bytes())
    problems = song.problems()
    if problems:
        raise RuntimeError("TXT inválido: " + "; ".join(problems))
    chk = check_folder(folder)
    if not chk.audio or not chk.cover:
        raise RuntimeError("Faltam áudio ou capa para montar o .txt")
    normalized = normalize(
        song,
        audio_file=chk.audio[0].name,
        cover_file=chk.cover[0].name,
        video_file=chk.video[0].name if chk.video else "",
        gap_ms=item.gap_ms,
    )
    target = folder / f"{item.folder_name}.txt"
    for other in chk.txt:  # um único .txt na pasta (o conversor usa o último)
        if other != target:
            other.unlink()
    data = normalized.to_bytes()
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)
        log.info("txt_written", extra={"fields": {"folder": item.folder_name, "source_encoding": enc}})
    return True
