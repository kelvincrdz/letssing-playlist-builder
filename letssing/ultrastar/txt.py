"""Leitura, validação e normalização de arquivos UltraStar `.txt`.

Saída sempre em UTF-8 **com BOM**: o ultrastar2singIt detecta o encoding com
`chardet` olhando só os primeiros 4 KB. Sem BOM, um começo todo ASCII é
detectado como "ascii" e os acentos do resto do arquivo são descartados.
Com BOM, o chardet devolve UTF-8-SIG e o Python remove o BOM na leitura.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OUTPUT_ENCODING = "utf-8-sig"
_NOTE_TYPES = (":", "*", "F", "R", "G")
_MEDIA_TAGS = ("MP3", "AUDIO", "VIDEO", "COVER")


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Detecta o encoding. Devolve (texto, encoding usado)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # tag #ENCODING de arquivos antigos
    m = re.search(rb"^#ENCODING:\s*([\w-]+)", raw, re.IGNORECASE | re.MULTILINE)
    if m:
        enc = m.group(1).decode("ascii", "ignore").lower()
        enc = {"auto": "cp1252", "cp1250": "cp1250", "cp1252": "cp1252"}.get(enc, enc)
        try:
            return raw.decode(enc), enc
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


@dataclass
class UltraStarSong:
    headers: list[tuple[str, str]] = field(default_factory=list)  # ordem preservada
    body: list[str] = field(default_factory=list)

    # --- cabeçalhos ---
    def get(self, key: str, default: str = "") -> str:
        key = key.upper()
        for k, v in self.headers:
            if k == key:
                return v
        return default

    def set(self, key: str, value: str) -> None:
        key = key.upper()
        for i, (k, _) in enumerate(self.headers):
            if k == key:
                self.headers[i] = (key, value)
                return
        self.headers.append((key, value))

    def remove(self, key: str) -> None:
        key = key.upper()
        self.headers = [(k, v) for k, v in self.headers if k != key]

    # --- propriedades úteis ---
    @property
    def artist(self) -> str:
        return self.get("ARTIST")

    @property
    def title(self) -> str:
        return self.get("TITLE")

    @property
    def language(self) -> str:
        return self.get("LANGUAGE")

    @property
    def is_duet(self) -> bool:
        return any(re.fullmatch(r"P\s*[12]", line.strip()) for line in self.body) or bool(
            self.get("DUETSINGERP1") or self.get("P1")
        )

    @property
    def note_count(self) -> int:
        return sum(1 for line in self.body if line[:1] in _NOTE_TYPES)

    def problems(self) -> list[str]:
        out = []
        if not self.title:
            out.append("sem #TITLE")
        if not self.artist:
            out.append("sem #ARTIST")
        bpm = self.get("BPM").replace(",", ".")
        try:
            if float(bpm) <= 0:
                raise ValueError
        except ValueError:
            out.append("#BPM ausente ou inválido")
        if self.note_count == 0:
            out.append("nenhuma nota encontrada")
        return out

    def to_text(self) -> str:
        lines = [f"#{k}:{v}" for k, v in self.headers]
        lines.extend(self.body)
        text = "\n".join(lines)
        if not any(line.strip() == "E" for line in self.body):
            text += "\nE"
        return text + "\n"

    def to_bytes(self) -> bytes:
        return self.to_text().encode(OUTPUT_ENCODING)


def parse(text: str) -> UltraStarSong:
    song = UltraStarSong()
    in_body = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.lstrip("﻿")
        if not in_body and line.startswith("#"):
            key, sep, value = line[1:].partition(":")
            if sep:
                song.headers.append((key.strip().upper(), value.strip()))
            continue
        if not line.strip():
            continue
        in_body = True
        song.body.append(line.rstrip("\r"))
        if line.strip() == "E":
            break  # o que vier depois do fim é lixo
    return song


def parse_bytes(raw: bytes) -> tuple[UltraStarSong, str]:
    text, enc = decode_bytes(raw)
    return parse(text), enc



@dataclass
class MetaTags:
    """Recursos do `#VIDEO` "sobrecarregado" do USDB (formato do usdb_syncer).

    Ex.: `#VIDEO:a=AbCdEfGhIjK,v=ZyXwVuTsRqP,co=12345-cover.jpg`
    """

    audio: str = ""
    video: str = ""
    cover: str = ""

    @property
    def any(self) -> bool:
        return bool(self.audio or self.video or self.cover)


def parse_meta_tags(video_tag: str) -> MetaTags:
    tags = MetaTags()
    if "=" not in video_tag:
        return tags  # é um nome de arquivo normal
    for pair in video_tag.split(","):
        key, sep, value = pair.strip().partition("=")
        if not sep:
            continue
        value = value.strip().replace("%2C", ",")
        if key == "a":
            tags.audio = resource_url(value)
        elif key == "v":
            tags.video = resource_url(value)
        elif key == "co":
            tags.cover = image_url(value)
    return tags


def resource_url(resource: str) -> str:
    """ID/URL de vídeo -> URL (mesma regra do usdb_syncer)."""
    resource = resource.strip()
    if not resource:
        return ""
    if "://" in resource:
        return resource
    if "/" in resource:
        return f"https://{resource}"
    if re.fullmatch(r"\d{2,10}", resource):
        return f"https://vimeo.com/{resource}"
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", resource):
        return f"https://www.youtube.com/watch?v={resource}"
    return ""


def image_url(source: str) -> str:
    source = source.strip()
    if not source:
        return ""
    if "://" in source:
        return source.replace("images.fanart.tv", "assets.fanart.tv")
    if "/" in source:
        return f"https://{source}"
    return f"https://assets.fanart.tv/fanart/{source}"


def normalize(
    song: UltraStarSong,
    *,
    audio_file: str,
    cover_file: str,
    video_file: str = "",
    gap_ms: int | None = None,
) -> UltraStarSong:
    """Aponta as tags de mídia para os arquivos locais e aplica o #GAP manual."""
    out = UltraStarSong(headers=list(song.headers), body=list(song.body))
    out.remove("ENCODING")  # agora é sempre UTF-8
    out.set("MP3", audio_file)
    if out.get("VERSION") or any(k == "AUDIO" for k, _ in song.headers):
        out.set("AUDIO", audio_file)  # formato 1.1+
    out.set("COVER", cover_file)
    if video_file:
        out.set("VIDEO", video_file)
    else:
        out.remove("VIDEO")
        out.remove("VIDEOGAP")
    if gap_ms is not None:
        out.set("GAP", str(int(gap_ms)))
    return out
