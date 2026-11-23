from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from letssing import __version__
from letssing.api import (
    routes_config, routes_import, routes_jobs, routes_playlist, routes_search, routes_spotify,
)
from letssing.config import ConfigManager
from letssing.converter import singit
from letssing.generators import ultrasinger
from letssing.jobs import JobManager
from letssing.paths import DATA_DIR
from letssing.pipeline import media
from letssing.playlist import PlaylistRepository
from letssing.sources import youtube
from letssing.sources.spotify import SpotifyClient
from letssing.sources.usdb import UsdbClient

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(
    config: ConfigManager | None = None,
    playlist: PlaylistRepository | None = None,
    *,
    search_sources: dict | None = None,
    ensure_media=None,
    usdb=None,
    spotify=None,
    run_converter=None,
    prepare_tool=None,
    generate_txt=None,
) -> FastAPI:
    app = FastAPI(title="LetsSing Playlist Builder", version=__version__)
    app.state.config = config or ConfigManager()
    app.state.playlist = playlist or PlaylistRepository(DATA_DIR / "playlist.db")
    app.state.jobs = JobManager()
    if usdb is None:
        mgr = app.state.config

        def credentials() -> tuple[str, str | None]:
            s = mgr.load()
            return s.usdb_username, mgr.get_usdb_password(s)

        usdb = UsdbClient(credentials)
    app.state.usdb = usdb
    app.state.youtube_cookies = youtube.load_youtube_cookies
    app.state.youtube_my_playlists = youtube.my_playlists
    if spotify is None:
        cfg = app.state.config
        spotify = SpotifyClient(lambda: cfg.load().spotify_client_id, cfg.secrets)
    app.state.spotify = spotify
    app.state.search_sources = (
        search_sources if search_sources is not None else routes_search.default_sources(usdb)
    )
    app.state.ensure_media = ensure_media or media.ensure_media
    app.state.run_converter = run_converter or singit.run_converter
    app.state.prepare_tool = prepare_tool or singit.prepare_tool
    app.state.generate_txt = generate_txt or ultrasinger.generate

    app.include_router(routes_config.router)
    app.include_router(routes_playlist.router)
    app.include_router(routes_search.router)
    app.include_router(routes_jobs.router)
    app.include_router(routes_import.router)
    app.include_router(routes_spotify.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    return app
