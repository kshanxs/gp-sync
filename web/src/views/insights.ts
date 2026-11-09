import {
  get,
  tz,
  type History,
  type Insights,
  type ProcessKey,
  type Processes,
  type UploadRow,
} from "../lib/api";
import { PROCESS, PROCESS_KEYS } from "../lib/processes";
import { columns } from "../lib/charts";
import { $, esc, html } from "../lib/dom";
import { ago, bytes, duration, fileName, ms, num, pct, speed } from "../lib/format";
import { icon } from "../lib/icons";
import { badge, cardHead, openAttrs, thumb, tile } from "../lib/ui";
import type { View } from "./view";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function uploadRows(rows: UploadRow[], key: "bytes" | "ms"): string {
  return rows.length
    ? rows
        .map(
          (r) =>
            `<div class="row clickable" ${openAttrs(r.name)}>${thumb(r.name)}<div class="min-w-0 flex-1"><div class="truncate text-sm">${esc(fileName(r.name))}</div><div class="text-xs text-ink-3">${ago(r.time)} · ${key === "bytes" ? ms(r.ms) : bytes(r.bytes)}</div></div><span class="num text-sm">${key === "bytes" ? bytes(r.bytes) : ms(r.ms)}</span></div>`,
        )
        .join("")
    : `<div class="empty">${icon("timer")}Shows up after the next uploads</div>`;
}

function render(i: Insights): void {
  const nowHour = new Date().getHours();
  html(
    $("#in-tiles"),
    [
      tile({
        label: "Time uploading",
        value: duration(i.upload_ms / 1000),
        ic: "timer",
        sub: `${num(i.timed_files)} timed uploads`,
      }),
      tile({
        label: "Per photo",
        value: i.photo.avg_ms ? ms(i.photo.avg_ms) : "–",
        ic: "photo",
        sub: i.photo.max_ms ? `Slowest ${ms(i.photo.max_ms)}` : "Average time",
      }),
      tile({
        label: "Per video",
        value: i.video.avg_ms ? ms(i.video.avg_ms) : "–",
        ic: "video",
        sub: i.video.max_ms ? `Slowest ${ms(i.video.max_ms)}` : "Average time",
      }),
      tile({
        label: "Avg photo size",
        value: bytes(i.photo.avg_bytes),
        ic: "photo",
        sub: `${num(i.photo.files)} photos · ${bytes(i.photo.bytes)}`,
      }),
      tile({
        label: "Avg video size",
        value: bytes(i.video.avg_bytes),
        ic: "video",
        sub: `${num(i.video.files)} videos · ${bytes(i.video.bytes)}`,
      }),
      tile({
        label: "Upload speed",
        value: i.avg_speed ? speed(i.avg_speed) : "–",
        ic: "gauge",
        sub: "Average while uploading",
      }),
    ].join(""),
  );
  html(
    $("#in-hours"),
    columns(
      i.by_hour,
      i.by_hour.map((_, h) => String(h).padStart(2, "0")),
      {
        tip: (v, h) =>
          `${String(h).padStart(2, "0")}:00–${String((h + 1) % 24).padStart(2, "0")}:00\n${num(v)} uploads`,
        every: 3,
        height: 180,
        highlight: nowHour,
      },
    ),
  );
  html(
    $("#in-week"),
    columns(i.by_weekday, WEEKDAYS, {
      tip: (v, d) => `${WEEKDAYS[d]}\n${num(v)} uploads`,
      height: 180,
      highlight: (new Date().getDay() + 6) % 7,
    }),
  );
  const totalBytes = i.types.reduce((a, t) => a + t.bytes, 0);
  html(
    $("#in-types"),
    i.types.length
      ? i.types
          .slice(0, 10)
          .map(
            (t) =>
              `<div class="py-2"><div class="mb-1 flex items-center gap-2 text-sm"><span class="badge font-mono uppercase">${esc(t.ext)}</span><span class="text-ink-3">${num(t.files)} files</span><span class="num ml-auto">${bytes(t.bytes)}</span></div><div class="bar h-1.5"><i style="width:${pct(t.bytes, totalBytes)}%"></i></div></div>`,
          )
          .join("")
      : `<div class="empty">${icon("layers")}Shown after the first scan</div>`,
  );
  const split = pct(i.photo.bytes, i.photo.bytes + i.video.bytes);
  html(
    $("#in-split"),
    `<div class="flex h-4 gap-[2px] overflow-hidden rounded-full"><div class="bg-s1" style="width:${split}%"></div><div class="flex-1 bg-s2"></div></div>
     <div class="mt-3 grid grid-cols-2 gap-2">
       <div class="stat-tile"><div class="flex items-center gap-1.5 text-xs text-ink-3"><i class="size-2 rounded-full bg-s1"></i>Photos</div><div class="num text-lg">${bytes(i.photo.bytes)}</div><div class="text-xs text-ink-3">${split.toFixed(0)}% · ${num(i.photo.files)} files</div></div>
       <div class="stat-tile"><div class="flex items-center gap-1.5 text-xs text-ink-3"><i class="size-2 rounded-full bg-s2"></i>Videos</div><div class="num text-lg">${bytes(i.video.bytes)}</div><div class="text-xs text-ink-3">${(100 - split).toFixed(0)}% · ${num(i.video.files)} files</div></div>
     </div>`,
  );
  html($("#in-largest"), uploadRows(i.largest, "bytes"));
  html($("#in-slowest"), uploadRows(i.slowest, "ms"));
  html(
    $("#in-note"),
    i.timed_files < i.uploaded
      ? badge(`Timed: ${num(i.timed_files)} of ${num(i.uploaded)} uploads`, "info", "info")
      : badge("All uploads timed", "good", "ok"),
  );
}

function delta(now: number, before: number): string {
  if (!before && !now) return badge("No change", "");
  if (!before) return badge("New", "info", "trend");
  const d = ((now - before) / before) * 100;
  const up = d >= 0;
  return `<span class="badge ${Math.abs(d) < 1 ? "" : "info"}">${icon(up ? "up" : "down")}${Math.abs(d).toFixed(0)}%</span>`;
}

function renderWeeks(h: History): void {
  const by = new Map(h.data.map((d) => [d.day, d]));
  const today = new Date(h.today + "T00:00:00");
  const dow = (today.getDay() + 6) % 7; // Monday = 0
  const day = (offset: number) => {
    const d = new Date(today);
    d.setDate(d.getDate() - dow + offset);
    return { d, row: by.get(d.toLocaleDateString("sv")) };
  };
  const thisWeek = Array.from({ length: 7 }, (_, i) => day(i));
  const lastWeek = Array.from({ length: 7 }, (_, i) => day(i - 7));
  const files = (r: (typeof thisWeek)[number]) => (r.row?.photos ?? 0) + (r.row?.videos ?? 0);
  const sum = (w: typeof thisWeek, f: (r: (typeof thisWeek)[number]) => number) =>
    w.reduce((a, r) => a + f(r), 0);
  const dataB = (r: (typeof thisWeek)[number]) => (r.row?.photo_bytes ?? 0) + (r.row?.video_bytes ?? 0);
  const timeMs = (r: (typeof thisWeek)[number]) => (r.row?.photo_ms ?? 0) + (r.row?.video_ms ?? 0);
  const max = Math.max(1, ...thisWeek.map(files), ...lastWeek.map(files));
  const stats: [string, string, string, number, number][] = [
    [
      "Files",
      num(sum(thisWeek, files)),
      num(sum(lastWeek, files)),
      sum(thisWeek, files),
      sum(lastWeek, files),
    ],
    [
      "Data",
      bytes(sum(thisWeek, dataB)),
      bytes(sum(lastWeek, dataB)),
      sum(thisWeek, dataB),
      sum(lastWeek, dataB),
    ],
    [
      "Upload time",
      duration(sum(thisWeek, timeMs) / 1000),
      duration(sum(lastWeek, timeMs) / 1000),
      sum(thisWeek, timeMs),
      sum(lastWeek, timeMs),
    ],
  ];
  html(
    $("#in-weeks"),
    `<div class="grid grid-cols-1 gap-4 md:grid-cols-[1fr_16rem]">
      <div><div class="cols flex h-60 items-end gap-3">${thisWeek
        .map((r, i) => {
          const a = files(r);
          const b = files(lastWeek[i]!);
          const future = i > dow;
          return `<div class="col flex h-full flex-1 items-end gap-[3px]" data-tip="${esc(`${WEEKDAYS[i]}\nThis week: ${future ? "–" : num(a)}\nLast week: ${num(b)}`)}">
            <div class="flex h-full flex-1 items-end">${b ? `<div class="col-bar w-full rounded-t-[4px] bg-s2" style="height:${(b / max) * 100}%"></div>` : '<div class="h-[2px] w-full rounded bg-panel-2"></div>'}</div>
            <div class="flex h-full flex-1 items-end">${a ? `<div class="col-bar w-full rounded-t-[4px] bg-s1" style="height:${(a / max) * 100}%"></div>` : `<div class="h-[2px] w-full rounded ${future ? "" : "bg-panel-2"}"></div>`}</div></div>`;
        })
        .join("")}</div>
        <div class="mt-1.5 flex gap-3 text-[10px] text-ink-3">${WEEKDAYS.map((d, i) => `<div class="flex-1 text-center ${i === dow ? "font-semibold text-ink-2" : ""}">${d}</div>`).join("")}</div></div>
      <div class="space-y-2">${stats
        .map(
          ([l, a, b, na, nb]) =>
            `<div class="stat-tile"><div class="flex items-center justify-between text-xs text-ink-3">${l}${delta(na, nb)}</div><div class="num text-lg">${a}</div><div class="text-xs text-ink-3">Last week ${b}</div></div>`,
        )
        .join("")}</div></div>`,
  );
}

let procs: Processes | null = null;
let proc: ProcessKey = "uploads";

/** Items in the 7 days up to today, and the 7 before. */
function weekCounts(k: ProcessKey): [number, number] {
  const p = procs?.processes[k];
  if (!p || !procs) return [0, 0];
  const today = new Date(`${procs.today}T00:00:00`).getTime();
  let now = 0;
  let before = 0;
  for (const d of p.days) {
    const age = Math.round((today - new Date(`${d.day}T00:00:00`).getTime()) / 86400000);
    if (age >= 0 && age < 7) now += d.count;
    else if (age >= 7 && age < 14) before += d.count;
  }
  return [now, before];
}

/** Every process side by side, and the chosen one's busiest hours and weekdays. */
function renderProcs(): void {
  if (!procs) {
    html($("#in-procs"), `<div class="empty">${icon("chart")}Couldn't load</div>`);
    return;
  }
  const p = procs.processes[proc];
  const hourNow = new Date().getHours();
  const side = PROCESS_KEYS.map((k) => {
    const [a, b] = weekCounts(k);
    const change = b ? Math.round(((a - b) / b) * 100) : a ? 100 : 0;
    return `<button class="flex min-w-0 items-center gap-3 rounded-xl border p-3 text-left transition ${k === proc ? "border-accent bg-accent/10" : "border-line hover:bg-panel-2"}" data-proc="${k}" aria-pressed="${k === proc}">
      <span class="grid size-9 shrink-0 place-items-center rounded-lg text-white" style="background:${PROCESS[k].color}">${icon(PROCESS[k].icon, "size-4")}</span>
      <span class="min-w-0 flex-1"><span class="block truncate text-xs text-ink-3">${PROCESS[k].label}</span><span class="num block text-lg">${num(a)}</span></span>
      <span class="shrink-0 text-xs ${change > 0 ? "text-good" : change < 0 ? "text-bad" : "text-ink-3"}">${b ? `${change > 0 ? "+" : ""}${change}%` : a ? "New" : ""}</span>
    </button>`;
  }).join("");
  html(
    $("#in-procs"),
    `<div class="grid grid-cols-2 gap-3 p-4 lg:grid-cols-4">${side}</div>
    <div class="grid gap-4 border-t border-line p-4 lg:grid-cols-12">
      <div class="lg:col-span-8"><div class="label mb-2">${PROCESS[proc].label} by hour of day</div>${
        p.count
          ? columns(
              p.hours,
              p.hours.map((_, h) => String(h)),
              {
                tip: (v, h) => `${h}:00–${h + 1}:00 · ${num(v)} ${PROCESS[proc].noun}`,
                every: 3,
                height: 120,
                highlight: hourNow,
                color: PROCESS[proc].color,
              },
            )
          : `<div class="empty">${icon(PROCESS[proc].icon)}Nothing ${PROCESS[proc].noun} in the last year</div>`
      }</div>
      <div class="lg:col-span-4"><div class="label mb-2">By weekday</div>${
        p.count
          ? columns(p.weekdays, WEEKDAYS, {
              tip: (v, i) => `${WEEKDAYS[i]} · ${num(v)} ${PROCESS[proc].noun}`,
              height: 120,
              color: PROCESS[proc].color,
            })
          : ""
      }</div>
    </div>
    <p class="border-t border-line px-4 py-2.5 text-xs text-ink-3">Last 12 months, in your time zone. ${num(p.count)} ${PROCESS[proc].noun}${p.bytes ? ` · ${bytes(p.bytes)}` : ""}. Each process counts its own items.</p>`,
  );
}

const insights: View = {
  subtitle: "Patterns in every process",
  mount(el) {
    el.innerHTML = `
    <section class="card mb-4 min-w-0 overflow-hidden">${cardHead("Every process", "layers", "This week vs last · pick one for its hours")}<div id="in-procs"><div class="skeleton m-4 h-44"></div></div></section>
    <h2 class="mt-6 mb-3 px-1 text-sm font-semibold text-ink-2">Uploads in detail</h2>
    <div class="mb-3 flex justify-end" id="in-note"></div>
    <div class="grid grid-cols-2 gap-4 md:grid-cols-3 2xl:grid-cols-6" id="in-tiles">${'<div class="card skeleton h-28"></div>'.repeat(6)}</div>
    <div class="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
      <section class="card lg:col-span-12">${cardHead("This week vs last week", "trend", '<span class="flex items-center gap-3"><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s1"></i>This week</span><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s2"></i>Last week</span></span>')}<div class="card-b" id="in-weeks"><div class="skeleton h-44"></div></div></section>
      <section class="card lg:col-span-8">${cardHead("Uploads by hour of day", "clock", "Your time zone · current hour in aqua")}<div class="card-b" id="in-hours"><div class="skeleton h-44"></div></div></section>
      <section class="card lg:col-span-4">${cardHead("By weekday", "calendar")}<div class="card-b" id="in-week"><div class="skeleton h-44"></div></div></section>
      <section class="card lg:col-span-4">${cardHead("Photos vs videos", "layers", "Library size")}<div class="card-b" id="in-split"></div></section>
      <section class="card lg:col-span-8">${cardHead("File types", "package", "By space used")}<div class="card-b grid grid-cols-1 gap-x-6 md:grid-cols-2 [&>*]:border-b [&>*]:border-line" id="in-types"></div></section>
      <section class="card lg:col-span-6">${cardHead("Largest uploads", "disk")}<div id="in-largest" data-gallery></div></section>
      <section class="card lg:col-span-6">${cardHead("Slowest uploads", "hourglass")}<div id="in-slowest" data-gallery></div></section>
    </div>`;
    el.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("[data-proc]");
      if (!b) return;
      proc = b.dataset.proc as ProcessKey;
      renderProcs();
    });
    void get<Processes>(`processes?days=371&tz=${tz}`)
      .then((p) => {
        procs = p;
      })
      .catch(() => undefined)
      .finally(renderProcs);
    void get<Insights>(`insights?tz=${tz}`).then(render);
    void get<History>(`history?days=15&tz=${tz}`).then(renderWeeks);
  },
};

export default insights;
