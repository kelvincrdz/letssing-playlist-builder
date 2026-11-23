import chardet
import pytest

from letssing.ultrastar.txt import (
    decode_bytes,
    image_url,
    normalize,
    parse,
    parse_bytes,
    parse_meta_tags,
    resource_url,
)

SONG = """#TITLE:Tempo Perdido
#ARTIST:Legião Urbana
#LANGUAGE:Portuguese
#MP3:Legiao Urbana - Tempo Perdido [orig].mp3
#VIDEO:a=M4uG1MmZVl0,v=qku-HL1pmYs,co=12345-cover.jpg
#VIDEOGAP:1.5
#COVER:cover.jpg
#BPM:280
#GAP:12000
: 0 4 60 To~
: 4 4 62 dos
- 10
* 12 6 64 os
F 20 2 0 dias
E
lixo depois do fim
"""


def test_decode_utf8_and_bom():
    assert decode_bytes("ção".encode("utf-8")) == ("ção", "utf-8")
    assert decode_bytes(b"\xef\xbb\xbf" + "ção".encode("utf-8")) == ("ção", "utf-8-sig")


def test_decode_cp1252_and_latin1():
    text, enc = decode_bytes("Legião – “aspas”".encode("cp1252"))
    assert text == "Legião – “aspas”" and enc == "cp1252"
    # 0x81 não existe em cp1252 -> cai para latin-1
    text, enc = decode_bytes(b"caf\xe9 \x81")
    assert enc == "latin-1" and text.startswith("café")


def test_decode_encoding_tag():
    raw = "#ENCODING:CP1250\n#TITLE:Łódź\n".encode("cp1250")
    text, enc = decode_bytes(raw)
    assert "Łódź" in text and enc == "cp1250"


def test_parse_headers_body_and_end():
    s = parse(SONG.replace("\n", "\r\n"))
    assert s.title == "Tempo Perdido" and s.artist == "Legião Urbana"
    assert s.note_count == 4
    assert s.body[-1] == "E" and "lixo" not in s.to_text()
    assert s.problems() == []
    assert not s.is_duet


def test_duet_detection():
    s = parse("#TITLE:a\n#ARTIST:b\n#BPM:100\nP1\n: 0 1 1 a\nP2\n: 2 1 1 b\nE\n")
    assert s.is_duet


def test_problems():
    s = parse("#TITLE:a\n#BPM:abc\nE\n")
    assert set(s.problems()) == {"sem #ARTIST", "#BPM ausente ou inválido", "nenhuma nota encontrada"}


def test_normalize_sets_local_media_and_gap():
    s = parse(SONG)
    out = normalize(s, audio_file="A - B.mp3", cover_file="A - B.jpg", video_file="A - B.mp4", gap_ms=12500)
    assert out.get("MP3") == "A - B.mp3"
    assert out.get("VIDEO") == "A - B.mp4"
    assert out.get("COVER") == "A - B.jpg"
    assert out.get("GAP") == "12500"
    assert out.get("VIDEOGAP") == "1.5"
    assert out.body == s.body  # notas intactas
    assert s.get("GAP") == "12000"  # original não muda


def test_normalize_without_video_drops_video_tags():
    out = normalize(parse(SONG), audio_file="a.mp3", cover_file="a.jpg")
    assert out.get("VIDEO") == "" and out.get("VIDEOGAP") == ""
    assert out.get("GAP") == "12000"  # sem ajuste manual, mantém


def test_normalize_sets_audio_tag_for_v11():
    s = parse("#VERSION:1.1.0\n#TITLE:a\n#ARTIST:b\n#BPM:100\n#AUDIO:x.ogg\n: 0 1 1 a\nE\n")
    out = normalize(s, audio_file="a.mp3", cover_file="a.jpg")
    assert out.get("AUDIO") == "a.mp3" and out.get("MP3") == "a.mp3"


def test_output_is_utf8_with_bom_and_chardet_reads_it():
    # começo todo ASCII + acentos só no fim: sem BOM o chardet erraria
    body = "\n".join(f": {i * 4} 4 60 la" for i in range(600))
    text = f"#TITLE:Tempo Perdido\n#ARTIST:Legiao Urbana\n#BPM:280\n{body}\n: 9999 4 60 ção\nE\n"
    out = normalize(parse(text), audio_file="a.mp3", cover_file="a.jpg").to_bytes()
    assert out.startswith(b"\xef\xbb\xbf")
    enc = chardet.detect(out[:4096])["encoding"]
    assert enc.lower() == "utf-8-sig"
    assert "ção" in out.decode(enc)
    assert out.decode(enc).startswith("#TITLE")  # BOM removido na leitura


def test_roundtrip_cp1252_file_to_utf8():
    raw = SONG.encode("cp1252", errors="replace")
    song, enc = parse_bytes(raw)
    assert enc == "cp1252" and song.artist == "Legião Urbana"
    assert "Legião" in song.to_bytes().decode("utf-8-sig")


def test_meta_tags():
    t = parse_meta_tags("a=M4uG1MmZVl0,v=qku-HL1pmYs,co=12345-cover.jpg,bg=x.jpg")
    assert t.audio == "https://www.youtube.com/watch?v=M4uG1MmZVl0"
    assert t.video == "https://www.youtube.com/watch?v=qku-HL1pmYs"
    assert t.cover == "https://assets.fanart.tv/fanart/12345-cover.jpg"
    assert not parse_meta_tags("video.mp4").any


@pytest.mark.parametrize(("res", "url"), [
    ("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("123456", "https://vimeo.com/123456"),
    ("youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
    ("https://x.com/v", "https://x.com/v"),
    ("bad", ""),
])
def test_resource_url(res, url):
    assert resource_url(res) == url


def test_image_url_fanart_rewrite():
    assert image_url("https://images.fanart.tv/a.jpg") == "https://assets.fanart.tv/a.jpg"

