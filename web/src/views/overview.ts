// The Overview: only what is live. A card shows while its work runs, waits or ran today, and the
// rest stay out of the way; the cards left share the row evenly. Upload history (today, streak,
// where the photos are, the year) is on the Sync page.

import { get, post, tz, type Disk, type Processes, type ProcessKey, type Status } from "../lib/api";
import { host, refresh, setPageStatus, setPauseControl, setRefreshControl } from "../lib/store";
import { sparkline } from "../lib/charts";
import { $, $$, esc, html, toast } from "../lib/dom";
import { ago, bytes, clock, date, fileName, hour, num, pct, speed } from "../lib/format";
import { dismissVisit, sinceVisit } from "../lib/since-visit";
import { icon, type IconName } from "../lib/icons";
import { NetChart, rangeButtons, type Range } from "../lib/netchart";
import { jobLink, jobTitle } from "../lib/jobs";
import { workBox, workCount, type WorkItem } from "../lib/work-panel";
import { badge, cardHead, eventMeta, openAttrs, spaceTone, thumb, tile, type Tone } from "../lib/ui";
import { liveProcesses, PROCESS_HREF } from "../lib/live";
import { PROCESS } from "../lib/processes";
import type { View } from "./view";

let chart: NetChart | null = null;
let procTimer = 0;
let last: Status | null = null;
let procs: Processes | null = null;

async function loadProcs(): Promise<void> {
  procs = await get<Processes>(`processes?days=14&tz=${tz}`).catch(() => procs);
  if (last) renderProcs(last);
}

/** Every kind of work running now, in one list: normal sync first, then the background jobs. Each
 * row is one file or item, labelled with its process and account, and opens its page. */
function runningWork(s: Status): WorkItem[] {
  const items: WorkItem[] = [];
  if (s.phase !== "paused")
    for (const u of s.uploading ?? (s.current ? [s.current] : []))
      items.push({
        key: `up:${u.account}:${u.name}`,
        name: fileName(u.name),
        step: u.step,
        done: u.bytes_done,
        total: u.bytes_total,
        label: u.account ? `Backup · ${u.account}` : "Backup",
        href: "#/sync",
      });
  for (const j of s.jobs ?? []) {
    const paused =
      j.kind === "storage"
        ? !!s.reclaim?.[j.account]?.paused
        : j.kind === "snapchat"
          ? !!s.snapchat?.[j.account]?.paused
          : false;
    items.push({
      key: `${j.kind}:${j.account}:${j.to ?? ""}:${j.name ?? ""}`,
      name: (j.name ?? "").split("/").pop() || jobTitle(j),
      step: j.kind === "library" ? "reading" : j.step || "working",
      done: j.done,
      total: j.total,
      label: jobTitle(j),
      // Paused while it runs: this step finishes, then it stops.
      note: paused ? "Pausing" : undefined,
      href: jobLink(j),
    });
  }
  return items;
}

/** Free space on the disk the photos are on, and on the app's data disk when it is another one. */
function spaceRows(s: Status): [string, string, string][] {
  const sp = s.space;
  const row = (label: string, d: Disk | null): [string, string, string] => [
    "disk",
    label,
    d
      ? `<span class="text-ink-2 tabular-nums">${bytes(d.free)} free</span> ${spaceTone(d) === "good" ? "" : badge(spaceTone(d) === "bad" ? "Almost full" : "Low", spaceTone(d))}`
      : `<span class="text-ink-3">Unknown</span>`,
  ];
  if (!sp) return [];
  const rows = [row(`Space on ${host()}`, sp.media)];
  if (!sp.same && sp.data) rows.push(row("App data disk", sp.data));
  return rows;
}

/** The control strip: every job at once (Pause everything / Resume everything) and a scan. Each
 * page has the same strip for its own job. */
function controls(s: Status): void {
  const busy = liveProcesses(s).filter((l) => l.running);
  const paused = !!s.all_paused;
  setPageStatus(
    paused
      ? {
          tone: "warn",
          // Work under way finishes first, then everything stops.
          title: busy.length ? `Pausing · finishing ${num(runningWork(s).length)}` : "Everything is paused",
          detail: busy.length ? "Paused once they're done" : "Uploads, Free storage, copies and date fixes",
        }
      : busy.length
        ? {
            tone: "run",
            // The same count as the Running now card below: each upload batch and job.
            title: `${num(runningWork(s).length)} running`,
            detail: busy.map((l) => PROCESS[l.key].label).join(", "),
          }
        : { tone: "good", title: "Nothing running right now", detail: "Work shows here as it starts" },
  );
  setPauseControl({
    paused,
    working: busy.length > 0,
    label: paused ? "Resume everything" : "Pause everything: uploads, Free storage, copies and date fixes",
    toggle: async () => {
      await post("pause-all", { paused: !paused });
      return paused ? "Everything resumed" : "Everything paused: work under way finishes first";
    },
  });
  setRefreshControl({
    label: "Scan your folders for new photos and videos",
    busy: s.phase === "scanning" || s.phase === "starting",
    run: async () => {
      await post("sync");
      return "Scanning for new photos and videos";
    },
  });
}

// ---- jobs: one card, a row per job in use, each with its own Pause / Resume

/** A job's state in words, with a tone. Paused while it still runs: what is under way finishes. */
function jobState(running: boolean, paused: boolean, waiting: number): [string, Tone, IconName] {
  if (paused) return running ? ["Pausing", "warn", "pause"] : ["Paused", "warn", "pause"];
  if (running) return ["Running", "info", "loader"];
  return waiting ? ["Waiting", "", "hourglass"] : ["Idle", "", "ok"];
}

/** One job's row: what it is doing, today's count with its last 14 days, and its Pause. */
function jobRow(s: Status, k: ProcessKey, l: ReturnType<typeof liveProcesses>[number]): string {
  const p = PROCESS[k];
  const today = procs?.today;
  const hist = procs?.processes[k];
  const day = hist?.days.find((d) => d.day === today);
  const days = Array.from({ length: 14 }, (_, i) => {
    if (!today) return 0;
    const d = new Date(`${today}T00:00:00`);
    d.setDate(d.getDate() - 13 + i);
    return hist?.days.find((x) => x.day === d.toLocaleDateString("sv"))?.count ?? 0;
  });
  const held = !!s.jobs_paused?.[k];
  const [state, tone, ic] = jobState(l.running, held, l.waiting);
  const verb = `${held ? "Resume" : "Pause"} ${p.label.toLowerCase()}`;
  const detail = l.detail.replace(/^Paused · /, ""); // the badge says Paused already
  return `<div class="job-row ${l.running && !held ? "is-running" : ""}" data-job="${k}">
    <a href="${PROCESS_HREF[k]}" class="job-main">
      <span class="job-ic">${icon(p.icon, "size-4")}</span>
      <span class="min-w-0 flex-1">
        <span class="flex items-center gap-2"><span class="truncate text-sm font-medium">${esc(p.label)}</span>${badge(state, tone, ic)}</span>
        <span class="mt-0.5 block truncate text-xs text-ink-3" title="${esc(detail)}">${esc(detail)}</span>
        <span class="bar mt-1.5 block h-1 ${l.running && l.pct !== null ? "" : "invisible"}"><i style="width:${l.pct ?? 0}%"></i></span>
      </span>
      <span class="shrink-0 text-right max-sm:hidden"><span class="num block text-lg leading-tight">${procs ? num(day?.count ?? 0) : "–"}</span><span class="block text-[11px] text-ink-3">${esc(p.noun)} today</span></span>
      <span class="hidden h-8 w-20 shrink-0 sm:block">${sparkline(days, p.color)}</span>
    </a>
    <button type="button" class="btn sm icon-sm shrink-0 ${held ? "primary" : ""}" data-job-pause="${k}" data-held="${held ? 1 : 0}" title="${esc(verb)}" aria-label="${esc(verb)}">${icon(held ? "play" : "pause", "size-3.5")}</button>
  </div>`;
}

/** The Jobs card: a row for each job running, waiting, paused or busy today. */
function renderProcs(s: Status): void {
  const body = document.getElementById("ov-jobs");
  if (!body) return;
  const today = procs?.today;
  const all = liveProcesses(s);
  const todayCount = (k: ProcessKey) => procs?.processes[k]?.days.find((d) => d.day === today)?.count ?? 0;
  const used = all.filter(
    (l) => l.running || l.paused || !!s.jobs_paused?.[l.key] || l.waiting > 0 || todayCount(l.key) > 0,
  );
  // All quiet: still show how the backup stands.
  const rows = used.length ? used : all.filter((l) => l.key === "uploads");
  const busy = rows.filter((l) => l.running).length;
  html($("#ov-jobs-count"), busy ? badge(`${num(busy)} running`, "info") : "");
  html(body, rows.map((l) => jobRow(s, l.key, l)).join(""));
}

async function pauseJob(b: HTMLButtonElement): Promise<void> {
  const job = b.dataset.jobPause as ProcessKey;
  const pause = b.dataset.held !== "1";
  b.disabled = true;
  try {
    await post("jobs/pause", { job, paused: pause });
    const name = PROCESS[job].label;
    toast(pause ? `${name} paused: work under way finishes first` : `${name} resumed`, "good");
    await refresh();
  } catch (e) {
    toast((e as Error).message, "bad");
  } finally {
    b.disabled = false;
  }
}

function runningCount(s: Status): string {
  return workCount(runningWork(s));
}

function runningList(s: Status): string {
  return (
    workBox(runningWork(s)) ||
    `<div class="empty">${icon("shield")}Nothing is running. Uploads, freeing storage, copies, date fixes and Snapchat memories show here as they start.</div>`
  );
}

// ---- customise: show, hide and reorder cards (saved in this browser)

const CARDS: [string, string][] = [
  ["ov-running", "Running now"],
  ["ov-procs", "Jobs"],
  ["ov-kpis", "Upload, download, CPU, memory"],
  ["ov-network", "Network"],
  ["ov-glance", "At a glance"],
  ["ov-recent", "Recent activity"],
];
// Saved under a new name since the upload history cards moved to the Sync page.
const LAYOUT_KEY = "ov-layout-3";

interface Layout {
  order: string[];
  hidden: string[];
}

function layout(): Layout {
  try {
    const l = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "") as Layout;
    const known = CARDS.map(([id]) => id);
    const order = l.order.filter((id) => known.includes(id));
    return { order: [...order, ...known.filter((id) => !order.includes(id))], hidden: l.hidden ?? [] };
  } catch {
    return { order: CARDS.map(([id]) => id), hidden: [] };
  }
}

function saveLayout(l: Layout | null): void {
  if (l) localStorage.setItem(LAYOUT_KEY, JSON.stringify(l));
  else localStorage.removeItem(LAYOUT_KEY);
  applyLayout();
}

function applyLayout(): void {
  const l = layout();
  document.querySelectorAll<HTMLElement>("[data-card]").forEach((c) => {
    const id = c.dataset.card ?? "";
    c.style.order = String(l.order.indexOf(id));
    c.classList.toggle("hidden", l.hidden.includes(id));
  });
}

function openCustomize(): void {
  let sheet = document.getElementById("ov-sheet");
  if (!sheet) {
    sheet = document.createElement("div");
    sheet.id = "ov-sheet";
    sheet.className =
      "fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 backdrop-blur-sm sm:items-center sm:p-4";
    document.body.append(sheet);
    sheet.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      const l = layout();
      const b = t.closest<HTMLElement>("[data-act]");
      if (t === sheet || t.closest("[data-done]")) {
        sheet?.remove();
        return;
      }
      if (t.closest("[data-reset]")) {
        saveLayout(null);
        renderSheet();
        return;
      }
      if (!b) return;
      const id = b.dataset.id ?? "";
      const i = l.order.indexOf(id);
      if (b.dataset.act === "toggle") {
        l.hidden = l.hidden.includes(id) ? l.hidden.filter((x) => x !== id) : [...l.hidden, id];
      } else {
        const j = b.dataset.act === "up" ? i - 1 : i + 1;
        if (j < 0 || j >= l.order.length) return;
        [l.order[i], l.order[j]] = [l.order[j]!, l.order[i]!];
      }
      saveLayout(l);
      renderSheet();
    });
  }
  renderSheet();
}

function renderSheet(): void {
  const sheet = document.getElementById("ov-sheet");
  if (!sheet) return;
  const l = layout();
  const title = new Map(CARDS);
  sheet.innerHTML = `<div class="card w-full max-w-md rounded-b-none bg-bg p-2 sm:rounded-2xl" style="padding-bottom:calc(.5rem + env(safe-area-inset-bottom))">
    <div class="flex items-center gap-2 px-3 py-2"><span class="font-medium">Customize overview</span><button class="btn sm ml-auto" data-reset>${icon("retry")}Reset</button><button class="btn sm primary" data-done>Done</button></div>
    ${l.order
      .map((id, i) => {
        const on = !l.hidden.includes(id);
        return `<div class="row rounded-xl border-0"><button type="button" role="switch" class="switch" aria-checked="${on}" data-act="toggle" data-id="${id}" aria-label="Show ${esc(title.get(id) ?? id)}"></button>
          <span class="flex-1 text-sm ${on ? "" : "text-ink-3"}">${esc(title.get(id) ?? id)}</span>
          <button class="btn icon sm" data-act="up" data-id="${id}" ${i === 0 ? "disabled" : ""} aria-label="Move up">${icon("up")}</button>
          <button class="btn icon sm" data-act="down" data-id="${id}" ${i === l.order.length - 1 ? "disabled" : ""} aria-label="Move down">${icon("down")}</button></div>`;
      })
      .join("")}</div>`;
}

/** "Since your last visit, 14 h ago: +142 added · 138 backed up · 2 failed". */
function drawSince(s: Status): void {
  const box = document.getElementById("ov-since");
  if (!box) return;
  const d = sinceVisit(s);
  box.hidden = !d;
  if (!d) return;
  const parts = [
    d.added ? `<span><span class="num text-ink">+${num(d.added)}</span> added</span>` : "",
    d.backedUp ? `<span><span class="num text-ink">${num(d.backedUp)}</span> backed up</span>` : "",
    d.failed
      ? `<a class="text-bad hover:underline" href="#/activity/failed"><span class="num">${num(d.failed)}</span> failed</a>`
      : "",
  ].filter(Boolean);
  html(
    box,
    `<div class="card mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm text-ink-2">
      <span class="flex items-center gap-2 text-ink-3">${icon("clock", "size-4")}<span title="${esc(`${date(d.at, true)} ${clock(d.at)}`)}">Since your last visit, ${esc(ago(d.at))}</span></span>
      ${parts.join('<span class="text-ink-3" aria-hidden="true">·</span>')}
      <button type="button" class="link-btn ml-auto" data-since-ok>Got it</button>
    </div>`,
  );
}

const overview: View = {
  mount(el) {
    el.innerHTML = `
    <!-- A quiet "Edit" link like iOS lists: there when wanted, no button weight on top of the page. -->
    <div class="-mt-2 mb-1 flex justify-end">
      <button class="link-btn" data-ov-custom aria-label="Customize overview">${icon("sliders")}Edit</button>
    </div>
    <div id="ov-since" hidden></div>
    <div class="grid grid-flow-row-dense grid-cols-1 gap-4 lg:grid-cols-12" id="ov-grid">
      <section class="card is-idle lg:col-span-12" data-card="ov-running" id="ov-running-card">
        ${cardHead("Running now", "loader", '<span id="ov-running-count"></span>')}
        <div id="ov-running"></div>
      </section>

      <section class="card lg:col-span-12" data-card="ov-procs" id="ov-procs">
        ${cardHead("Jobs", "layers", '<span id="ov-jobs-count"></span>')}
        <div id="ov-jobs"><div class="skeleton m-4 h-16"></div></div>
      </section>

      <section class="grid grid-cols-2 gap-4 lg:col-span-12 xl:grid-cols-4" data-card="ov-kpis">
        ${tile({ id: "k-up", label: "Upload", value: "–", ic: "up", chart: "", href: "#/network" })}
        ${tile({ id: "k-down", label: "Download", value: "–", ic: "down", chart: "", href: "#/network" })}
        ${tile({ id: "k-cpu", label: "App CPU", value: "–", ic: "cpu", chart: "", href: "#/health" })}
        ${tile({ id: "k-mem", label: "App memory", value: "–", ic: "memory", chart: "", href: "#/health" })}
      </section>

      <section class="card flex min-w-0 flex-col lg:col-span-8" data-card="ov-network">
        ${cardHead("Network", "wifi", `<span class="hidden items-center gap-3 sm:flex"><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s1"></i>Upload</span><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s2"></i>Download</span></span><div class="seg ml-2" id="ov-range">${rangeButtons("live")}</div>`)}
        <div class="card-b flex-1"><div id="ov-net" data-chart class="h-[200px] w-full sm:h-[300px]"></div></div>
      </section>

      <section class="card lg:col-span-4" data-card="ov-glance">
        ${cardHead("At a glance", "server")}
        <div class="px-4 py-1" id="ov-glance"></div>
      </section>

      <section class="card is-idle lg:col-span-12" data-card="ov-recent" id="ov-recent-card">
        ${cardHead("Recent activity", "activity", '<a href="#/activity" class="hover:text-ink">See all ›</a>')}
        <div id="ov-recent" class="lg:grid lg:grid-cols-2 lg:gap-x-4" data-gallery></div>
      </section>
    </div>`;
    applyLayout();
    $("#ov-jobs").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-job-pause]");
      if (b) void pauseJob(b);
    });
    $$("[data-ov-custom]").forEach((b) => b.addEventListener("click", openCustomize));
    $("#ov-since").addEventListener("click", (e) => {
      if (!(e.target as HTMLElement).closest("[data-since-ok]") || !last) return;
      dismissVisit(last);
      drawSince(last);
    });
    chart = new NetChart($("#ov-net"), matchMedia("(max-width: 640px)").matches ? 200 : 300);
    $("#ov-range").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-range]");
      if (!b) return;
      $$("button", $("#ov-range")).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      chart?.setRange(b.dataset.range as Range, last);
    });
    void loadProcs();
    procTimer = window.setInterval(loadProcs, 60_000);
  },
  update(s) {
    last = s;
    controls(s);
    drawSince(s);
    const r = s.resources;
    const setTile = (id: string, v: string, sub: string, chartSvg?: string) => {
      const t = $(id);
      html($("[data-v]", t), v);
      html($("[data-s]", t), sub);
      if (chartSvg !== undefined) html($("[data-c]", t), chartSvg);
    };
    const peak = (a: number[]) => Math.max(0, ...a);
    setTile(
      "#k-up",
      speed(s.net.up_now),
      `Peak ${speed(peak(s.net.up))} · sent ${bytes(s.net.sent)}`,
      sparkline(s.net.up, "var(--s1)"),
    );
    setTile(
      "#k-down",
      speed(s.net.down_now),
      `Peak ${speed(peak(s.net.down))} · received ${bytes(s.net.received)}`,
      sparkline(s.net.down, "var(--s2)"),
    );
    setTile(
      "#k-cpu",
      `${(r.cpu_now / Math.max(1, r.cores)).toFixed(1)}%`,
      `of all ${r.cores} cores · ${r.cpu_now.toFixed(0)}% of one`,
      sparkline(r.cpu, "var(--s1)"),
    );
    setTile(
      "#k-mem",
      bytes(r.mem_now),
      r.mem_limit ? `${pct(r.mem_now, r.mem_limit).toFixed(1)}% of ${bytes(r.mem_limit)}` : "",
      sparkline(r.mem, "var(--s1)"),
    );
    chart?.update(s);

    const lat = r.latency_now;
    const latTone = lat == null ? "bad" : lat < 150 ? "good" : lat < 400 ? "warn" : "bad";
    const glance: [string, string, string][] = [
      [
        "users",
        "Google accounts",
        s.accounts.length
          ? s.login_failed.length
            ? badge(`${s.login_failed.length} rejected`, "bad", "warn")
            : badge(`${s.accounts.length} connected`, "good", "ok")
          : `<a href="#/settings/accounts">${badge("Connect one", "warn", "key")}</a>`,
      ],
      [
        "zap",
        "Google response",
        lat == null
          ? badge("Unreachable", "bad", "cloudOff")
          : `<span class="text-ink-2 tabular-nums">${Math.round(lat)} ms</span> ${badge(latTone === "good" ? "Fast" : latTone === "warn" ? "OK" : "Slow", latTone)}`,
      ],
      ["pause", "Work", s.all_paused ? badge("All paused", "warn", "pause") : badge("On", "good", "play")],
      ...spaceRows(s),
      [
        "error",
        "Failed files",
        s.counts.failed
          ? `<a href="#/activity/failed">${badge(num(s.counts.failed), "bad")}</a>`
          : `<span class="text-ink-2">None</span>`,
      ],
      [
        "clock",
        "Upload hours",
        s.schedule.enabled
          ? badge(`${hour(s.schedule.start)}–${hour(s.schedule.end)}`, s.schedule.allowed ? "good" : "warn")
          : `<span class="text-ink-2">Any time</span>`,
      ],
      [
        "gauge",
        "Speed limit",
        s.speed_limit_mbps
          ? badge(`${s.speed_limit_mbps} Mbps`, "info")
          : `<span class="text-ink-2">No limit</span>`,
      ],
      [
        "database",
        "Statistics",
        `<span class="truncate text-ink-2">${esc(s.stats_engine.split(" (")[0])}</span>`,
      ],
    ];
    html(
      $("#ov-glance"),
      glance
        .map(
          ([ic, l, v]) =>
            `<div class="kv"><span class="flex items-center gap-2">${icon(ic as "users", "size-4")}${l}</span><span class="flex items-center gap-1.5">${v}</span></div>`,
        )
        .join(""),
    );

    const running = runningWork(s);
    $("#ov-running-card").classList.toggle("is-idle", !running.length);
    html($("#ov-running"), runningList(s));
    html($("#ov-running-count"), runningCount(s));
    renderProcs(s);

    // Work done, not housekeeping ("Settings saved", scans): those stay in Activity › System.
    const recent = s.log.filter((l) => l.kind !== "info" && l.kind !== "scan");
    $("#ov-recent-card").classList.toggle("is-idle", !recent.length);
    html(
      $("#ov-recent"),
      recent
        .slice(0, 6)
        .map((l) => {
          const m = eventMeta[l.kind] ?? eventMeta.info!;
          const lead = l.name
            ? thumb(l.name)
            : `<span class="thumb grid place-items-center text-ink-3">${icon(m.icon, "size-4")}</span>`;
          return `<div class="row ${l.name ? "clickable" : ""}" ${l.name ? openAttrs(l.name, true) : ""}>${lead}<div class="min-w-0 flex-1"><div class="truncate text-sm">${esc(l.name ? fileName(l.name) : l.text)}</div><div class="text-xs text-ink-3">${clock(l.time)}</div></div>${badge(m.label, m.tone)}</div>`;
        })
        .join(""),
    );
  },
  unmount() {
    clearInterval(procTimer);
    chart = null;
    document.getElementById("ov-sheet")?.remove();
  },
};

export default overview;
