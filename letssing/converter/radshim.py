"""Intermediário entre o ultrastar2singIt e o RAD Video Tools (radvideo64.exe).

O conversor (v1.0.2, ConvertFiles.create_video_bink) chama:
    radvideo64.exe binkc <in> <out.bk2> /V200 /(1280 /)720 /L-1 [/D<pct>] [/Q<q>] /#
O RAD atual não aceita `/(`, `/)` nem `/Q`: em vez de falhar, abre a janela de
ajuda e espera alguém fechar — o conversor trava. Testado em 2026-11-22.

Este módulo vira o executável `letssing-rad.exe` (console script). A app passa
esse .exe ao conversor como `--rad-path`; ele traduz os parâmetros antigos para
os equivalentes documentados na ajuda do próprio `binkc` e chama o RAD real,
com tempo limite. A tradução segue a intenção do código do conversor:

    /(W /)H   -> /ScaleW,H              (largura/altura de saída)
    /Q<q>     -> /DataRateByQuality<q>  (qualidade 0.001–1.0)
    /D<n>     -> /DataRateByPercent<n>  (o conversor passa uma porcentagem 1–200;
                                         no RAD atual /D é bytes por segundo)
    /L-1      -> /NoSound               (variável `remove_sound` no conversor)
    + /O      (sobrescrever sem perguntar, para nunca abrir diálogo)

Variáveis de ambiente (definidas pela app ao rodar o conversor):
    LETSSING_RAD_REAL     caminho do radvideo64.exe verdadeiro
    LETSSING_RAD_LOG      arquivo .jsonl onde cada chamada é registrada
    LETSSING_RAD_TIMEOUT  segundos por vídeo (padrão 1800)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT_S = 1800
TIMEOUT_EXIT = 124

_SCALE_W = re.compile(r"^/\((\d+)$")
_SCALE_H = re.compile(r"^/\)(\d+)$")
_QUALITY = re.compile(r"^/Q(\d*\.?\d+)$", re.IGNORECASE)
_PERCENT = re.compile(r"^/D(\d+)$", re.IGNORECASE)


def translate(args: list[str]) -> list[str]:
    """Converte a linha de comando antiga do conversor para a sintaxe atual do binkc."""
    out: list[str] = []
    width = height = None
    scale_at = None
    for a in args:
        if m := _SCALE_W.match(a):
            width = m.group(1)
            scale_at = len(out) if scale_at is None else scale_at
        elif m := _SCALE_H.match(a):
            height = m.group(1)
            scale_at = len(out) if scale_at is None else scale_at
        elif m := _QUALITY.match(a):
            out.append(f"/DataRateByQuality{m.group(1)}")
        elif m := _PERCENT.match(a):
            out.append(f"/DataRateByPercent{m.group(1)}")
        elif a == "/L-1":
            out.append("/NoSound")
        else:
            out.append(a)
    if scale_at is not None:
        out.insert(scale_at, f"/Scale{width or 0},{height or 0}")
    if out[:1] == ["binkc"]:
        if not any(a.upper() in ("/O", "/OVERWRITE") for a in out):
            out.append("/O")
        if "/#" in out:
            out.remove("/#")
        out.append("/#")  # fechar a janela ao terminar
    return out


def _log(entry: dict) -> None:
    path = os.environ.get("LETSSING_RAD_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def run(argv: list[str], real: str, timeout: float) -> int:
    new_args = translate(argv)
    entry: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "args": argv, "translated": new_args}
    if not real or not Path(real).is_file():
        entry.update(rc=2, error=f"radvideo64.exe não encontrado: {real!r}")
        _log(entry)
        print(entry["error"], file=sys.stderr)
        return 2
    t0 = time.time()
    proc = subprocess.Popen([real, *new_args], stdin=subprocess.DEVNULL)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        proc.wait()
        rc = TIMEOUT_EXIT
        entry["error"] = f"RAD não terminou em {int(timeout)} s e foi encerrado"
        if len(new_args) >= 3 and new_args[0] == "binkc":
            Path(new_args[2]).unlink(missing_ok=True)  # .bk2 parcial
    entry.update(rc=rc, seconds=round(time.time() - t0, 1))
    _log(entry)
    return rc


def main() -> None:
    real = os.environ.get("LETSSING_RAD_REAL", "")
    try:
        timeout = float(os.environ.get("LETSSING_RAD_TIMEOUT") or DEFAULT_TIMEOUT_S)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_S
    raise SystemExit(run(sys.argv[1:], real, timeout))
