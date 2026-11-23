from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    """Resultado de busca, igual para todas as fontes."""

    source: str  # "youtube" | "usdb"
    source_id: str
    artist: str
    title: str
    language: str = ""
    has_txt: bool = False
    has_video: bool | None = False  # None = só se sabe depois de baixar o .txt
    is_duet: bool = False
    duration_s: float | None = None
    url: str = ""  # página/vídeo
    audio_url: str = ""
    video_url: str = ""
    cover_url: str = ""
    thumbnail: str = ""
    extra: str = ""  # info curta (canal, visualizações…)
