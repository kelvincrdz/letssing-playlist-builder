import re

import pytest

from letssing.naming import (
    converter_name_id,
    converter_sanitize,
    duplicate_key,
    folder_name,
    sanitize_part,
)

WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

CASES = [
    ("Legião Urbana", "Tempo Perdido"),
    ("AC/DC", "Highway to Hell"),
    ("Guns N' Roses", "Sweet Child o' Mine"),
    ("Scatman John", "Scatman (Ski-Ba-Bop-Ba-Dop-Bop)"),
    ("Panic! At The Disco", "I Write Sins Not Tragedies"),
    ("Artist - With Dash", "Title - Live"),
    ("CON", "NUL"),
    ("Os Paralamas do Sucesso", "Óculos [Ao Vivo]"),
    ("...", "???"),
    ("Beyoncé", 'What\'s "Up": <Remix>?'),
    ("A", ".(..)."),
    ("宇多田ヒカル", "First Love"),
]


@pytest.mark.parametrize(("artist", "title"), CASES)
def test_folder_name_is_stable_under_converter_rename(artist, title):
    name = folder_name(artist, title)
    # o conversor renomeia a pasta com sanitize_name; não pode mudar nada
    assert converter_sanitize(name) == name


@pytest.mark.parametrize(("artist", "title"), CASES)
def test_folder_name_is_valid_on_windows(artist, title):
    name = folder_name(artist, title)
    assert not WINDOWS_BAD.search(name)
    assert not name.endswith((" ", "."))
    assert not name.startswith(("_", "."))
    assert name.count(" - ") == 1  # o conversor divide em ' - '
    left, right = name.split(" - ")
    assert left and right
    assert left.upper() not in {"CON", "PRN", "AUX", "NUL"}
    assert right.upper() not in {"CON", "PRN", "AUX", "NUL"}


def test_folder_name_examples():
    assert folder_name("Legião Urbana", "Tempo Perdido") == "Legiao Urbana - Tempo Perdido"
    assert folder_name("AC/DC", "Highway to Hell") == "AC DC - Highway to Hell"
    assert folder_name("Guns N' Roses", "Sweet Child o' Mine") == "Guns N Roses - Sweet Child o Mine"
    assert folder_name("Os Paralamas do Sucesso", "Óculos [Ao Vivo]") == "Os Paralamas do Sucesso - Oculos"
    assert folder_name("...", "???") == "Artista - Titulo"
    assert folder_name("CON", "x") == "CON_ - x"


def test_sanitize_part_is_idempotent():
    for a, t in CASES:
        for s in (a, t):
            once = sanitize_part(s)
            assert sanitize_part(once) == once


def test_converter_name_id_matches_converter_logic():
    assert converter_name_id("Legiao Urbana - Tempo Perdido") == "LUtempoperdido"
    assert converter_name_id("AC DC - Highway to Hell") == "ADhighwaytohell"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (("Legião Urbana", "Tempo Perdido"), ("legiao urbana", "TEMPO PERDIDO")),
        (("Legião Urbana", "Tempo Perdido"), ("Legiao  Urbana", "Tempo-Perdido")),
        (("Ed Sheeran", "Perfect"), ("Ed Sheeran", "Perfect (feat. Beyoncé)")),
        (("Ed Sheeran", "Perfect"), ("Ed Sheeran", "Perfect ft. Someone")),
        (("Simon & Garfunkel", "The Boxer"), ("Simon e Garfunkel", "The Boxer")),
        (("Guns N' Roses", "Patience"), ("Guns N Roses", "Patience")),
        (("Os Paralamas", "Óculos [Karaoke]"), ("Os Paralamas", "Oculos")),
    ],
)
def test_duplicate_key_matches(a, b):
    assert duplicate_key(*a) == duplicate_key(*b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (("Legião Urbana", "Tempo Perdido"), ("Legião Urbana", "Pais e Filhos")),
        (("Queen", "Love of My Life"), ("Queen", "Love of My Life (Live)")),
        (("Adele", "Hello"), ("Lionel Richie", "Hello")),
    ],
)
def test_duplicate_key_differs(a, b):
    assert duplicate_key(*a) != duplicate_key(*b)
