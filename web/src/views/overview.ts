import {
  get,
  tz,
  type FolderReport,
  type History,
  type Insights,
  type Processes,
  type Status,
} from "../lib/api";
import { donut, heatLegend, heatmap, sparkline, type Slice } from "../lib/charts";
import { $, $$, animateChange, esc, html } from "../lib/dom";
import { ago, bytes, clock, duration, fileName, ms, num, pct, speed } from "../lib/format";
import { icon, type IconName } from "../lib/icons";
import { NetChart, rangeButtons, type Range } from "../lib/netchart";
import { jobLink, jobStep, jobTitle } from "../lib/jobs";
import { badge, cardHead, eventMeta, openAttrs, thumb, tile } from "../lib/ui";
import { liveProcesses, PROCESS_HREF } from "../lib/live";
import { PROCESS, PROCESS_KEYS } from "../lib/processes";
import type { View } from "./view";

let chart: NetChart | null = null;
let hist: History | null = null;
let ins: Insights | null = null;
let slowTimer = 0;
let slowSynced = -1; // uploaded count the heat map and streak were last loaded for
let slowAt = 0;
let last: Status | null = null;
let procs: Processes | null = null;

// ---- where the photos are: pie charts per device (or folder) and photos vs videos
let share: FolderReport | null = null;
let shareBy: "devices" | "folders" = "devices";
let shareMetric: "files" | "bytes" = "files";
let shareAt = 0;
let shareRequest = 0;
let shareError = false;
let kindsDrawn = false; // the photos/videos donut needs the status, which can come later
const SLICE_COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"];
const OTHER = "var(--ink-3)";

async function loadShare(interactive = false): Promise<void> {
  shareAt = Date.now();
  const mine = ++shareRequest;
  const by = shareBy;
  if (interactive) {
    share = null;
    shareError = false;
    renderShare();
  }
  const r = await get<FolderReport>(`folders?group=${by}&days=30&tz=${tz}`).catch(() => null);
  if (mine !== shareRequest || by !== shareBy) return;
  share = r;
  shareError = !r;
  renderShare();
  if (interactive) animateChange(document.getElementById("ov-share-body"));
}

function pctText(part: number, whole: number): string {
  const p = whole ? (part / whole) * 100 : 0;
  return p > 0 && p < 1 ? "<1%" : `${Math.round(p)}%`;
}

function legendRow(color: string, name: string, value: number, total: number, extra = "", href = ""): string {
  const label = href
    ? `<a class="truncate hover:underline" href="${href}">${esc(name)}</a>`
    : `<span class="truncate">${esc(name)}</span>`;
  return `<li class="flex items-center gap-2 py-1.5 text-sm"><i class="size-2.5 shrink-0 rounded-full" style="background:${color}"></i>${label}
    <span class="ml-auto shrink-0 tabular-nums">${pctText(value, total)}</span>
    <span class="w-16 shrink-0 text-right text-xs text-ink-3 tabular-nums sm:w-20">${shareMetric === "files" ? num(value) : bytes(value)}</span>${extra}</li>`;
}

function renderShare(): void {
  const el = document.getElementById("ov-share-body");
  if (!el) return;
  $$("button", $("#ov-share-by")).forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.by === shareBy)),
  );
  $$("button", $("#ov-share-metric")).forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.m === shareMetric)),
  );
  const m = (f: { files: number; bytes: number }) => (shareMetric === "files" ? f.files : f.bytes);
  const fmt = (v: number) => (shareMetric === "files" ? num(v) : bytes(v));
  // Groups: biggest first; past five slices the rest is one grey "Other", so colours never repeat.
  const groups = [...(share?.children ?? [])].filter((c) => c.name && m(c) > 0).sort((a, b) => m(b) - m(a));
  const shown = groups.length > 5 ? groups.slice(0, 4) : groups;
  const rest = groups.slice(shown.length);
  const total = groups.reduce((a, g) => a + m(g), 0);
  const link = (g: (typeof groups)[number]) =>
    `#/folders/${shareBy === "devices" || g.device ? "d" : "f"}/${(g.folders[0] ?? g.name).split("/").map(encodeURIComponent).join("/")}`;
  const slices: Slice[] = shown.map((g, i) => ({
    label: g.name,
    value: m(g),
    color: SLICE_COLORS[i]!,
    tip: `${g.name}\n${fmt(m(g))} · ${pctText(m(g), total)}\n${Math.round(pct(g.synced, g.files))}% in Google Photos\nClick to open`,
    href: link(g),
  }));
  const restValue = rest.reduce((a, g) => a + m(g), 0);
  if (rest.length)
    slices.push({
      label: "Other",
      value: restValue,
      color: OTHER,
      tip: `${rest.length} more\n${fmt(restValue)} · ${pctText(restValue, total)}\nClick to see all`,
      href: shareBy === "devices" ? "#/folders" : "#/folders/f",
    });
  const backed = (g: (typeof groups)[number]) => {
    const p = Math.round(pct(g.synced, g.files));
    return `<span class="flex w-16 shrink-0 items-center gap-1.5 sm:w-24" title="${p}% in Google Photos"><span class="bar h-1.5 flex-1"><i style="width:${p}%"></i></span><span class="w-8 text-right text-[11px] text-ink-3 tabular-nums">${p}%</span></span>`;
  };
  const lib = last?.library;
  kindsDrawn = !!lib;
  const kinds = lib
    ? [
        {
          label: "Photos",
          value: shareMetric === "files" ? (lib.photos ?? 0) : (lib.photo_bytes ?? 0),
          color: "var(--s1)",
          href: "#/folders/f/~photo",
        },
        {
          label: "Videos",
          value: shareMetric === "files" ? (lib.videos ?? 0) : (lib.video_bytes ?? 0),
          color: "var(--s2)",
          href: "#/folders/f/~video",
        },
      ]
    : [];
  const kindTotal = kinds.reduce((a, k) => a + k.value, 0);
  const center = (v: number, sub: string) =>
    `<div><div class="num text-xl">${shareMetric === "files" ? num(v) : bytes(v)}</div><div class="text-[11px] text-ink-3">${sub}</div></div>`;
  html(
    el,
    `<div class="grid gap-6 md:grid-cols-2">
      <div class="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
        ${donut(slices, center(total, shareBy === "devices" ? "on devices" : "in folders"))}
        <div class="w-full min-w-0 flex-1"><div class="label mb-1">${shareBy === "devices" ? "By device" : "By folder"}</div>
          ${
            groups.length
              ? `<ul>${shown.map((g, i) => legendRow(SLICE_COLORS[i]!, g.name, m(g), total, backed(g), link(g))).join("")}${rest.length ? legendRow(OTHER, `Other (${rest.length})`, restValue, total, "", shareBy === "devices" ? "#/folders" : "#/folders/f") : ""}</ul>`
              : `<p class="py-2 text-sm text-ink-3">${share ? (shareBy === "devices" ? "No device folders found. Try Folders." : "No folders found.") : shareError ? "Couldn't load this view." : "Loading…"}</p>`
          }</div>
      </div>
      <div class="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
        ${donut(
          kinds.map((k) => ({
            ...k,
            tip: `${k.label}\n${fmt(k.value)} · ${pctText(k.value, kindTotal)}\nClick to open`,
          })),
          center(kindTotal, "backed-up library"),
        )}
        <div class="w-full min-w-0 flex-1"><div class="label mb-1">Photos and videos</div>
          <ul>${kinds.map((k) => legendRow(k.color, k.label, k.value, kindTotal, "", k.href)).join("")}</ul>
          ${lib ? `<p class="mt-2 text-xs text-ink-3">${shareMetric === "files" ? `Videos are ${pctText(lib.videos ?? 0, lib.files)} of the files but ${pctText(lib.video_bytes ?? 0, lib.bytes)} of the space.` : `Photos take ${bytes(lib.photo_bytes)} and videos ${bytes(lib.video_bytes)}.`}</p>` : ""}
        </div>
      </div>
    </div>`,
  );
}

function streak(h: History): number {
  // Same count as the heat map, so the streak and the coloured days agree.
  const byDay = new Map(h.data.map((d) => [d.day, d.photos + d.videos]));
  let n = 0;
  const d = new Date(h.today + "T00:00:00");
  if (!byDay.get(h.today)) d.setDate(d.getDate() - 1); // today may still be coming
  for (;;) {
    const key = d.toLocaleDateString("sv"); // YYYY-MM-DD
    if (!byDay.get(key)) return n;
    n++;
    d.setDate(d.getDate() - 1);
  }
}

async function loadSlow(): Promise<void> {
  slowAt = Date.now();
  [hist, ins, procs] = await Promise.all([
    get<History>(`history?days=371&tz=${tz}`),
    get<Insights>(`insights?tz=${tz}`),
    get<Processes>(`processes?days=14&tz=${tz}`).catch(() => procs),
  ]);
  renderSlow();
  if (last) renderProcs(last);
}

function renderSlow(): void {
  if (!hist || !document.getElementById("ov-year")) return;
  const today = hist.data.find((d) => d.day === hist?.today);
  const last14 = (() => {
    const m = new Map(hist.data.map((d) => [d.day, d.photos + d.videos]));
    return Array.from({ length: 14 }, (_, i) => {
      const d = new Date(hist!.today + "T00:00:00");
      d.setDate(d.getDate() - 13 + i);
      return m.get(d.toLocaleDateString("sv")) ?? 0;
    });
  })();
  const t = $("#k-today");
  html($("[data-v]", t), num((today?.photos ?? 0) + (today?.videos ?? 0)));
  html(
    $("[data-s]", t),
    `${num(today?.photos ?? 0)} photos · ${num(today?.videos ?? 0)} videos · ${bytes((today?.photo_bytes ?? 0) + (today?.video_bytes ?? 0))}`,
  );
  html($("[data-c]", t), sparkline(last14, "var(--s1)"));
  const st = streak(hist);
  const k = $("#k-streak");
  html($("[data-v]", k), `${st} day${st === 1 ? "" : "s"}`);
  const active = hist.data.filter((d) => d.photos + d.videos > 0).length;
  html($("[data-s]", k), `${num(active)} active day${active === 1 ? "" : "s"} this year`);
  html(
    $("[data-c]", k),
    `<div class="flex gap-1">${last14
      .slice(-14)
      .map(
        (v) =>
          `<i class="h-3 flex-1 rounded-[3px]" style="background:${v ? "var(--s1)" : "var(--panel-2)"}"></i>`,
      )
      .join("")}</div>`,
  );

  // Year of uploads
  const days: { day: string; value: number; tip: string }[] = [];
  const byDay = new Map(hist.data.map((d) => [d.day, d]));
  const end = new Date(hist.today + "T00:00:00");
  for (let i = 364; i >= 0; i--) {
    const d = new Date(end);
    d.setDate(d.getDate() - i);
    const key = d.toLocaleDateString("sv");
    const row = byDay.get(key);
    const n = (row?.photos ?? 0) + (row?.videos ?? 0);
    days.push({
      day: key,
      value: n,
      tip: `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" })}\n${n ? `${row?.photos ?? 0} photos, ${row?.videos ?? 0} videos · ${bytes((row?.photo_bytes ?? 0) + (row?.video_bytes ?? 0))}` : "Nothing uploaded"}`,
    });
  }
  const yearTotal = days.reduce((a, d) => a + d.value, 0);
  html($("#ov-year-sub"), `${num(yearTotal)} files in the last year`);
  const year = $("#ov-year");
  const first = !year.dataset.html;
  html(year, heatmap(days));
  if (first) year.scrollLeft = year.scrollWidth; // phones: start at today

  if (ins) {
    const rows: [string, string, string][] = [
      ["Time spent uploading", duration(ins.upload_ms / 1000), "timer"],
      ["Average per photo", ins.photo.avg_ms ? ms(ins.photo.avg_ms) : "–", "photo"],
      ["Average per video", ins.video.avg_ms ? ms(ins.video.avg_ms) : "–", "video"],
      ["Average photo size", bytes(ins.photo.avg_bytes), "photo"],
      ["Average video size", bytes(ins.video.avg_bytes), "video"],
      ["Average upload speed", ins.avg_speed ? speed(ins.avg_speed) : "–", "gauge"],
    ];
    html(
      $("#ov-times"),
      `<div class="grid grid-cols-2 gap-2">${rows
        .map(
          ([l, v, ic]) =>
            `<div class="stat-tile"><div class="flex items-center gap-1.5 text-xs text-ink-3">${icon(ic as "timer", "size-3.5")}${esc(l)}</div><div class="num text-lg">${esc(v)}</div></div>`,
        )
        .join("")}</div>
       ${ins.timed_files < ins.uploaded ? `<p class="mt-3 text-xs text-ink-3">${icon("info", "inline size-3.5 -mt-0.5")} Upload times are known for ${num(ins.timed_files)} of ${num(ins.uploaded)} uploads (older ones weren't timed).</p>` : ""}`,
    );
  }
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

/** One card per process: what it is doing now and what it did today, with the last 14 days. */
function renderProcs(s: Status): void {
  if (!document.getElementById("ov-procs")) return;
  const today = procs?.today;
  for (const l of liveProcesses(s)) {
    const el = document.querySelector<HTMLElement>(`[data-proc="${l.key}"]`);
    if (!el) continue;
    const p = PROCESS[l.key];
    const hist = procs?.processes[l.key];
    const day = hist?.days.find((d) => d.day === today);
    const last14 = Array.from({ length: 14 }, (_, i) => {
      if (!today) return 0;
      const d = new Date(`${today}T00:00:00`);
      d.setDate(d.getDate() - 13 + i);
      return hist?.days.find((x) => x.day === d.toLocaleDateString("sv"))?.count ?? 0;
    });
    const state = l.paused
      ? badge("Paused", "warn", "pause")
      : l.running
        ? badge("Running", "info", "loader")
        : l.waiting
          ? badge("Waiting", "", "hourglass")
          : badge("Idle", "", "ok");
    const count = day?.count ?? 0;
    el.classList.toggle("is-running", l.running && !l.paused);
    html(
      el,
      `<div class="flex items-center gap-2.5"><span class="grid size-9 shrink-0 place-items-center rounded-lg bg-panel-2" style="color:${p.color}">${icon(p.icon, "size-4")}</span>
        <span class="min-w-0 flex-1 truncate font-medium">${esc(p.label)}</span>${state}</div>
      <div class="mt-3 flex items-end justify-between gap-3">
        <div class="min-w-0"><div class="num text-2xl">${procs ? num(count) : "–"}</div>
          <div class="text-xs text-ink-3">${esc(p.noun)} today${day?.bytes ? ` · ${bytes(day.bytes)}` : ""}</div></div>
        <div class="h-8 w-24 shrink-0">${sparkline(last14, p.color)}</div>
      </div>
      <div class="mt-3 truncate text-xs text-ink-2" title="${esc(l.detail)}">${esc(l.detail)}</div>
      ${l.running && l.pct !== null ? `<span class="bar active mt-1.5 block h-1.5"><i style="width:${l.pct}%"></i></span>` : `<span class="mt-1.5 block h-1.5"></span>`}`,
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
        ${icon("chevron", "size-4 shrink-0 -rotate-90 text-ink-3")}
      </a></li>`,
    )
    .join("")}</ul>`;
}

// ---- customise: show, hide and reorder cards (saved in this browser)

const CARDS: [string, string][] = [
  ["ov-running", "Running now"],
  ["ov-procs", "Uploads, Free storage, Copies, Date fixes"],
  ["ov-kpis", "Speed, today, streak, CPU, memory"],
  ["ov-share", "Where your photos are"],
  ["ov-network", "Network"],
  ["ov-glance", "At a glance"],
  ["ov-recent", "Recent activity"],
  ["ov-times", "Upload times and sizes"],
  ["ov-year", "Uploads per day"],
];
// Saved under a new name since the Sync page took Backup progress and Now: the new cards come first.
const LAYOUT_KEY = "ov-layout-2";

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
      <section class="card lg:col-span-12" data-card="ov-running">
        ${cardHead("Running now", "loader", '<span id="ov-running-count"></span>')}
        <div id="ov-running"></div>
      </section>

      <section class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:col-span-12 xl:grid-cols-4" data-card="ov-procs" id="ov-procs">
        ${PROCESS_KEYS.map((k) => `<a href="${PROCESS_HREF[k]}" class="card proc-card block p-4" data-proc="${k}"><div class="skeleton h-28"></div></a>`).join("")}
      </section>

      <section class="grid grid-cols-2 gap-4 md:grid-cols-3 lg:col-span-12 xl:grid-cols-6" data-card="ov-kpis">
        ${tile({ id: "k-up", label: "Upload", value: "–", ic: "up", chart: "", href: "#/network" })}
        ${tile({ id: "k-down", label: "Download", value: "–", ic: "down", chart: "", href: "#/network" })}
        ${tile({ id: "k-today", label: "Today", value: "–", ic: "calendar", chart: "", href: "#/activity/uploaded" })}
        ${tile({ id: "k-streak", label: "Streak", value: "–", ic: "flame", chart: "", href: "#/history" })}
        ${tile({ id: "k-cpu", label: "App CPU", value: "–", ic: "cpu", chart: "", href: "#/health" })}
        ${tile({ id: "k-mem", label: "App memory", value: "–", ic: "memory", chart: "", href: "#/health" })}
      </section>

      <section class="card lg:col-span-12" data-card="ov-share">
        ${cardHead("Where your photos are", "chart", `<div class="seg" id="ov-share-by"><button data-by="devices">Devices</button><button data-by="folders">Folders</button></div><div class="seg ml-2" id="ov-share-metric"><button data-m="files">Files</button><button data-m="bytes">Size</button></div>`)}
        <div class="card-b" id="ov-share-body" aria-live="polite"><div class="skeleton h-44"></div></div>
      </section>

      <section class="card flex min-w-0 flex-col lg:col-span-8" data-card="ov-network">
        ${cardHead("Network", "wifi", `<span class="hidden items-center gap-3 sm:flex"><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s1"></i>Upload</span><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s2"></i>Download</span></span><div class="seg ml-2" id="ov-range">${rangeButtons("live")}</div>`)}
        <div class="card-b flex-1"><div id="ov-net" data-chart class="h-[200px] w-full sm:h-[300px]"></div></div>
      </section>

      <section class="card lg:col-span-4" data-card="ov-glance">
        ${cardHead("At a glance", "sparkles")}
        <div class="px-4 py-1" id="ov-glance"></div>
      </section>

      <section class="card lg:col-span-6 xl:col-span-8" data-card="ov-recent">
        ${cardHead("Recent activity", "activity", '<a href="#/activity" class="hover:text-ink">See all ›</a>')}
        <div id="ov-recent" data-gallery></div>
      </section>

      <section class="card lg:col-span-12 xl:col-span-4" data-card="ov-times">
        ${cardHead("Upload times and sizes", "timer", '<a href="#/insights" class="hover:text-ink">Insights ›</a>')}
        <div class="card-b" id="ov-times"><div class="skeleton h-40"></div></div>
      </section>

      <section class="card lg:col-span-12" data-card="ov-year">
        ${cardHead("Uploads per day", "calendar", `<span id="ov-year-sub"></span>`)}
        <div class="card-b"><div class="scroll-thin overflow-x-auto pb-1" id="ov-year"><div class="skeleton h-28"></div></div><div class="mt-2 flex justify-end">${heatLegend()}</div></div>
      </section>
    </div>`;
    applyLayout();
    $$("[data-ov-custom]").forEach((b) => b.addEventListener("click", openCustomize));
    $("#ov-share-by").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (!b) return;
      if (shareBy === b.dataset.by) return;
      shareBy = b.dataset.by as "devices" | "folders";
      void loadShare(true);
    });
    $("#ov-share-metric").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (!b) return;
      if (shareMetric === b.dataset.m) return;
      shareMetric = b.dataset.m as "files" | "bytes";
      renderShare();
      animateChange(document.getElementById("ov-share-body"));
    });
    void loadShare();
    chart = new NetChart($("#ov-net"), matchMedia("(max-width: 640px)").matches ? 200 : 300);
    $("#ov-range").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-range]");
      if (!b) return;
      $$("button", $("#ov-range")).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      chart?.setRange(b.dataset.range as Range, last);
    });
    slowSynced = -1;
    void loadSlow();
    slowTimer = window.setInterval(loadSlow, 60_000);
  },
  update(s) {
    last = s;
    // New uploads: refresh the heat map and streak now instead of waiting for the minute timer.
    if (s.library.synced_files !== slowSynced && Date.now() - slowAt > 10_000) {
      if (slowSynced >= 0) void loadSlow();
      slowSynced = s.library.synced_files;
    }
    if (Date.now() - shareAt > 60_000) void loadShare(); // counts change slowly: once a minute
    else if (!kindsDrawn) renderShare();
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
      `${r.cpu_now.toFixed(1)}%`,
      `of 1 core · ${r.cores} cores here`,
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
      [
        "error",
        "Failed files",
        s.counts.failed
          ? `<a href="#/activity/failed">${badge(num(s.counts.failed), "bad")}</a>`
          : `<span class="text-ink-2">None</span>`,
      ],
      ["search", "Last scan", `<span class="text-ink-2">${ago(s.last_scan, s.now)}</span>`],
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
        "cloudUp",
        "This session",
        `<span class="text-ink-2 tabular-nums">${num(s.session.uploaded_files)} · ${bytes(s.session.uploaded_bytes)}</span>`,
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

    html($("#ov-running"), runningList(s));
    renderProcs(s);
    html($("#ov-running-count"), runningCount(s));

    html(
      $("#ov-recent"),
      s.log.length
        ? s.log
            .slice(0, 6)
            .map((l) => {
              const m = eventMeta[l.kind] ?? eventMeta.info!;
              const lead = l.name
                ? thumb(l.name)
                : `<span class="thumb grid place-items-center text-ink-3">${icon(m.icon, "size-4")}</span>`;
              return `<div class="row ${l.name ? "clickable" : ""}" ${l.name ? openAttrs(l.name, true) : ""}>${lead}<div class="min-w-0 flex-1"><div class="truncate text-sm">${esc(l.name ? fileName(l.name) : l.text)}</div><div class="text-xs text-ink-3">${clock(l.time)}</div></div>${badge(m.label, m.tone)}</div>`;
            })
            .join("")
        : `<div class="empty">${icon("inbox")}No activity yet</div>`,
    );
  },
  unmount() {
    clearInterval(slowTimer);
    shareRequest++;
    chart = null;
    document.getElementById("ov-sheet")?.remove();
  },
};

export default overview;
