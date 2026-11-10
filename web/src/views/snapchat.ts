import { workPanel } from "../lib/work-panel";
import { openUploadOptions, paceButton } from "../lib/pace-sheet";
import { get, post, type SnapAction, type SnapCopy, type SnapItem, type SnapchatReport } from "../lib/api";
import { accountChips } from "../lib/account-menu";
import { confirmDialog } from "../lib/dialog";
import { $, animateChange, esc, html, toast } from "../lib/dom";
import { fileName, num, pct } from "../lib/format";
import { ring } from "../lib/charts";
import { icon } from "../lib/icons";
import { choices, matchBadge, missingNote, offset, SNAP_ACTION_LABEL, SNAP_GROUPS } from "../lib/snapchat";
import { setPageStatus, setPauseControl, setRefreshControl, status } from "../lib/store";
import { openAttrs, thumb } from "../lib/ui";
import type { View } from "./view";

// Snapchat: memories from a Snapchat export (made into a folder by scripts/snapchat_export.py, with
// Snapchat's exact date and place in every file). Each is looked for in Google Photos first, so
// nothing is uploaded twice: ones Google has with the right date only go into the album, ones it
// has with a wrong date are replaced, the rest are uploaded. Nothing changes until Start.

let report: SnapchatReport | null = null;
let show: SnapAction = "replace";
let limit = 60;
let listHtml = ""; // the list as last drawn: redrawn only when it changes (images keep loading)
let timer = 0;
let ticks = 0;
let request = 0;

async function load(): Promise<void> {
  const mine = ++request;
  const r = await get<SnapchatReport>(`snapchat?action=${show}&limit=${limit}`).catch(() => null);
  if (mine !== request) return;
  report = r;
  render();
}

/** "5 Nov 2026, 18:47" in the viewer's time zone. */
function when(unix: number | null | undefined): string {
  if (unix == null) return "unknown";
  return new Date(unix * 1000).toLocaleString([], {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "Jun 4, 2026" and "11:19 PM", for a memory's line and the line under it. */
function day(unix: number): string {
  return new Date(unix * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}
function time(unix: number): string {
  return new Date(unix * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function googleThumb(account: string, key: string, kind: string, cls = "thumb"): string {
  return `<img class="${cls}" loading="lazy" decoding="async" alt="" data-kind="${kind}" src="api/storage/thumb?small=1&account=${encodeURIComponent(account)}&key=${encodeURIComponent(key)}">`;
}

/** Each group's colour, the same in the hero bar, the tiles and the list. */
const COLOR: Record<SnapAction, string> = {
  album: "var(--s1)",
  replace: "var(--s2)",
  upload: "var(--s3)",
  skip: "var(--ink-3)",
};

/** One of Google's copies: a small thumbnail with a corner mark for what happens to it. */
function copyThumb(c: SnapCopy, i: SnapItem, r: SnapchatReport): string {
  const gone = c.state === "deleted";
  const [what, mark, tone] = gone
    ? ["Deleted from Google", "trash", "bad"]
    : c.role === "keep"
      ? ["Stays in Google", "check", "good"]
      : c.role === "leave"
        ? ["From a camera: stays", "camera", ""]
        : ["Deleted from Google", "trash", "warn"];
  const tip = `In Google · ${when(c.taken)} · ${what}`;
  const img = gone
    ? `<span class="thumb grid size-10 place-items-center text-ink-3">${icon("trash", "size-4")}</span>`
    : googleThumb(r.account, c.key, i.kind, "thumb size-10");
  const body = `<span class="sc-copy ${gone ? "gone" : ""}" title="${esc(tip)}">${img}<span class="sc-mark ${tone}">${icon(mark as Parameters<typeof icon>[0], "size-2.5")}</span></span>`;
  return c.url && !gone
    ? `<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(tip)}">${body}</a>`
    : body;
}

/** Where a memory is at, as one small icon. */
function stateMark(i: SnapItem, r: SnapchatReport): string {
  const now = working(r).find((w) => w.key === i.name);
  const [ic, tone, tip] = now
    ? (["loader", "text-accent spin", STEP[now.step] ?? "Working on it"] as const)
    : i.state === "queued"
      ? (["hourglass", "text-accent", "Queued"] as const)
      : i.state === "done"
        ? (["ok", "text-good", i.action === "album" ? "In the album" : "Done"] as const)
        : i.state === "failed"
          ? (["error", "text-bad", "Didn't work"] as const)
          : (["", "", ""] as const);
  return ic
    ? `<span class="grid size-7 shrink-0 place-items-center ${tone.includes("spin") ? "text-accent" : tone}" title="${esc(tip)}" aria-label="${esc(tip)}">${icon(ic, `size-4 ${tone.includes("spin") ? "spin" : ""}`)}</span>`
    : "";
}

function row(i: SnapItem, r: SnapchatReport): string {
  const [label, , tip] = matchBadge(i);
  const open = i.url || i.google?.url;
  const editable = !i.state || i.state === "failed";
  const select = editable
    ? `<select class="input sc-pick" data-action="${esc(i.name)}" aria-label="What happens to ${esc(fileName(i.name))}">${choices(
        i,
      )
        .map(
          (a) => `<option value="${a}" ${a === i.action ? "selected" : ""}>${SNAP_ACTION_LABEL[a]}</option>`,
        )
        .join("")}</select>`
    : "";
  // The latest of Google's dates: how much later than Snapchat's it is.
  const dates = i.copies.map((c) => c.taken).filter((t): t is number => t != null);
  const latest = dates.length ? Math.max(...dates) : null;
  const off =
    latest != null && Math.abs(latest - i.taken) > 120
      ? `<span class="sc-off ${latest < i.taken ? "older" : ""}" title="${esc(latest < i.taken ? `Google's original is older (${when(latest)}): it keeps its date` : `Google's date: ${when(latest)}`)}">${icon("calendar", "size-3")}${offset(latest - i.taken)}</span>`
      : "";
  const place = i.place
    ? `<span title="${i.place[0].toFixed(4)}, ${i.place[1].toFixed(4)}">${icon("pin", "size-3")}</span>`
    : "";
  const copies = i.copies.length
    ? `<span class="hidden text-ink-3 sm:inline">${icon("arrowRight", "size-3.5")}</span><span class="flex shrink-0 gap-1">${i.copies
        .slice(0, 3)
        .map((c) => copyThumb(c, i, r))
        .join(
          "",
        )}${i.copies.length > 3 ? `<span class="grid size-10 place-items-center rounded-lg bg-panel-2 text-xs text-ink-3">+${i.copies.length - 3}</span>` : ""}</span>`
    : "";
  return `<div data-key="${esc(i.name)}" class="sc-row">
    <span class="clickable relative shrink-0" ${openAttrs(i.name)} title="${esc(fileName(i.name))}">${thumb(i.name, "thumb size-12")}${i.kind === "video" ? `<span class="sc-vid">${icon("video", "size-3")}</span>` : ""}</span>
    <div class="min-w-0 flex-1">
      <div class="truncate text-sm font-medium">${esc(day(i.taken))}</div>
      <div class="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-3"><span class="tabular-nums">${esc(time(i.taken))}</span><span title="${esc(tip)}">${esc(label)}</span>${place}${off}${i.decided ? `<span class="text-warn">You chose</span>` : ""}</div>
      ${i.error ? `<div class="mt-0.5 truncate text-xs ${i.state === "failed" ? "text-bad" : "text-ink-3"}" title="${esc(i.error)}">${esc(i.error)}</div>` : ""}
    </div>
    ${copies}
    <div class="sc-end">${select}${stateMark(i, r)}${
      open
        ? `<a class="btn icon sm" href="${esc(open)}" target="_blank" rel="noopener noreferrer" title="Open in Google Photos" aria-label="Open in Google Photos">${icon("external")}</a>`
        : ""
    }</div>
  </div>`;
}

function chip(ic: Parameters<typeof icon>[0], text: string, tone = "", tip = ""): string {
  return `<span class="sc-chip ${tone}" ${tip ? `title="${esc(tip)}"` : ""}>${icon(ic, "size-3.5")}${esc(text)}</span>`;
}

function searchCard(r: SnapchatReport): string {
  const steps = [
    ["Fingerprint", r.fingerprinted, r.memories],
    ["Compare with Google", r.thumbs.done, r.thumbs.total],
    ["Match", r.checked, r.memories],
  ] as const;
  return `<section class="card mb-4 p-4" aria-live="polite">
    <div class="flex flex-wrap items-center gap-2"><h2 class="font-semibold">${icon("search", "inline size-4 -mt-0.5")} Finding your memories in Google</h2>${r.slow_until ? chip("clock", `Paused by Google until ${new Date(r.slow_until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`, "warn") : ""}</div>
    <div class="mt-3 grid gap-3 sm:grid-cols-3">${steps
      .map(
        ([label, done, total]) =>
          `<div class="text-xs"><div class="flex justify-between text-ink-2"><span>${label}</span><span class="tabular-nums text-ink-3">${total ? `${num(done)} / ${num(total)}` : "…"}</span></div><div class="bar mt-1.5"><i style="width:${total ? pct(done, total) : 0}%"></i></div></div>`,
      )
      .join("")}</div>
  </section>`;
}

/** The three jobs, each started on its own: adding to the album is safe (nothing is uploaded),
 * replacing deletes Google's later-dated copies, uploading adds what Google doesn't have. */
const JOBS: {
  id: Exclude<SnapAction, "skip">;
  button: string;
  what: (r: SnapchatReport) => string;
  ic: Parameters<typeof icon>[0];
}[] = [
  {
    id: "album",
    button: "Add to album",
    what: (r) => `already in Google with the right date: added to the album ${r.album}, nothing uploaded`,
    ic: "library",
  },
  {
    id: "replace",
    button: "Replace",
    what: () => "in Google with a later date: replaced by the memory with Snapchat's date",
    ic: "calendar",
  },
  {
    id: "upload",
    button: "Upload",
    what: () => "not in Google: uploaded with Snapchat's date and place",
    ic: "cloudUp",
  },
];

function left(r: SnapchatReport, a: SnapAction): number {
  return r.counts[a].total - r.counts[a].done - r.counts[a].queued;
}

/** The memories being worked on now (older servers only send one). */
function working(r: SnapchatReport): NonNullable<SnapchatReport["working"]> {
  return r.working ?? (r.current ? [r.current] : []);
}

/** What the memory being worked on is at (snapchat_state "current" steps), for its tooltip. */
const STEP: Record<string, string> = {
  keeping: "Saving Google's extra copy here before deleting it",
  downloading: "Downloading Google's extra copy before deleting it",
  local: "Copying Google's extra copy from this server",
  deleting: "Deleting an extra copy from Google",
  hashing: "Checking before uploading",
  uploading: "Uploading",
};

/** The top of the page: a ring for how far it is, the memories split by what happens to them, and
 * short chips for the folder, the album and anything to know. */
function hero(r: SnapchatReport): string {
  const total = JOBS.reduce((n, j) => n + r.counts[j.id].total, 0);
  const done = JOBS.reduce((n, j) => n + r.counts[j.id].done, 0);
  const busy = Object.values(r.counts).some((c) => c.queued > 0);
  const p = total ? (done / total) * 100 : 0;
  const all = Object.values(r.counts).reduce((n, c) => n + c.total, 0) || 1;
  const seg = SNAP_GROUPS.filter((g) => r.counts[g.id].total)
    .map(
      (g) =>
        `<span style="width:${(r.counts[g.id].total / all) * 100}%;background:${COLOR[g.id]}" title="${esc(`${g.title}: ${num(r.counts[g.id].total)}`)}"></span>`,
    )
    .join("");
  const notes: string[] = [];
  for (const m of r.manifests) {
    const note = missingNote(m.export);
    const missing = m.export.missing ?? 0;
    if (missing)
      notes.push(
        chip(
          "warn",
          `${num(missing)} not in the zips`,
          "warn",
          `${note}. Download the missing parts from Snapchat and run the script again: only new memories are added.`,
        ),
      );
  }
  if (r.exporting.length)
    notes.push(chip("loader", "Export still running", "warn", `Still adding to ${r.exporting.join(", ")}`));
  if (r.held)
    notes.push(
      chip(
        "info",
        `${num(r.held)} not listed yet`,
        "warn",
        "In the folder but not in its list, so not backed up. Run the script again to list them.",
      ),
    );
  const dupes = r.duplicates.total - r.duplicates.deleted;
  if (dupes)
    notes.push(
      chip(
        "copy",
        `${num(dupes)} extra ${dupes === 1 ? "copy" : "copies"} to remove`,
        "",
        "Deleted from Google as their memory is done. Less certain matches are saved here first.",
      ),
    );
  const folders = r.manifests.map((m) => m.folder).join(", ");
  return `<section class="card sc-hero mb-4 p-4 sm:p-5">
    <div class="flex items-center gap-4 sm:gap-5">
      <div class="relative shrink-0">${ring(p, 88, 8)}<div class="absolute inset-0 grid place-items-center text-center"><div><div class="text-lg font-semibold tabular-nums">${Math.floor(p)}%</div><div class="text-[9px] uppercase tracking-wider text-ink-3">${busy ? "sending" : "done"}</div></div></div></div>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-baseline gap-x-2"><h2 class="text-2xl font-semibold tracking-tight tabular-nums">${num(r.memories)}</h2><span class="text-sm text-ink-3">memories</span><span class="text-sm text-ink-3">· ${num(done)} in Google</span></div>
        <div class="sc-seg mt-3" role="img" aria-label="${esc(SNAP_GROUPS.map((g) => `${g.title} ${r.counts[g.id].total}`).join(", "))}">${seg}</div>
      </div>
    </div>
    <div class="mt-4 flex flex-wrap gap-1.5">${paceButton("upload", status()?.upload_workers ?? 0, status()?.pace?.[r.account]?.upload)}${chip("folder", folders)}${chip("library", `Album ${r.album}`, "", "Change it under Sources")}${notes.join("")}</div>
  </section>`;
}

/** One tile per group: its colour, count and progress, and the button that starts its job. The
 * tile itself picks the list below. */
function groupTiles(r: SnapchatReport): string {
  return `<div class="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4" role="tablist" aria-label="Groups">${SNAP_GROUPS.map(
    (g) => {
      const c = r.counts[g.id];
      const n = g.id === "skip" ? 0 : left(r, g.id);
      const job = JOBS.find((j) => j.id === g.id);
      const state =
        g.id === "skip"
          ? `<span class="text-ink-3">Nothing happens</span>`
          : !c.total
            ? `<span class="text-ink-3">None</span>`
            : c.queued
              ? `<span class="text-accent">${icon("loader", "inline size-3 -mt-0.5 spin")} ${num(c.queued)} going</span>`
              : !n
                ? `<span class="text-good">${icon("ok", "inline size-3.5 -mt-0.5")} Done</span>`
                : `<span class="text-ink-3">${num(n)} ready</span>`;
      const go =
        job && n && !c.queued
          ? `<button class="btn sm ${g.id === "album" ? "primary" : ""} mt-3 w-full justify-center" data-start="${g.id}">${icon("play", "size-3.5")}${job.button}</button>`
          : "";
      return `<div class="card sc-tile ${show === g.id ? "on" : ""}" style="--tc:${COLOR[g.id]}" role="tab" tabindex="0" aria-selected="${show === g.id}" data-show="${g.id}" title="${esc(g.hint)}">
        <div class="flex items-center gap-2"><span class="sc-ic">${icon(g.ic, "size-4")}</span><span class="min-w-0 truncate text-sm font-medium">${g.title}</span></div>
        <div class="mt-2 text-2xl font-semibold tabular-nums">${num(c.total)}</div>
        <div class="mt-0.5 text-xs">${state}${c.failed ? ` <span class="text-bad">· ${num(c.failed)} failed</span>` : ""}</div>
        ${g.id !== "skip" && c.total ? `<div class="sc-bar mt-2"><i style="width:${pct(c.done, c.total)}%"></i></div>` : ""}
        ${go}
      </div>`;
    },
  ).join("")}</div>`;
}

function render(): void {
  const r = report;
  const accs = status()?.accounts ?? [];
  html(
    $("#sc-chips"),
    r && r.memories
      ? accountChips(r.account, (a) =>
          a === r.account ? { line: "Memories go here", tone: "text-accent" } : { line: "Use this account" },
        )
      : "",
  );
  if (!r) return;
  if (status() && !accs.length) {
    html(
      $("#sc-body"),
      `<div class="card empty">${icon("users")}Connect a Google account first (Settings › Google accounts)</div>`,
    );
    return;
  }
  if (!r.memories) {
    setPageStatus({
      tone: "idle",
      title: "No Snapchat export yet",
      detail: "Make one with the script below",
    });
    setPauseControl(null);
    setRefreshControl(null);
    html(
      $("#sc-body"),
      `<section class="card p-5 text-sm text-ink-2">
        <h2 class="text-lg font-semibold tracking-tight">Bring in your Snapchat Memories</h2>
        <ol class="mt-4 grid gap-3 sm:grid-cols-3">
          <li class="sc-step">${icon("download", "size-5")}<b>Download</b><span>Snapchat › Settings › My Data: tick Memories, download every zip.</span></li>
          <li class="sc-step">${icon("code", "size-5")}<b>Run the script</b><span><code>python3 scripts/snapchat_export.py &lt;zips&gt; &lt;Photos/Snapchat&gt;</code></span></li>
          <li class="sc-step">${icon("sparkles", "size-5")}<b>Review here</b><span>Each memory is found in Google before anything is uploaded.</span></li>
        </ol>
      </section>`,
    );
    return;
  }
  const going = r.current != null || Object.values(r.counts).some((c) => c.queued > 0);
  setPageStatus(
    r.paused
      ? {
          tone: "warn",
          title: "Snapchat memories are paused",
          detail: `${r.account} · nothing is uploaded or deleted`,
        }
      : r.searching
        ? { tone: "run", title: "Looking for memories in Google Photos", detail: r.account }
        : going
          ? { tone: "run", title: "Sending Snapchat memories", detail: r.account }
          : { tone: "good", title: `${num(r.memories)} Snapchat memories`, detail: r.account },
  );
  setPauseControl(
    !(r.paused || going)
      ? null
      : {
          paused: r.paused,
          working: going,
          label: r.paused ? "Resume Snapchat memories" : "Pause Snapchat memories",
          toggle: async () => {
            await post("snapchat/pause", { account: r.account, paused: !r.paused });
            void load();
            return r.paused ? "Resumed" : "Paused";
          },
        },
  );
  setRefreshControl({
    label: `Look for the memories in ${r.account} again`,
    busy: r.searching,
    run: async () => {
      await post("snapchat/recheck", { account: r.account });
      void load();
      return "Reading your library from Google";
    },
  });
  const group = SNAP_GROUPS.find((g) => g.id === show) ?? SNAP_GROUPS[0];
  const top = `${r.error && !r.slow_until ? `<section class="card mb-4 flex items-center gap-2 p-3 text-sm text-bad" role="alert">${icon("error", "size-4 shrink-0")}${esc(r.error)}</section>` : ""}
    ${hero(r)}
    ${r.searching ? searchCard(r) : groupTiles(r)}
    ${(() => {
      const w = working(r);
      return w.length
        ? `<div class="mb-4">${workPanel(
            "Working now",
            "loader",
            w.map((c) => ({
              key: c.key,
              name: fileName(c.key),
              step: c.step,
              done: c.done,
              total: c.total,
              label: STEP[c.step],
            })),
          )}</div>`
        : "";
    })()}`;
  const list = `<section class="card min-w-0 overflow-hidden">
      <div class="flex items-center gap-2 border-b border-line px-4 py-3">
        <span class="size-2.5 shrink-0 rounded-full" style="background:${COLOR[group.id]}"></span>
        <h2 class="min-w-0 flex-1 truncate font-semibold">${group.title} <span class="font-normal text-ink-3">· ${num(r.counts[group.id].total)}</span></h2>
        <span class="hidden text-xs text-ink-3 sm:inline">Memory ${icon("arrowRight", "inline size-3")} Google's copies</span>
      </div>
      <div id="sc-list" aria-live="polite">${
        r.items.length
          ? `<div class="divide-y divide-line" data-gallery>${r.items.map((i) => row(i, r)).join("")}</div>${
              r.more
                ? `<div class="flex justify-center p-3"><button class="btn" id="sc-more">${icon("plus")}Show more (${num(r.more)} left)</button></div>`
                : ""
            }`
          : `<div class="empty">${icon("shield")}${r.searching ? "Still looking…" : "None here"}</div>`
      }</div>
    </section>`;
  let topEl = document.getElementById("sc-top");
  let listEl = document.getElementById("sc-listbox");
  if (!topEl || !listEl) {
    html($("#sc-body"), '<div id="sc-top"></div><div id="sc-listbox"></div>');
    topEl = $("#sc-top");
    listEl = $("#sc-listbox");
    listHtml = "";
  }
  html(topEl, top);
  // The list is redrawn only when it changes: a redraw every few seconds would restart the
  // thumbnails still loading.
  if (list !== listHtml) {
    html(listEl, list);
    listHtml = list;
  }
}

async function start(action: Exclude<SnapAction, "skip">): Promise<void> {
  const r = report;
  if (!r) return;
  const job = JOBS.find((j) => j.id === action);
  if (!job) return;
  const n = left(r, action);
  const ok = await confirmDialog({
    title: `${job.button}: ${num(n)} ${n === 1 ? "memory" : "memories"}?`,
    icon: job.ic,
    body: [
      `In ${r.account}: ${num(n)} ${job.what(r)}. Each goes into the album ${r.album}.`,
      ...(action === "replace"
        ? [
            "Google's later-dated copies are deleted from Google (a less certain match is downloaded here first). They leave albums you made yourself; favourite, archive and your caption come back.",
          ]
        : []),
      ...(action !== "upload" && r.duplicates.total - r.duplicates.deleted
        ? [
            "Extra copies of these memories in Google are deleted from Google: the memory here is the same picture (a less certain match is downloaded here first).",
          ]
        : []),
    ],
    confirm: job.button,
  });
  if (!ok) return;
  try {
    const res = await post<{ queued: number }>("snapchat/start", { account: r.account, actions: [action] });
    toast(
      res.queued ? `Started: ${num(res.queued)} memories` : "Nothing to start",
      res.queued ? "good" : "info",
    );
  } catch (e) {
    toast((e as Error).message, "bad");
  }
  void load();
}

async function pickAccount(next: string): Promise<void> {
  const r = report;
  if (!r || next === r.account) return;
  const ok = await confirmDialog({
    title: `Send memories to ${next}?`,
    icon: "users",
    body: ["They're looked for again in that account's library. Memories already sent stay where they are."],
    confirm: "Use this account",
  });
  if (!ok) return;
  try {
    await post("snapchat/account", { account: next });
    toast(`Memories go to ${next}`, "good");
  } catch (e) {
    toast((e as Error).message, "bad");
  }
  report = null;
  html($("#sc-body"), '<div class="card skeleton h-72"></div>');
  void load();
}

function onClick(e: Event): void {
  const b = (e.target as HTMLElement).closest<HTMLElement>("button, [data-show]");
  if (!b) return;
  if (b.dataset.pace === "upload") {
    void openUploadOptions(() => void load(), status()?.pace?.[report?.account ?? ""]?.upload);
    return;
  }
  if (b.dataset.acc) void pickAccount(b.dataset.acc);
  else if (b.dataset.start) void start(b.dataset.start as Exclude<SnapAction, "skip">);
  else if (b.dataset.show) {
    if (b.dataset.show === show) return;
    show = b.dataset.show as SnapAction;
    limit = 60;
    void load().then(() => animateChange(document.getElementById("sc-list")));
  } else if (b.id === "sc-more") {
    limit += 60;
    void load();
  }
}

async function onChange(e: Event): Promise<void> {
  const t = e.target as HTMLSelectElement;
  if (!t.dataset.action) return;
  try {
    await post("snapchat/action", { paths: [t.dataset.action], action: t.value });
    toast(`${fileName(t.dataset.action)}: ${SNAP_ACTION_LABEL[t.value as SnapAction]}`, "good");
  } catch (err) {
    toast((err as Error).message, "bad");
  }
  void load();
}

const snapchat: View = {
  subtitle: "Snapchat Memories with their real date, without duplicates",
  mount(el) {
    show = "replace";
    limit = 60;
    listHtml = "";
    el.innerHTML = `
    <div id="sc-chips"></div>
    <details class="sc-how mb-4">
      <summary>${icon("info", "size-4")}How it works</summary>
      <ol class="mt-3 grid gap-3 sm:grid-cols-3">
        <li class="sc-step">${icon("search", "size-5")}<b>Find</b><span>Each memory is looked for in Google: the same file, the same second, or a picture that looks the same.</span></li>
        <li class="sc-step">${icon("calendar", "size-5")}<b>Decide</b><span>Right date: into the album. Later date: replaced with Snapchat's. Missing: uploaded. Originals keep their date.</span></li>
        <li class="sc-step">${icon("copy", "size-5")}<b>Clean up</b><span>Extra copies leave Google. Less certain ones are saved here first.</span></li>
      </ol>
    </details>
    <div id="sc-body"><div class="card skeleton h-72"></div></div>`;
    el.addEventListener("click", onClick);
    el.addEventListener("keydown", (e) => {
      const t = e.target as HTMLElement;
      if (t.dataset.show && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        t.click();
      }
    });
    el.addEventListener("change", (e) => void onChange(e));
    void load();
    timer = window.setInterval(() => {
      const r = report;
      const active = r?.searching || r?.current || Object.values(r?.counts ?? {}).some((c) => c.queued > 0);
      if (active || ++ticks % 8 === 0) void load(); // busy: every 4 s, else every 30 s
    }, 4000);
  },
  unmount() {
    clearInterval(timer);
    request++;
    setPageStatus(null);
    setPauseControl(null);
    setRefreshControl(null);
  },
};

export default snapchat;
