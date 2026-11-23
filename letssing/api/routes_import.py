"""Importação manual de .txt/.zip (corpo da requisição = bytes do arquivo)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from letssing.paths import Workspace
from letssing.playlist import DuplicateError, ItemCreate, Status, TxtSource
from letssing.sources.manual_import import MAX_UPLOAD, ImportError_, install_package, read_package

router = APIRouter(prefix="/api", tags=["import"])

_SOURCES = {"ultrastar_es": TxtSource.ULTRASTAR_ES, "manual": TxtSource.MANUAL}


async def _body(request: Request) -> bytes:
    data = await request.body()
    if not data:
        raise HTTPException(400, "Arquivo vazio")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Arquivo grande demais")
    return data


def _ws(request: Request) -> Workspace:
    return Workspace(Path(request.app.state.config.load().workspace_dir))


def _source(source: str) -> TxtSource:
    if source not in _SOURCES:
        raise HTTPException(400, "source deve ser ultrastar_es ou manual")
    return _SOURCES[source]


def _install(request: Request, item_id: int, pkg, src: TxtSource, filename: str) -> dict[str, Any]:
    repo = request.app.state.playlist
    item = repo.get(item_id)
    assert item is not None
    written = install_package(pkg, _ws(request).input / item.folder_name, item.folder_name)
    changes: dict[str, Any] = {"txt_source": src.value, "txt_ref": filename}
    if pkg.song.is_duet:
        changes["is_duet"] = True
    if pkg.song.language and not item.language:
        changes["language"] = pkg.song.language
    repo.set_fields(item_id, **changes)
    item = repo.set_status(item_id, Status.PENDENTE)
    return {
        "item": item,
        "files": written,
        "media": sorted(pkg.media),
        "notes": pkg.notes,
        "encoding": pkg.encoding,
        "song": {"artist": pkg.song.artist, "title": pkg.song.title, "language": pkg.song.language,
                 "is_duet": pkg.song.is_duet},
    }


@router.post("/playlist/{item_id}/txt")
async def import_into_item(request: Request, item_id: int, filename: str, source: str = "ultrastar_es") -> dict[str, Any]:
    src = _source(source)
    item = request.app.state.playlist.get(item_id)
    if item is None:
        raise HTTPException(404, "Item não encontrado")
    data = await _body(request)
    try:
        pkg = read_package(filename, data, item.artist, item.title)
    except ImportError_ as e:
        raise HTTPException(400, str(e)) from None
    return _install(request, item_id, pkg, src, filename)


@router.post("/import", status_code=201)
async def import_new(request: Request, filename: str, source: str = "ultrastar_es") -> dict[str, Any]:
    """Cria uma música nova a partir do #ARTIST/#TITLE do .txt enviado."""
    src = _source(source)
    data = await _body(request)
    try:
        pkg = read_package(filename, data)
    except ImportError_ as e:
        raise HTTPException(400, str(e)) from None
    repo = request.app.state.playlist
    try:
        item = repo.add(ItemCreate(artist=pkg.song.artist, title=pkg.song.title, language=pkg.song.language,
                                   is_duet=pkg.song.is_duet, txt_source=src, txt_ref=filename))
    except DuplicateError as e:
        raise HTTPException(409, detail={"message": f"{e} — use 'Importar .txt' nessa linha da playlist",
                                         "existing_id": e.existing.id}) from None
    return _install(request, item.id, pkg, src, filename)
