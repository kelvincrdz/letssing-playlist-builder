"""Configuração editável pela UI, salva em `data/config.json`.

A senha do USDB NUNCA vai para o arquivo nem para os logs: fica no Cofre de
Credenciais do Windows (via `keyring`). O arquivo guarda só o usuário.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from letssing.paths import DATA_DIR, DEFAULT_WORKSPACE

log = logging.getLogger("letssing.config")

KEYRING_SERVICE = "LetsSingPlaylistBuilder/usdb"
TITLE_ID_RE = re.compile(r"^[0-9A-F]{16}$")
JSON_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
# Valores aceitos por --whisper no UltraSinger (enum WhisperModel em
# src/modules/Speech_Recognition/Whisper.py; os ".en" do README não estão no enum)
# Navegadores do --cookies-from-browser do yt-dlp (yt_dlp.cookies.SUPPORTED_BROWSERS), sem o Safari
YOUTUBE_COOKIE_BROWSERS = ("firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium", "whale")
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3")


class Settings(BaseModel):
    # Ferramentas externas
    ffmpeg_path: str = ""
    rad_path: str = ""  # radvideo64.exe
    converter_path: str = ""  # Lets.Sing.Ultrastar.Converter.exe (ou pasta do código-fonte)
    ultrasinger_dir: str = ""  # pasta do repositório UltraSinger
    ultrasinger_python: str = ""  # python.exe do ambiente do UltraSinger (vazio: <pasta>/.venv)
    # Modelo do Whisper (opção --whisper do UltraSinger). O padrão dele é large-v2,
    # lento demais sem GPU; "small" é um meio-termo para CPU.
    ultrasinger_whisper_model: str = "small"
    # Com GPU NVIDIA e o UltraSinger instalado com CUDA, tira o --force_cpu
    ultrasinger_use_gpu: bool = False
    # Antes do UltraSinger completo, tentar a letra e o tempo das legendas do
    # YouTube (só as notas ficam com a IA, sem o Whisper)
    use_youtube_captions: bool = True
    # Sem legendas: letra sincronizada do LRCLIB (lrclib.net), também só com re-pitch
    use_lrclib: bool = True

    # Jogo / DLC (lidos pelo usuário no console via DBI — nunca chutados)
    game_version: str = "2026"
    core_title_id: str = ""
    dlc_title_id: str = ""
    dlc_json_name: str = ""

    # Spotify: só o Client ID (login PKCE, sem client secret); o token fica no keyring
    spotify_client_id: str = ""

    # Login do YouTube só para ler as playlists da conta (privadas, Curtidos,
    # Assistir mais tarde): sessão de um navegador ou um cookies.txt exportado
    youtube_cookies_browser: str = ""  # nome aceito pelo yt-dlp (--cookies-from-browser)
    youtube_cookies_file: str = ""

    # USDB (senha fica no keyring)
    usdb_username: str = ""

    # Mídia
    video_max_height: int = Field(default=720, ge=144, le=1080)
    cover_only: bool = False  # "só capa, sem vídeo" -> --still-video

    workspace_dir: str = str(DEFAULT_WORKSPACE)

    @field_validator("core_title_id", "dlc_title_id", mode="before")
    @classmethod
    def _title_id(cls, v: object) -> str:
        s = str(v or "").strip().upper()
        if s and not TITLE_ID_RE.fullmatch(s):
            raise ValueError("TitleID deve ter 16 caracteres hexadecimais (ex.: 0100EE4020D19003)")
        return s

    @field_validator("ultrasinger_whisper_model", mode="before")
    @classmethod
    def _whisper_model(cls, v: object) -> str:
        s = str(v or "").strip() or "small"
        if s not in WHISPER_MODELS:
            raise ValueError("Modelo do Whisper inválido; use um de: " + ", ".join(WHISPER_MODELS))
        return s

    @field_validator("youtube_cookies_browser", mode="before")
    @classmethod
    def _cookies_browser(cls, v: object) -> str:
        s = str(v or "").strip().lower()
        if s and s not in YOUTUBE_COOKIE_BROWSERS:
            raise ValueError("Navegador não suportado; use um de: " + ", ".join(YOUTUBE_COOKIE_BROWSERS))
        return s

    @field_validator("dlc_json_name", mode="before")
    @classmethod
    def _json_name(cls, v: object) -> str:
        s = str(v or "").strip()
        if s.lower().endswith(".json"):
            s = s[:-5]
        if s and not JSON_NAME_RE.fullmatch(s):
            raise ValueError("DLC JSON Name deve conter só letras, números e _ (ex.: songs_int)")
        return s

    @field_validator(
        "ffmpeg_path", "rad_path", "converter_path", "ultrasinger_dir",
        "ultrasinger_python", "workspace_dir", "usdb_username", "spotify_client_id", "youtube_cookies_file", "game_version",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: object) -> str:
        return str(v or "").strip().strip('"')


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringStore:
    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self.service = service

    def get(self, key: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, key)

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, key)
        except PasswordDeleteError:
            pass


class MemoryStore:
    """Usado nos testes."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._d.get(key)

    def set(self, key: str, value: str) -> None:
        self._d[key] = value

    def delete(self, key: str) -> None:
        self._d.pop(key, None)


class ConfigManager:
    def __init__(self, path: Path | None = None, secrets: SecretStore | None = None) -> None:
        self.path = path or (DATA_DIR / "config.json")
        self.secrets: SecretStore = secrets or KeyringStore()

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("config_unreadable", extra={"fields": {"path": str(self.path)}})
            return Settings()
        raw.pop("usdb_password", None)  # nunca aceitar senha vinda do arquivo
        return Settings.model_validate(raw)

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.path)
        log.info("config_saved", extra={"fields": {"path": str(self.path)}})

    # --- senha USDB ---
    def _key(self, username: str) -> str:
        return username or "_default"

    def get_usdb_password(self, settings: Settings) -> str | None:
        return self.secrets.get(self._key(settings.usdb_username))

    def set_usdb_password(self, settings: Settings, password: str) -> None:
        self.secrets.set(self._key(settings.usdb_username), password)
        log.info("usdb_password_stored", extra={"fields": {"user": settings.usdb_username}})

    def clear_usdb_password(self, settings: Settings) -> None:
        self.secrets.delete(self._key(settings.usdb_username))
