"""Imita o UltraSinger nos testes: mesmas opções e mesma estrutura de saída.

Lê -i/-o como o original (getopt), cria "<saída>/<Artista> - <Título>/<...>.txt".
Se o nome do áudio contém "Falha", termina com erro.
"""

import getopt
import os
import sys

opts, _ = getopt.getopt(sys.argv[1:], "hi:o:", [
    "whisper=", "language=", "force_cpu", "format_version=", "disable_karaoke", "ffmpeg="])
o = dict(opts)
src, out = o["-i"], o["-o"]
base = os.path.splitext(os.path.basename(src))[0]
print("[UltraSinger] UltraSinger Version: fake")
print("[UltraSinger] args: " + " ".join(sys.argv[1:]))
sys.stdout.write("Transcribing  10%\rTranscribing  60%\rTranscribing 100%\n")
if src.endswith(".txt"):
    # re-pitch: mesma letra e tempo, só troca a nota das linhas ":" (como o original)
    lines = open(src, encoding="utf-8").read().splitlines()
    head = dict(l[1:].split(":", 1) for l in lines if l.startswith("#") and ":" in l)
    assert os.path.exists(os.path.join(os.path.dirname(src), head["MP3"])), "MP3 do rascunho não existe"
    folder = os.path.join(out, f"{head['ARTIST'].strip()} - {head['TITLE'].strip()}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, head["TITLE"].strip() + ".txt"), "w", encoding="utf-8") as f:
        for l in lines:
            if l.startswith(": "):
                parts = l.split(" ", 4)
                parts[3] = "64"
                l = " ".join(parts)
            f.write(l + "\n")
    print("[UltraSinger] repitch done")
    sys.exit(0)
if "Lento" in base:
    sys.stdout.flush()
    import time
    time.sleep(60)
if "Falha" in base:
    print("[UltraSinger] Error: modelo não carregou")
    sys.exit(3)
folder = os.path.join(out, "MB Artist - MB Title")
os.makedirs(folder, exist_ok=True)
with open(os.path.join(folder, "MB Artist - MB Title.txt"), "w", encoding="utf-8") as f:
    f.write("#ARTIST:MB Artist\n#TITLE:MB Title\n#YEAR:2011\n#GENRE:Rock\n#MP3:MB Artist - MB Title.mp3\n#BPM:240\n#GAP:1000\n"
            f"#LANGUAGE:{o.get('--language', '')}\n"
            ": 0 4 60 olá\n: 8 4 62 mun\n: 12 4 64 do\n- 20\n: 24 4 60 tchau\nE\n")
os.makedirs(os.path.join(out, "MB Artist - MB Title", "cache"), exist_ok=True)
print("[UltraSinger] done")
