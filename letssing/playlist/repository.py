"""Playlist persistida em SQLite (sqlite3 da stdlib)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from letssing.naming import duplicate_key, folder_name
from letssing.playlist.models import (
    EXPORT_FIELDS,
    EXPORT_FORMAT,
    EXPORT_VERSION,
    DuplicateError,
    Item,
    ItemCreate,
    ItemPatch,
    Status,
)

log = logging.getLogger("letssing.playlist")

SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artist        TEXT NOT NULL,
    title         TEXT NOT NULL,
    dup_key       TEXT NOT NULL UNIQUE,
    folder_name   TEXT NOT NULL,
    language      TEXT NOT NULL DEFAULT '',
    duration_s    REAL,
    is_duet       INTEGER NOT NULL DEFAULT 0,
    txt_source    TEXT NOT NULL DEFAULT 'none',
    txt_ref       TEXT NOT NULL DEFAULT '',
    audio_url     TEXT NOT NULL DEFAULT '',
    video_url     TEXT NOT NULL DEFAULT '',
    cover_url     TEXT NOT NULL DEFAULT '',
    gap_ms        INTEGER,
    no_video      INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pendente'
                  CHECK (status IN ('pendente','baixando','pronto','gerado_ia','erro')),
    error_message TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_item(row: sqlite3.Row) -> Item:
    d = dict(row)
    d["is_duet"] = bool(d["is_duet"])
    d["no_video"] = bool(d["no_video"])
    return Item.model_validate(d)


class PlaylistRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # --- leitura ---
    def list(self) -> list[Item]:
        with self._conn() as c:
            return [_row_to_item(r) for r in c.execute("SELECT * FROM items ORDER BY id")]

    def get(self, item_id: int) -> Item | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return _row_to_item(row) if row else None

    def find_duplicate(self, artist: str, title: str) -> Item | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM items WHERE dup_key = ?", (duplicate_key(artist, title),)
            ).fetchone()
        return _row_to_item(row) if row else None

    # --- escrita ---
    def add(self, data: ItemCreate) -> Item:
        key = duplicate_key(data.artist, data.title)
        now = _now()
        values: dict[str, Any] = data.model_dump(mode="json")
        values.update(
            dup_key=key,
            folder_name=folder_name(data.artist, data.title),
            is_duet=int(data.is_duet),
            no_video=int(data.no_video),
            status=Status.PENDENTE.value,
            created_at=now,
            updated_at=now,
        )
        cols = ", ".join(values)
        marks = ", ".join(f":{k}" for k in values)
        try:
            with self._conn() as c:
                cur = c.execute(f"INSERT INTO items ({cols}) VALUES ({marks})", values)
                new_id = cur.lastrowid
        except sqlite3.IntegrityError:
            existing = self.find_duplicate(data.artist, data.title)
            if existing is None:
                raise
            raise DuplicateError(existing) from None
        log.info("item_added", extra={"fields": {"item_id": new_id, "folder": values["folder_name"]}})
        item = self.get(int(new_id))
        assert item is not None
        return item

    def update(self, item_id: int, patch: ItemPatch) -> Item | None:
        changes: dict[str, Any] = {}
        for f in ("audio_url", "video_url", "cover_url"):
            v = getattr(patch, f)
            if v is not None:
                changes[f] = v
        if patch.clear_gap:
            changes["gap_ms"] = None
        elif patch.gap_ms is not None:
            changes["gap_ms"] = patch.gap_ms
        if patch.no_video is not None:
            changes["no_video"] = int(patch.no_video)
        if changes:
            # mídia/sincronia mudou: o item precisa ser reprocessado
            changes["status"] = Status.PENDENTE.value
            changes["error_message"] = ""
        return self._set(item_id, changes)

    def set_status(self, item_id: int, status: Status, error_message: str = "") -> Item | None:
        return self._set(item_id, {"status": status.value, "error_message": error_message})

    def set_fields(self, item_id: int, **fields: Any) -> Item | None:
        """Uso interno do pipeline (ex.: registrar fonte do .txt)."""
        allowed = set(EXPORT_FIELDS) | {"status", "error_message"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"Campos não editáveis: {bad}")
        return self._set(item_id, fields)

    def _set(self, item_id: int, changes: dict[str, Any]) -> Item | None:
        if changes:
            changes = {**changes, "updated_at": _now()}
            sets = ", ".join(f"{k} = :{k}" for k in changes)
            with self._conn() as c:
                c.execute(f"UPDATE items SET {sets} WHERE id = :id", {**changes, "id": item_id})
        return self.get(item_id)

    def delete(self, item_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if cur.rowcount:
            log.info("item_removed", extra={"fields": {"item_id": item_id}})
        return bool(cur.rowcount)

    # --- exportar / importar ---
    def export(self) -> dict[str, Any]:
        items = [it.model_dump(mode="json", include=set(EXPORT_FIELDS)) for it in self.list()]
        return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "exported_at": _now(), "items": items}

    def import_(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("format") != EXPORT_FORMAT:
            raise ValueError("Arquivo não é uma playlist exportada por esta aplicação")
        if int(payload.get("version", 0)) > EXPORT_VERSION:
            raise ValueError("Versão do arquivo mais nova que a aplicação")
        added, duplicates, errors = 0, [], []
        for i, raw in enumerate(payload.get("items") or []):
            try:
                self.add(ItemCreate.model_validate(raw))
                added += 1
            except DuplicateError as e:
                duplicates.append(f"{e.existing.artist} - {e.existing.title}")
            except Exception as e:  # item inválido não derruba a importação
                errors.append(f"item {i + 1}: {e}")
        return {"added": added, "duplicates": duplicates, "errors": errors}
