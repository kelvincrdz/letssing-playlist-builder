from letssing.generators import captions
from letssing.generators.captions import Track, choose_track, parse_json3, to_draft_txt
from letssing.ultrastar.txt import parse_bytes

AUTO = {"events": [
    {"tStartMs": 540, "dDurationMs": 2250, "segs": [{"utf8": "[Music]"}]},
    {"tStartMs": 6470, "aAppend": 1, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 6480, "dDurationMs": 2480, "segs": [{"utf8": "hello", "acAsrConf": 255}]},
    {"tStartMs": 9200, "dDurationMs": 8800, "segs": [
        {"utf8": "it's"}, {"utf8": " me", "tOffsetMs": 319}, {"utf8": " i", "tOffsetMs": 2880},
        {"utf8": " was", "tOffsetMs": 3120}, {"utf8": " wondering", "tOffsetMs": 3640}]},
]}

MANUAL = {"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "ab cdef\ngh"}]}]}


def _info(auto=None, manual=None):
    fmt = [{"ext": "vtt", "url": "v"}, {"ext": "json3", "url": "J"}]
    return {"automatic_captions": {k: fmt for k in (auto or [])}, "subtitles": {k: fmt for k in (manual or [])}}


def test_parse_auto_word_timings():
    lines = parse_json3(AUTO)
    words = [w for line in lines for w in line.words]
    assert [w.text for w in words] == ["hello", "it's", "me", "i", "was", "wondering"]  # sem [Music]
    assert words[1].start_ms == 9200 and words[2].start_ms == 9519
    assert words[1].end_ms == 9519  # termina onde a próxima começa
    assert words[-1].end_ms == 9200 + 3640 + captions.MAX_WORD_MS  # última limitada


def test_parse_manual_splits_line_by_length():
    words = parse_json3(MANUAL)[0].words
    assert [w.text for w in words] == ["ab", "cdef", "gh"]
    assert words[0].start_ms == 1000 and words[1].start_ms == 1500 and words[2].end_ms == 3000


def test_times_never_go_backwards():
    data = {"events": [
        {"tStartMs": 1000, "dDurationMs": 3000, "segs": [{"utf8": "a"}, {"utf8": " b", "tOffsetMs": 500}]},
        {"tStartMs": 1200, "dDurationMs": 1000, "segs": [{"utf8": "c d"}]},
    ]}
    words = [w for line in parse_json3(data) for w in line.words]
    starts = [w.start_ms for w in words]
    assert starts == sorted(starts)
    assert all(w.end_ms > w.start_ms for w in words)


def test_choose_track_prefers_auto_orig_in_song_language():
    assert choose_track(_info(auto=["en", "pt-orig"]), "pt") == Track("pt-orig", "auto", "J")
    # idioma detectado errado (ex.: show ao vivo): usa a manual no idioma da música
    t = choose_track(_info(auto=["en-orig"], manual=["en", "pt-BR"]), "pt")
    assert t == Track("pt-BR", "manual", "J")
    assert choose_track(_info(auto=["en-orig"]), "pt") is None
    assert choose_track(_info(), None) is None
    assert choose_track(_info(auto=["es-orig"]), None).lang == "es-orig"


def test_draft_txt_is_valid_ultrastar():
    lines = parse_json3(AUTO)
    text = to_draft_txt(lines, artist="Adele", title="Hello", language="English", audio_file="a.mp3")
    song, _ = parse_bytes(text.encode("utf-8"))
    assert song.problems() == []
    assert song.get("GAP") == "6480" and song.get("BPM") == str(captions.BPM) and song.get("MP3") == "a.mp3"
    body = [l for l in text.splitlines() if l.startswith((":", "-"))]
    assert body[0] == ": 0 50 0 hello"  # 2480 ms / 50 ms por batida
    assert body[1].startswith("- ")  # quebra entre linhas da legenda
    beats = [int(l.split()[1]) for l in body if l.startswith(":")]
    assert beats == sorted(beats)


def test_last_word_of_line_ends_at_next_line_not_event_end():
    # caso real ("Hello"): evento fica 8800 ms na tela, mas a próxima linha começa antes
    data = {"events": [
        {"tStartMs": 9200, "dDurationMs": 8800, "segs": [{"utf8": "it's"}, {"utf8": " if", "tOffsetMs": 300}]},
        {"tStartMs": 10000, "dDurationMs": 5000, "segs": [
            {"utf8": "after"}, {"utf8": " all", "tOffsetMs": 400}, {"utf8": " these", "tOffsetMs": 700}]},
    ]}
    words = [w for line in parse_json3(data) for w in line.words]
    assert words[1].end_ms == 10000  # "if" termina onde "after" começa
    assert [w.start_ms for w in words[2:]] == [10000, 10400, 10700]  # nada espremido
    assert words[2].end_ms - words[2].start_ms == 400
