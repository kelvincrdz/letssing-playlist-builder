"""Empacota `workspace/output/` como `atmosphere/contents/...` num .zip.

Descompactando o .zip na RAIZ do cartão SD, as pastas caem em
`sd:/atmosphere/contents/<TITLEID>/romfs/...` (LayeredFS do Atmosphere).
"""

from __future__ import annotations

import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger("letssing.package")

TITLE_ID_DIR = re.compile(r"^[0-9A-F]{16}$")
_STORED = {".bk2", ".ogg", ".png", ".mp4", ".jpg"}  # já comprimidos


def build_zip(output_dir: Path, dist_dir: Path, now: datetime | None = None) -> Path:
    title_dirs = sorted(d for d in output_dir.iterdir() if d.is_dir() and TITLE_ID_DIR.match(d.name))
    if not title_dirs:
        raise RuntimeError("Saída do conversor vazia: nenhuma pasta de TitleID gerada")
    dist_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = dist_dir / f"letssing-dlc-{stamp}.zip"
    tmp = target.with_suffix(".zip.part")
    count = 0
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for tdir in title_dirs:
            for f in sorted(p for p in tdir.rglob("*") if p.is_file()):
                arc = "atmosphere/contents/" + f.relative_to(output_dir).as_posix()
                ctype = zipfile.ZIP_STORED if f.suffix.lower() in _STORED else zipfile.ZIP_DEFLATED
                z.write(f, arc, compress_type=ctype)
                count += 1
    tmp.replace(target)
    log.info("zip_built", extra={"fields": {"path": str(target), "files": count}})
    return target


def latest_zip(dist_dir: Path) -> Path | None:
    zips = sorted(dist_dir.glob("letssing-dlc-*.zip")) if dist_dir.exists() else []
    return zips[-1] if zips else None
