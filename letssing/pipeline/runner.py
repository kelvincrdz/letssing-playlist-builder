"""Orquestra o lote "Gerar DLC".

Fase 1, por música: .txt (USDB/importado, ou gerado) -> mídia -> .txt normalizado -> checklist.
Fase 2, lote: staging -> ultrastar2singIt (CLI) -> conferência da saída -> .zip.
Cada música é tratada isoladamente: erro em uma vira status `erro` e o lote segue.
"""

from __future__ import annotations

from dataclasses import dataclass

import logging
import re
import time
from typing import Callable

from letssing.config import Settings
from letssing.converter import package, singit, staging
from letssing.generators import captions, ultrasinger
from letssing.jobs import Job
from letssing.paths import Workspace
from letssing.pipeline import media, txt_step
from letssing.pipeline.validation import check_folder
from letssing.playlist import Item, PlaylistRepository, Status, TxtSource
from letssing.playlist.models import AI_SOURCES, GENERATED_SOURCES
from letssing.sources import lrclib, youtube

log = logging.getLogger("letssing.pipeline")


def run_generate(
    job: Job,
    items: list[Item],
    repo: PlaylistRepository,
    settings: Settings,
    ws: Workspace,
    ensure: Callable[..., tuple] = media.ensure_media,
    convert: bool = True,
    run_converter: Callable[..., singit.RunResult] = singit.run_converter,
    prepare_tool: Callable[..., singit.ConverterTool] = singit.prepare_tool,
    generate_txt: Callable[..., object] = ultrasinger.generate,
    fetch_captions: Callable[..., object] = youtube.fetch_captions,
    usdb: txt_step.TxtProvider | None = None,
    find_lyrics: Callable[..., object] = lrclib.find_synced,
) -> None:
    ws.ensure()
    ready, failed, was_ai = _prepare_all(job, items, repo, settings, ws, ensure, generate_txt, fetch_captions, usdb,
                                         find_lyrics)
    if job.cancel.is_set():
        job.emit("summary", ok=0, failed=failed, converted=0, zip=None)
        return
    if not convert:
        job.emit("summary", ok=len(ready), failed=failed, converted=0, zip=None)
        return
    _convert(job, ready, failed, was_ai, repo, settings, ws, run_converter, prepare_tool)


def _convert(job, ready, failed, was_ai, repo, settings, ws, run_converter, prepare_tool) -> None:
    def summary(converted: int, zip_path=None, **extra) -> None:
        job.emit("summary", ok=converted, failed=failed, converted=converted,
                 zip=zip_path.name if zip_path else None, **extra)

    if not ready:
        job.log("Nenhuma música pronta para converter.", level="warn")
        summary(0)
        return
    problems = singit.preflight(settings)
    if problems:
        for p in problems:
            job.log(f"Conversão não executada: {p}", level="error")
        for it in ready:
            repo.set_status(it.id, Status.PENDENTE, "Preparada; conversão bloqueada: " + problems[0])
            job.item(it.id, stage="aguardando config", status="pendente")
        summary(0, config_errors=problems)
        return

    job.total += len(ready)
    job.emit("progress", done=job.done, total=job.total)
    staged = staging.stage_batch([(it.id, ws.input / it.folder_name) for it in ready], ws)
    by_id = {it.id: it for it in ready}
    for item_id, reason in staged.conflicts.items():
        it = by_id[item_id]
        repo.set_status(item_id, Status.ERRO, reason)
        job.item(item_id, status="erro", error=reason)
        job.log(f"ERRO em {it.artist} - {it.title}: {reason}", level="error", item_id=item_id)
        failed += 1
        job.advance()
    songs = {s.folder.name: s.name_id for s in staged.songs}
    item_by_folder = {by_id[s.item_id].folder_name: by_id[s.item_id] for s in staged.songs}
    for s in staged.songs:
        if s.rebuilt:
            job.log(f"{s.folder.name}: entradas atualizadas ({', '.join(s.rebuilt)}); cache do conversor refeito",
                    item_id=s.item_id)
        job.item(s.item_id, stage="na fila do conversor", status="baixando", progress=None)
    if not songs:
        summary(0)
        return

    tool = prepare_tool(settings, ws)
    job.log(f"Convertendo {len(songs)} música(s) com o ultrastar2singIt "
            f"(formato {singit.output_format(settings).upper()}). Vídeos .bk2 podem levar minutos cada.")
    current: list[str] = []

    def on_song(folder: str) -> None:
        if current:
            job.advance()
        current.append(folder)
        it = item_by_folder[folder]
        job.item(it.id, stage="convertendo", progress=None)

    def on_line(line: str) -> None:
        it = item_by_folder.get(current[-1]) if current else None
        job.log(line, level="debug", item_id=it.id if it else None)

    result = run_converter(tool, settings, ws, songs, on_line=on_line, on_song=on_song, cancel=job.cancel)
    if current:
        job.advance()
    converted = 0
    for folder, outcome in result.outcomes.items():
        it = item_by_folder[folder]
        name = f"{it.artist} - {it.title}"
        if outcome.ok:
            status = Status.GERADO_IA if it.id in was_ai else Status.PRONTO
            repo.set_status(it.id, status)
            job.item(it.id, stage="no DLC", status=status.value, progress=1.0, error="")
            converted += 1
        else:
            msg = "Conversão cancelada" if result.cancelled else f"Conversor: {outcome.message}"
            repo.set_status(it.id, Status.ERRO, msg)
            job.item(it.id, stage="conversão", status="erro", error=msg)
            job.log(f"ERRO em {name}: {msg}", level="error", item_id=it.id)
            failed += 1

    zip_path = None
    if converted and not result.cancelled:
        zip_path = package.build_zip(ws.output, ws.dist)
        job.log(f"Pacote pronto: {zip_path.name} — descompacte na RAIZ do cartão SD.")
    elif result.returncode != 0 and not converted:
        job.log(f"O conversor terminou com código {result.returncode}.", level="error")
    summary(converted, zip_path)


def _ffmpeg_or_empty(settings: Settings) -> str:
    try:
        return singit.resolve_ffmpeg(settings)
    except singit.ConverterConfigError:
        return ""


@dataclass
class _Draft:
    text: str  # .txt UltraStar com letra e tempo, notas neutras
    ref: str  # "captions:<idioma>" | "lrclib:<id>"
    words: list[str]
    msg: str  # o que registrar no log se este rascunho for usado


# Legenda automática com idioma detectado errado (ex.: "es-orig" numa música em
# português) vira uma letra inventada. Se o LRCLIB tem a letra, a legenda precisa
# ter pelo menos esta fração das palavras dela para ser usada.
MIN_CAPTION_OVERLAP = 0.4


def lyrics_overlap(caption_words: list[str], lyric_words: list[str]) -> float:
    """Fração das palavras (3+ letras, sem acento) da legenda que estão na letra."""
    norm = lambda ws: {w for w in (lrclib._fold(x) for x in ws) if len(w) >= 3}  # noqa: E731
    cap, lyr = norm(caption_words), norm(lyric_words)
    return len(cap & lyr) / len(cap) if cap else 0.0


def _words(lines) -> list[str]:
    return [w.text for line in lines for w in line.words]


def _captions_draft(it: Item, fetch_captions) -> tuple[_Draft | None, str]:
    """Rascunho a partir das legendas do YouTube, ou (None, motivo)."""
    if not it.audio_url or "youtu" not in it.audio_url:
        return None, "o áudio não é do YouTube"
    try:
        got = fetch_captions(it.audio_url, ultrasinger.language_code(it.language))
    except Exception as e:  # legenda é opcional: qualquer falha cai no próximo passo
        return None, f"falha ao baixar legendas ({_friendly(e)[:120]})"
    if got is None:
        return None, "o vídeo não tem legendas utilizáveis"
    track, data = got
    lines = captions.parse_json3(data)
    n = captions.word_count(lines)
    if n < captions.MIN_WORDS:
        return None, f"legenda {track.lang} com só {n} palavras"
    kind = "automáticas" if track.kind == "auto" else "manuais"
    draft = captions.to_draft_txt(lines, artist=it.artist, title=it.title,
                                  language=it.language, audio_file="audio.mp3")
    return _Draft(draft, f"captions:{track.lang}", _words(lines),
                  f"usando legendas {kind} do YouTube ({track.lang}, {n} palavras)"), ""


def _lrclib_draft(it: Item, find_lyrics) -> tuple[_Draft | None, str]:
    """Rascunho a partir da letra sincronizada do LRCLIB, ou (None, motivo)."""
    try:
        rec = find_lyrics(it.artist, it.title, it.duration_s)
    except Exception as e:  # opcional: qualquer falha cai no UltraSinger completo
        return None, f"falha ao consultar o LRCLIB ({_friendly(e)[:120]})"
    if not rec:
        return None, "o LRCLIB não tem letra sincronizada desta versão"
    end_ms = int(it.duration_s * 1000) if it.duration_s else None
    lines = lrclib.lines_from_lrc(rec["syncedLyrics"], end_ms)
    n = captions.word_count(lines)
    if n < captions.MIN_WORDS:
        return None, f"letra do LRCLIB com só {n} palavras"
    draft = captions.to_draft_txt(lines, artist=it.artist, title=it.title,
                                  language=it.language, audio_file="audio.mp3")
    return _Draft(draft, f"lrclib:{rec.get('id')}", _words(lines),
                  f"usando a letra sincronizada do LRCLIB #{rec.get('id')} "
                  f"({rec.get('artistName')} - {rec.get('trackName')}, {n} palavras; tempo por linha)"), ""


def _choose_draft(job, it: Item, name: str, settings, fetch_captions, find_lyrics):
    """(rascunho, fonte, rótulo) ou (None, motivo, None). Legenda primeiro (tempo por
    palavra); o LRCLIB serve de reserva e de conferência da letra da legenda."""
    cap, cap_why = None, "desativado em Configurações"
    if settings.use_youtube_captions:
        job.item(it.id, stage="legendas do YouTube", progress=None)
        cap, cap_why = _captions_draft(it, fetch_captions)
    lrc, lrc_why = None, "desativado em Configurações"
    if settings.use_lrclib:
        if cap is None:
            job.log(f"{name}: sem legendas ({cap_why}); procurando a letra sincronizada no LRCLIB", item_id=it.id)
        job.item(it.id, stage="letra (LRCLIB)", progress=None)
        lrc, lrc_why = _lrclib_draft(it, find_lyrics)
    caption = (cap, TxtSource.YOUTUBE_SUBS, "legendas do YouTube + UltraSinger")
    lyrics = (lrc, TxtSource.LRCLIB, "letra do LRCLIB + UltraSinger")
    if cap and lrc:
        ov = lyrics_overlap(cap.words, lrc.words)
        if ov < MIN_CAPTION_OVERLAP:
            job.log(f"{name}: a legenda do YouTube ({cap.ref.split(':', 1)[1]}) não bate com a letra do LRCLIB "
                    f"(só {ov:.0%} das palavras): idioma detectado errado; usando o LRCLIB", level="warn", item_id=it.id)
            chosen = lyrics
        else:
            chosen = caption
    else:
        chosen = caption if cap else lyrics
    draft = chosen[0]
    if draft is None:
        return None, (lrc_why if settings.use_lrclib else cap_why), None
    job.log(f"{name}: {draft.msg}", item_id=it.id)
    return draft, chosen[1], chosen[2]


def _ai_txt(job, it: Item, name: str, repo, settings, ws, generate_txt, fetch_captions,
            find_lyrics=lrclib.find_synced) -> tuple[Item, str]:
    """Gera o .txt se preciso. Letra e tempo das legendas do YouTube ou da letra
    sincronizada do LRCLIB; as notas vêm do re-pitch do UltraSinger. Sem nenhuma
    das duas, o UltraSinger completo (Whisper). Devolve (item, motivo se não gerou)."""
    if it.txt_source not in GENERATED_SOURCES:
        return it, ""
    if not ultrasinger.needs_generation(it, ws):
        return it, ""
    problems = ultrasinger.preflight(settings)
    if problems:
        job.log(f"{name}: sem .txt e {problems[0]}", level="warn", item_id=it.id)
        return it, problems[0]

    draft, source, label = _choose_draft(job, it, name, settings, fetch_captions, find_lyrics)
    if draft is None:
        job.log(f"{name}: sem letra sincronizada ({source}); gerando com o UltraSinger completo "
                f"(modelo {settings.ultrasinger_whisper_model}, CPU) — pode levar muitos minutos", item_id=it.id)
        source, label = TxtSource.ULTRASINGER, "UltraSinger"
    else:
        job.log(f"{name}: calculando as notas com o UltraSinger (re-pitch, sem Whisper)", item_id=it.id)
    job.item(it.id, stage=f"{label} (IA)", progress=None)
    last = [0.0]

    def on_line(line: str) -> None:
        job.log(line, level="debug", item_id=it.id)
        now = time.monotonic()
        if now - last[0] >= 1.0:
            last[0] = now
            job.item(it.id, stage="UltraSinger: " + line[:70])

    t0 = time.monotonic()
    kwargs = {"draft": draft.text, "txt_ref": draft.ref} if draft is not None else {}
    generate_txt(it, ws, settings, _ffmpeg_or_empty(settings), on_line=on_line, cancel=job.cancel, **kwargs)
    if it.txt_source != source:
        it = repo.set_fields(it.id, txt_source=source) or it
    job.log(f"{name}: .txt gerado ({label}) em {int(time.monotonic() - t0)} s — "
            "notas por IA, revise", item_id=it.id)
    return it, ""


def _prepare_all(job, items, repo, settings, ws, ensure, generate_txt=ultrasinger.generate,
                 fetch_captions=youtube.fetch_captions, usdb=None, find_lyrics=lrclib.find_synced):
    """Fase 1. Devolve (itens prontos, nº de falhas, ids que eram 'gerado_ia')."""
    ready: list[Item] = []
    was_ai: set[int] = set()
    failed = 0
    for it in items:
        if job.cancel.is_set():
            job.log("Cancelado pelo usuário.", level="warn")
            break
        name = f"{it.artist} - {it.title}"
        if it.status == Status.GERADO_IA:
            was_ai.add(it.id)
        repo.set_status(it.id, Status.BAIXANDO)
        job.item(it.id, name=name, stage="txt", status="baixando", progress=None, error="")

        last = [0.0]

        def progress(phase: str, frac: float | None, _id: int = it.id) -> None:
            now = time.monotonic()
            if frac is not None and frac < 1.0 and now - last[0] < 0.5:
                return  # no máx. 2 eventos/s por música
            last[0] = now
            job.item(_id, stage=phase, progress=frac)

        try:
            it, txt_msg = txt_step.fetch_txt(it, ws, repo, usdb)
            job.log(f"{name}: {txt_msg}", item_id=it.id)

            job.item(it.id, stage="mídia", progress=0.0)
            plan, media_info = ensure(it, settings, ws, progress=progress)
            for w in (media_info or {}).get("download_warnings", []):
                job.log(f"{name}: {w}", level="warn", item_id=it.id)
            if not it.audio_url:
                it = repo.set_fields(it.id, audio_url=plan.audio_url) or it
            fetched = [k for k, v in (("áudio", plan.download_audio), ("capa", plan.download_cover),
                                      ("vídeo", plan.download_video)) if v]
            job.log(f"{name}: mídia " + (f"baixada ({', '.join(fetched)})" if fetched else "já estava baixada"),
                    item_id=it.id)

            it, ai_skip = _ai_txt(job, it, name, repo, settings, ws, generate_txt, fetch_captions,
                                  find_lyrics)
            if it.txt_source in AI_SOURCES:
                was_ai.add(it.id)

            job.item(it.id, stage="txt final")
            if txt_step.write_normalized_txt(it, ws):
                job.log(f"{name}: .txt normalizado (UTF-8, tags de mídia locais"
                        + (f", #GAP={it.gap_ms}" if it.gap_ms is not None else "") + ")", item_id=it.id)

            check = check_folder(ws.input / it.folder_name).summary()
            for w in check["warnings"]:
                job.log(f"{name}: {w}", level="warn", item_id=it.id)
            if check["ok"]:
                repo.set_status(it.id, Status.PENDENTE, "")
                job.item(it.id, status="pendente", stage="preparada", progress=1.0, check=check)
                ready.append(it)
            else:
                why = ", ".join(check["missing"] + check["problems"])
                hint = f" ({ai_skip})" if ai_skip else ""
                repo.set_status(it.id, Status.PENDENTE, f"Pulada na conversão: falta {why}{hint}")
                job.item(it.id, status="pendente", stage="pulada", progress=1.0, check=check)
                job.log(f"{name}: pulada (falta {why})", level="warn", item_id=it.id)
        except Exception as e:
            msg = _friendly(e)
            log.warning("item_failed", extra={"fields": {"item_id": it.id, "error": msg}})
            repo.set_status(it.id, Status.ERRO, msg)
            job.item(it.id, status="erro", error=msg)
            job.log(f"ERRO em {name}: {msg}", level="error", item_id=it.id)
            failed += 1
        job.advance()
    return ready, failed, was_ai


_ANSI = re.compile(r"(?:\x1b)?\[[0-9;]*m")  # cores do terminal (com ou sem o ESC)


def _friendly(e: Exception) -> str:
    s = _ANSI.sub("", str(e)).strip() or e.__class__.__name__
    s = s.removeprefix("ERROR: ").removeprefix("ERROR:").strip()
    return s[:500]
