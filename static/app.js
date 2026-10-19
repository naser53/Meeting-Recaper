(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    text: "", fileName: "", type: "teams", format: "auto",
    analysis: null, markpoints: [], turns: null, hallIds: null,
  };

  // ---------- small helpers
  function seg(id, key, onChange) {
    $(id).addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      state[key] = b.dataset.v;
      onChange && onChange(b.dataset.v);
    });
  }
  function setSeg(id, value) {
    $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.v === value));
  }
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({ error: `Server error ${r.status}` }));
    if (!r.ok || j.error) throw new Error(j.error || `Server error ${r.status}`);
    return j;
  }
  function msg(id, text, err = false) { $(id).textContent = text; $(id).classList.toggle("err", err); }
  function download(name, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/plain;charset=utf-8" }));
    a.download = name;
    a.click();
    URL.revokeObjectURL(a.href);
  }
  function baseName() {
    if (isFree()) return "Free text";
    return (state.fileName || "transcript").replace(/\.[^.]+$/, "");
  }
  const isFree = () => state.type === "free";
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function fmtTime(sec) {
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  // ---------- 1. file loading
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && loadFile(e.dataTransfer.files[0]));
  $("file").addEventListener("change", (e) => e.target.files[0] && loadFile(e.target.files[0]));

  async function loadFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    msg("processMsg", "Reading file…");
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      state.text = j.text;
      state.fileName = j.name;
      state.hallIds = null;
      drop.classList.add("loaded");
      $("dropText").textContent = `${j.name} · ${j.text.split("\n").length} lines. Drop another file to replace it.`;
      $("process").disabled = false;
      msg("processMsg", "");
      resetBelow();
    } catch (e) { msg("processMsg", e.message, true); }
  }

  function resetBelow() {
    $("markCard").hidden = true; $("resultCard").hidden = true; $("trCard").hidden = true;
    $("sumCard").hidden = true; $("summarize").hidden = true; msg("sumMsg", "");
    state.turns = null;
  }

  seg("type", "type", () => { state.hallIds = null; setMode(); });

  // Free text mode: no file and no speaker steps, only the text box and the translation card.
  function setMode() {
    const free = isFree();
    document.querySelectorAll(".transcript-only").forEach((el) => { el.hidden = free; });
    document.querySelectorAll(".free-only").forEach((el) => { el.hidden = !free; });
    $("inTitle").textContent = free ? "Text to translate" : "Transcript file";
    resetBelow();
    if (free) {
      $("trStep").textContent = "2";
      $("trCard").hidden = false;
      freeStats();
    }
  }

  function freeStats() {
    const t = $("freeText").value;
    $("freeStats").textContent = t.trim() ? `${t.length.toLocaleString()} characters, ${t.split("\n").length} lines` : "";
  }
  $("freeText").addEventListener("input", freeStats);
  $("pasteFree").addEventListener("click", async () => {
    try {
      $("freeText").value = await navigator.clipboard.readText();
      freeStats();
    } catch {
      $("freeStats").textContent = "The browser did not allow reading the clipboard. Click in the box and press Ctrl+V.";
    }
  });
  $("clearFree").addEventListener("click", () => { $("freeText").value = ""; freeStats(); $("freeText").focus(); });
  seg("format", "format", () => state.turns && buildTranscript());
  $("showTime").addEventListener("change", () => state.turns && buildTranscript());
  $("dropFillers").addEventListener("change", () => state.turns && buildTranscript());

  // ---------- processing
  $("process").addEventListener("click", processFile);

  async function processFile() {
    msg("processMsg", "Analysing…");
    try {
      const a = await post("/api/analyze", { text: state.text, type: state.type, hall_ids: state.hallIds });
      state.analysis = a;
      state.markpoints = a.markpoints.map((m) => ({ ...m }));
      msg("processMsg", `${a.stats.blocks} blocks, ${a.ids.length} speaker ID(s).`);
      if (state.type === "teams") {
        $("markCard").hidden = true;
        $("resStep").textContent = "2"; $("trStep").textContent = "3";
        await buildTranscript();
      } else {
        $("resStep").textContent = "3"; $("trStep").textContent = "4";
        renderIds();
        renderMarks();
        $("markCard").hidden = false;
        $("resultCard").hidden = true; $("trCard").hidden = true;
        $("markCard").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    } catch (e) { msg("processMsg", e.message, true); }
  }

  // ---------- 2. markpoints
  function renderIds() {
    const a = state.analysis;
    $("idBox").hidden = state.type !== "mixed";
    $("ids").innerHTML = a.ids.map((x) =>
      `<label class="check"><input type="checkbox" value="${esc(x.id)}" ${a.hall_ids.includes(x.id) ? "checked" : ""}> ${esc(x.id)} <small>(${x.words} words)</small></label>`
    ).join("");
    $("ids").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => {
      state.hallIds = [...$("ids").querySelectorAll("input:checked")].map((c) => c.value);
      processFile();
    }));
  }

  function refreshNames() {
    const names = new Set([...state.analysis.names, ...state.markpoints.map((m) => m.name)].filter(Boolean));
    $("nameList").innerHTML = [...names].map((n) => `<option value="${esc(n)}">`).join("");
  }

  function setContext(m) {
    const s = state.analysis.sentences;
    m.before = m.pos > 0 ? s[m.pos - 1].text : "";
    m.after = s[m.pos].text;
    m.time = s[m.pos].time;
    m.time_label = fmtTime(m.time);
  }

  function renderMarks() {
    state.markpoints.sort((a, b) => a.pos - b.pos);
    const showLow = $("showLow").checked;
    const tbody = $("marks").querySelector("tbody");
    tbody.innerHTML = state.markpoints.map((m, i) => {
      const hide = !showLow && m.confidence === "low" && !m.enabled;
      const before = m.before.length > 110 ? "…" + m.before.slice(-110) : m.before;
      const after = m.after.length > 140 ? m.after.slice(0, 140) + "…" : m.after;
      return `<tr data-i="${i}" class="${m.enabled ? "" : "off"} ${hide ? "low-hidden" : ""}">
        <td><input type="checkbox" class="use" ${m.enabled ? "checked" : ""} ${m.fixed ? "disabled title='Teams speaker ID changes here'" : ""}></td>
        <td>${m.time_label}</td>
        <td><span class="badge ${m.confidence}">${m.pos === 0 ? "start" : m.confidence === "fixed" ? "ID change" : m.confidence}</span></td>
        <td><input class="name" list="nameList" value="${esc(m.name || "")}"></td>
        <td class="ctx"><span class="before">${esc(before)}</span> <span class="after">${esc(after)}</span>
          <span class="why">${esc(m.reason || "")}</span></td>
        <td class="nudge">${m.fixed ? "" : `<button class="l" title="One sentence earlier">◀</button> <button class="r" title="One sentence later">▶</button>${m.manual ? ` <button class="x" title="Remove">✕</button>` : ""}`}</td>
      </tr>`;
    }).join("");
    refreshNames();
  }

  $("showLow").addEventListener("change", renderMarks);

  $("marks").addEventListener("input", (e) => {
    const tr = e.target.closest("tr");
    if (!tr) return;
    const m = state.markpoints[+tr.dataset.i];
    if (e.target.classList.contains("name")) { m.name = e.target.value; refreshNames(); }
  });
  $("marks").addEventListener("change", (e) => {
    const tr = e.target.closest("tr");
    if (!tr || !e.target.classList.contains("use")) return;
    state.markpoints[+tr.dataset.i].enabled = e.target.checked;
    tr.classList.toggle("off", !e.target.checked);
  });
  $("marks").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    const tr = e.target.closest("tr");
    if (!b || !tr) return;
    const i = +tr.dataset.i, m = state.markpoints[i];
    if (b.classList.contains("x")) { state.markpoints.splice(i, 1); renderMarks(); return; }
    const np = m.pos + (b.classList.contains("l") ? -1 : 1);
    if (np < 1 || np >= state.analysis.sentences.length) return;
    if (state.markpoints.some((x) => x !== m && x.pos === np)) return;
    m.pos = np;
    setContext(m);
    renderMarks();
  });

  $("addPoint").addEventListener("click", addPoint);
  $("findText").addEventListener("keydown", (e) => e.key === "Enter" && addPoint());
  function addPoint() {
    const q = $("findText").value.trim().toLowerCase();
    if (!q) return;
    const s = state.analysis.sentences;
    const from = state._lastFind && state._lastFind.q === q ? state._lastFind.i + 1 : 0;
    let hit = s.findIndex((x, i) => i >= from && x.text.toLowerCase().includes(q));
    if (hit < 0) hit = s.findIndex((x) => x.text.toLowerCase().includes(q));
    if (hit < 0) { alertRow("No sentence contains that text."); return; }
    state._lastFind = { q, i: hit };
    const existing = state.markpoints.find((m) => m.pos === hit);
    if (existing) { existing.enabled = true; renderMarks(); flash(existing); return; }
    const m = { pos: hit, fixed: false, enabled: true, manual: true, confidence: "high", name: "", reason: "added by you" };
    setContext(m);
    state.markpoints.push(m);
    renderMarks();
    flash(m);
  }
  function alertRow(t) { $("findText").value = ""; $("findText").placeholder = t; }
  function flash(m) {
    const i = state.markpoints.indexOf(m);
    const tr = $("marks").querySelector(`tr[data-i="${i}"]`);
    if (tr) { tr.classList.remove("low-hidden"); tr.scrollIntoView({ block: "center" }); tr.querySelector(".name").focus(); }
  }

  $("build").addEventListener("click", buildTranscript);

  // ---------- 3. build the clean transcript
  async function buildTranscript() {
    try {
      const r = await post("/api/build", {
        sentences: state.analysis.sentences,
        markpoints: state.markpoints,
        format: state.format,
        show_time: $("showTime").checked,
        drop_fillers: $("dropFillers").checked,
      });
      state.turns = r.turns;
      $("result").value = r.text;
      $("resStats").textContent = `${r.turns.length} speaker turns`;
      const first = $("resultCard").hidden;
      $("resultCard").hidden = false;
      $("trCard").hidden = false;
      if (first) $("resultCard").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { msg("processMsg", e.message, true); }
  }

  $("copyRes").addEventListener("click", () => navigator.clipboard.writeText($("result").value));
  $("dlRes").addEventListener("click", () => download(`${baseName()} - clean.txt`, $("result").value));

  // ---------- 4. translation
  seg("dir", "dir", (v) => applyDir(v));
  function applyDir(v) {
    for (const t of [$("trOut"), $("sumOut")]) {
      t.dir = v;
      t.classList.toggle("rtl", v === "rtl");
      t.classList.toggle("ltr", v === "ltr");
    }
  }
  $("lang").addEventListener("change", () => {
    const v = window.RECAPER.rtlLangs.includes($("lang").value) ? "rtl" : "ltr";
    setSeg("dir", v);
    applyDir(v);
  });

  $("translate").addEventListener("click", translateAll);

  async function showUsage() {
    if (!window.RECAPER.hasDeepl || $("engine").value !== "deepl") { $("usage").textContent = ""; return; }
    try {
      const u = await (await fetch("/api/deepl-usage")).json();
      $("usage").textContent = u.limit ? `DeepL this month: ${u.used.toLocaleString()} of ${u.limit.toLocaleString()} characters used.` : "";
    } catch { $("usage").textContent = ""; }
  }
  let modelsLoaded = false;
  async function onEngine() {
    showUsage();
    const ollama = $("engine").value === "ollama";
    $("modelField").hidden = !ollama;
    if (ollama && !modelsLoaded) {
      modelsLoaded = true;
      try {
        const r = await (await fetch("/api/ollama-models")).json();
        $("modelList").innerHTML = (r.models || []).map((m) => `<option value="${esc(m)}">`).join("");
      } catch { /* the model box still works without the list */ }
    }
  }
  $("engine").addEventListener("change", onEngine);
  onEngine();

  // a few lines per request, so no request is ever too long
  function makeChunks(items, maxLines, maxChars) {
    const chunks = [];
    let cur = [], size = 0;
    for (const it of items) {
      const len = it.text.length + 1;
      if (cur.length && (cur.length >= maxLines || size + len > maxChars)) { chunks.push(cur); cur = []; size = 0; }
      cur.push(it); size += len;
    }
    if (cur.length) chunks.push(cur);
    return chunks;
  }

  // A pasted paragraph can be longer than one request allows: cut it at sentence
  // ends, or at spaces if one sentence is still too long.
  function splitLong(text, max) {
    if (text.length <= max) return [text];
    const parts = [];
    let cur = "";
    const flush = () => { if (cur) parts.push(cur); cur = ""; };
    for (let s of text.split(/(?<=[.!?؟。！？])\s+/)) {
      if (cur && cur.length + 1 + s.length > max) flush();
      while (s.length > max) {
        let cut = s.lastIndexOf(" ", max);
        if (cut < max / 2) cut = max;
        parts.push(s.slice(0, cut).trim());
        s = s.slice(cut).trim();
      }
      if (s) cur = cur ? cur + " " + s : s;
    }
    flush();
    return parts.filter((p) => p);
  }

  function translateAll() { return isFree() ? translateFree() : translateTranscript(); }

  // Send the chunks one after another and call put(item, translation) for each line.
  async function runChunks(chunks, extra, put) {
    const engine = $("engine").value;
    for (let c = 0; c < chunks.length; c++) {
      msg("trMsg", `Translating part ${c + 1} of ${chunks.length}…`);
      const r = await post("/api/translate", { lines: chunks[c].map((x) => x.text), target: $("lang").value, engine, model: $("model").value, ...extra });
      chunks[c].forEach((it, j) => put(it, r.lines[j] ?? it.text));
      $("trBar").style.width = `${Math.round(((c + 1) / chunks.length) * 100)}%`;
    }
  }

  async function translateFree() {
    const lines = $("freeText").value.replace(/\r/g, "").split("\n");
    if (!lines.some((l) => l.trim())) { msg("trMsg", "Paste some text first.", true); return; }
    const { lines: maxLines, chars: maxChars } = window.RECAPER.batch[$("engine").value];
    const items = []; // {li, pi, text}: line li, piece pi of that line
    const out = lines.map((l) => (l.trim() ? splitLong(l, maxChars) : [l]));
    out.forEach((pieces, li) => {
      if (lines[li].trim()) pieces.forEach((p, pi) => items.push({ li, pi, text: p }));
    });
    const chunks = makeChunks(items, maxLines, maxChars);
    await withProgress(chunks, async () => {
      await runChunks(chunks, { source: $("srcLang").value }, (it, v) => { out[it.li][it.pi] = v; });
      return out.map((pieces) => pieces.join(" ")).join("\n");
    });
  }

  async function translateTranscript() {
    // Use the transcript as shown, so hand edits in the result box are kept.
    const turns = parseResult($("result").value);
    if (!turns.length) return;
    const withNames = $("trNames").checked;
    const items = []; // {t, k, text}  k = -1 for the header
    turns.forEach((t, ti) => {
      if (withNames) items.push({ t: ti, k: -1, text: t.header });
      t.lines.forEach((l, li) => items.push({ t: ti, k: li, text: l }));
    });
    const { lines: maxLines, chars: maxChars } = window.RECAPER.batch[$("engine").value];
    const chunks = makeChunks(items, maxLines, maxChars);
    const out = turns.map((t) => ({ header: t.header, lines: t.lines.slice() }));
    await withProgress(chunks, async () => {
      await runChunks(chunks, {}, (it, v) => { if (it.k === -1) out[it.t].header = v; else out[it.t].lines[it.k] = v; });
      return out.map((t) => t.header + "\n" + t.lines.join("\n")).join("\n\n") + "\n";
    });
  }

  async function withProgress(chunks, work) {
    const engine = $("engine").value;
    const btn = $("translate");
    btn.disabled = true;
    document.querySelector(".progress").hidden = false;
    $("trBar").style.width = "0";
    try {
      $("trOut").value = await work();
      $("trOut").hidden = false;
      $("trActions").hidden = false;
      $("summarize").hidden = false;
      msg("trMsg", `Done, ${chunks.length} part(s).`);
      if (engine === "deepl") showUsage();
    } catch (e) {
      msg("trMsg", e.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  // The result box: blocks separated by a blank line, first line is the speaker.
  function parseResult(text) {
    return text.replace(/\r/g, "").split(/\n\s*\n/).map((b) => b.split("\n").filter((l) => l.trim()))
      .filter((b) => b.length).map((b) => ({ header: b[0], lines: b.slice(1) }));
  }

  $("copyTr").addEventListener("click", () => navigator.clipboard.writeText($("trOut").value));
  $("dlTr").addEventListener("click", () => download(`${baseName()} - ${$("lang").value}.txt`, $("trOut").value));

  // ---------- 5. summary: the gist of the original text, written by Ollama in the output language
  $("summarize").addEventListener("click", async () => {
    const text = isFree() ? $("freeText").value : $("result").value;
    const btn = $("summarize");
    btn.disabled = true;
    msg("sumMsg", "Writing the summary… a long text can take a few minutes.");
    try {
      const r = await post("/api/summarize", { text, target: $("lang").value, model: $("model").value, kind: isFree() ? "text" : "transcript" });
      $("sumOut").value = r.summary;
      $("sumStep").textContent = String(+$("trStep").textContent + 1);
      $("sumCard").hidden = false;
      msg("sumMsg", r.parts > 1 ? `Done, summarised in ${r.parts} parts.` : "Done.");
      $("sumCard").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      msg("sumMsg", e.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  $("copySum").addEventListener("click", () => navigator.clipboard.writeText($("sumOut").value));
  $("dlSum").addEventListener("click", () => download(`${baseName()} - summary - ${$("lang").value}.txt`, $("sumOut").value));
})();
