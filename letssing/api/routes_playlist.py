from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from letssing.playlist import DuplicateError, Item, ItemCreate, ItemPatch, PlaylistRepository

router = APIRouter(prefix="/api/playlist", tags=["playlist"])


def _repo(request: Request) -> PlaylistRepository:
    return request.app.state.playlist


@router.get("")
def list_items(request: Request) -> list[Item]:
    return _repo(request).list()


@router.get("/check-duplicate")
def check_duplicate(request: Request, artist: str, title: str) -> dict[str, Any]:
    existing = _repo(request).find_duplicate(artist, title)
    return {"duplicate": existing is not None, "existing": existing}


@router.post("", status_code=201)
def add_item(request: Request, body: ItemCreate) -> Item:
    try:
        return _repo(request).add(body)
    except DuplicateError as e:
        raise HTTPException(409, detail={"message": str(e), "existing_id": e.existing.id}) from None


@router.patch("/{item_id}")
def patch_item(request: Request, item_id: int, body: ItemPatch) -> Item:
    item = _repo(request).update(item_id, body)
    if item is None:
        raise HTTPException(404, "Item não encontrado")
    return item


@router.delete("/{item_id}", status_code=204)
def delete_item(request: Request, item_id: int) -> Response:
    if not _repo(request).delete(item_id):
        raise HTTPException(404, "Item não encontrado")
    return Response(status_code=204)


@router.get("/export")
def export_playlist(request: Request) -> JSONResponse:
    filename = f"playlist-letssing-{date.today().isoformat()}.json"
    return JSONResponse(
        _repo(request).export(),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
def import_playlist(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return _repo(request).import_(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{item_id}/usdb-match")
def usdb_match(request: Request, item_id: int) -> dict[str, Any]:
    """Procura a música no USDB; achando, passa a usar o .txt de lá (feito por
    pessoas) e os links de mídia que ele indica, no lugar do .txt gerado."""
    from letssing.playlist import Status, TxtSource
    from letssing.sources.usdb import UsdbError, UsdbLoginRequired, find_match

    repo = _repo(request)
    item = repo.get(item_id)
    if item is None:
        raise HTTPException(404, "Item não encontrado")
    if item.txt_source == TxtSource.USDB:
        return {"matched": True, "already": True, "item": item}
    try:
        found = find_match(request.app.state.usdb.search, item.artist, item.title)
    except UsdbLoginRequired as e:
        raise HTTPException(401, str(e)) from None
    except UsdbError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        raise HTTPException(502, f"USDB inacessível: {e}") from None
    if found is None:
        return {"matched": False, "item": item}
    # o tempo do .txt do USDB segue o áudio que ele indica: os links atuais saem
    updated = repo.set_fields(item.id, txt_source=TxtSource.USDB, txt_ref=found.source_id,
                              audio_url="", video_url="", cover_url="",
                              status=Status.PENDENTE, error_message="")
    return {"matched": True, "usdb": found.model_dump(), "item": updated}
