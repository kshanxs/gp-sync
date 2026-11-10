// The Overview: only what is live. A card shows while its work runs, waits or ran today, and the
// rest stay out of the way; the cards left share the row evenly. Upload history (today, streak,
// where the photos are, the year) is on the Sync page.

import { get, post, tz, type Disk, type Processes, type Status } from "../lib/api";
import { host, setPageStatus, setPauseControl, setRefreshControl } from "../lib/store";
import { sparkline } from "../lib/charts";
import { $, $$, esc, html } from "../lib/dom";
import { bytes, clock, fileName, num, pct, speed } from "../lib/format";
import { icon, type IconName } from "../lib/icons";
import { NetChart, rangeButtons, type Range } from "../lib/netchart";
import { jobLink, jobStep, jobTitle } from "../lib/jobs";
import { badge, cardHead, eventMeta, openAttrs, spaceTone, thumb, tile, type Tone } from "../lib/ui";
import { liveProcesses, PROCESS_HREF } from "../lib/live";
import { PROCESS, PROCESS_KEYS } from "../lib/processes";
import type { View } from "./view";

let chart: NetChart | null = null;
let procTimer = 0;
let last: Status | null = null;
let procs: Processes | null = null;

async function loadProcs(): Promise<void> {
  procs = await get<Processes>(`processes?days=14&tz=${tz}`).catch(() => procs);
  if (last) renderProcs(last);
}

/** Every kind of work running now, in one list: normal sync first, then the background jobs. */
function runningItems(s: Status): {
  icon: IconName;
  title: string;
  step: string;
  pct: number | null;
  href: string;
  spin: boolean;
  paused: boolean;
}[] {
  const items: ReturnType<typeof runningItems> = [];
  const up = s.uploading ?? (s.current ? [s.current] : []);
  if (up.length && s.phase !== "paused") {
    items.push({
      icon: "upload",
      title: `Uploading · ${num(up.length)} file${up.length === 1 ? "" : "s"} at once`,
      step:
        up.length === 1
          ? fileName(up[0]!.name)
          : up
              .map((u) => fileName(u.name))
              .slice(0, 2)
              .join(", "),
      pct: up.length === 1 ? pct(up[0]!.bytes_done, up[0]!.bytes_total) : null,
      href: "#/sync",
      spin: false,
      paused: false,
    });
  }
  for (const j of s.jobs ?? []) {
    const paused = j.kind === "storage" ? !!s.reclaim?.[j.account]?.paused : false;
    items.push({
      icon:
        j.kind === "library"
          ? "refresh"
          : j.kind === "copy"
            ? "users"
            : j.kind === "dates"
              ? "clock"
              : "cloud",
      title: jobTitle(j),
      step: paused ? "Paused" : jobStep(j),
      pct: j.total ? pct(j.done ?? 0, j.total) : null,
      href: jobLink(j),
      spin: j.kind === "library",
      paused,
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
          title: "Everything is paused",
          detail: "Uploads, Free storage, copies and date fixes",
        }
      : busy.length
        ? {
            tone: "run",
            // The same count as the Running now card below: each upload batch and job.
            title: `${num(runningItems(s).length)} running`,
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
      return paused ? "Everything resumed" : "Everything paused: items under way finish their step";
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

// Columns for the process cards in use, so they always fill the row.
const PROC_COLS: Record<number, string> = {
  1: "",
  2: "sm:grid-cols-2",
  3: "md:grid-cols-3",
  4: "sm:grid-cols-2 xl:grid-cols-4",
};

/** One card per process in use: what it is doing now and what it did today, with the last 14 days. */
function renderProcs(s: Status): void {
  if (!document.getElementById("ov-procs")) return;
  const today = procs?.today;
  const all = liveProcesses(s);
  const todayCount = (k: string) =>
    procs?.processes[k as keyof Processes["processes"]]?.days.find((d) => d.day === today)?.count ?? 0;
  // In use: running, waiting, paused with items left, or did something today.
  const used = new Set(
    all.filter((l) => l.running || l.paused || l.waiting > 0 || todayCount(l.key) > 0).map((l) => l.key),
  );
  if (!used.size) used.add("uploads"); // all quiet: still show how the backup stands
  const grid = $("#ov-procs");
  grid.className = `grid grid-cols-1 gap-4 lg:col-span-12 ${PROC_COLS[used.size] ?? PROC_COLS[4]}`;
  for (const l of all) {
    const el = document.querySelector<HTMLElement>(`[data-proc="${l.key}"]`);
    if (!el) continue;
    el.classList.toggle("is-idle", !used.has(l.key));
    if (!used.has(l.key)) continue;
    const p = PROCESS[l.key];
    const hist = procs?.processes[l.key];
    const day = hist?.days.find((d) => d.day === today);
    const last14 = Array.from({ length: 14 }, (_, i) => {
      if (!today) return 0;
      const d = new Date(`${today}T00:00:00`);
      d.setDate(d.getDate() - 13 + i);
      return hist?.days.find((x) => x.day === d.toLocaleDateString("sv"))?.count ?? 0;
    });
    // In a narrow card (four in a row) the badge keeps only its icon, so the name fits.
    const stateBadge = (label: string, tone: Tone, ic: IconName) =>
      `<span class="badge ${tone} shrink-0" title="${label}">${icon(ic)}<span class="hidden @[15rem]:inline">${label}</span></span>`;
    const state = l.paused
      ? stateBadge("Paused", "warn", "pause")
      : l.running
        ? stateBadge("Running", "info", "loader")
        : l.waiting
          ? stateBadge("Waiting", "", "hourglass")
          : stateBadge("Idle", "", "ok");
    const count = day?.count ?? 0;
    el.classList.toggle("is-running", l.running && !l.paused);
    html(
      el,
      `<div class="flex items-center gap-2.5"><span class="grid size-9 shrink-0 place-items-center rounded-lg bg-panel-2" style="color:${p.color}">${icon(p.icon, "size-4")}</span>
        <span class="min-w-0 flex-1 truncate font-medium">${esc(p.label)}</span>${state}</div>
      <div class="mt-3 flex items-end justify-between gap-3">
        <div class="min-w-0"><div class="num text-2xl">${procs ? num(count) : "–"}</div>
          <div class="text-xs text-ink-3">${esc(p.noun)} today${day?.bytes ? ` · ${bytes(day.bytes)}` : ""}</div></div>
        <div class="h-8 w-16 shrink-0 @[15rem]:w-24">${sparkline(last14, p.color)}</div>
      </div>
      <div class="mt-3 truncate text-xs text-ink-2" title="${esc(l.detail)}">${esc(l.detail)}</div>
      <span class="bar mt-1.5 block h-1.5 ${l.running && l.pct !== null ? "" : "invisible"}"><i style="width:${l.pct ?? 0}%"></i></span>`,
    );
  }
}

function runningCount(s: Status): string {
  const n = runningItems(s).length;
  return n
    ? `<span class="badge info">${num(n)} running</span>`
    : `<span class="text-xs text-ink-3">Nothing running</span>`;
}

function runningList(s: Status): string {
  const items = runningItems(s);
  if (!items.length)
    return `<div class="empty">${icon("shield")}Nothing is running. Uploads, freeing storage, copies and date fixes show here as they start.</div>`;
  return `<ul class="divide-y divide-line">${items
    .map(
      (i) => `<li><a href="${i.href}" class="flex items-center gap-3 px-4 py-3 hover:bg-panel-2">
        <span class="grid size-9 shrink-0 place-items-center rounded-lg bg-panel-2 text-accent">${icon(i.icon, `size-4 ${i.spin ? "spin" : ""}`)}</span>
        <span class="min-w-0 flex-1">
          <span class="flex items-baseline gap-2"><span class="truncate text-sm font-medium">${esc(i.title)}</span>${i.paused ? badge("Paused", "warn", "pause") : ""}</span>
          ${i.step ? `<span class="block truncate text-xs text-ink-3">${esc(i.step)}</span>` : ""}
          ${i.pct !== null ? `<span class="bar mt-1.5 block h-1.5"><i style="width:${i.pct}%"></i></span>` : ""}
        </span>
        ${icon("chevron", "size-4 shrink-0 text-ink-3")}
      </a></li>`,
    )
    .join("")}</ul>`;
}

// ---- customise: show, hide and reorder cards (saved in this browser)

const CARDS: [string, string][] = [
  ["ov-running", "Running now"],
  ["ov-procs", "Uploads, Free storage, Copies, Date fixes"],
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
    <div class="flex items-center gap-2 px-3 py-2"><span class="font-semibold">Customize overview</span><button class="btn sm ml-auto" data-reset>${icon("retry")}Reset</button><button class="btn sm primary" data-done>Done</button></div>
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

const overview: View = {
  mount(el) {
    el.innerHTML = `
    <!-- A quiet "Edit" link like iOS lists: there when wanted, no button weight on top of the page. -->
    <div class="-mt-2 mb-1 flex justify-end">
      <button class="link-btn" data-ov-custom aria-label="Customize overview">${icon("sliders")}Edit</button>
    </div>
    <div class="grid grid-flow-row-dense grid-cols-1 gap-4 lg:grid-cols-12" id="ov-grid">
      <section class="card is-idle lg:col-span-12" data-card="ov-running" id="ov-running-card">
        ${cardHead("Running now", "loader", '<span id="ov-running-count"></span>')}
        <div id="ov-running"></div>
      </section>

      <section class="grid grid-cols-1 gap-4 lg:col-span-12" data-card="ov-procs" id="ov-procs">
        ${PROCESS_KEYS.map((k) => `<a href="${PROCESS_HREF[k]}" class="card proc-card @container block p-4 ${k === "uploads" ? "" : "is-idle"}" data-proc="${k}"><div class="skeleton h-28"></div></a>`).join("")}
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
        ${cardHead("At a glance", "sparkles")}
        <div class="px-4 py-1" id="ov-glance"></div>
      </section>

      <section class="card is-idle lg:col-span-12" data-card="ov-recent" id="ov-recent-card">
        ${cardHead("Recent activity", "activity", '<a href="#/activity" class="hover:text-ink">See all ›</a>')}
        <div id="ov-recent" class="lg:grid lg:grid-cols-2 lg:gap-x-4" data-gallery></div>
      </section>
    </div>`;
    applyLayout();
    $$("[data-ov-custom]").forEach((b) => b.addEventListener("click", openCustomize));
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
          ? badge(
              `${String(s.schedule.start).padStart(2, "0")}–${String(s.schedule.end).padStart(2, "0")}`,
              s.schedule.allowed ? "good" : "warn",
            )
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

    const running = runningItems(s);
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
