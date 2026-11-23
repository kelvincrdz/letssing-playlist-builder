from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from letssing.jobs import Job, JobBusy, JobManager
from letssing.paths import Workspace
from letssing.converter import package
from letssing.pipeline.runner import run_generate

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

FINAL = ("done", "failed", "cancelled")


class GenerateBody(BaseModel):
    item_ids: list[int] | None = None  # vazio = playlist inteira


def _jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def _get(request: Request, job_id: str) -> Job:
    job = _jobs(request).get(job_id)
    if not job:
        raise HTTPException(404, "Tarefa não encontrada")
    return job


@router.post("/generate", status_code=202)
def generate(request: Request, body: GenerateBody | None = None) -> dict[str, Any]:
    repo = request.app.state.playlist
    settings = request.app.state.config.load()
    items = repo.list()
    if body and body.item_ids:
        wanted = set(body.item_ids)
        items = [i for i in items if i.id in wanted]
    if not items:
        raise HTTPException(400, "Playlist vazia")
    ws = Workspace(Path(settings.workspace_dir))
    st = request.app.state
    try:
        job = _jobs(request).start(
            "generate", len(items),
            lambda j: run_generate(j, items, repo, settings, ws, ensure=st.ensure_media,
                                   run_converter=st.run_converter, prepare_tool=st.prepare_tool,
                                   generate_txt=st.generate_txt, usdb=st.usdb),
        )
    except JobBusy as e:
        raise HTTPException(409, detail={"message": "Já existe uma geração em andamento", "job_id": str(e)}) from None
    return job.snapshot()


@router.get("/dlc/latest")
def dlc_latest_info(request: Request) -> dict[str, Any] | None:
    ws = Workspace(Path(request.app.state.config.load().workspace_dir))
    z = package.latest_zip(ws.dist)
    if not z:
        return None
    st = z.stat()
    return {"name": z.name, "size": st.st_size, "mtime": st.st_mtime, "url": "/api/jobs/dlc/latest.zip"}


@router.get("/dlc/latest.zip")
def dlc_latest_download(request: Request) -> FileResponse:
    ws = Workspace(Path(request.app.state.config.load().workspace_dir))
    z = package.latest_zip(ws.dist)
    if not z:
        raise HTTPException(404, "Nenhum pacote gerado ainda")
    return FileResponse(z, media_type="application/zip", filename=z.name)


@router.get("/current")
def current(request: Request) -> dict[str, Any] | None:
    job = _jobs(request).current
    return job.snapshot() if job else None


@router.get("/{job_id}")
def get_job(request: Request, job_id: str) -> dict[str, Any]:
    return _get(request, job_id).snapshot()


@router.post("/{job_id}/cancel")
def cancel(request: Request, job_id: str) -> dict[str, Any]:
    job = _get(request, job_id)
    job.cancel.set()
    job.log("Cancelamento pedido; termina após a música atual.", level="warn")
    return job.snapshot()


@router.get("/{job_id}/events")
async def events(request: Request, job_id: str, after: int = 0) -> StreamingResponse:
    job = _get(request, job_id)

    async def stream() -> AsyncIterator[str]:
        seq = after
        while True:
            if await request.is_disconnected():
                return
            evs = await anyio.to_thread.run_sync(job.events_after, seq, 10.0)
            if not evs:
                if job.state in FINAL:
                    return
                yield ": keepalive\n\n"
                continue
            for ev in evs:
                seq = ev["seq"]
                yield f"id: {seq}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if job.state in FINAL and evs[-1].get("type") == "state":
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
