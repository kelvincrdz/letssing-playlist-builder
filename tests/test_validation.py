from letssing.pipeline.validation import check_folder


def touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_complete_folder_ok(tmp_path):
    d = tmp_path / "Legiao Urbana - Tempo Perdido"
    for ext in (".txt", ".mp3", ".jpg", ".mp4"):
        touch(d / f"{d.name}{ext}")
    chk = check_folder(d)
    assert chk.ok and chk.missing == []
    assert chk.summary()["video"] is True


def test_video_is_optional(tmp_path):
    d = tmp_path / "A - B"
    for ext in (".txt", ".ogg", ".png"):
        touch(d / f"{d.name}{ext}")
    assert check_folder(d).ok


def test_missing_items_reported(tmp_path):
    d = tmp_path / "A - B"
    touch(d / "A - B.mp3")
    chk = check_folder(d)
    assert not chk.ok
    assert chk.missing == ["txt", "capa"]


def test_converter_cache_files_do_not_count(tmp_path):
    # name_id de "A - Song" é "Asong": arquivos de cache do conversor
    d = tmp_path / "A - Song"
    touch(d / "Asong.ogg")
    touch(d / "Asong.png")
    touch(d / "Asong_preview.ogg")
    touch(d / "A - Song.txt")
    chk = check_folder(d)
    assert chk.missing == ["áudio", "capa"]


def test_hidden_files_and_subfolders_ignored(tmp_path):
    d = tmp_path / "A - B"
    touch(d / ".letssing.json", b"{}")
    touch(d / "_sem_video" / "A - B.mp4")
    for ext in (".txt", ".mp3", ".jpg"):
        touch(d / f"A - B{ext}")
    chk = check_folder(d)
    assert chk.ok and not chk.video


def test_empty_file_is_a_problem(tmp_path):
    d = tmp_path / "A - B"
    touch(d / "A - B.txt", b"")
    touch(d / "A - B.mp3")
    touch(d / "A - B.jpg")
    chk = check_folder(d)
    assert not chk.ok and "arquivo vazio: A - B.txt" in chk.problems


def test_bad_folder_name(tmp_path):
    d = tmp_path / "SemSeparador"
    for ext in (".txt", ".mp3", ".jpg"):
        touch(d / f"x{ext}")
    assert not check_folder(d).ok


def test_multiple_audio_warns(tmp_path):
    d = tmp_path / "A - B"
    for ext in (".txt", ".mp3", ".ogg", ".jpg"):
        touch(d / f"A - B{ext}")
    chk = check_folder(d)
    assert chk.ok and chk.warnings
