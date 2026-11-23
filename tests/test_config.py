import logging

import pytest
from pydantic import ValidationError

from letssing.config import Settings


def test_title_ids_normalized_and_validated():
    s = Settings(core_title_id="0100ee4020d18000", dlc_title_id=" 0100EE4020D19003 ")
    assert s.core_title_id == "0100EE4020D18000"
    assert s.dlc_title_id == "0100EE4020D19003"
    with pytest.raises(ValidationError):
        Settings(dlc_title_id="123")


def test_json_name_strips_extension():
    assert Settings(dlc_json_name="songs_int.json").dlc_json_name == "songs_int"
    with pytest.raises(ValidationError):
        Settings(dlc_json_name="songs int")


def test_api_config_roundtrip(client, config, caplog):
    caplog.set_level(logging.DEBUG, logger="letssing")
    r = client.put("/api/config", json={
        "dlc_title_id": "0100EE4020D19003", "dlc_json_name": "songs_int",
        "core_title_id": "0100EE4020D18000", "usdb_username": "kel", "usdb_password": "segredo123",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["usdb_password_set"] is True
    assert "usdb_password" not in body["settings"]
    assert "segredo123" not in r.text
    assert "segredo123" not in config.path.read_text(encoding="utf-8")
    assert "segredo123" not in caplog.text
    assert body["warnings"] == []

    # trocar usuário leva a senha junto
    r = client.put("/api/config", json={"usdb_username": "kel2"})
    assert r.json()["usdb_password_set"] is True

    r = client.delete("/api/config/usdb-password")
    assert r.json()["usdb_password_set"] is False


def test_password_never_written_to_file(config):
    s = Settings(usdb_username="kel")
    config.save(s)
    config.set_usdb_password(s, "segredo123")
    assert "segredo123" not in config.path.read_text(encoding="utf-8")
    assert config.get_usdb_password(config.load()) == "segredo123"


def test_api_config_validation_error(client):
    r = client.put("/api/config", json={"dlc_title_id": "xyz"})
    assert r.status_code == 422
    assert r.json()["detail"][0]["field"] == "dlc_title_id"


def test_api_warns_when_json_name_differs_from_converter_db(client):
    r = client.put("/api/config", json={
        "dlc_title_id": "0100EE4020D19003", "dlc_json_name": "songs_xx", "core_title_id": "0100EE4020D18000",
    })
    assert any("songs_int" in w for w in r.json()["warnings"])


def test_dlc_suggestions(client):
    r = client.get("/api/config/dlc-suggestions", params={"edition": "2026"})
    ids = {d["dlc_id"] for d in r.json()}
    assert "0100EE4020D19003" in ids and all(d["edition"] == "2026" for d in r.json())


def test_youtube_cookies_browser_validated():
    assert Settings(youtube_cookies_browser="Firefox").youtube_cookies_browser == "firefox"
    with pytest.raises(ValueError, match="Navegador"):
        Settings(youtube_cookies_browser="netscape")
