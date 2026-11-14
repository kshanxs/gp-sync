// Normal sync on its own page: what uploads now, how much of the library is backed up, each
// folder's progress, and the upload history (today, streak, where the photos are, times, the year).
// The Overview shows only what runs now, for every process, and links here.

import { workBox } from "../lib/work-panel";
import { openUploadOptions, paceButton } from "../lib/pace-sheet";
import { get, post, tz, type FolderReport, type History, type Insights, type Status } from "../lib/api";
import { donut, heatLegend, heatmap, ring, setRing, sparkline, type Slice } from "../lib/charts";
import { $, $$, animateChange, esc, html } from "../lib/dom";
import { ago, bytes, duration, fileName, ms, num, pct, speed } from "../lib/format";
import { icon } from "../lib/icons";
import { host, setPageStatus, setPauseControl, setRefreshControl } from "../lib/store";
import { badge, cardHead, etaText, openAttrs, phaseInfo, spaceTone, thumb, tile } from "../lib/ui";
import {
  accountLanes,
  copiesLeft,
  copiesText,
  lanePhaseText,
  uploadSummary,
  uploadTitle,
  type AccountLane,
} from "../lib/lanes";
import type { View } from "./view";

let hist: History | null = null;
let ins: Insights | null = null;
let slowTimer = 0;
let slowSynced = -1; // uploaded count the heat map and streak were last loaded for
let slowAt = 0;
let last: Status | null = null;

// ---- where the photos are: pie charts per subfolder (or top folder) and photos vs videos
// "devices" is the API's name for the folders one level inside each backed-up folder.
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
  if (interactive) animateChange(document.getElementById("sy-share-body"));
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
  const el = document.getElementById("sy-share-body");
  if (!el) return;
  $$("button", $("#sy-share-by")).forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.by === shareBy)),
  );
  $$("button", $("#sy-share-metric")).forEach((b) =>
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
    `#/folders/f/${(g.folders[0] ?? g.name).split("/").map(encodeURIComponent).join("/")}`;
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
      href: "#/folders",
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
    // Side by side only on very wide screens: otherwise the folder names get cut to a letter.
    `<div class="grid gap-6 2xl:grid-cols-2">
      <div class="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
        ${donut(slices, center(total, shareBy === "devices" ? "in subfolders" : "in folders"))}
        <div class="w-full min-w-0 flex-1"><div class="label mb-1">${shareBy === "devices" ? "By subfolder" : "By folder"}</div>
          ${
            groups.length
              ? `<ul>${shown.map((g, i) => legendRow(SLICE_COLORS[i]!, g.name, m(g), total, backed(g), link(g))).join("")}${rest.length ? legendRow(OTHER, `Other (${rest.length})`, restValue, total, "", "#/folders") : ""}</ul>`
              : `<p class="py-2 text-sm text-ink-3">${share ? (shareBy === "devices" ? "No subfolders found. Try Folders." : "No folders found.") : shareError ? "Couldn't load this view." : "Loading…"}</p>`
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
  [hist, ins] = await Promise.all([
    get<History>(`history?days=371&tz=${tz}`),
    get<Insights>(`insights?tz=${tz}`),
  ]);
  renderSlow();
}

function renderSlow(): void {
  if (!hist || !document.getElementById("sy-year")) return;
  const today = hist.data.find((d) => d.day === hist?.today);
  const last14 = (() => {
    const m = new Map(hist.data.map((d) => [d.day, d.photos + d.videos]));
    return Array.from({ length: 14 }, (_, i) => {
      const d = new Date(hist!.today + "T00:00:00");
      d.setDate(d.getDate() - 13 + i);
      return m.get(d.toLocaleDateString("sv")) ?? 0;
    });
  })();
  const t = $("#sy-today");
  html($("[data-v]", t), num((today?.photos ?? 0) + (today?.videos ?? 0)));
  html(
    $("[data-s]", t),
    `${num(today?.photos ?? 0)} photos · ${num(today?.videos ?? 0)} videos · ${bytes((today?.photo_bytes ?? 0) + (today?.video_bytes ?? 0))}`,
  );
  html($("[data-c]", t), sparkline(last14, "var(--s1)"));
  const st = streak(hist);
  const k = $("#sy-streak");
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
  html($("#sy-year-sub"), `${num(yearTotal)} files in the last year`);
  const year = $("#sy-year");
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
      $("#sy-times"),
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

const STEPS: Record<string, string> = {
  hashing: "Fingerprinting",
  checking: "Checking Google",
  uploading: "Uploading",
  finalizing: "Finishing",
  skipped: "Already there",
  complete: "Done",
};

function stepBadge(c: NonNullable<Status["current"]>): string {
  const up = c.step === "uploading";
  const label =
    up && c.bytes_total
      ? `Uploading ${pct(c.bytes_done, c.bytes_total).toFixed(0)}%`
      : (STEPS[c.step] ?? c.step);
  return `<span class="badge info">${icon(up ? "cloudUp" : "loader", up ? "" : "spin")}${esc(label)}</span>`;
}

/** What the Now card says when no file is being sent. */
function nowTitle(s: Status): string {
  switch (s.phase) {
    case "paused":
      return "Uploads are paused";
    case "scanning":
      return "Scanning your folders";
    case "scheduled":
      return "Waiting for upload hours";
    case "setup":
      return "No Google account yet";
    case "error":
      return "Something went wrong";
    case "starting":
      return "Starting up";
    default:
      return s.library.files > s.library.synced_files
        ? "Next files go at the next scan"
        : s.copies_pending
          ? "Main library is in; copies still going"
          : "All caught up";
  }
}

/** Only what the control strip can't do: connecting an account. Pause and scan are in the strip. */
function nowAction(s: Status): string {
  if (s.phase === "setup")
    return `<a class="btn sm primary shrink-0" href="#/settings/accounts">${icon("key")}Connect</a>`;
  return "";
}

/** The control strip: uploads (status, Pause/Resume) and scanning the folders. */
function controls(s: Status): void {
  const left = s.library.files - s.library.synced_files;
  setPageStatus(
    s.phase === "uploading"
      ? {
          tone: "run",
          title: uploadTitle(s),
          detail: s.total ? `${num(s.done)} of ${num(s.total)} done this round` : "",
        }
      : s.phase === "scanning"
        ? { tone: "run", title: "Scanning your folders" }
        : s.paused
          ? {
              tone: "warn",
              title: "Folder uploads are paused",
              detail: left ? `${num(left)} files waiting` : "",
            }
          : s.phase === "scheduled"
            ? { tone: "idle", title: "Waiting for upload hours", detail: phaseInfo(s).detail }
            : s.phase === "setup"
              ? { tone: "idle", title: "No Google account yet", detail: "Connect one in Settings" }
              : s.phase === "error"
                ? { tone: "warn", title: "Something went wrong", detail: s.error }
                : left
                  ? { tone: "idle", title: `${num(left)} files waiting`, detail: "They go at the next scan" }
                  : s.copies_pending
                    ? {
                        tone: "idle",
                        title: "Main library is in",
                        detail: `Still copying to ${copiesText(s) || "your other accounts"}`,
                      }
                    : {
                        tone: "good",
                        title: "All backed up",
                        detail: `Next scan ${s.interval ? `within ${num(s.interval)} min` : "soon"}`,
                      },
  );
  setPauseControl({
    paused: !!s.paused,
    working: s.phase === "uploading",
    label: s.paused ? "Resume folder uploads" : "Pause folder uploads",
    toggle: async () => {
      await post("pause");
      return s.paused ? "Folder uploads resumed" : "Folder uploads paused: files under way finish first";
    },
  });
  setRefreshControl({
    label: "Scan your folders for new photos and videos",
    busy: s.phase === "scanning" || s.phase === "starting",
    run: async () => {
      await post("sync");
      return s.paused ? "Scanning (uploads stay paused)" : "Scanning for new photos and videos";
    },
  });
}

/** Files in flight, in the shared work panel: how many on each step, then a list that scrolls. */
function flight(list: NonNullable<Status["uploading"]>): string {
  return workBox(
    list.map((u) => ({
      key: `${u.account}:${u.name}`,
      name: fileName(u.name),
      step: u.step,
      done: u.bytes_done,
      total: u.bytes_total,
      label: STEPS[u.step],
    })),
  );
}

/** One account's folder backup: what it is doing, its files in flight and how far it is. */
function laneCard(l: AccountLane, s: Status): string {
  const tone =
    l.phase === "uploading" ? "info" : l.phase === "stopped" ? "warn" : l.phase === "caught_up" ? "good" : "";
  // While a round is running, the bar is that round. The library total barely moves on a big folder.
  const round = l.phase === "uploading" && l.roundTotal > 0;
  const p = round ? pct(l.roundDone, l.roundTotal) : pct(l.done, l.total);
  const what = [
    l.backup.length ? `Backup: ${l.backup.join(", ")}` : "",
    l.also.length ? `Also in: ${l.also.join(", ")}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const pace = paceButton("upload", s.upload_workers ?? 0, s.pace?.[l.account]?.upload);
  return `<div class="min-w-0 border-b border-line md:odd:border-r" data-lane="${esc(l.account)}">
    <div class="p-4">
      <div class="flex items-center gap-2"><span class="min-w-0 flex-1 truncate text-sm font-medium" title="${esc(l.account)}">${esc(l.account)}</span>${badge(lanePhaseText(l, s), tone)}</div>
      <div class="mt-1 truncate text-xs text-ink-3" title="${esc(what)}">${esc(what)}</div>
      <span class="bar mt-2.5 block h-1.5" title="${round ? "This round" : "In this account"}"><i style="width:${p}%"></i></span>
      <div class="mt-1 flex items-center justify-between gap-2 text-xs text-ink-3 tabular-nums"><span>${num(l.done)} of ${num(l.total)} in this account${l.roundTotal ? ` · ${num(l.roundDone)} of ${num(l.roundTotal)} this round` : ""}</span>${pace}</div>
    </div>
    ${l.uploading.length ? `<div class="border-t border-line">${flight(l.uploading)}</div>` : ""}
  </div>`;
}

function lanesCard(s: Status): string {
  const lanes = accountLanes(s);
  if (!lanes.length) return "";
  return `${cardHead(
    "Accounts",
    "users",
    `<span class="text-xs text-ink-3">${lanes.length > 1 ? "Each account uploads side by side; the speed limit is shared" : "Folder backup"}</span>`,
  )}<div class="grid md:grid-cols-2">${lanes.map((l) => laneCard(l, s)).join("")}</div>`;
}

function upNext(s: Status, current: string): string {
  const next = (s.pending ?? []).filter((n) => n !== current).slice(0, 6);
  if (!next.length) return "";
  return `<div class="mt-4 label">Up next</div><div class="-mx-4 mt-1" data-gallery>${next
    .map(
      (n) =>
        `<div class="row clickable py-2" ${openAttrs(n)}>${thumb(n, "thumb size-8")}<div class="min-w-0 flex-1 truncate text-sm">${esc(fileName(n))}</div></div>`,
    )
    .join("")}</div>`;
}

function nowCard(s: Status): string {
  const left = s.library.files - s.library.synced_files;
  const summary = uploadSummary(s);
  // One featured file jumps between accounts. With several uploading, the account cards are the list.
  if (summary.accounts > 1)
    return `<div class="flex items-center gap-3"><span class="grid size-12 shrink-0 place-items-center rounded-xl bg-panel-2 text-accent">${icon("cloudUp", "size-6")}</span>
      <div class="min-w-0 flex-1"><div class="font-medium">${esc(uploadTitle(s))}</div>
      <div class="truncate text-xs text-ink-3">${s.total ? `${num(s.done)} of ${num(s.total)} done this round` : "Sending files"} · ${speed(s.net.up_now)}</div></div></div>
      <p class="mt-3 text-xs text-ink-3">Each account's files are in the cards below. They share one speed limit.</p>`;
  const c = s.current;
  if (c)
    return `<div class="flex items-center gap-3">${thumb(c.name, "thumb size-14")}
        <div class="min-w-0 flex-1"><div class="truncate font-medium" title="${esc(c.name)}">${esc(fileName(c.name))}</div>
        <div class="mt-1 flex flex-wrap gap-1">${badge(c.kind === "video" ? "Video" : "Photo", "", c.kind === "video" ? "video" : "photo")}${stepBadge(c)}</div>
        ${c.account ? `<div class="mt-1 truncate text-xs text-ink-3">To ${esc(c.account)}</div>` : ""}</div></div>
      <div class="bar active mt-3"><i style="width:${pct(c.bytes_done, c.bytes_total)}%"></i></div>
      <div class="mt-1.5 flex justify-between text-xs text-ink-3 tabular-nums"><span>${bytes(c.bytes_done)} of ${bytes(c.bytes_total)}</span><span>${speed(s.net.up_now)}</span></div>
      ${s.total ? `<div class="mt-3 text-xs text-ink-3">${num(s.done)} of ${num(s.total)} done this round${(s.uploading?.length ?? 0) > 1 ? ` · ${esc(uploadTitle(s).replace("Uploading · ", ""))}; see Accounts below` : ""}</div>` : ""}
      ${upNext(s, c.name)}`;
  return `<div class="flex items-center gap-3"><span class="grid size-12 shrink-0 place-items-center rounded-xl bg-panel-2 ${s.phase === "paused" ? "text-warn" : s.phase === "scanning" ? "text-accent" : "text-ink-3"}">${icon(phaseInfo(s).icon, `size-6 ${s.phase === "scanning" ? "spin" : ""}`)}</span>
      <div class="min-w-0 flex-1"><div class="font-medium">${esc(nowTitle(s))}</div><div class="truncate text-xs text-ink-3">${esc(phaseInfo(s).detail)}</div></div>${nowAction(s)}</div>
    <div class="mt-4 grid grid-cols-3 gap-2 text-center">
      <div class="stat-tile"><div class="num text-lg">${num(s.counts.uploaded)}</div><div class="text-[11px] text-ink-3">Uploaded</div></div>
      <div class="stat-tile"><div class="num text-lg">${num(s.counts.exists)}</div><div class="text-[11px] text-ink-3">Already there</div></div>
      <div class="stat-tile"><div class="num text-lg">${num(left)}</div><div class="text-[11px] text-ink-3">Waiting</div></div>
    </div>${upNext(s, "")}`;
}

function heroBadges(s: Status): string {
  const left = s.library.files - s.library.synced_files;
  const eta = etaText(s);
  return [
    left
      ? badge(
          `${num(left)} to go`,
          s.phase === "uploading" ? "info" : s.paused ? "warn" : "",
          "hourglass",
          s.paused ? "Waiting until uploads resume" : "Waiting for upload",
        )
      : s.copies_pending
        ? badge(
            "Main library in",
            "info",
            "copy",
            "The backup account has every file. Other accounts are still catching up.",
          )
        : badge("All backed up", "good", "ok"),
    eta ? badge(`~${eta} left`, "info", "clock", "At the current upload speed") : "",
    ...copiesLeft(s).map(([a, n]) =>
      badge(`${num(n)} to ${a.split("@")[0]}`, "", "copy", `Still copying to ${a} (Also in)`),
    ),
    s.counts.failed
      ? `<a href="#/activity/failed">${badge(`${num(s.counts.failed)} failed`, "bad", "error")}</a>`
      : "",
    s.counts.gave_up
      ? badge(`${num(s.counts.gave_up)} gave up`, "bad", "warn", "Failed 5 times; retry from Activity")
      : "",
  ].join("");
}

function folderList(s: Status): string {
  const rows = Object.entries(s.folders).filter(([, f]) => f.account && f.files);
  if (!rows.length)
    return `<div class="empty">${icon("folder")}No folders are backed up yet. Choose them in Settings.</div>`;
  return `<ul class="divide-y divide-line">${rows
    .sort((a, b) => b[1].files - a[1].files)
    .map(([name, f]) => {
      const fp = pct(f.synced_files, f.files);
      const extra = (f.accounts ?? []).slice(1);
      const chips = extra
        .map(
          (a) =>
            `<span class="badge" title="Also in ${esc(a)}: ${num(f.copies?.[a] ?? 0)} of ${num(f.files)} files">${esc(a.split("@")[0])} ${num(f.copies?.[a] ?? 0)}/${num(f.files)}</span>`,
        )
        .join("");
      return `<li><a href="#/folders/f/${encodeURIComponent(name)}" class="block px-4 py-3 hover:bg-panel-2">
        <span class="mb-1.5 flex items-center gap-2 text-sm"><span class="flex min-w-0 items-center gap-1.5 font-medium">${icon("folder", "size-4 shrink-0 text-ink-3")}<span class="truncate">${esc(name)}</span></span>
        <span class="ml-auto shrink-0 text-xs text-ink-3 tabular-nums">${num(f.synced_files)} / ${num(f.files)}</span></span>
        <span class="bar block"><i style="width:${fp}%"></i></span>
        <span class="mt-1 flex min-w-0 flex-wrap items-center gap-1 text-xs text-ink-3"><span class="truncate">Backup: ${esc(f.account)}</span>${extra.length ? `<span>· Also in</span>${chips}` : ""}</span></a></li>`;
    })
    .join("")}</ul>`;
}

const sync: View = {
  subtitle: "Backing up your folders to Google Photos: what uploads now and how far each folder is.",
  mount(el) {
    el.innerHTML = `
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-12">
      <!-- Phones: progress, Now, then the tiles. Wider: progress with the tiles under it, beside Now. -->
      <div class="contents lg:col-span-5 lg:flex lg:flex-col lg:gap-4">
      <section class="card overview-hero relative order-1 overflow-hidden p-5 lg:order-none" id="sy-hero">
        <div class="flex items-center justify-between gap-2"><div><span class="label">Your library</span><h2 class="mt-1 text-base font-medium">Backup progress</h2></div><span id="sy-rate" class="text-xs text-ink-3 tabular-nums"></span></div>
        <div class="mt-6 flex items-center gap-6">
          <div class="ring-box relative shrink-0">${ring(0, 148, 8)}<div class="absolute inset-0 grid place-items-center text-center"><div><div class="num text-[32px] leading-9" id="sy-pct">–</div><div class="meta mt-1" id="sy-pct-sub">of files</div></div></div></div>
          <div class="min-w-0 space-y-2" id="sy-hero-text"></div>
        </div>
        <div class="even mt-4" id="sy-badges"></div>
      </section>
      <section class="order-3 grid grid-cols-2 gap-4 lg:order-none">
        ${tile({ id: "sy-today", label: "Today", value: "–", ic: "calendar", chart: "", href: "#/activity/uploaded" })}
        ${tile({ id: "sy-streak", label: "Streak", value: "–", ic: "flame", chart: "", href: "#/insights/activity" })}
        ${tile({ id: "sy-space", label: "Space here", value: "–", ic: "disk", chart: "" })}
        ${tile({ id: "sy-scan", label: "Last scan", value: "–", ic: "search" })}
      </section>
      </div>
      <section class="card order-2 lg:order-none lg:col-span-7">
        ${cardHead("Now", "cloudUp", '<span id="sy-pace"></span><a href="#/upload" class="ml-2 hover:text-ink">Add files ›</a>')}
        <div class="card-b" id="sy-now"><div class="skeleton h-24"></div></div>
      </section>

      <section class="card order-2 min-w-0 overflow-hidden lg:order-none lg:col-span-12" id="sy-lanes" hidden></section>

      <section class="card order-4 self-start lg:order-none lg:col-span-7">
        ${cardHead("Folders", "folder", '<a href="#/folders" class="hover:text-ink">All folders ›</a>')}
        <div id="sy-folders"><div class="card-b"><div class="skeleton h-24"></div></div></div>
      </section>
      <section class="card order-4 self-start lg:order-none lg:col-span-5">
        ${cardHead("Upload times and sizes", "timer", '<a href="#/insights/patterns" class="hover:text-ink">Insights ›</a>')}
        <div class="card-b" id="sy-times"><div class="skeleton h-40"></div></div>
      </section>

      <section class="card order-5 lg:order-none lg:col-span-12">
        ${cardHead("Where your photos are", "chart", `<div class="seg" id="sy-share-by"><button data-by="devices">Subfolders</button><button data-by="folders">Folders</button></div><div class="seg ml-2" id="sy-share-metric"><button data-m="files">Files</button><button data-m="bytes">Size</button></div>`)}
        <div class="card-b" id="sy-share-body" aria-live="polite"><div class="skeleton h-44"></div></div>
      </section>

      <section class="card order-6 lg:order-none lg:col-span-12">
        ${cardHead("Uploads per day", "calendar", `<span id="sy-year-sub"></span>`)}
        <div class="card-b"><div class="scroll-thin fade-x overflow-x-auto pb-1" id="sy-year"><div class="skeleton h-28"></div></div><div class="mt-2 flex justify-end">${heatLegend()}</div></div>
      </section>
    </div>`;
    el.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      if (t.closest('[data-pace="upload"]')) {
        // Each account's own pace: the lane's, or the file on show's account in the Now card.
        const acc =
          t.closest<HTMLElement>("[data-lane]")?.dataset.lane ??
          last?.current?.account ??
          last?.accounts?.[0] ??
          "";
        void openUploadOptions(undefined, last?.pace?.[acc]?.upload);
      }
    });
    $("#sy-share-by").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (!b || shareBy === b.dataset.by) return;
      shareBy = b.dataset.by as "devices" | "folders";
      void loadShare(true);
    });
    $("#sy-share-metric").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (!b || shareMetric === b.dataset.m) return;
      shareMetric = b.dataset.m as "files" | "bytes";
      renderShare();
      animateChange(document.getElementById("sy-share-body"));
    });
    kindsDrawn = false;
    void loadShare();
    slowSynced = -1;
    void loadSlow();
    slowTimer = window.setInterval(loadSlow, 60_000);
  },
  update(s) {
    last = s;
    controls(s);
    // With one account the pace is in the Now card; with several, each lane has its own.
    const acc = s.accounts?.[0] ?? "";
    const pace =
      (s.accounts?.length ?? 0) > 1 ? "" : paceButton("upload", s.upload_workers ?? 0, s.pace?.[acc]?.upload);
    const slot = document.getElementById("sy-pace");
    if (slot && slot.innerHTML !== pace) slot.innerHTML = pace;
    // New uploads: refresh the heat map and streak now instead of waiting for the minute timer.
    if (s.library.synced_files !== slowSynced && Date.now() - slowAt > 10_000) {
      if (slowSynced >= 0) void loadSlow();
      slowSynced = s.library.synced_files;
    }
    if (Date.now() - shareAt > 60_000) void loadShare(); // counts change slowly: once a minute
    else if (!kindsDrawn) renderShare();
    const lib = s.library;
    const p = pct(lib.synced_files, lib.files);
    setRing($("#sy-hero .ring-box svg"), p);
    $("#sy-pct").textContent = lib.files ? `${p >= 99.95 || p === 0 ? p.toFixed(0) : p.toFixed(1)}%` : "–";
    const left = lib.files - lib.synced_files;
    html(
      $("#sy-rate"),
      s.phase === "uploading" && s.net.up_avg > 1000
        ? `${icon("up", "inline size-3 -mt-0.5")} ${speed(s.net.up_avg)}`
        : "",
    );
    const copying = copiesText(s);
    $("#sy-pct-sub").textContent = copying ? "main library" : "of files";
    html(
      $("#sy-hero-text"),
      `<div><div class="font-mono text-lg whitespace-nowrap tabular-nums sm:text-xl">${num(lib.synced_files)}<span class="text-ink-3"> / ${num(lib.files)}</span></div><div class="mt-1 text-xs text-ink-3">files in Google Photos</div></div>
       <div class="text-sm text-ink-2">${bytes(lib.synced_bytes)} of ${bytes(lib.bytes)} <span class="text-ink-3">(${pctText(lib.synced_bytes, lib.bytes)} of the space)</span></div>
       <div class="text-xs text-ink-3">${left ? `${bytes(lib.bytes - lib.synced_bytes)} still to back up` : copying ? `Main library is in. Still copying to ${esc(copying)}` : "Every file is in Google Photos"}</div>`,
    );
    html($("#sy-badges"), heroBadges(s));
    html($("#sy-now"), nowCard(s));
    const lanes = $("#sy-lanes");
    const lanesHtml = lanesCard(s);
    lanes.hidden = !lanesHtml;
    html(lanes, lanesHtml);
    html($("#sy-folders"), folderList(s));
    // Room left on the disk the photos are on, read from the system (any host).
    const space = $("#sy-space");
    const d = s.space?.media;
    $(".label", space).textContent = `Space on ${host()}`;
    html($("[data-v]", space), d ? `${bytes(d.free)} free` : "–");
    html(
      $("[data-s]", space),
      d ? `${pct(d.used, d.total).toFixed(0)}% of ${bytes(d.total)} used` : "Couldn't read the disk",
    );
    html(
      $("[data-c]", space),
      d
        ? `<span class="bar block h-1.5 ${spaceTone(d) === "good" ? "" : `space-${spaceTone(d)}`}"><i style="width:${pct(d.used, d.total)}%"></i></span>`
        : "",
    );
    const scan = $("#sy-scan");
    html($("[data-v]", scan), ago(s.last_scan, s.now));
    html($("[data-s]", scan), `Every ${num(s.interval)} min${s.paused ? " · paused" : ""}`);
  },
  unmount() {
    clearInterval(slowTimer);
    shareRequest++;
  },
};

export default sync;
