from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field, field_validator


class Status(StrEnum):
    PENDENTE = "pendente"
    BAIXANDO = "baixando"
    PRONTO = "pronto"
    GERADO_IA = "gerado_ia"
    ERRO = "erro"


class TxtSource(StrEnum):
    USDB = "usdb"  # baixado do USDB (feito por pessoas)
    ULTRASTAR_ES = "ultrastar_es"  # importado manualmente
    MANUAL = "manual"
    ULTRASINGER = "ultrasinger"  # gerado por IA (letra pelo Whisper)
    LRCLIB = "lrclib"  # letra sincronizada do LRCLIB (tempo por linha), notas pela IA
    YOUTUBE_SUBS = "youtube_subs"  # letra/tempo das legendas do YouTube, notas pela IA
    NONE = "none"  # ainda sem .txt -> UltraSinger na geração



# fontes em que a app gera o .txt (notas sempre calculadas por IA)
AI_SOURCES = frozenset({TxtSource.ULTRASINGER, TxtSource.YOUTUBE_SUBS, TxtSource.LRCLIB})
GENERATED_SOURCES = AI_SOURCES | {TxtSource.NONE}

def _clean_url(v: object) -> str:
    s = str(v or "").strip()
    if s and not s.startswith(("http://", "https://")):
        raise ValueError("Link deve começar com http:// ou https://")
    return s


class ItemBase(BaseModel):
    artist: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    language: str = ""
    duration_s: float | None = None
    is_duet: bool = False
    txt_source: TxtSource = TxtSource.NONE
    txt_ref: str = ""  # nome do .txt importado etc.
    audio_url: str = ""
    video_url: str = ""
    cover_url: str = ""
    gap_ms: int | None = None  # sobrescreve #GAP do .txt
    no_video: bool = False  # "sem vídeo (usar capa)"

    @field_validator("artist", "title", mode="before")
    @classmethod
    def _strip(cls, v: object) -> str:
        return " ".join(str(v or "").split())

    @field_validator("audio_url", "video_url", "cover_url", mode="before")
    @classmethod
    def _urls(cls, v: object) -> str:
        return _clean_url(v)


class ItemCreate(ItemBase):
    pass


class ItemPatch(BaseModel):
    """Campos editáveis pela UI. Ausente = não altera."""

    audio_url: str | None = None
    video_url: str | None = None
    cover_url: str | None = None
    gap_ms: int | None = None
    clear_gap: bool = False  # volta a usar o #GAP original do .txt
    no_video: bool | None = None

    @field_validator("audio_url", "video_url", "cover_url", mode="before")
    @classmethod
    def _urls(cls, v: object) -> str | None:
        return None if v is None else _clean_url(v)


class Item(ItemBase):
    id: int
    folder_name: str
    dup_key: str
    status: Status = Status.PENDENTE
    error_message: str = ""
    created_at: str
    updated_at: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_review(self) -> bool:
        return self.status == Status.GERADO_IA or self.txt_source in AI_SOURCES


class DuplicateError(Exception):
    def __init__(self, existing: Item) -> None:
        super().__init__(f"Já está na playlist: {existing.artist} - {existing.title}")
        self.existing = existing


EXPORT_FORMAT = "letssing-playlist"
EXPORT_VERSION = 1
EXPORT_FIELDS = tuple(ItemBase.model_fields)
