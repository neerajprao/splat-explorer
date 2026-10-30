"use strict";

const $ = (sel) => document.querySelector(sel);
const STAGES = ["probe", "frames", "colmap", "train", "export"];
const STAGE_LABEL = { probe: "Check", frames: "Frames", colmap: "Poses", train: "Train", export: "Export" };
const STATUS_PILL = {
  uploaded: ["Ready to start", ""], queued: ["Queued", "run"], running: ["Processing", "run"],
  done: ["Ready", "ok"], failed: ["Failed", "bad"], cancelled: ["Cancelled", "warn"],
};

let jobs = [];
let pollTimer = null;
let selectedFile = null;

// ---------------------------------------------------------------- helpers --
async function api(path, options = {}) {
  const res = await fetch(path, options);
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const msg = body && body.detail ? body.detail : `${res.status} ${res.statusText}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return body;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const fmtDuration = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);
const fmtNum = (n) => (n || 0).toLocaleString();
function fmtAgo(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
function elapsed(job) {
  if (!job.started_at) return null;
  const end = job.finished_at || Date.now() / 1000;
  return fmtDuration(end - job.started_at);
}

// ----------------------------------------------------------------- health --
async function loadHealth() {
  const box = $("#health");
  try {
    const h = await api("/api/health");
    box.replaceChildren(
      ...Object.entries(h.tools).map(([name, ok]) =>
        el("span", { class: `pill ${ok ? "ok" : "bad"}`, title: ok ? "found" : "not found - run scripts/doctor.py" },
          `${ok ? "✓" : "✗"} ${name}`)),
    );
    if (h.default_quality) $("#quality").value = h.default_quality;
  } catch (e) {
    box.replaceChildren(el("span", { class: "pill bad" }, "Server unreachable"));
  }
}

// ----------------------------------------------------------------- upload --
function chooseFile(file) {
  selectedFile = file || null;
  const preview = $("#preview");
  const msg = $("#form-msg");
  msg.textContent = "";
  msg.className = "form-msg";
  if (preview.src) URL.revokeObjectURL(preview.src);
  if (!selectedFile) {
    preview.hidden = true;
    $("#upload-btn").disabled = true;
    return;
  }
  preview.src = URL.createObjectURL(selectedFile);
  preview.hidden = false;
  if (!$("#name").value) $("#name").value = selectedFile.name.replace(/\.[^.]+$/, "");
  $("#upload-btn").disabled = false;
  $("#drop .drop-title").textContent = selectedFile.name;
  $("#drop .drop-hint").textContent = `${(selectedFile.size / 1048576).toFixed(0)} MB`;
}

function upload(event) {
  event.preventDefault();
  if (!selectedFile) return;
  const form = new FormData();
  form.append("file", selectedFile);
  form.append("name", $("#name").value.trim());
  form.append("quality", $("#quality").value);
  form.append("auto_start", $("#auto-start").checked ? "true" : "false");

  const bar = $("#upload-progress");
  const msg = $("#form-msg");
  const btn = $("#upload-btn");
  bar.hidden = false;
  bar.firstElementChild.style.width = "0%";
  btn.disabled = true;
  msg.className = "form-msg";
  msg.textContent = "Uploading…";

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = (100 * e.loaded) / e.total;
      bar.firstElementChild.style.width = `${pct}%`;
      msg.textContent = pct >= 100 ? "Checking the video…" : `Uploading… ${pct.toFixed(0)}%`;
    }
  };
  xhr.onload = () => {
    let body = null;
    try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    bar.hidden = true;
    if (xhr.status === 201) {
      msg.className = "form-msg ok";
      msg.textContent = $("#auto-start").checked ? "Uploaded. Reconstruction queued." : "Uploaded. Press Start when ready.";
      $("#upload-form").reset();
      $("#drop .drop-title").textContent = "Drop a video here or click to choose";
      $("#drop .drop-hint").textContent = "MP4 or MOV · 10 s to 10 min · 1080p or 4K";
      chooseFile(null);
      loadJobs();
    } else {
      msg.className = "form-msg bad";
      msg.textContent = (body && body.detail) || `Upload failed (${xhr.status})`;
      btn.disabled = false;
    }
  };
  xhr.onerror = () => {
    bar.hidden = true;
    msg.className = "form-msg bad";
    msg.textContent = "Upload failed: server unreachable.";
    btn.disabled = false;
  };
  xhr.send(form);
}

// ------------------------------------------------------------------- jobs --
async function action(job, verb, body) {
  try {
    if (verb === "delete") {
      if (!confirm(`Delete "${job.name}" and all its files?`)) return;
      await api(`/api/jobs/${job.id}`, { method: "DELETE" });
    } else {
      await api(`/api/jobs/${job.id}/${verb}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
      });
    }
  } catch (e) {
    alert(e.message);
  }
  loadJobs();
}

function stageStrip(job) {
  const idx = STAGES.indexOf(job.stage);
  return el("div", { class: "stages" }, STAGES.map((s, i) => {
    let cls = "";
    if (job.status === "done" || (idx >= 0 && i < idx)) cls = "done";
    else if (i === idx && (job.status === "running" || job.status === "queued")) cls = "now";
    return el("span", { class: cls }, STAGE_LABEL[s]);
  }));
}

function jobCard(job) {
  const [label, cls] = STATUS_PILL[job.status] || [job.status, ""];
  const info = job.video_info || {};
  const active = job.status === "running" || job.status === "queued";
  const pct = Math.round((job.progress || 0) * 100);
  const qualitySelect = el("select", { class: "btn small ghost", title: "Quality for start / retrain" },
    ["preview", "standard", "high"].map((q) => el("option", { value: q, selected: q === job.quality }, q)));

  const meta = [
    info.duration_s ? `${fmtDuration(info.duration_s)} video` : null,
    info.display_width ? `${info.display_width}×${info.display_height}` : null,
    job.frames_total ? `${job.frames_registered ?? "–"} / ${job.frames_total} frames placed` : null,
    job.splat_count ? `${fmtNum(job.splat_count)} splats` : null,
    `quality: ${job.quality}`,
    elapsed(job) ? `took ${elapsed(job)}` : null,
    fmtAgo(job.created_at),
  ].filter(Boolean);

  const thumb = job.preview_url
    ? el("img", { class: "thumb", src: `${job.preview_url}?t=${job.updated_at}`, alt: "" })
    : job.has_video
      ? el("video", { class: "thumb", src: `/api/jobs/${job.id}/video#t=1`, muted: true, preload: "metadata" })
      : el("div", { class: "thumb" });

  const actions = [];
  if (job.status === "done") {
    actions.push(el("button", { class: "btn primary small", onclick: () => openExplore(job) }, "Explore"));
    actions.push(qualitySelect);
    actions.push(el("button", { class: "btn small", onclick: () => action(job, "retrain", { quality: qualitySelect.value }) }, "Retrain"));
  } else if (job.status === "uploaded") {
    actions.push(qualitySelect);
    actions.push(el("button", { class: "btn primary small", onclick: () => action(job, "start", { quality: qualitySelect.value }) }, "Start"));
  } else if (job.status === "failed" || job.status === "cancelled") {
    actions.push(qualitySelect);
    actions.push(el("button", { class: "btn primary small", onclick: () => action(job, "start", { quality: qualitySelect.value }) }, "Retry"));
  }
  if (active) actions.push(el("button", { class: "btn small", onclick: () => action(job, "cancel") }, "Cancel"));
  actions.push(el("button", { class: "btn ghost small", onclick: () => openLog(job) }, "Log"));
  if (!active) actions.push(el("button", { class: "btn ghost small danger", onclick: () => action(job, "delete") }, "Delete"));

  const notes = [
    ...(job.warnings || []).map((w) => el("li", { class: "warn" }, w)),
    job.status === "failed" && job.error ? el("li", { class: "bad" }, job.error) : null,
  ].filter(Boolean);

  return el("article", { class: "job" },
    thumb,
    el("div", { class: "job-main" },
      el("div", { class: "job-head" },
        el("span", { class: "job-name", title: job.name }, job.name),
        el("span", { class: `pill ${cls}` }, active ? `${label} · ${pct}%` : label)),
      (active || job.status === "done" || job.stage) ? stageStrip(job) : null,
      active ? el("div", { class: "progress" }, el("div", { style: `width:${pct}%` })) : null,
      job.message ? el("div", { class: "job-msg" }, job.message) : null,
      el("div", { class: "meta" }, meta.map((m) => el("span", {}, m))),
      notes.length ? el("ul", { class: "notes" }, notes) : null,
      el("div", { class: "actions" }, actions)));
}

async function loadJobs() {
  try {
    jobs = await api("/api/jobs");
  } catch {
    return schedule(10000);
  }
  const box = $("#jobs");
  box.replaceChildren(...(jobs.length ? jobs.map(jobCard) : [el("p", { class: "empty" }, "No scenes yet. Upload a video to start.")]));
  const busy = jobs.some((j) => j.status === "running" || j.status === "queued");
  schedule(busy ? 2500 : 10000);
}

function schedule(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(loadJobs, ms);
}

// --------------------------------------------------------------- dialogs --
function openExplore(job) {
  const server = window.location.origin;
  $("#explore-title").textContent = `Explore “${job.name}”`;
  $("#deep-link").href = job.viewer_link || `splatexplorer://open?scene=${job.id}&server=${encodeURIComponent(server)}`;
  const cmd = `open -a SplatViewer --args -scene ${job.id} -server ${server}`;
  $("#open-cmd").textContent = cmd;
  $("#copy-cmd").onclick = async () => {
    try { await navigator.clipboard.writeText(cmd); $("#copy-cmd").textContent = "Copied"; }
    catch { $("#copy-cmd").textContent = "Select & copy"; }
    setTimeout(() => { $("#copy-cmd").textContent = "Copy"; }, 1500);
  };
  $("#download-ply").href = `/api/scenes/${job.id}/scene.ply`;
  $("#download-ply").setAttribute("download", `${job.name}.ply`);
  $("#explore").showModal();
}

async function openLog(job) {
  $("#log-title").textContent = `Log — ${job.name}`;
  $("#log-text").textContent = "Loading…";
  $("#log-dialog").showModal();
  try {
    const res = await fetch(`/api/jobs/${job.id}/log?lines=400`);
    $("#log-text").textContent = (await res.text()) || "(empty)";
    $("#log-text").scrollTop = $("#log-text").scrollHeight;
  } catch {
    $("#log-text").textContent = "Could not load the log.";
  }
}

// ------------------------------------------------------------------ init --
function init() {
  const drop = $("#drop");
  $("#file").addEventListener("change", (e) => chooseFile(e.target.files[0]));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) chooseFile(e.dataTransfer.files[0]); });
  $("#upload-form").addEventListener("submit", upload);
  $("#refresh").addEventListener("click", () => { loadHealth(); loadJobs(); });
  loadHealth();
  loadJobs();
}

init();
