from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from letssing.config import ConfigManager, Settings
from letssing.converter import dlc_catalog, singit
from letssing.generators import ultrasinger

router = APIRouter(prefix="/api/config", tags=["config"])


def _mgr(request: Request) -> ConfigManager:
    return request.app.state.config


def _tool(label: str, path: str, *, required: bool, on_path: str | None = None, is_dir: bool = False) -> dict:
    if not path:
        found = shutil.which(on_path) if on_path else None
        if found:
            return {"label": label, "ok": True, "message": f"Encontrado no PATH: {found}"}
        return {"label": label, "ok": not required, "message": "Não configurado" + (" (obrigatório)" if required else "")}
    p = Path(path)
    ok = p.is_dir() if is_dir else p.is_file()
    return {"label": label, "ok": ok, "message": "OK" if ok else "Caminho não encontrado"}


def _rad_status(s: Settings) -> dict:
    label = "RAD Video Tools (radvideo64.exe)"
    # O conversor gera .bk2 também no modo "só capa" (vídeo parado), então
    # o RAD é obrigatório sempre que o formato de saída é JSON (2024+).
    required = singit.output_format(s) == "json"
    if not s.rad_path:
        found = singit.resolve_rad(s)
        if found:
            return {"label": label, "ok": True, "message": f"Encontrado no caminho padrão: {found}"}
    return _tool(label, s.rad_path, required=required)


def _ultrasinger_dir_status(s: Settings) -> dict:
    label = "UltraSinger (pasta)"
    if not s.ultrasinger_dir:
        root = ultrasinger.ultrasinger_root(s)
        if root:
            return {"label": label, "ok": True, "message": f"Encontrado na pasta padrão: {root}"}
        return {"label": label, "ok": True,
                "message": "Não configurado: músicas sem .txt (e sem USDB) serão puladas"}
    ok = (Path(s.ultrasinger_dir) / ultrasinger.SCRIPT).is_file()
    return {"label": label, "ok": ok,
            "message": "OK" if ok else f"{ultrasinger.SCRIPT.as_posix()} não encontrado nesta pasta"}


def _ultrasinger_python_status(s: Settings) -> dict:
    label = "UltraSinger (python.exe)"
    tool = ultrasinger.resolve_tool(s)
    if tool is None:
        return {"label": label, "ok": True, "message": "—"}
    ok = tool.python.is_file()
    where = "configurado" if s.ultrasinger_python else "padrão: .venv da pasta"
    return {"label": label, "ok": ok, "message": f"OK ({where})" if ok else f"Não encontrado: {tool.python}"}


def _view(mgr: ConfigManager, s: Settings) -> dict[str, Any]:
    warnings = dlc_catalog.consistency_warnings(s.core_title_id, s.dlc_title_id, s.dlc_json_name)
    if not (s.dlc_title_id and s.dlc_json_name):
        warnings.append("Preencha TitleID do DLC e DLC JSON Name (necessários para Let's Sing 2024+).")
    if not s.core_title_id and not dlc_catalog.find(s.dlc_title_id or ""):
        warnings.append("TitleID do jogo vazio: necessário quando o DLC não é conhecido pelo conversor.")
    tools = {
        "ffmpeg": _tool("FFmpeg", s.ffmpeg_path, required=True, on_path="ffmpeg"),
        "rad": _rad_status(s),
        "converter": _tool("ultrastar2singIt", s.converter_path, required=True,
                           is_dir=bool(s.converter_path) and Path(s.converter_path).is_dir()),
        "ultrasinger_dir": _ultrasinger_dir_status(s),
        "ultrasinger_python": _ultrasinger_python_status(s),
    }
    return {
        "settings": s.model_dump(),
        "usdb_password_set": bool(mgr.get_usdb_password(s)),
        "warnings": warnings,
        "tools": tools,
    }


@router.get("")
def get_config(request: Request) -> dict[str, Any]:
    mgr = _mgr(request)
    return _view(mgr, mgr.load())


@router.put("")
def put_config(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    mgr = _mgr(request)
    old = mgr.load()
    password = body.pop("usdb_password", None)
    try:
        new = Settings.model_validate({**old.model_dump(), **body})
    except ValidationError as e:
        raise HTTPException(422, detail=[
            {"field": ".".join(map(str, err["loc"])), "message": err["msg"].removeprefix("Value error, ")}
            for err in e.errors()
        ]) from None
    # usuário trocou: leva a senha guardada junto, se nenhuma nova foi enviada
    if new.usdb_username != old.usdb_username and not password:
        prev = mgr.get_usdb_password(old)
        if prev:
            mgr.set_usdb_password(new, prev)
            mgr.clear_usdb_password(old)
    mgr.save(new)
    if password:
        mgr.set_usdb_password(new, str(password))
    return _view(mgr, new)


@router.delete("/usdb-password")
def delete_password(request: Request) -> dict[str, Any]:
    mgr = _mgr(request)
    s = mgr.load()
    mgr.clear_usdb_password(s)
    return _view(mgr, s)


@router.post("/usdb-test")
def usdb_test(request: Request) -> dict[str, Any]:
    from letssing.sources.usdb import UsdbError

    try:
        user = request.app.state.usdb.check_login()
    except UsdbError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        raise HTTPException(502, f"USDB inacessível: {e}") from None
    return {"ok": True, "user": user}


@router.get("/dlc-suggestions")
def dlc_suggestions(edition: str | None = None) -> list[dict]:
    return dlc_catalog.suggestions(edition)
