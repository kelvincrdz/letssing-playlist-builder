"""Fila simples de tarefas longas (uma por vez) com eventos para SSE."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("letssing.jobs")

MAX_EVENTS = 5000


@dataclass
class Job:
    id: str
    kind: str
    total: int
    state: str = "queued"  # queued | running | done | failed | cancelled
    done: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    items: dict[int, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    _cond: threading.Condition = field(default_factory=threading.Condition)

    # ---- usado pela tarefa ----
    def emit(self, type_: str, **data: Any) -> None:
        with self._cond:
            ev = {"seq": len(self.events) + 1, "type": type_, "ts": time.time(), **data}
            self.events.append(ev)
            if len(self.events) > MAX_EVENTS:
                del self.events[: len(self.events) - MAX_EVENTS]
            self._cond.notify_all()

    def log(self, message: str, level: str = "info", item_id: int | None = None) -> None:
        self.emit("log", level=level, message=message, item_id=item_id)

    def item(self, item_id: int, **state: Any) -> None:
        cur = self.items.setdefault(item_id, {})
        cur.update(state)
        self.emit("item", item_id=item_id, **cur)

    def advance(self) -> None:
        self.done += 1
        self.emit("progress", done=self.done, total=self.total)

    # ---- leitura ----
    def events_after(self, seq: int, timeout: float = 15.0) -> list[dict[str, Any]]:
        with self._cond:
            if not self.events or self.events[-1]["seq"] <= seq:
                self._cond.wait(timeout)
            return [e for e in self.events if e["seq"] > seq]

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "state": self.state, "done": self.done,
            "total": self.total, "items": self.items, "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_seq": self.events[-1]["seq"] if self.events else 0,
        }


class JobBusy(Exception):
    pass


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: Job | None = None
        self.history: dict[str, Job] = {}

    def start(self, kind: str, total: int, fn: Callable[[Job], None]) -> Job:
        with self._lock:
            if self.current and self.current.state in ("queued", "running"):
                raise JobBusy(self.current.id)
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, total=total)
            self.current = job
            self.history[job.id] = job

        def run() -> None:
            job.state = "running"
            job.emit("state", state="running", total=total)
            try:
                fn(job)
                job.state = "cancelled" if job.cancel.is_set() else "done"
            except Exception as e:  # erro geral (os por item são tratados na tarefa)
                log.exception("job_failed", extra={"fields": {"job": job.id}})
                job.log(f"Falha geral: {e}", level="error")
                job.state = "failed"
            job.finished_at = time.time()
            job.emit("state", state=job.state)

        threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self.history.get(job_id)
