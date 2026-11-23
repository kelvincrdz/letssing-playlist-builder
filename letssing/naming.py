"""Nomes de pasta/arquivo e chave de duplicata.

O ultrastar2singIt renomeia fisicamente as pastas de entrada com o seu
`sanitize_name` (ConvertFiles.py, v1.0.2). Para o cache por música não se
perder, geramos nomes que já são ponto fixo dessa função E válidos no Windows.
"""

from __future__ import annotations

import re
import unicodedata

SEPARATOR = " - "
_MAX_PART = 90
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Mesmo conjunto removido pelo conversor
_CONVERTER_BAD = re.compile(r"[!?#$%'\"‘’´`“”()\[\]]")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def converter_sanitize(name: str) -> str:
    """Cópia fiel de `sanitize_name` do ultrastar2singIt (usada em testes)."""
    name = re.sub(r"\[.*?\]", "", name)
    name = strip_accents(name)
    name = name.replace("...", "")
    name = name.replace("…", "")
    name = _CONVERTER_BAD.sub("", name)
    return " ".join(name.split()).strip()


def _clean_part(part: str) -> str:
    s = unicodedata.normalize("NFC", part)
    s = _WINDOWS_BAD.sub(" ", s)
    s = s.replace(SEPARATOR, " ")  # o conversor divide a pasta em ' - '
    s = converter_sanitize(s)
    s = s.replace(SEPARATOR, " ")
    s = s[:_MAX_PART].rstrip(" .")
    s = s.lstrip("_.")  # o conversor ignora pastas iniciadas por '_' ou '.'
    return " ".join(s.split())


def sanitize_part(part: str, fallback: str = "Desconhecido") -> str:
    prev = None
    s = part
    # repete até estabilizar (remover caracteres pode formar novos '...')
    for _ in range(10):
        if s == prev:
            break
        prev, s = s, _clean_part(s)
    if not s:
        s = fallback
    if s.upper() in _WINDOWS_RESERVED:
        s = f"{s}_"
    return s


def folder_name(artist: str, title: str) -> str:
    """'Artista - Titulo', estável sob o rename do conversor e válido no Windows."""
    return f"{sanitize_part(artist, 'Artista')}{SEPARATOR}{sanitize_part(title, 'Titulo')}"


def converter_name_id(folder: str) -> str:
    """Reproduz `construct_name_id_from_directory_name` do conversor.

    Dois itens com o mesmo name_id se sobrescrevem no DLC; usado na validação.
    """
    parts = folder.split(SEPARATOR)
    artist, title = strip_accents(parts[0]), strip_accents(parts[1])
    return "".join(w[0].upper() for w in artist.split()) + "".join(
        c.lower() for c in title if c.isalnum()
    )


_FEAT = re.compile(r"\s*[\(\[]?\s*\b(feat\.?|ft\.?|featuring)\s.*$", re.IGNORECASE)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\[.*?\]", " ", s)
    s = strip_accents(s).casefold()
    s = s.replace("&", " e ")
    s = re.sub(r"[^\w]+", " ", s)
    return " ".join(s.split())


def duplicate_key(artist: str, title: str) -> str:
    """Chave para detectar duplicatas: artista+título normalizados."""
    title = _FEAT.sub("", title)
    artist = _FEAT.sub("", artist)
    return f"{_norm(artist)}|{_norm(title)}"
