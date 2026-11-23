"""Imita o ultrastar2singIt --cli para testes (lê os mesmos argumentos)."""

import argparse
import json
import shutil
import sys
from pathlib import Path

p = argparse.ArgumentParser()
for flag in ("--dlc-id", "--core-id", "--dlc-json-name", "--ffmpeg-path", "--rad-path", "--input",
             "--output", "--pitch-correction", "--max-video-size"):
    p.add_argument(flag)
p.add_argument("--cli", action="store_true")
p.add_argument("--still-video", action="store_true")
a = p.parse_args()

cfg = json.loads(Path("config.yml").read_text(encoding="utf-8"))
assert cfg["conversion_tweaks"]["enable"] is True

out = Path(a.output)
if out.exists():
    shutil.rmtree(out)  # o real também apaga a saída
for d in sorted(Path(a.input).iterdir()):
    if not d.is_dir() or d.name.startswith(("_", ".")) or " - " not in d.name:
        continue
    artist, title = d.name.split(" - ", 1)
    nid = "".join(w[0].upper() for w in artist.split()) + "".join(c.lower() for c in title if c.isalnum())
    print(nid, flush=True)
    if "Falha" in d.name:
        print(f"Error with directory {d.name}: simulated failure", flush=True)
        continue
    (d / f"{nid}.ogg").write_bytes(b"cache")  # cache como o real
    base = out / a.dlc_id / "romfs" / "Songs"
    for sub, name in (("audio", f"{nid}.ogg"), ("audio_preview", f"{nid}_preview.ogg"),
                      ("covers", f"{nid}.png"), ("vxla", f"{nid}.vxla"), ("videos", f"{nid}.bk2")):
        (base / sub).mkdir(parents=True, exist_ok=True)
        (base / sub / name).write_bytes(b"x")
    (out / a.dlc_id / "romfs" / f"{a.dlc_json_name}.json").write_text("[]")
print("done", file=sys.stderr)
