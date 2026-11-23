import json
import sys

from letssing.converter import radshim


def test_translate_converter_command():
    old = ["binkc", "in.mp4", "out.bk2", "/V200", "/(1280", "/)720", "/L-1", "/D57", "/Q0.1", "/#"]
    assert radshim.translate(old) == [
        "binkc", "in.mp4", "out.bk2", "/V200", "/Scale1280,720", "/NoSound",
        "/DataRateByPercent57", "/DataRateByQuality0.1", "/O", "/#",
    ]


def test_translate_keeps_documented_switches_and_adds_autoclose():
    new = ["binkc", "a.mp4", "b.bk2", "/DataRate400000", "/Bink2"]
    assert radshim.translate(new) == ["binkc", "a.mp4", "b.bk2", "/DataRate400000", "/Bink2", "/O", "/#"]


def test_translate_single_dimension_keeps_aspect():
    assert "/Scale0,720" in radshim.translate(["binkc", "a", "b", "/)720"])


def test_run_missing_rad(tmp_path, monkeypatch):
    log = tmp_path / "rad.jsonl"
    monkeypatch.setenv("LETSSING_RAD_LOG", str(log))
    assert radshim.run(["binkc", "a", "b"], str(tmp_path / "nope.exe"), 5) == 2
    assert json.loads(log.read_text(encoding="utf-8"))["rc"] == 2


def test_run_timeout_kills_and_logs(tmp_path, monkeypatch):
    log = tmp_path / "rad.jsonl"
    monkeypatch.setenv("LETSSING_RAD_LOG", str(log))
    rc = radshim.run(["-c", "import time; time.sleep(30)"], sys.executable, 0.5)
    assert rc == radshim.TIMEOUT_EXIT
    entry = json.loads(log.read_text(encoding="utf-8"))
    assert "encerrado" in entry["error"]


def test_run_passes_exit_code(tmp_path):
    assert radshim.run(["-c", "raise SystemExit(3)"], sys.executable, 10) == 3
