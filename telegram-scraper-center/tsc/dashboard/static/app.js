/* Dashboard Telegram Scraper Center.
   Tanpa framework: satu koneksi SSE mengalirkan snapshot + log, sisanya
   render langsung ke DOM. */

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const STATE_LABEL = {
  unverified: "belum divalidasi",
  ready: "siap",
  working: "bekerja",
  cooldown: "jeda",
  flood_wait: "flood wait",
  limited: "dilimitasi",
  quota: "kuota habis",
  paused: "dijeda",
  disabled: "nonaktif",
};

const PHASE_LABEL = {
  idle: "menganggur",
  connecting: "menghubungkan agent",
  validating: "validasi",
  scraping: "mengambil member",
  planning: "menyusun batch",
  running: "berjalan",
  paused: "dijeda",
  done: "selesai",
  error: "error",
};

const fmt = new Intl.NumberFormat("id-ID");
const num = (v) => fmt.format(v || 0);

function duration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds <= 0) return "sekarang";
  if (seconds < 60) return `${Math.round(seconds)}d`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}j`;
}

function timeOf(ts) {
  return new Date(ts * 1000).toLocaleTimeString("id-ID", { hour12: false });
}

/* ------------------------------------------------------------------- tema */

const savedTheme = localStorage.getItem("tsc-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("#theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("tsc-theme", next);
});

/* ----------------------------------------------------------------- kontrol */

let toastTimer = null;
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 4000);
}

async function post(url, body) {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json();
    toast(data.message || (data.ok ? "Berhasil." : "Gagal."));
    return data;
  } catch (err) {
    toast(`Tidak bisa menghubungi server: ${err.message}`);
    return { ok: false };
  }
}

document.querySelectorAll(".controls [data-action]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const action = btn.dataset.action;
    if (action === "stop" && !confirm("Hentikan campaign? Antrean tetap tersimpan.")) return;
    post(`/api/control/${action}`);
  });
});

/* -------------------------------------------------------------- render KPI */

function renderHeader(snap) {
  const { campaign, status, mode } = snap;
  $("#campaign-line").textContent =
    `${campaign.name} → ${campaign.target} · ${num(snap.optout_count)} di daftar jangan-undang`;

  const modeChip = $("#mode-chip");
  modeChip.textContent = mode === "simulate" ? "mode simulasi" : "mode live";
  modeChip.className = `chip ${mode === "simulate" ? "" : "warn"}`;

  const phaseChip = $("#phase-chip");
  phaseChip.textContent = PHASE_LABEL[status.phase] || status.phase;
  phaseChip.className = `chip ${status.phase === "error" ? "bad" : status.phase === "running" ? "ok" : ""}`;

  const dot = $("#pulse");
  dot.className = "brand-dot" +
    (status.phase === "error" ? " err" : snap.runner?.running ? " live" : "");

  const parts = [];
  if (status.detail) parts.push(status.detail);
  if (status.elapsed) parts.push(`berjalan ${duration(status.elapsed)}`);
  if (snap.notifier?.available) parts.push(`bot: ${num(snap.notifier.sent)} pesan terkirim`);
  $("#foot-status").textContent = parts.join(" · ") || "menunggu perintah";
}

function renderKpi(snap) {
  const m = snap.members || {};
  const pct = Math.round((snap.progress || 0) * 100);
  $("#kpi-progress").textContent = `${pct}%`;
  $("#progress-bar").style.width = `${pct}%`;

  $("#kpi-invited").textContent = num(m.invited);
  $("#kpi-rate").textContent = `${num(snap.throughput_1h?.ok)} / jam terakhir`;
  $("#kpi-queued").textContent = num(m.queued);
  $("#kpi-pending").textContent = `${num(m.pending)} belum dijadwalkan`;
  $("#kpi-failed").textContent = num((m.failed || 0) + (m.skipped || 0));
  $("#kpi-privacy").textContent = `${num(m.blocked_privacy)} tertutup privasi`;

  const agents = snap.agents || [];
  const usable = agents.filter((a) => ["ready", "working", "cooldown"].includes(a.state));
  const limited = agents.filter((a) => ["limited", "flood_wait", "quota"].includes(a.state));
  $("#kpi-agents").textContent = `${usable.length}/${agents.length}`;
  $("#kpi-limited").textContent = `${limited.length} dilimitasi`;
}

function renderBanners(snap) {
  const guard = snap.status?.guard;
  const banner = $("#guard-banner");
  const warnBanner = $("#warn-banner");

  const blocking = guard?.blocking || [];
  if (blocking.length) {
    const list = $("#guard-reasons");
    list.replaceChildren(...blocking.map((r) => el("li", null, r)));
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }

  const warnings = guard?.warnings || [];
  if (warnings.length) {
    const list = $("#warn-reasons");
    list.replaceChildren(...warnings.map((r) => el("li", null, r)));
    warnBanner.hidden = false;
  } else {
    warnBanner.hidden = true;
  }
}

/* ----------------------------------------------------------------- agents */

function renderAgents(snap) {
  const host = $("#agents");
  const agents = snap.agents || [];
  if (!agents.length) {
    host.replaceChildren(el("p", "empty", "Belum ada agent."));
    return;
  }
  const quota = snap.limits?.per_day || 0;

  host.replaceChildren(...agents.map((a) => {
    const card = el("div", `agent s-${a.state}`);

    const top = el("div", "agent-top");
    top.append(el("span", "agent-name", a.label));
    const chip = el("span", "chip chip-sm", STATE_LABEL[a.state] || a.state);
    if (["ready", "working"].includes(a.state)) chip.classList.add("ok");
    else if (["limited", "disabled"].includes(a.state)) chip.classList.add("bad");
    else if (["flood_wait", "quota"].includes(a.state)) chip.classList.add("warn");
    top.append(chip);
    card.append(top);

    card.append(el("span", "agent-user", a.username ? `@${a.username}` : `id ${a.user_id ?? "—"}`));

    const meta = el("div", "agent-meta");
    meta.append(el("span", null, `✅ ${num(a.total_invited)}`));
    meta.append(el("span", null, `⚠ ${num(a.total_failed)}`));
    meta.append(el("span", null, `kuota ${a.daily_used}/${quota}`));
    meta.append(el("span", null, `siap ${duration(a.available_in)}`.replace("siap sekarang", "siap")));
    card.append(meta);

    card.append(el("div", "agent-note", a.note || ""));

    const actions = el("div", "agent-actions");
    const pause = el("button", "btn", a.state === "paused" ? "Aktifkan" : "Jeda");
    pause.addEventListener("click", () =>
      post(`/api/agent/${a.id}/${a.state === "paused" ? "resume" : "pause"}`));
    actions.append(pause);
    card.append(actions);

    return card;
  }));
}

/* ---------------------------------------------------------------- batches */

function renderBatches(snap) {
  const host = $("#batches");
  const batches = snap.batches || [];
  const plan = snap.status?.plan;
  $("#plan-hint").textContent = plan
    ? `${plan.batch_count} batch × ~${plan.batch_size} · estimasi ${plan.estimated_days} hari`
    : "belum ada rencana";

  if (!batches.length) {
    host.replaceChildren(el("p", "empty", "Belum ada batch. Klik “Susun ulang batch”."));
    return;
  }

  host.replaceChildren(...batches.slice(0, 60).map((b) => {
    const row = el("div", "batch");
    const title = el("div", "batch-title");
    title.append(document.createTextNode(`${b.agent_label} · sesi ${b.session_no} `));
    title.append(el("small", null, `(${b.state})`));
    row.append(title);
    row.append(el("span", "batch-num", `${b.done_count}+${b.failed_count}/${b.size}`));

    const bar = el("div", "bar");
    const fill = el("div", `bar-fill ${b.state === "done" ? "done" : b.state === "running" ? "running" : ""}`);
    const pct = b.size ? ((b.done_count + b.failed_count) / b.size) * 100 : 0;
    fill.style.width = `${pct}%`;
    bar.append(fill);
    row.append(bar);
    return row;
  }));
}

/* ---------------------------------------------------------------- sources */

function renderSources(snap) {
  const body = $("#sources-table tbody");
  const sources = snap.sources || [];
  if (!sources.length) {
    body.replaceChildren(el("tr", null)).firstChild?.append(
      Object.assign(el("td", "empty", "Belum ada source."), { colSpan: 4 }));
    return;
  }
  body.replaceChildren(...sources.map((s) => {
    const tr = el("tr");
    tr.append(el("td", null, s.title || s.chat_ref));
    const verif = el("td");
    const chip = el("span", `chip chip-sm ${s.admin_verified ? "ok" : "bad"}`,
      s.admin_verified ? s.verified_by : "ditolak");
    verif.append(chip);
    tr.append(verif);
    tr.append(el("td", "num", num(s.member_count)));
    tr.append(el("td", "num", num(s.scraped_count)));
    return tr;
  }));
}

/* ----------------------------------------------------------------- errors */

function renderErrors(snap) {
  const host = $("#errors");
  const errors = snap.errors || [];
  if (!errors.length) {
    host.replaceChildren(el("p", "empty", "Belum ada error. 🎉"));
    return;
  }
  host.replaceChildren(...errors.map((e) => {
    const row = el("div", "error-row");
    row.append(el("span", "error-code", e.error_code));
    row.append(el("span", "error-count", num(e.n)));
    row.append(el("span", "error-detail", `${timeOf(e.last_seen)} · ${e.sample || ""}`));
    return row;
  }));
}

/* ----------------------------------------------------------------- triage */

function renderTriage(snap) {
  const host = $("#triage");
  const llm = snap.llm || {};
  const chip = $("#llm-chip");
  chip.textContent = llm.available ? `LLM aktif · ${llm.model}` : "LLM nonaktif · aturan bawaan";
  chip.className = `chip chip-sm ${llm.available ? "ok" : ""}`;

  const items = snap.triage || [];
  if (!items.length) {
    host.replaceChildren(el("p", "empty", "Belum ada keputusan triage."));
    return;
  }
  host.replaceChildren(...items.map((t) => {
    const item = el("div", "triage-item");
    const head = el("div", "triage-head");
    head.append(el("span", `triage-action ${t.action}`, t.action));
    head.append(el("span", "agent-user", t.agent));
    head.append(el("span", "error-code", t.error_code));
    head.append(el("span", "triage-src", t.source));
    item.append(head);
    item.append(el("div", "triage-reason", t.reason || ""));
    return item;
  }));
}

/* -------------------------------------------------------------------- log */

let severityFilter = "all";
const logHost = $("#log");

$("#log-filters").addEventListener("click", (event) => {
  const pill = event.target.closest(".pill");
  if (!pill) return;
  document.querySelectorAll("#log-filters .pill").forEach((p) => p.classList.remove("is-active"));
  pill.classList.add("is-active");
  severityFilter = pill.dataset.sev;
  Array.from(logHost.children).forEach(applyFilter);
});

function applyFilter(line) {
  const sev = line.dataset.sev;
  const visible =
    severityFilter === "all" ||
    (severityFilter === "warn" && ["warn", "error", "critical"].includes(sev)) ||
    (severityFilter === "error" && ["error", "critical"].includes(sev)) ||
    severityFilter === sev;
  line.style.display = visible ? "" : "none";
}

function appendLogs(events) {
  const follow = $("#follow").checked;
  for (const ev of events) {
    const line = el("div", `log-line sev-${ev.severity}`);
    line.dataset.sev = ev.severity;
    line.append(el("span", "log-time", timeOf(ev.ts)));
    line.append(el("span", "log-kind", ev.kind));
    line.append(el("span", "log-msg", ev.agent_label ? `[${ev.agent_label}] ${ev.message}` : ev.message));
    applyFilter(line);
    logHost.append(line);
  }
  while (logHost.children.length > 600) logHost.removeChild(logHost.firstChild);
  if (follow) logHost.scrollTop = logHost.scrollHeight;
}

/* -------------------------------------------------------------------- SSE */

function render(snap) {
  renderHeader(snap);
  renderKpi(snap);
  renderBanners(snap);
  renderAgents(snap);
  renderBatches(snap);
  renderSources(snap);
  renderErrors(snap);
  renderTriage(snap);
}

function connect() {
  const source = new EventSource("/api/stream");
  source.addEventListener("snapshot", (e) => {
    try { render(JSON.parse(e.data)); } catch (err) { console.error(err); }
  });
  source.addEventListener("logs", (e) => {
    try { appendLogs(JSON.parse(e.data)); } catch (err) { console.error(err); }
  });
  source.onerror = () => {
    source.close();
    $("#pulse").className = "brand-dot err";
    setTimeout(connect, 3000); // sambung ulang otomatis
  };
}

// Muat kondisi awal lalu masuk ke mode realtime.
fetch("/api/snapshot").then((r) => r.json()).then(render).catch(() => {});
fetch("/api/events?limit=200").then((r) => r.json()).then((d) => appendLogs(d.events || [])).catch(() => {});
connect();
