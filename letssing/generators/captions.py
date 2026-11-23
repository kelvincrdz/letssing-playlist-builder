"""Legendas do YouTube -> rascunho de .txt UltraStar (letra + tempo, sem notas).

O rascunho depois passa pelo "re-pitch" do UltraSinger (entrada .txt), que só
troca o número da nota de cada linha ":" e mantém letra e tempo
(modules/Ultrastar/ultrastar_writer.create_repitched_txt_from_ultrastar_data).

Formato json3 do YouTube (conferido em 2026-11-22):
    {"events": [{"tStartMs": 9200, "dDurationMs": 8800,
                 "segs": [{"utf8": "it's"}, {"utf8": " me", "tOffsetMs": 319}, ...]}]}
- Legenda automática (`<idioma>-orig`): cada palavra tem `tOffsetMs`.
- Legenda manual: um evento por linha, sem tempo por palavra; o tempo da linha
  é dividido entre as palavras pelo tamanho delas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# UltraStar: 1 batida = 15/BPM s. Com BPM 300 cada batida vale 50 ms.
BPM = 300
MS_PER_BEAT = 15_000 / BPM
MAX_WORD_MS = 2_000  # palavra sem fim claro (última da linha) não passa disso
MIN_WORDS = 20  # menos que isso não é letra de música, é ruído
PLACEHOLDER_PITCH = 0  # trocado pelo re-pitch do UltraSinger

_NOISE = re.compile(r"^\[.*\]$|^\(.*\)$|^[♪♫#*~\-–—.]+$")


@dataclass
class Word:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Line:
    words: list[Word] = field(default_factory=list)


@dataclass
class Track:
    lang: str  # código do yt-dlp, ex.: "pt-orig", "pt-BR"
    kind: str  # "auto" | "manual"
    url: str


def _base_lang(code: str) -> str:
    return code.removesuffix("-orig").split("-")[0].lower()


def choose_track(info: dict[str, Any], language: str | None) -> Track | None:
    """Escolhe a legenda: automática no idioma original (tempo por palavra),
    depois manual no mesmo idioma. `language` é o código ISO da música, se conhecido."""
    auto = info.get("automatic_captions") or {}
    manual = info.get("subtitles") or {}

    def json3(formats: list[dict]) -> str:
        return next((f.get("url", "") for f in formats if f.get("ext") == "json3"), "")

    orig = [k for k in auto if k.endswith("-orig")]
    for code in orig:
        if language and _base_lang(code) != language:
            continue  # ex.: show ao vivo com idioma detectado errado
        if url := json3(auto[code]):
            return Track(code, "auto", url)
    want = language or (_base_lang(orig[0]) if orig else None)
    if want:
        for code in sorted(manual):
            if _base_lang(code) == want and (url := json3(manual[code])):
                return Track(code, "manual", url)
    return None


def _clean(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def parse_json3(data: dict[str, Any]) -> list[Line]:
    lines: list[Line] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs") or []
        start = int(ev.get("tStartMs") or 0)
        dur = int(ev.get("dDurationMs") or 0)
        texts = [(_clean(s.get("utf8", "")), s.get("tOffsetMs")) for s in segs]
        texts = [(t, off) for t, off in texts if t]
        if not texts:
            continue
        line = Line()
        has_offsets = len(texts) > 1 and any(off is not None for _, off in texts)
        if has_offsets:  # automática: um seg por palavra
            for t, off in texts:
                for w in t.split():
                    line.words.append(Word(start + int(off or 0), 0, w))
        else:  # manual (ou automática de 1 palavra): divide o tempo da linha
            words = " ".join(t for t, _ in texts).split()
            total = sum(len(w) for w in words) or 1
            span = dur or MAX_WORD_MS * len(words)
            pos = start
            for w in words:
                d = span * len(w) / total
                line.words.append(Word(int(pos), int(pos + d), w))
                pos += d
        line.words = [w for w in line.words if not _NOISE.match(w.text)]
        if line.words:
            lines.append(line)
    _close_words([w for line in lines for w in line.words])
    _fix_overlaps(lines)
    return lines


def _close_words(words: list[Word]) -> None:
    """Fim de cada palavra = início da seguinte (mesmo na linha seguinte), no máx.
    MAX_WORD_MS. A duração do evento não serve: a legenda automática fica na tela
    além do trecho cantado e invadiria a linha seguinte."""
    for i, w in enumerate(words):
        nxt = words[i + 1].start_ms if i + 1 < len(words) else None
        if w.end_ms > 0:  # fim conhecido (legenda manual): só não invade a próxima
            w.end_ms = min(w.end_ms, nxt) if nxt is not None else w.end_ms
        else:
            cap = w.start_ms + MAX_WORD_MS
            w.end_ms = min(nxt, cap) if nxt is not None else cap


def _fix_overlaps(lines: list[Line]) -> None:
    """Legendas automáticas repetem/encostam linhas; garante tempo crescente."""
    last_end = 0
    for line in lines:
        for w in line.words:
            if w.start_ms < last_end:
                w.start_ms = last_end
            if w.end_ms <= w.start_ms:
                w.end_ms = w.start_ms + int(MS_PER_BEAT)
            last_end = w.end_ms


def word_count(lines: list[Line]) -> int:
    return sum(len(line.words) for line in lines)


def to_draft_txt(lines: list[Line], *, artist: str, title: str, language: str, audio_file: str) -> str:
    """Monta o .txt UltraStar com notas neutras (pitch 0), pronto para o re-pitch."""
    words = [w for line in lines for w in line.words]
    if not words:
        raise ValueError("legenda sem palavras")
    gap = words[0].start_ms

    def beat(ms: float) -> int:
        return int(round((ms - gap) / MS_PER_BEAT))

    out = [f"#ARTIST:{artist}", f"#TITLE:{title}"]
    if language:
        out.append(f"#LANGUAGE:{language}")
    out += [f"#MP3:{audio_file}", f"#BPM:{BPM}", f"#GAP:{gap}"]
    prev_end = None
    for li, line in enumerate(lines):
        if li > 0 and prev_end is not None:
            out.append(f"- {prev_end}")
        for wi, w in enumerate(line.words):
            start = beat(w.start_ms)
            if prev_end is not None and start < prev_end:
                start = prev_end
            length = max(1, beat(w.end_ms) - start)
            sep = " " if wi < len(line.words) - 1 else ""
            out.append(f": {start} {length} {PLACEHOLDER_PITCH} {w.text}{sep}")
            prev_end = start + length
    out.append("E")
    return "\n".join(out) + "\n"
