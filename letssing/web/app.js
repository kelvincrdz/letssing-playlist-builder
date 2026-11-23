"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS_LABEL = { pendente: "pendente", baixando: "baixando", pronto: "pronto", gerado_ia: "gerado por IA", erro: "erro" };
const TXT_LABEL = { usdb: "USDB", lrclib: "letra do LRCLIB + IA", ultrastar_es: "UltraStar-ES", manual: "manual", ultrasinger: "UltraSinger (IA)", youtube_subs: "legendas do YouTube + IA", none: "— (será gerado)" };

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data?.detail;
    let msg = res.statusText;
    if (typeof d === "string") msg = d;
    else if (Array.isArray(d)) msg = d.map((e) => `${e.field ?? (e.loc || []).slice(1).join(".")}: ${e.message ?? e.msg}`).join(" · ");
    else if (d?.message) msg = d.message;
    const err = new Error(msg); err.status = res.status; err.detail = d; throw err;
  }
  return data;
}

async function upload(url, file) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data?.detail;
    const err = new Error(typeof d === "string" ? d : d?.message ?? res.statusText);
    err.status = res.status; err.detail = d; throw err;
  }
  return data;
}

function importSummary(r) {
  let msg = `.txt importado: ${r.song.artist} - ${r.song.title}`;
  if (r.media.length) msg += ` (+ ${r.media.map((m) => ({ audio: "áudio", video: "vídeo", cover: "capa" })[m]).join(", ")} do ZIP)`;
  if (r.song.is_duet) msg += " · dueto";
  if (r.notes.length) msg += " · " + r.notes.join("; ");
  return msg;
}

function flash(el, text, ok = true) {
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
}

/* ---------------- abas ---------------- */
function showTab(name) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".panel").forEach((p) => (p.hidden = p.id !== `tab-${name}`));
  try { localStorage.setItem("tab", name); } catch {}
  if (name === "playlist") { renderPlaylist(); loadPlaylist(); }
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------------- playlist ---------------- */
let items = [];

function mediaLinks(it) {
  const links = [];
  if (it.audio_url) links.push(`<a href="${esc(it.audio_url)}" target="_blank" rel="noopener">áudio</a>`);
  if (it.video_url) links.push(`<a href="${esc(it.video_url)}" target="_blank" rel="noopener">vídeo</a>`);
  if (it.cover_url) links.push(`<a href="${esc(it.cover_url)}" target="_blank" rel="noopener">capa</a>`);
  return links.length ? `<span class="links">${links.join("")}</span>` : "mídia automática (YouTube)";
}

/* ---------------- seleção por linha (clique; Shift+clique = intervalo) ---------------- */
const NO_TOGGLE = "input, button, a, select, textarea, label, .no-toggle";

function setSelected(row, on) {
  row.classList.toggle("selected", on);
  row.setAttribute("aria-selected", String(on));
}

/** Liga a seleção por linha em `container`. `onChange` roda depois de cada mudança. */
function rowSelection(container, rowSel, onChange) {
  let anchor = null;
  const selectable = () => $$(rowSel, container).filter((r) => !r.hidden && r.dataset.selectable !== "0");
  container.addEventListener("mousedown", (ev) => {
    if (ev.shiftKey && !ev.target.closest(NO_TOGGLE)) ev.preventDefault(); // sem selecionar texto
  });
  container.addEventListener("click", (ev) => {
    if (ev.target.closest(NO_TOGGLE)) return;
    const row = ev.target.closest(rowSel);
    if (!row || !container.contains(row) || row.dataset.selectable === "0") return;
    const rows = selectable();
    if (ev.shiftKey && anchor && rows.includes(anchor) && anchor !== row) {
      const [a, b] = [rows.indexOf(anchor), rows.indexOf(row)].sort((x, y) => x - y);
      const on = anchor.classList.contains("selected");
      rows.slice(a, b + 1).forEach((r) => setSelected(r, on));
    } else {
      setSelected(row, !row.classList.contains("selected"));
      anchor = row;
    }
    onChange();
  });
  container.addEventListener("keydown", (ev) => {
    if (ev.key !== " " && ev.key !== "Enter") return;
    const row = ev.target.closest(rowSel);
    if (!row || ev.target !== row) return;
    ev.preventDefault();
    row.dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: ev.shiftKey }));
  });
  return {
    selectAll(on = true) { selectable().forEach((r) => setSelected(r, on)); onChange(); },
    resetAnchor() { anchor = null; },
  };
}

/* ---------------- playlist: tabela, filtro e ações em lote ---------------- */
const plSelected = new Set();

function matchesFilter(it) {
  const q = $("#pl-filter").value.trim().toLowerCase();
  const st = $("#pl-status").value;
  if (q && !`${it.artist} ${it.title}`.toLowerCase().includes(q)) return false;
  if (st === "review") return it.needs_review;
  if (st === "notxt") return it.txt_source === "none";
  if (st) return it.status === st;
  return true;
}

function renderPlaylist() {
  const tbody = $("#pl-table tbody");
  $("#count").textContent = items.length;
  $("#pl-empty").hidden = items.length > 0;
  $("#pl-table").hidden = items.length === 0;
  $("#pl-bar").hidden = items.length === 0;
  for (const id of [...plSelected]) if (!items.some((i) => i.id === id)) plSelected.delete(id);
  tbody.innerHTML = items.map((it) => `
    <tr data-id="${it.id}" tabindex="0" class="${plSelected.has(it.id) ? "selected" : ""}" aria-selected="${plSelected.has(it.id)}">
      <td class="selcol"><span class="sel" aria-hidden="true"></span></td>
      <td class="song"><strong>${esc(it.title)}</strong>${it.is_duet ? ' <span class="badge">dueto</span>' : ""}
        <div>${esc(it.artist)}</div>
        <div class="sub">${mediaLinks(it)}</div></td>
      <td class="txtcol">${it.txt_source === "usdb" && it.txt_ref
          ? `<a href="https://usdb.animux.de/?link=detail&id=${encodeURIComponent(it.txt_ref)}" target="_blank" rel="noopener">USDB #${esc(it.txt_ref)}</a>`
          : esc(TXT_LABEL[it.txt_source] ?? it.txt_source)}</td>
      <td><span class="badge st-${esc(it.status)}">${esc(STATUS_LABEL[it.status] ?? it.status)}</span>
        ${it.needs_review ? ' <span class="badge review" title="Gerado por IA: revise notas e sincronia">revisar</span>' : ""}
        ${it.error_message ? `<div class="err-text">${esc(it.error_message)}</div>` : ""}</td>
      <td><input class="gap" type="number" step="10" value="${it.gap_ms ?? ""}" placeholder="do .txt" aria-label="GAP em ms"></td>
      <td class="center"><input class="novideo" type="checkbox" ${it.no_video ? "checked" : ""} aria-label="Sem vídeo"></td>
      <td><div class="row-actions">
        <button class="import-txt" title="Enviar .txt ou .zip (ex.: do UltraStar-ES)">.txt</button>
        <button class="media" title="Trocar links de áudio, vídeo e capa">Mídia</button>
        <button class="danger remove" title="Remover da playlist" aria-label="Remover">✕</button></div></td>
    </tr>`).join("");
  applyPlFilter();
}

function applyPlFilter() {
  let shown = 0;
  for (const tr of $$("#pl-table tbody tr")) {
    const it = items.find((i) => i.id === Number(tr.dataset.id));
    tr.hidden = !matchesFilter(it);
    if (tr.hidden && plSelected.delete(it.id)) setSelected(tr, false);
    if (!tr.hidden) shown++;
  }
  const filtered = shown !== items.length;
  $("#pl-empty").hidden = items.length > 0;
  $("#pl-msg").dataset.filter = filtered ? `${shown} de ${items.length} visíveis` : "";
  plSelectionChanged(false);
}

function plSelectionChanged(sync = true) {
  if (sync) {
    for (const tr of $$("#pl-table tbody tr")) {
      const id = Number(tr.dataset.id);
      if (tr.classList.contains("selected")) plSelected.add(id); else plSelected.delete(id);
    }
  }
  const n = plSelected.size;
  const filter = $("#pl-msg").dataset.filter;
  $("#pl-selcount").textContent = (n ? `${n} selecionada(s)` : "Nenhuma selecionada") + (filter ? ` · ${filter}` : "");
  $("#pl-bulk").hidden = n === 0;
}

const plSel = rowSelection($("#pl-table tbody"), "tr[data-id]", () => plSelectionChanged());
$("#pl-filter").addEventListener("input", () => { plSel.resetAnchor(); applyPlFilter(); });
$("#pl-status").addEventListener("change", () => { plSel.resetAnchor(); applyPlFilter(); });
$("#pl-selall").addEventListener("click", () => plSel.selectAll(true));
$("#pl-selnone").addEventListener("click", () => plSel.selectAll(false));

async function plBulk(label, fn) {
  const ids = items.filter((i) => plSelected.has(i.id)).map((i) => i.id);
  let ok = 0; const errors = [];
  for (const id of ids) {
    flash($("#pl-msg"), `${label} ${ok + errors.length + 1} de ${ids.length}…`);
    try { await fn(id); ok++; } catch (e) { errors.push(e.message); }
  }
  flash($("#pl-msg"), `${label}: ${ok} de ${ids.length}.` + (errors.length ? ` Erros: ${errors.join("; ")}` : ""), !errors.length);
}

$("#pl-bulk-remove").addEventListener("click", async () => {
  const n = plSelected.size;
  if (!confirm(`Remover ${n} música(s) da playlist? Os arquivos já baixados ficam no workspace.`)) return;
  await plBulk("Removendo", async (id) => {
    await api("DELETE", `/api/playlist/${id}`);
    items = items.filter((i) => i.id !== id); plSelected.delete(id);
  });
  renderPlaylist();
});
$("#pl-bulk-usdb").addEventListener("click", async (ev) => {
  const ids = items.filter((i) => plSelected.has(i.id)).map((i) => i.id);
  if (!confirm(`Procurar ${ids.length} música(s) no USDB?\nAs encontradas passam a usar o .txt do USDB (feito por pessoas) `
    + "e o áudio/vídeo que ele indica; o .txt gerado por IA delas é substituído na próxima geração.")) return;
  ev.target.disabled = true;
  let found = 0, missing = 0, already = 0; const errors = [];
  try {
    for (const [k, id] of ids.entries()) {
      flash($("#pl-msg"), `Procurando no USDB ${k + 1} de ${ids.length}… (${found} encontrada(s))`);
      try {
        const r = await api("POST", `/api/playlist/${id}/usdb-match`);
        if (r.already) already++; else if (r.matched) found++; else missing++;
        items = items.map((i) => (i.id === r.item.id ? r.item : i));
      } catch (e) {
        if (e.status === 401) { errors.push(e.message); break; } // sem login: não adianta seguir
        errors.push(e.message);
      }
    }
  } finally { ev.target.disabled = false; }
  renderPlaylist();
  let msg = `USDB: ${found} encontrada(s), ${missing} não está(ão) lá`;
  if (already) msg += `, ${already} já usava(m) o USDB`;
  msg += ".";
  if (errors.length) msg += ` Erro: ${errors[0]}`;
  flash($("#pl-msg"), msg, !errors.length);
});

for (const [btn, value] of [["#pl-bulk-novideo", true], ["#pl-bulk-video", false]]) {
  $(btn).addEventListener("click", async () => {
    await plBulk(value ? "Marcando sem vídeo" : "Marcando com vídeo", async (id) => {
      const up = await api("PATCH", `/api/playlist/${id}`, { no_video: value });
      items = items.map((i) => (i.id === up.id ? up : i));
    });
    renderPlaylist();
  });
}

async function loadPlaylist() {
  try { items = await api("GET", "/api/playlist"); renderPlaylist(); }
  catch (e) { flash($("#pl-msg"), "Erro ao carregar playlist: " + e.message, false); }
}

function replaceItem(updated) {
  items = items.map((i) => (i.id === updated.id ? updated : i));
  renderPlaylist();
}

$("#pl-table").addEventListener("click", async (ev) => {
  const tr = ev.target.closest("tr[data-id]"); if (!tr) return;
  const id = Number(tr.dataset.id);
  const it = items.find((i) => i.id === id);
  if (ev.target.classList.contains("remove")) {
    if (!confirm(`Remover "${it.artist} - ${it.title}" da playlist?`)) return;
    try { await api("DELETE", `/api/playlist/${id}`); items = items.filter((i) => i.id !== id); renderPlaylist(); flash($("#pl-msg"), "Removida."); }
    catch (e) { flash($("#pl-msg"), e.message, false); }
  } else if (ev.target.classList.contains("media")) {
    openMediaDialog(it);
  } else if (ev.target.classList.contains("import-txt")) {
    const input = $("#row-txt-file");
    input.dataset.id = id;
    input.click();
  }
});

$("#row-txt-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0]; ev.target.value = "";
  if (!file) return;
  const id = ev.target.dataset.id;
  try {
    const r = await upload(`/api/playlist/${id}/txt?filename=${encodeURIComponent(file.name)}&source=ultrastar_es`, file);
    replaceItem(r.item);
    flash($("#pl-msg"), importSummary(r));
  } catch (e) { flash($("#pl-msg"), "Importação falhou: " + e.message, false); }
});

$("#import-new").addEventListener("change", async (ev) => {
  const file = ev.target.files[0]; ev.target.value = "";
  if (!file) return;
  try {
    const r = await upload(`/api/import?filename=${encodeURIComponent(file.name)}&source=ultrastar_es`, file);
    items.push(r.item); $("#count").textContent = items.length;
    flash($("#import-msg"), importSummary(r) + " — adicionada à playlist.");
  } catch (e) { flash($("#import-msg"), e.message, false); }
});

$("#pl-table").addEventListener("change", async (ev) => {
  const tr = ev.target.closest("tr[data-id]"); if (!tr) return;
  const id = Number(tr.dataset.id);
  let body;
  if (ev.target.classList.contains("gap")) {
    const v = ev.target.value.trim();
    body = v === "" ? { clear_gap: true } : { gap_ms: Math.round(Number(v)) };
  } else if (ev.target.classList.contains("novideo")) {
    body = { no_video: ev.target.checked };
  } else return;
  try { replaceItem(await api("PATCH", `/api/playlist/${id}`, body)); flash($("#pl-msg"), "Salvo."); }
  catch (e) { flash($("#pl-msg"), e.message, false); loadPlaylist(); }
});

const dlg = $("#media-dialog");
let dlgItem = null;
function openMediaDialog(it) {
  dlgItem = it;
  $("#media-title").textContent = `${it.artist} — ${it.title}`;
  const f = $("#media-form");
  f.audio_url.value = it.audio_url; f.video_url.value = it.video_url; f.cover_url.value = it.cover_url;
  dlg.showModal();
}
dlg.addEventListener("close", async () => {
  if (dlg.returnValue !== "ok" || !dlgItem) return;
  const f = $("#media-form");
  try {
    replaceItem(await api("PATCH", `/api/playlist/${dlgItem.id}`, {
      audio_url: f.audio_url.value.trim(), video_url: f.video_url.value.trim(), cover_url: f.cover_url.value.trim(),
    }));
    flash($("#pl-msg"), "Mídia atualizada; a música será reprocessada.");
  } catch (e) { flash($("#pl-msg"), e.message, false); }
});

$("#import-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0]; ev.target.value = "";
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    const r = await api("POST", "/api/playlist/import", payload);
    let msg = `${r.added} adicionada(s).`;
    if (r.duplicates.length) msg += ` ${r.duplicates.length} duplicada(s) ignorada(s).`;
    if (r.errors.length) msg += ` ${r.errors.length} inválida(s): ${r.errors.join("; ")}`;
    flash($("#pl-msg"), msg, r.errors.length === 0);
    loadPlaylist();
  } catch (e) { flash($("#pl-msg"), "Importação falhou: " + e.message, false); }
});

/* ---------------- adicionar manual ---------------- */
$("#add-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const body = {
    artist: f.artist.value, title: f.title.value, language: f.language.value.trim(),
    audio_url: f.audio_url.value.trim(), video_url: f.video_url.value.trim(), no_video: f.no_video.checked,
  };
  try {
    const it = await api("POST", "/api/playlist", body);
    flash($("#add-msg"), `Adicionada: ${it.artist} - ${it.title}`);
    f.reset();
    items.push(it); $("#count").textContent = items.length;
  } catch (e) {
    flash($("#add-msg"), e.status === 409 ? `Duplicata: ${e.message}` : e.message, false);
  }
});

/* ---------------- configurações ---------------- */
const cfgForm = $("#cfg-form");
let suggestions = [];

function fillConfig(view) {
  const s = view.settings;
  for (const [k, v] of Object.entries(s)) {
    const el = cfgForm.elements[k]; if (!el) continue;
    if (el.type === "checkbox") el.checked = Boolean(v); else el.value = v ?? "";
  }
  cfgForm.usdb_password.value = "";
  $("#pw-state").textContent = view.usdb_password_set ? "Senha salva: sim." : "Senha salva: não.";
  $("#pw-clear").hidden = !view.usdb_password_set;
  $("#cfg-warnings").innerHTML = view.warnings.map((w) => `<li>${esc(w)}</li>`).join("");
  for (const [key, t] of Object.entries(view.tools)) {
    const el = $(`small[data-tool="${key}"]`); if (!el) continue;
    el.textContent = t.message; el.className = t.ok ? "ok" : "bad";
  }
  const bad = view.warnings.length > 0 || Object.values(view.tools).some((t) => !t.ok);
  $("#cfg-alert").hidden = !bad;
  fillSuggestions();
}

function fillSuggestions() {
  const ed = cfgForm.game_version.value;
  const sel = $("#dlc-suggest");
  sel.innerHTML = '<option value="">— escolher para preencher —</option>' + suggestions
    .filter((d) => d.edition === ed)
    .map((d) => `<option value="${esc(d.dlc_id)}">${esc(d.dlc_name)} (${esc(d.dlc_json_name)})</option>`).join("");
}
cfgForm.game_version.addEventListener("change", fillSuggestions);
$("#dlc-suggest").addEventListener("change", (ev) => {
  const d = suggestions.find((x) => x.dlc_id === ev.target.value); if (!d) return;
  cfgForm.core_title_id.value = d.core_id;
  cfgForm.dlc_title_id.value = d.dlc_id;
  cfgForm.dlc_json_name.value = d.dlc_json_name;
  flash($("#cfg-msg"), "Preenchido com a sugestão — confira com o name.txt e salve.");
});

cfgForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {};
  for (const el of cfgForm.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") body[el.name] = el.checked;
    else if (el.name === "video_max_height") body[el.name] = Number(el.value);
    else body[el.name] = el.value;
  }
  if (!body.usdb_password) delete body.usdb_password;
  try { fillConfig(await api("PUT", "/api/config", body)); flash($("#cfg-msg"), "Configurações salvas."); }
  catch (e) { flash($("#cfg-msg"), e.message, false); }
});

$("#usdb-test").addEventListener("click", async (ev) => {
  ev.target.disabled = true; flash($("#usdb-msg"), "Testando…");
  try { const r = await api("POST", "/api/config/usdb-test"); flash($("#usdb-msg"), `Login OK como ${r.user}.`); }
  catch (e) { flash($("#usdb-msg"), e.message, false); }
  finally { ev.target.disabled = false; }
});

$("#pw-clear").addEventListener("click", async () => {
  if (!confirm("Apagar a senha do USDB salva no Windows?")) return;
  try { fillConfig(await api("DELETE", "/api/config/usdb-password")); flash($("#cfg-msg"), "Senha apagada."); }
  catch (e) { flash($("#cfg-msg"), e.message, false); }
});

/* ---------------- busca ---------------- */
const SOURCE_LABEL = { spotify: "Spotify", usdb: "USDB", youtube: "YouTube", ultrastar_es: "UltraStar-ES" };
const NO_TXT_CONFIRM = "Sem .txt UltraStar, ele será gerado (legendas do YouTube, LRCLIB e/ou UltraSinger) e a música ficará marcada para revisão.";
let lastQuery = "";

function fmtDuration(s) {
  if (!s) return "";
  s = Math.round(s);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function itemBody(r, row) {
  return {
    artist: $(".r-artist", row).value, title: $(".r-title", row).value,
    language: r.language, duration_s: r.duration_s, is_duet: r.is_duet,
    txt_source: r.has_txt ? r.source : "none", txt_ref: r.has_txt ? r.source_id : "",
    audio_url: r.audio_url, video_url: r.video_url, cover_url: r.cover_url,
  };
}

function resultRow(r, i) {
  const done = Boolean(r.in_playlist);
  const tags = [
    r.has_txt ? '<span class="badge tag-yes">tem .txt</span>'
      : '<span class="badge tag-no" title="Será gerado e marcado para revisão">sem .txt</span>',
    r.duration_s ? `<span class="badge">${fmtDuration(r.duration_s)}</span>` : "",
    r.is_duet ? '<span class="badge">dueto</span>' : "",
    r.language ? `<span class="badge">${esc(r.language)}</span>` : "",
  ].join("");
  return `
    <div class="result${done ? " done" : ""}" role="option" tabindex="0" data-i="${i}"
         data-selectable="${done ? 0 : 1}" aria-selected="false">
      <span class="sel" aria-hidden="true"></span>
      ${r.thumbnail ? `<img src="${esc(r.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<span class="noimg"></span>'}
      <div class="fields">
        <input class="r-artist" value="${esc(r.artist)}" aria-label="Artista" title="Artista (editável)">
        <input class="r-title" value="${esc(r.title)}" aria-label="Título" title="Título (editável)">
        <div class="info">${tags}
          ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.extra)}">${esc(r.extra || r.url)}</a>`
                  : `<span>${esc(r.extra)}</span>`}</div>
      </div>
      <div class="act">${done ? '<span class="badge st-pronto">na playlist</span>'
        : '<button type="button" class="add-one" title="Adicionar só esta">Adicionar</button>'}</div>
    </div>`;
}

/**
 * Lista de resultados com seleção por linha, filtro e "Adicionar selecionadas".
 * `preselect`: já marcar tudo (importações de playlist).
 */
function makePicker(root, { label, preselect = false, msgEl }) {
  root.innerHTML = `
    <div class="pick-bar">
      <input type="search" class="pk-filter" placeholder="Filtrar por artista ou título" aria-label="Filtrar">
      <span class="pk-count muted"></span>
      <button type="button" class="pk-all">Selecionar todas</button>
      <button type="button" class="pk-none">Limpar</button>
      <button type="button" class="pk-add primary">Adicionar selecionadas</button>
    </div>
    <div class="pick-list" role="listbox" aria-multiselectable="true"></div>
    <p class="muted pick-hint">Clique numa linha para selecionar; Shift+clique seleciona um intervalo. Artista e título são editáveis.</p>`;
  const list = $(".pick-list", root);
  const state = { results: [] };
  const msg = (t, ok = true) => flash(msgEl, t, ok);

  const update = () => {
    const rows = $$(".result", list);
    const visible = rows.filter((r) => !r.hidden);
    const n = rows.filter((r) => r.classList.contains("selected")).length;
    $(".pk-count", root).textContent = `${n} selecionada(s) · ${visible.length}${visible.length !== rows.length ? ` de ${rows.length}` : ""} na lista`;
    $(".pk-add", root).textContent = n ? `Adicionar selecionadas (${n})` : "Adicionar selecionadas";
    $(".pk-add", root).disabled = n === 0;
  };
  const sel = rowSelection(list, ".result", update);

  const applyFilter = () => {
    const q = $(".pk-filter", root).value.trim().toLowerCase();
    for (const row of $$(".result", list)) {
      const text = `${$(".r-artist", row).value} ${$(".r-title", row).value}`.toLowerCase();
      row.hidden = Boolean(q) && !text.includes(q);
      if (row.hidden) setSelected(row, false);
    }
    sel.resetAnchor(); update();
  };

  state.set = (results) => {
    state.results = [];
    list.innerHTML = "";
    $(".pk-filter", root).value = "";
    state.append(results);
    root.hidden = false;
  };
  state.append = (results) => {
    const start = state.results.length;
    state.results.push(...results);
    list.insertAdjacentHTML("beforeend", results.map((r, k) => resultRow(r, start + k)).join(""));
    if (preselect) $$(".result", list).slice(start).forEach((row) => row.dataset.selectable !== "0" && setSelected(row, true));
    applyFilter();
  };
  state.clear = () => { state.results = []; list.innerHTML = ""; root.hidden = true; };

  async function add(rows) {
    const missingTxt = rows.some((row) => !state.results[Number(row.dataset.i)].has_txt);
    const ask = rows.length > 1 ? `Adicionar ${rows.length} música(s) do ${label}?` : null;
    if ((ask || missingTxt) && !confirm([ask, missingTxt ? NO_TXT_CONFIRM : ""].filter(Boolean).join("\n"))) return;
    let added = 0, dups = 0; const errors = [];
    for (const row of rows) {
      const r = state.results[Number(row.dataset.i)];
      if (rows.length > 1) msg(`Adicionando ${added + dups + errors.length + 1} de ${rows.length}…`);
      try {
        const it = await api("POST", "/api/playlist", itemBody(r, row));
        items.push(it); added++; r.in_playlist = it.id;
        markDone(row, "adicionada");
      } catch (e) {
        if (e.status === 409) { dups++; markDone(row, "na playlist"); }
        else errors.push(`${$(".r-artist", row).value} - ${$(".r-title", row).value}: ${e.message}`);
      }
    }
    $("#count").textContent = items.length;
    let text = `${added} adicionada(s).`;
    if (dups) text += ` ${dups} já estava(m) na playlist.`;
    if (errors.length) text += ` ${errors.length} com erro: ${errors.join("; ")}`;
    msg(text, errors.length === 0);
    update();
  }
  function markDone(row, text) {
    setSelected(row, false);
    row.dataset.selectable = "0"; row.classList.add("done");
    $(".act", row).innerHTML = `<span class="badge st-pronto">${text}</span>`;
  }

  $(".pk-filter", root).addEventListener("input", applyFilter);
  $(".pk-all", root).addEventListener("click", () => sel.selectAll(true));
  $(".pk-none", root).addEventListener("click", () => sel.selectAll(false));
  $(".pk-add", root).addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try { await add($$(".result.selected", list)); } finally { update(); }
  });
  list.addEventListener("click", (ev) => {
    if (ev.target.classList.contains("add-one")) add([ev.target.closest(".result")]);
  });
  list.addEventListener("input", (ev) => { if (ev.target.matches(".r-artist, .r-title") && $(".pk-filter", root).value) update(); });
  return state;
}

/* ---------------- busca ---------------- */
const searchGroups = {}; // fonte -> { picker, more }

function renderResults(data) {
  const box = $("#search-results");
  box.innerHTML = "";
  for (const k of Object.keys(searchGroups)) delete searchGroups[k];
  for (const [src, g] of Object.entries(data)) {
    const el = document.createElement("div");
    el.className = "source-group";
    el.innerHTML = `<h3>${esc(SOURCE_LABEL[src] ?? src)} <span class="pill">${g.results.length}${g.more ? "+" : ""}</span></h3>
      <p class="msg group-msg" role="status"></p><div class="picker"></div>`;
    box.append(el);
    if (g.error && !g.results.length) { flash($(".group-msg", el), `Falha nesta fonte: ${g.error}`, false); $(".picker", el).remove(); continue; }
    if (!g.results.length) { $(".group-msg", el).textContent = "Nada encontrado."; $(".picker", el).remove(); continue; }
    const picker = makePicker($(".picker", el), { label: SOURCE_LABEL[src] ?? src, msgEl: $(".group-msg", el) });
    picker.set(g.results);
    searchGroups[src] = { picker, more: g.more, el };
    if (g.more) el.insertAdjacentHTML("beforeend", `<button type="button" class="load-more" data-src="${esc(src)}">Carregar mais</button>`);
  }
}

$("#search-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#search-q").value.trim(); if (!q) return;
  const btn = ev.submitter || $("#search-form button");
  btn.disabled = true; flash($("#search-msg"), "Buscando…");
  try {
    renderResults(await api("GET", `/api/search?q=${encodeURIComponent(q)}`));
    lastQuery = q;
    $("#search-msg").textContent = "";
  } catch (e) { flash($("#search-msg"), "Erro na busca: " + e.message, false); }
  finally { btn.disabled = false; }
});

$("#search-results").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".load-more"); if (!btn) return;
  const src = btn.dataset.src;
  const g = searchGroups[src];
  btn.disabled = true; btn.textContent = "Carregando…";
  try {
    const data = await api("GET", `/api/search?q=${encodeURIComponent(lastQuery)}&source=${encodeURIComponent(src)}&offset=${g.picker.results.length}`);
    const page = data[src];
    const seen = new Set(g.picker.results.map((r) => r.source_id));
    const fresh = page.results.filter((r) => !seen.has(r.source_id));
    g.picker.append(fresh); // acrescenta sem apagar o que já foi editado/selecionado
    g.more = page.more && fresh.length > 0;
    $(".pill", g.el).textContent = g.picker.results.length + (g.more ? "+" : "");
    if (g.more) { btn.disabled = false; btn.textContent = "Carregar mais"; }
    else btn.outerHTML = page.error ? `<p class="msg err">${esc(page.error)}</p>` : '<p class="muted">Não há mais resultados.</p>';
  } catch (e) {
    btn.disabled = false; btn.textContent = "Carregar mais";
    flash($("#search-msg"), "Erro ao carregar mais: " + e.message, false);
  }
});

/* ---------------- playlist do YouTube ---------------- */
const ytPicker = makePicker($("#yt-pl-picker"), { label: "YouTube", preselect: true, msgEl: $("#yt-pl-msg") });
$("#yt-pl-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const url = $("#yt-pl-url").value.trim(); if (!url) return;
  const btn = ev.submitter || $("#yt-pl-form button");
  btn.disabled = true; flash($("#yt-pl-msg"), "Lendo a playlist…"); ytPicker.clear();
  try {
    const data = await api("GET", `/api/youtube/playlist?url=${encodeURIComponent(url)}`);
    showYtPlaylist(data);
  } catch (e) { flash($("#yt-pl-msg"), e.message, false); }
  finally { btn.disabled = false; }
});

function showYtPlaylist(data) {
  ytPicker.set(data.results);
  const already = data.results.filter((r) => r.in_playlist).length;
  flash($("#yt-pl-msg"), `${data.title || "Playlist"}: ${data.results.length} música(s)` + (already ? `, ${already} já na sua playlist` : "")
    + ". Todas vêm selecionadas; confira artista e título." + (data.warning ? ` (${data.warning})` : ""), !data.warning);
}

$("#yt-my-load").addEventListener("click", async (ev) => {
  ev.target.disabled = true; flash($("#yt-pl-msg"), "Lendo suas playlists do YouTube…");
  try {
    const { playlists } = await api("GET", "/api/youtube/my-playlists");
    $("#yt-my-list").innerHTML = playlists.map((p) =>
      `<option value="${esc(p.id)}">${esc(p.title)}${p.count != null ? ` (${p.count})` : ""}</option>`).join("");
    $("#yt-my-list").hidden = $("#yt-my-show").hidden = false;
    flash($("#yt-pl-msg"), `${playlists.length} playlist(s) na sua conta.`);
  } catch (e) { flash($("#yt-pl-msg"), e.message, false); }
  finally { ev.target.disabled = false; }
});

$("#yt-my-show").addEventListener("click", async (ev) => {
  const id = $("#yt-my-list").value; if (!id) return;
  ev.target.disabled = true; flash($("#yt-pl-msg"), "Lendo a playlist…"); ytPicker.clear();
  try {
    const url = `https://www.youtube.com/playlist?list=${encodeURIComponent(id)}`;
    const data = await api("GET", `/api/youtube/playlist?url=${encodeURIComponent(url)}`);
    data.title = data.title || $("#yt-my-list").selectedOptions[0]?.textContent;
    showYtPlaylist(data);
  } catch (e) { flash($("#yt-pl-msg"), e.message, false); }
  finally { ev.target.disabled = false; }
});

/* ---------------- Spotify ---------------- */
const spPicker = makePicker($("#sp-picker"), { label: "Spotify", preselect: true, msgEl: $("#sp-msg") });
let spPlaylists = [];

async function loadSpotify() {
  let st;
  try { st = await api("GET", "/api/spotify/status"); }
  catch (e) { flash($("#sp-msg"), e.message, false); return; }
  $("#sp-redirect").textContent = st.redirect_uri;
  $("#sp-cfg-redirect").textContent = st.redirect_uri;
  $("#sp-connect").hidden = !st.configured || st.connected;
  $("#sp-disconnect").hidden = !st.connected;
  $("#sp-pick").hidden = !st.connected || !st.user;
  if (!st.configured) { $("#sp-state").textContent = "Preencha o Client ID do Spotify em Configurações."; return; }
  if (!st.connected) { $("#sp-state").textContent = "Conta não conectada."; return; }
  if (st.error) { $("#sp-state").textContent = ""; flash($("#sp-msg"), st.error, false); return; }
  $("#sp-state").textContent = `Conectado como ${st.user}.`;
  try {
    spPlaylists = await api("GET", "/api/spotify/playlists");
    $("#sp-list").innerHTML = '<option value="saved">Músicas Curtidas</option>' + spPlaylists.map((p) =>
      `<option value="${esc(p.id)}" ${p.readable ? "" : "disabled"}>${esc(p.name)}${p.total != null ? ` (${p.total})` : ""}`
      + `${p.readable ? "" : " — de outra pessoa (veja a dica abaixo)"}</option>`).join("");
    $("#sp-copy-hint").hidden = spPlaylists.every((p) => p.readable);
  } catch (e) { flash($("#sp-msg"), e.message, false); }
}

$("#sp-connect").addEventListener("click", () => { location.href = "/api/spotify/login"; });
$("#sp-disconnect").addEventListener("click", async () => {
  if (!confirm("Desconectar sua conta do Spotify desta app?")) return;
  await api("POST", "/api/spotify/disconnect"); spPicker.clear(); $("#sp-msg").textContent = ""; loadSpotify();
});
$("#sp-load").addEventListener("click", async (ev) => {
  const id = $("#sp-list").value; if (!id) return;
  ev.target.disabled = true; flash($("#sp-msg"), "Lendo as músicas…"); spPicker.clear();
  try {
    const data = await api("GET", id === "saved" ? "/api/spotify/saved" : `/api/spotify/playlists/${encodeURIComponent(id)}`);
    const name = id === "saved" ? "Músicas Curtidas" : spPlaylists.find((p) => p.id === id)?.name ?? "Playlist";
    spPicker.set(data.results);
    const already = data.results.filter((r) => r.in_playlist).length;
    flash($("#sp-msg"), `${name}: ${data.results.length} música(s)` + (already ? `, ${already} já na sua playlist` : "")
      + ". Todas vêm selecionadas; confira artista e título.");
  } catch (e) { flash($("#sp-msg"), e.message, false); }
  finally { ev.target.disabled = false; }
});

{ // volta do login do Spotify (?spotify=ok / ?spotify_error=…)
  const q = new URLSearchParams(location.search);
  if (q.has("spotify") || q.has("spotify_error")) {
    history.replaceState(null, "", location.pathname);
    try { localStorage.setItem("tab", "busca"); } catch {}
    setTimeout(() => {
      if (q.has("spotify_error")) flash($("#sp-msg"), q.get("spotify_error"), false);
      $("#sp-card").scrollIntoView();
    }, 300);
  }
}
loadSpotify();

/* ---------------- gerar ---------------- */
let jobSource = null;
const genRows = {};

function renderGenRow(ev) {
  $("#gen-table").hidden = false;
  let tr = genRows[ev.item_id];
  if (!tr) {
    tr = document.createElement("tr");
    $("#gen-table tbody").appendChild(tr);
    genRows[ev.item_id] = tr;
  }
  const p = ev.progress;
  const chk = ev.check;
  const folder = chk ? ["txt", "audio", "cover", "video"].map((k) => `${chk[k] ? "✔" : "✘"} ${({ txt: ".txt", audio: "áudio", cover: "capa", video: "vídeo" })[k]}`).join(" · ") : "";
  tr.innerHTML = `
    <td>${esc(ev.name ?? "")}</td>
    <td>${esc(ev.stage ?? "")}</td>
    <td>${p == null ? (ev.status === "baixando" ? "<progress></progress>" : "") : `<progress max="1" value="${p}"></progress>`}</td>
    <td><span class="badge st-${esc(ev.status)}">${esc(STATUS_LABEL[ev.status] ?? ev.status ?? "")}</span>
      ${ev.error ? `<div class="err-text">${esc(ev.error)}</div>` : ""}</td>
    <td class="muted">${esc(folder)}</td>`;
}

function appendLog(ev) {
  const el = $("#gen-log");
  const line = document.createElement("div");
  line.className = ev.level;
  line.textContent = `${new Date(ev.ts * 1000).toLocaleTimeString()}  ${ev.message}`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function setGenRunning(running) {
  $("#gen-start").disabled = running;
  $("#gen-cancel").hidden = !running;
}

function attachJob(job) {
  if (jobSource) jobSource.close();
  $("#gen-overall").hidden = false;
  $("#gen-log").innerHTML = ""; $("#gen-table tbody").innerHTML = "";
  for (const k of Object.keys(genRows)) delete genRows[k];
  $("#gen-bar").max = job.total || 1; $("#gen-bar").value = job.done;
  $("#gen-count").textContent = `${job.done}/${job.total}`;
  setGenRunning(!["done", "failed", "cancelled"].includes(job.state));
  $("#gen-cancel").dataset.job = job.id;
  jobSource = new EventSource(`/api/jobs/${job.id}/events?after=0`);
  jobSource.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "log") appendLog(ev);
    else if (ev.type === "item") renderGenRow(ev);
    else if (ev.type === "progress") {
      $("#gen-bar").value = ev.done; $("#gen-count").textContent = `${ev.done}/${ev.total}`;
    } else if (ev.type === "state") {
      const label = { running: "Em andamento…", done: "Concluído", failed: "Falhou", cancelled: "Cancelado" }[ev.state] ?? ev.state;
      $("#gen-state").textContent = label;
      if (["done", "failed", "cancelled"].includes(ev.state)) {
        setGenRunning(false); jobSource.close(); jobSource = null; loadPlaylist();
      }
    } else if (ev.type === "summary") {
      let msg = `${ev.converted ?? ev.ok} música(s) no DLC, ${ev.failed} com erro.`;
      if (ev.config_errors?.length) msg = "Conversão bloqueada: " + ev.config_errors.join(" · ") + " (veja Configurações)";
      flash($("#gen-msg"), msg, ev.failed === 0 && !ev.config_errors?.length && (ev.converted ?? ev.ok) > 0);
      loadDlcInfo();
    }
  };
  jobSource.onerror = () => { /* o navegador reconecta sozinho */ };
}

$("#gen-start").addEventListener("click", async () => {
  try { $("#gen-msg").textContent = ""; attachJob(await api("POST", "/api/jobs/generate", {})); }
  catch (e) {
    if (e.status === 409 && e.detail?.job_id) attachJob(await api("GET", `/api/jobs/${e.detail.job_id}`));
    else flash($("#gen-msg"), e.message, false);
  }
});

async function loadDlcInfo() {
  try {
    const info = await api("GET", "/api/jobs/dlc/latest");
    $("#dlc-card").hidden = !info;
    if (info) {
      $("#dlc-link").textContent = `Baixar ${info.name}`;
      $("#dlc-meta").textContent = `${(info.size / 1048576).toFixed(1)} MB · ${new Date(info.mtime * 1000).toLocaleString()}`;
    }
  } catch {}
}

$("#log-verbose").addEventListener("change", (ev) => $("#gen-log").classList.toggle("verbose", ev.target.checked));

$("#gen-cancel").addEventListener("click", async (ev) => {
  try { await api("POST", `/api/jobs/${ev.target.dataset.job}/cancel`); } catch (e) { flash($("#gen-msg"), e.message, false); }
});

/* ---------------- início ---------------- */
(async function init() {
  let tab = "playlist";
  try { tab = localStorage.getItem("tab") || tab; } catch {}
  showTab(tab);
  if (tab !== "playlist") loadPlaylist();  // atualiza o contador
  try {
    suggestions = await api("GET", "/api/config/dlc-suggestions");
    fillConfig(await api("GET", "/api/config"));
  } catch (e) { flash($("#cfg-msg"), "Erro ao carregar configurações: " + e.message, false); }
  try { const job = await api("GET", "/api/jobs/current"); if (job) attachJob(job); } catch {}
  loadDlcInfo();
})();
