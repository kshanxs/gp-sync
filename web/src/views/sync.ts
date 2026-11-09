// Normal sync on its own page: what uploads now, how much of the library is backed up, and each
// folder's progress. (The Overview shows every process side by side and links here.)

import type { Status } from "../lib/api";
import { ring, setRing } from "../lib/charts";
import { $, esc, html } from "../lib/dom";
import { bytes, fileName, num, pct, speed } from "../lib/format";
import { icon } from "../lib/icons";
import { badge, cardHead, etaText, openAttrs, phaseInfo, thumb } from "../lib/ui";
import type { View } from "./view";

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
  return `<span class="badge info">${icon(up ? "cloudUp" : "loader", up ? "bob" : "spin")}${esc(label)}</span>`;
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
      return s.library.files > s.library.synced_files ? "Next files go at the next scan" : "All caught up";
  }
}

function nowAction(s: Status): string {
  if (s.phase === "paused")
    return `<button class="btn sm primary shrink-0" data-now="resume">${icon("play")}Resume</button>`;
  if (s.phase === "setup")
    return `<a class="btn sm primary shrink-0" href="#/settings/accounts">${icon("key")}Connect</a>`;
  if (s.phase === "idle" && s.library.files > s.library.synced_files)
    return `<button class="btn sm shrink-0" data-now="scan">${icon("refresh")}Scan now</button>`;
  return "";
}

/** The other files uploading at the same time (Settings › Sync, Files at once). */
function alsoUploading(s: Status, shown: string): string {
  const others = (s.uploading ?? []).filter((u) => u.name !== shown);
  if (!others.length) return "";
  return `<ul class="mt-3 space-y-1.5">${others
    .map(
      (u) =>
        `<li class="flex items-center gap-2 text-xs"><span class="min-w-0 flex-1 truncate" title="${esc(u.name)}">${esc(fileName(u.name))}</span><span class="bar w-20 shrink-0"><i style="width:${pct(u.bytes_done, u.bytes_total)}%"></i></span><span class="w-14 shrink-0 text-right text-ink-3 tabular-nums">${bytes(u.bytes_total)}</span></li>`,
    )
    .join("")}</ul>`;
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
  const c = s.current;
  const left = s.library.files - s.library.synced_files;
  if (c)
    return `<div class="flex items-center gap-3">${thumb(c.name, "thumb size-14")}
        <div class="min-w-0 flex-1"><div class="truncate font-medium" title="${esc(c.name)}">${esc(fileName(c.name))}</div>
        <div class="mt-1 flex flex-wrap gap-1">${badge(c.kind === "video" ? "Video" : "Photo", "", c.kind === "video" ? "video" : "photo")}${stepBadge(c)}</div></div></div>
      <div class="bar active mt-3"><i style="width:${pct(c.bytes_done, c.bytes_total)}%"></i></div>
      <div class="mt-1.5 flex justify-between text-xs text-ink-3 tabular-nums"><span>${bytes(c.bytes_done)} of ${bytes(c.bytes_total)}</span><span>${speed(s.net.up_now)}</span></div>
      ${alsoUploading(s, c.name)}
      ${s.total ? `<div class="mt-3 text-xs text-ink-3">File ${num(Math.min(s.done + (s.uploading?.length || 1), s.total))} of ${num(s.total)} this round${(s.uploading?.length ?? 0) > 1 ? ` · ${num(s.uploading!.length)} at once` : ""}</div>` : ""}
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
      : badge("All backed up", "good", "ok"),
    eta ? badge(`~${eta} left`, "info", "clock", "At the current upload speed") : "",
    s.copies_pending
      ? badge(`${num(s.copies_pending)} copies to go`, "", "copy", "Waiting for your other Google accounts")
      : "",
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
      return `<li><a href="#/folders/f/${encodeURIComponent(name)}" class="block px-4 py-3 hover:bg-panel-2">
        <span class="mb-1.5 flex items-center gap-2 text-sm"><span class="flex min-w-0 items-center gap-1.5 font-medium">${icon("folder", "size-4 shrink-0 text-ink-3")}<span class="truncate">${esc(name)}</span></span>
        <span class="ml-auto shrink-0 text-xs text-ink-3 tabular-nums">${num(f.synced_files)} / ${num(f.files)}</span></span>
        <span class="bar block"><i style="width:${fp}%"></i></span>
        <span class="mt-1 block truncate text-xs text-ink-3">${esc(f.account)}</span></a></li>`;
    })
    .join("")}</ul>`;
}

const sync: View = {
  subtitle: "Backing up your Umbrel folders to Google Photos: what uploads now and how far each folder is.",
  mount(el) {
    el.innerHTML = `
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-12">
      <section class="card overview-hero relative overflow-hidden p-5 lg:col-span-5" id="sy-hero">
        <div class="flex items-center justify-between gap-2"><div><span class="label">Your library</span><h2 class="mt-1 text-lg font-semibold tracking-tight">Backup progress</h2></div><span id="sy-rate" class="text-xs text-ink-3 tabular-nums"></span></div>
        <div class="mt-4 flex items-center gap-4 sm:gap-5">
          <div class="ring-box relative shrink-0">${ring(0, 148, 12)}<div class="absolute inset-0 grid place-items-center text-center"><div><div class="num text-3xl" id="sy-pct">–</div><div class="text-[11px] text-ink-3">of library</div></div></div></div>
          <div class="min-w-0 space-y-1.5" id="sy-hero-text"></div>
        </div>
        <div class="mt-4 flex flex-wrap gap-1.5" id="sy-badges"></div>
      </section>
      <section class="card lg:col-span-7">
        ${cardHead("Now", "cloudUp", '<a href="#/upload" class="hover:text-ink">Upload from phone ›</a>')}
        <div class="card-b" id="sy-now"><div class="skeleton h-24"></div></div>
      </section>
      <section class="card lg:col-span-12">
        ${cardHead("Folders", "folder", '<a href="#/folders" class="hover:text-ink">All folders ›</a>')}
        <div id="sy-folders"><div class="card-b"><div class="skeleton h-24"></div></div></div>
      </section>
    </div>`;
    $("#sy-now").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("[data-now]");
      if (b) document.getElementById(b.dataset.now === "resume" ? "btn-pause" : "btn-sync")?.click();
    });
  },
  update(s) {
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
    html(
      $("#sy-hero-text"),
      `<div class="num text-xl">${num(lib.synced_files)} <span class="text-sm font-normal text-ink-3">of ${num(lib.files)} files</span></div>
       <div class="text-sm text-ink-2">${bytes(lib.synced_bytes)} of ${bytes(lib.bytes)}</div>
       <div class="text-xs text-ink-3">${left ? `${bytes(lib.bytes - lib.synced_bytes)} still to back up` : "Every file is in Google Photos"}</div>`,
    );
    html($("#sy-badges"), heroBadges(s));
    html($("#sy-now"), nowCard(s));
    html($("#sy-folders"), folderList(s));
  },
};

export default sync;
