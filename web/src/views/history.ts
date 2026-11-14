import { get, tz, type Day, type History, type ProcessKey, type Processes, type Totals } from "../lib/api";
import { countsByDay, PROCESS, PROCESS_KEYS, streaks } from "../lib/processes";
import { heatLegend, heatmap } from "../lib/charts";
import { $, $$, esc, html } from "../lib/dom";
import { bytes, date, duration, num } from "../lib/format";
import { icon } from "../lib/icons";
import { cardHead, tile } from "../lib/ui";
import type { View } from "./view";

let hist: History | null = null;
let procs: Processes | null = null;
/** Which process the page shows: one of them, or everything together. */
let proc: ProcessKey | "all" = "uploads";
let metric: "files" | "data" = "files";
let span = 30;
let timer = 0;
let synced = -1; // uploaded count the page was last loaded for
let loadedAt = 0;

async function load(): Promise<void> {
  loadedAt = Date.now();
  const [h, t, p] = await Promise.all([
    get<History>(`history?days=371&tz=${tz}`),
    get<Totals>("totals"),
    get<Processes>(`processes?days=371&tz=${tz}`).catch(() => null),
  ]);
  if (!document.getElementById("h-year")) return; // left the page meanwhile
  hist = h;
  procs = p;
  totals = t;
  renderAll();
}

let totals: Totals | null = null;

/** The year heat map; phones scroll it, starting at today (the newest weeks are on the right). */
function showYear(svg: string): void {
  const year = $("#h-year");
  const first = !year.dataset.html;
  html(year, svg);
  if (first) year.scrollLeft = year.scrollWidth;
}

function renderAll(): void {
  const generic = proc !== "uploads" && !!procs;
  // The photo and video legend belongs to uploads; other processes draw one colour (or one per process).
  html($("#h-legend"), generic ? legend() : uploadLegend);
  html($("#h-streak"), streakText());
  if (generic) {
    renderGenericTotals();
    renderGenericYear();
    renderGenericDaily();
    renderGenericMonths();
  } else {
    if (totals) renderTotals(totals);
    renderYear();
    renderDaily();
    renderMonths();
  }
}

const uploadLegend = `<span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s1"></i>Photos</span><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s2"></i>Videos</span>`;

/** The processes shown now: one, or all four for Everything. */
function shown(): ProcessKey[] {
  return proc === "all" ? PROCESS_KEYS : [proc];
}

function legend(): string {
  return proc === "all"
    ? PROCESS_KEYS.map(
        (k) =>
          `<span class="flex items-center gap-1.5"><i class="size-2 rounded-full" style="background:${PROCESS[k].color}"></i>${PROCESS[k].label}</span>`,
      ).join("")
    : "";
}

function streakText(): string {
  if (!procs) return "";
  const active = new Set(
    [...countsByDay(shown().map((k) => procs!.processes[k]))].filter(([, n]) => n > 0).map(([d]) => d),
  );
  const s = streaks(active, procs.today);
  return `Streak <b class="text-ink">${num(s.current)} day${s.current === 1 ? "" : "s"}</b> · longest ${num(s.longest)}`;
}

/** Items and bytes per day for what is shown now (Everything adds the processes together). */
function genericDays(
  n: number,
): { key: string; date: Date; count: number; bytes: number; parts: number[] }[] {
  const by = shown().map((k) => new Map((procs?.processes[k].days ?? []).map((d) => [d.day, d])));
  const end = new Date((procs?.today ?? dayKey(new Date())) + "T00:00:00");
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(end);
    d.setDate(d.getDate() - (n - 1 - i));
    const key = dayKey(d);
    const rows = by.map((m) => m.get(key));
    const parts = rows.map((r) => (metric === "files" ? (r?.count ?? 0) : (r?.bytes ?? 0)));
    return {
      key,
      date: d,
      count: rows.reduce((a, r) => a + (r?.count ?? 0), 0),
      bytes: rows.reduce((a, r) => a + (r?.bytes ?? 0), 0),
      parts,
    };
  });
}

function genericLabel(): string {
  return proc === "all" ? "items" : `items ${PROCESS[proc].noun}`;
}

function renderGenericTotals(): void {
  const list = shown().map((k) => procs!.processes[k]);
  const count = list.reduce((a, p) => a + p.count, 0);
  const size = list.reduce((a, p) => a + p.bytes, 0);
  const year = genericDays(365);
  const active = year.filter((d) => d.count > 0);
  const best = active.reduce<(typeof year)[number] | null>((a, d) => (!a || d.count > a.count ? d : a), null);
  const st = streaks(new Set(active.map((d) => d.key)), procs!.today);
  const name = proc === "all" ? "Everything" : PROCESS[proc as ProcessKey].label;
  const tiles = [
    tile({
      label: name,
      value: num(count),
      ic: proc === "all" ? "layers" : PROCESS[proc as ProcessKey].icon,
      sub: `${genericLabel()} in the last year`,
    }),
    tile({
      label: "Data",
      value: bytes(size),
      ic: "database",
      sub: proc === "fixed" ? "Date fixes move no data" : "Moved in the last year",
    }),
    tile({
      label: "Active days",
      value: num(active.length),
      ic: "calendar",
      sub: "Days with something done",
    }),
    tile({
      label: "Current streak",
      value: `${num(st.current)} d`,
      ic: "flame",
      sub: st.current ? "Days in a row, up to today" : "Nothing today or yesterday",
    }),
    tile({ label: "Longest streak", value: `${num(st.longest)} d`, ic: "trend", sub: "Days in a row" }),
    tile({
      label: "Best day",
      value: best ? num(best.count) : "–",
      ic: "sparkles",
      sub: best ? best.date.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "",
    }),
  ];
  html($("#h-tiles"), tiles.join(""));
}

function renderGenericYear(): void {
  const days = genericDays(365).map((d) => ({
    day: d.key,
    value: metric === "files" ? d.count : d.bytes,
    tip: `${d.date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" })}\n${d.count ? `${num(d.count)} ${genericLabel()}${d.bytes ? ` · ${bytes(d.bytes)}` : ""}` : "Nothing"}`,
  }));
  showYear(heatmap(days, { cell: 14, gap: 3 }));
}

function renderGenericDaily(): void {
  const rows = genericDays(span);
  const keys = shown();
  const max = Math.max(1, ...rows.map((r) => r.parts.reduce((a, b) => a + b, 0)));
  const fmt = (v: number) => (metric === "files" ? num(v) : bytes(v));
  const every = span <= 31 ? 5 : 15;
  html(
    $("#h-daily"),
    `<div class="cols flex h-56 items-end gap-[3px]">${rows
      .map((r) => {
        const total = r.parts.reduce((a, b) => a + b, 0);
        const tip = `${r.date.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}\n${keys.map((k, i) => `${PROCESS[k].label}: ${fmt(r.parts[i] ?? 0)}`).join("\n")}`;
        return `<div class="col flex h-full flex-1 flex-col-reverse gap-[2px]" data-tip="${esc(tip)}">
          ${total ? keys.map((k, i) => (r.parts[i] ? `<div class="col-bar" style="height:${((r.parts[i] ?? 0) / max) * 100}%;background:${PROCESS[k].color}"></div>` : "")).join("") : `<div class="h-[2px] rounded bg-panel-2"></div>`}</div>`;
      })
      .join("")}</div>
    <div class="mt-1.5 flex gap-[2px] text-[11px] text-ink-3">${rows
      .map(
        (r, i) =>
          `<div class="flex-1 text-center whitespace-nowrap">${(rows.length - 1 - i) % every === 0 ? esc(date(r.date.getTime() / 1000)) : ""}</div>`,
      )
      .join("")}</div>`,
  );
}

function renderGenericMonths(): void {
  const months = new Map<string, { n: number[]; b: number }>();
  shown().forEach((k, i) => {
    for (const d of procs!.processes[k].days) {
      const key = d.day.slice(0, 7);
      const m = months.get(key) ?? { n: shown().map(() => 0), b: 0 };
      m.n[i] = (m.n[i] ?? 0) + d.count;
      m.b += d.bytes;
      months.set(key, m);
    }
  });
  const rows = [...months.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  const keys = shown();
  html(
    $("#h-months"),
    rows.length
      ? `<table class="w-full text-sm"><thead class="text-left text-xs text-ink-3"><tr class="border-b border-line">
          <th class="px-4 py-2 font-medium">Month</th>${keys.map((k) => `<th class="px-4 py-2 text-right font-medium">${PROCESS[k].label}</th>`).join("")}<th class="px-4 py-2 text-right font-medium">Data</th></tr></thead>
          <tbody>${rows
            .map(
              ([k, m]) =>
                `<tr class="border-b border-line last:border-0"><td class="px-4 py-2">${new Date(k + "-01T00:00:00").toLocaleDateString([], { month: "long", year: "numeric" })}</td>${m.n.map((v) => `<td class="px-4 py-2 text-right tabular-nums">${num(v)}</td>`).join("")}<td class="px-4 py-2 text-right tabular-nums">${bytes(m.b)}</td></tr>`,
            )
            .join("")}</tbody></table>`
      : `<div class="empty">${icon("calendar")}Nothing yet</div>`,
  );
}

function dayKey(d: Date): string {
  return d.toLocaleDateString("sv");
}

function lastDays(n: number): { key: string; date: Date; row: Day | undefined }[] {
  const by = new Map(hist?.data.map((d) => [d.day, d]));
  const end = new Date((hist?.today ?? dayKey(new Date())) + "T00:00:00");
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(end);
    d.setDate(d.getDate() - (n - 1 - i));
    return { key: dayKey(d), date: d, row: by.get(dayKey(d)) };
  });
}

const val = (r: Day | undefined) =>
  metric === "files" ? (r?.photos ?? 0) + (r?.videos ?? 0) : (r?.photo_bytes ?? 0) + (r?.video_bytes ?? 0);

function renderYear(): void {
  const days = lastDays(365).map(({ key, date: d, row }) => ({
    day: key,
    value: val(row),
    tip: `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" })}\n${row && row.photos + row.videos ? `${row.photos} photos, ${row.videos} videos · ${bytes(row.photo_bytes + row.video_bytes)}` : "Nothing uploaded"}`,
  }));
  showYear(heatmap(days, { cell: 14, gap: 3 }));
}

function renderDaily(): void {
  const rows = lastDays(span);
  const max = Math.max(1, ...rows.map((r) => val(r.row)));
  const fmt = (v: number) => (metric === "files" ? num(v) : bytes(v));
  const every = span <= 31 ? 5 : 15;
  html(
    $("#h-daily"),
    `<div class="cols flex h-56 items-end gap-[3px]">${rows
      .map(({ date: d, row }) => {
        const p = metric === "files" ? (row?.photos ?? 0) : (row?.photo_bytes ?? 0);
        const v = metric === "files" ? (row?.videos ?? 0) : (row?.video_bytes ?? 0);
        const tip = `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}\nPhotos: ${fmt(p)}\nVideos: ${fmt(v)}`;
        return `<div class="col flex h-full flex-1 flex-col justify-end gap-[2px]" data-tip="${esc(tip)}">
          ${v ? `<div class="col-bar rounded-t-[4px] bg-s2" style="height:${(v / max) * 100}%"></div>` : ""}
          ${p ? `<div class="col-bar ${v ? "" : "rounded-t-[4px]"} bg-s1" style="height:${(p / max) * 100}%"></div>` : ""}
          ${p || v ? "" : `<div class="h-[2px] rounded bg-panel-2"></div>`}</div>`;
      })
      .join("")}</div>
    <div class="mt-1.5 flex gap-[2px] text-[11px] text-ink-3">${rows
      .map(
        (r, i) =>
          `<div class="flex-1 text-center whitespace-nowrap">${(rows.length - 1 - i) % every === 0 ? esc(date(r.date.getTime() / 1000)) : ""}</div>`,
      )
      .join("")}</div>`,
  );
}

function renderMonths(): void {
  const months = new Map<string, { p: number; v: number; b: number; e: number; s: number; r: number }>();
  for (const d of hist?.data ?? []) {
    const k = d.day.slice(0, 7);
    const m = months.get(k) ?? { p: 0, v: 0, b: 0, e: 0, s: 0, r: 0 };
    m.p += d.photos;
    m.v += d.videos;
    m.b += d.photo_bytes + d.video_bytes;
    m.e += d.existing;
    m.s += d.net_sent;
    m.r += d.net_received;
    months.set(k, m);
  }
  const rows = [...months.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  html(
    $("#h-months"),
    rows.length
      ? `<table class="w-full text-sm"><thead class="text-left text-xs text-ink-3"><tr class="border-b border-line">
          <th class="px-4 py-2 font-medium">Month</th><th class="px-4 py-2 text-right font-medium">Photos</th><th class="px-4 py-2 text-right font-medium">Videos</th><th class="hidden px-4 py-2 text-right font-medium sm:table-cell">Already there</th><th class="px-4 py-2 text-right font-medium">Uploaded</th><th class="hidden px-4 py-2 text-right font-medium md:table-cell">Network sent</th><th class="hidden px-4 py-2 text-right font-medium md:table-cell">Received</th></tr></thead>
          <tbody>${rows
            .map(
              ([k, m]) =>
                `<tr class="border-b border-line last:border-0"><td class="px-4 py-2">${new Date(k + "-01T00:00:00").toLocaleDateString([], { month: "long", year: "numeric" })}</td><td class="px-4 py-2 text-right tabular-nums">${num(m.p)}</td><td class="px-4 py-2 text-right tabular-nums">${num(m.v)}</td><td class="hidden px-4 py-2 text-right tabular-nums sm:table-cell">${num(m.e)}</td><td class="px-4 py-2 text-right tabular-nums">${bytes(m.b)}</td><td class="hidden px-4 py-2 text-right tabular-nums md:table-cell">${bytes(m.s)}</td><td class="hidden px-4 py-2 text-right tabular-nums md:table-cell">${bytes(m.r)}</td></tr>`,
            )
            .join("")}</tbody></table>`
      : `<div class="empty">${icon("calendar")}No history yet</div>`,
  );
}

function renderTotals(t: Totals): void {
  const best = (hist?.data ?? []).reduce<Day | null>(
    (a, d) => (!a || d.photos + d.videos > a.photos + a.videos ? d : a),
    null,
  );
  const tiles = [
    tile({ label: "Photos", value: num(t.photos), ic: "photo", sub: bytes(t.photo_bytes) }),
    tile({ label: "Videos", value: num(t.videos), ic: "video", sub: bytes(t.video_bytes) }),
    // "Already in Google 0" says nothing: the total uploaded takes its place then.
    t.existing
      ? tile({
          label: "Already in Google",
          value: num(t.existing),
          ic: "verified",
          sub: "Found, not uploaded again",
        })
      : tile({
          label: "Uploaded in total",
          value: num(t.photos + t.videos),
          ic: "cloudUp",
          sub: bytes(t.photo_bytes + t.video_bytes),
        }),
    tile({
      label: "Upload time",
      value: duration((t.photo_ms + t.video_ms) / 1000),
      ic: "timer",
      sub: "Time spent sending files",
    }),
    tile({
      label: "Best day",
      value: best ? num(best.photos + best.videos) : "–",
      ic: "trend",
      sub: best
        ? new Date(best.day + "T00:00:00").toLocaleDateString([], {
            day: "numeric",
            month: "short",
            year: "numeric",
          })
        : "",
    }),
    tile({
      label: "Since",
      value: t.since ? date(t.since, true) : "–",
      ic: "history",
      sub: `${bytes(t.net_sent)} sent in total`,
    }),
  ];
  html($("#h-tiles"), tiles.join(""));
}

const historyView: View = {
  subtitle: "Every process, day by day",
  mount(el) {
    el.innerHTML = `
    <section class="toolbar"><div class="seg" id="h-proc" aria-label="Which process">${[
      ...PROCESS_KEYS.map((k) => [k, PROCESS[k].label]),
      ["all", "Everything"],
    ]
      .map(([k, l]) => `<button data-p="${k}" aria-pressed="${k === proc}">${l}</button>`)
      .join("")}</div></section>
    <div class="grid grid-cols-2 gap-4 md:grid-cols-3 2xl:grid-cols-6" id="h-tiles">${'<div class="card skeleton h-28"></div>'.repeat(6)}</div>
    <div class="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-12">
      <section class="card xl:col-span-12">
        ${cardHead("Last 12 months", "calendar", `<span class="hidden text-xs text-ink-3 sm:inline" id="h-streak"></span><div class="seg ml-2" id="h-metric"><button data-m="files" aria-pressed="true">Files</button><button data-m="data" aria-pressed="false">Data</button></div>`)}
        <div class="card-b"><div class="scroll-thin fade-x overflow-x-auto pb-1" id="h-year"><div class="skeleton h-32"></div></div><div class="mt-2 flex justify-end">${heatLegend()}</div></div>
      </section>
      <section class="card xl:col-span-12">
        ${cardHead("Per day", "chart", `<span class="hidden items-center gap-3 sm:flex" id="h-legend"></span><div class="seg ml-2" id="h-span"><button data-d="30" aria-pressed="true">30 d</button><button data-d="90" aria-pressed="false">90 d</button></div>`)}
        <div class="card-b" id="h-daily"><div class="skeleton h-56"></div></div>
      </section>
      <section class="card overflow-x-auto xl:col-span-12">${cardHead("Per month", "layers")}<div id="h-months"></div></section>
    </div>`;
    const seg = (id: string, fn: (b: HTMLButtonElement) => void) =>
      $(id).addEventListener("click", (e) => {
        const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
        if (!b) return;
        $$("button", $(id)).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        fn(b);
      });
    seg("#h-proc", (b) => {
      proc = b.dataset.p as typeof proc;
      renderAll();
    });
    seg("#h-metric", (b) => {
      metric = b.dataset.m as "files" | "data";
      renderAll();
    });
    seg("#h-span", (b) => {
      span = Number(b.dataset.d);
      renderAll();
    });
    synced = -1;
    void load();
    timer = window.setInterval(load, 60_000); // also rolls over to a new day
  },
  update(s) {
    // New uploads: show them now instead of waiting for the minute timer.
    if (s.library.synced_files !== synced && Date.now() - loadedAt > 10_000) {
      if (synced >= 0) void load();
      synced = s.library.synced_files;
    }
  },
  unmount() {
    clearInterval(timer);
  },
};

export default historyView;
