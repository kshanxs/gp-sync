import {
  get,
  post,
  tz,
  type FolderAccount,
  type FolderFiles,
  type FolderGroup,
  type FolderReport,
  type Settings,
  peek,
} from "../lib/api";
import { accountBreakdown, PICK_KEY, SEGMENTS } from "../lib/account-breakdown";
import { backupPriorityControl, PRIORITY_MAX } from "../lib/backup-priority";
import { $, esc, html, toast, touched } from "../lib/dom";
import { ago, bytes, clock, date, dayKey, fileName, num, pct } from "../lib/format";
import { icon, type IconName } from "../lib/icons";
import { folderChips, foldersMap, orderAfterTap, saveFolders } from "../lib/folder-accounts";
import { precheckLines, prechecksFor } from "../lib/prechecks";
import { host, status } from "../lib/store";
import { openAttrs, thumb, type Tone } from "../lib/ui";
import type { View } from "./view";

// A two-pane browser: the navigator on the left (one tree of the backed-up folders, the "Back up
// first" folders pinned on top), the open folder on the right (its header with what each account
// has, five numbers, then tabs: Photos, Folders inside, Added). With two or more accounts, the
// navigator's account switch makes every percentage that account's.
//
// #/folders                → everything on this server
// #/folders/f/Photos/x     → a folder (#/folders/d/… from older links opens the same)
// A trailing /~photo or /~video opens the Photos tab filtered to that kind.

/** Whole percent; 99 until the very last file is in, so "100%" always means done. */
const percent = (part: number, whole: number) => {
  const p = Math.round(pct(part, whole));
  return p === 100 && part < whole ? 99 : p;
};

type Tab = "photos" | "folders" | "added";

let path = "";
let days = 30;
let tab: Tab = "photos";
let report: FolderReport | null = null;
let opened = false;
let filesOpened = false;
let request = 0;
let refreshTimer = 0;
/** What the "Sends to" row last drew (it is redrawn with every status, only when it changed). */
let sendsDrawn = "";
/** Small screens: the navigator is folded away while a folder is open. */
let navOpen = false;

// the photos and videos of the open folder
let files: FolderFiles | null = null;
let shownFiles: FolderFiles["items"] = [];
let fStatus = "all";
let fKind = "all";
let fSort = "new";
let fDay = "";
let fRequest = 0;
let fLoading = false;
let observer: IntersectionObserver | null = null;

const lastPart = (p: string) => p.split("/").at(-1) ?? p;
const enc = (p: string) => p.split("/").map(encodeURIComponent).join("/");

// ---- the account the percentages are for ("" = each folder's backup), kept on this device

const ACC_KEY = "gp.foldersAccount";
let fAcc = "";
try {
  fAcc = localStorage.getItem(ACC_KEY) ?? "";
} catch {
  /* private mode */
}
function setAcc(next: string): void {
  fAcc = next;
  try {
    if (next) localStorage.setItem(ACC_KEY, next);
    else localStorage.removeItem(ACC_KEY);
  } catch {
    /* private mode */
  }
}
/** Accounts that get any folder, the main one first (from the top level's totals). */
const knownAccounts = () => (rootTotal?.accounts ?? []).map((a) => a.account);
const accOf = (accs: FolderAccount[] | undefined) =>
  fAcc ? accs?.find((a) => a.account === fAcc) : undefined;
const short = (account: string) => account.split("@")[0] ?? account;

/** Done, all and failed for a folder: its backup's, or the picked account's (null: not in it). */
function progressOf(c: {
  synced: number;
  files: number;
  failed?: number;
  accounts?: FolderAccount[];
}): { done: number; files: number; failed: number } | null {
  if (!fAcc) return { done: c.synced, files: c.files, failed: c.failed ?? 0 };
  const a = accOf(c.accounts);
  if (!a) return null;
  return { done: a.done, files: a.files + (a.main ? 0 : a.failed), failed: a.failed };
}

/** "now", "12m", "3h", "2d", "5w", "8mo", "2y": how long ago files last arrived, for a row. */
function shortAgo(unix: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - unix);
  if (d < 60) return "now";
  if (d < 3600) return `${Math.floor(d / 60)}m`;
  if (d < 86400) return `${Math.floor(d / 3600)}h`;
  if (d < 14 * 86400) return `${Math.floor(d / 86400)}d`;
  if (d < 60 * 86400) return `${Math.floor(d / (7 * 86400))}w`;
  if (d < 365 * 86400) return `${Math.floor(d / (30 * 86400))}mo`;
  return `${Math.floor(d / (365 * 86400))}y`;
}
function agoTag(unix: number | undefined): string {
  if (!unix) return "";
  const old = Date.now() / 1000 - unix > 30 * 86400;
  return `<span class="fo-ago ${old ? "old" : ""}" title="${esc(`Last added ${ago(unix)} · ${date(unix, true)} ${clock(unix)}`)}">${shortAgo(unix)}</span>`;
}

// ---- the navigator: one tree, loaded a level at a time

/** Each opened folder's children, by path ("" = the top folders). */
const tree = new Map<string, FolderGroup[]>();
let notBackedUp: { name: string; files: number }[] = [];
let rootTotal: FolderReport["total"] | null = null;
const EXPANDED_KEY = "gp.foldersOpen";
const expanded = new Set<string>();
try {
  for (const p of JSON.parse(sessionStorage.getItem(EXPANDED_KEY) || "[]") as unknown[])
    if (typeof p === "string") expanded.add(p);
} catch {
  /* a bad saved list: start folded */
}
function saveExpanded(): void {
  try {
    sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...expanded]));
  } catch {
    /* private mode */
  }
}

/** One navigator entry: a folder. */
interface NavNode {
  name: string;
  path: string;
  files: number;
  synced: number;
  failed: number;
  uploads: boolean;
  hasSub: boolean;
  accounts: FolderAccount[];
  lastAdded: number;
}

/** A level of the tree as navigator entries. */
function nodesOf(parent: string): NavNode[] {
  return (tree.get(parent) ?? [])
    .filter((c) => c.name) // loose files: they are in the folder's own photos
    .map((c) => ({
      name: c.name,
      path: c.folders[0] ?? (parent ? `${parent}/${c.name}` : c.name),
      files: c.files,
      synced: c.synced,
      failed: c.failed ?? 0,
      uploads: c.uploads,
      // A backup app spreads some folders over 00..ff: those inner folders aren't listed.
      hasSub: c.has_sub && !c.device,
      accounts: c.accounts ?? [],
      lastAdded: c.last_added,
    }));
}

async function loadLevel(parent: string, background = false): Promise<void> {
  const url = `folders?group=folders&path=${encodeURIComponent(parent)}&days=30&tz=${tz}`;
  if (!tree.has(parent)) {
    const seen = peek<FolderReport>(url);
    if (seen) storeLevel(parent, seen);
  }
  const r = await get<FolderReport>(url).catch(() => null);
  if (!r) {
    if (!background && !tree.has(parent)) tree.set(parent, []);
  } else storeLevel(parent, r);
  renderNav();
}

function storeLevel(parent: string, r: FolderReport): void {
  tree.set(parent, r.children);
  if (!parent) {
    notBackedUp = r.not_backed_up ?? [];
    rootTotal = r.total;
    // An account removed since, or folders no longer sent to it: back to each folder's backup.
    if (fAcc && r.ready && !knownAccounts().includes(fAcc)) setAcc("");
  }
}

/** The open folder's ancestors stay unfolded, so the navigator shows where you are. */
function revealPath(): void {
  const parts = path.split("/").filter(Boolean);
  for (let i = 1; i < parts.length; i++) {
    const p = parts.slice(0, i).join("/");
    if (!expanded.has(p)) expanded.add(p);
  }
  saveExpanded();
}

function hrefOf(p: string): string {
  return `#/folders/f/${enc(p)}`;
}

function pctBadge(c: Parameters<typeof progressOf>[0]): string {
  const g = progressOf(c);
  if (!g) return `<span class="fo-pct" title="${esc(`Not sent to ${fAcc}`)}">–</span>`;
  const p = percent(g.done, g.files);
  return `${g.failed ? `<i class="fo-fail" title="${num(g.failed)} failed"></i>` : ""}<span class="fo-pct ${p === 100 ? "done" : ""}">${g.files ? `${p}%` : "–"}</span>`;
}

function navRow(n: NavNode, depth: number): string {
  const open = expanded.has(n.path);
  const ic: IconName = n.uploads ? "upload" : open && n.hasSub ? "folderOpen" : "folder";
  const kids = !(n.hasSub && open)
    ? ""
    : tree.has(n.path)
      ? nodesOf(n.path)
          .map((c) => navRow(c, depth + 1))
          .join("") || `<div class="fo-empty" style="--d:${depth + 1}">No folders inside</div>`
      : `<div class="fo-empty" style="--d:${depth + 1}"><span class="skeleton h-3 w-24"></span></div>`;
  return `<div class="fo-node" style="--d:${depth}">
    ${n.hasSub ? `<button type="button" class="fo-twisty" data-twisty="${esc(n.path)}" aria-expanded="${open}" aria-label="${open ? "Fold" : "Unfold"} ${esc(n.name)}">${icon("chevron", "size-3.5")}</button>` : `<span class="fo-twisty-gap"></span>`}
    <a class="fo-row" href="${hrefOf(n.path)}"${n.path === path ? ' aria-current="page"' : ""} title="${esc(n.path)}">${icon(ic)}<span class="min-w-0 flex-1 truncate">${esc(n.name)}</span>${agoTag(n.lastAdded)}${n.files ? pctBadge(n) : ""}</a>
  </div>${kids}`;
}

function renderNav(): void {
  const nav = document.getElementById("fo-nav");
  if (!nav) return;
  const top = nodesOf("");
  const prioRows = prio
    .map(
      (p, i) => `<li class="fo-prio">
        <span class="fo-prio-n">${i + 1}</span>
        <a class="min-w-0 flex-1 truncate" href="${hrefOf(p)}" title="${esc(p)}">${esc(prioName(p))}</a>
        <span class="fo-prio-tools">
          <button type="button" class="fo-mini" data-prio-move="${esc(p)}" data-dir="-1" ${i === 0 ? "disabled" : ""} aria-label="Earlier">${icon("up", "size-3.5")}</button>
          <button type="button" class="fo-mini" data-prio-move="${esc(p)}" data-dir="1" ${i === prio.length - 1 ? "disabled" : ""} aria-label="Later">${icon("down", "size-3.5")}</button>
          <button type="button" class="fo-mini" data-prio-remove="${esc(p)}" aria-label="Back to the usual order">${icon("x", "size-3.5")}</button>
        </span></li>`,
    )
    .join("");
  const accs = knownAccounts();
  const accSwitch =
    accs.length > 1
      ? `<label class="fo-acc"><span class="sr-only">Percentages for</span><select class="input sm" id="fo-acc">${[
          ["", "Each folder's backup"],
          ...accs.map((a) => [a, a]),
        ]
          .map(([v, l]) => `<option value="${esc(v!)}" ${v === fAcc ? "selected" : ""}>${esc(l!)}</option>`)
          .join("")}</select></label>`
      : "";
  html(
    nav,
    `${accSwitch}<a class="fo-row fo-all" href="#/folders"${path ? "" : ' aria-current="page"'}>${icon("library")}<span class="min-w-0 flex-1 truncate">Everything</span>${rootTotal ? agoTag(rootTotal.last_added) + pctBadge(rootTotal) : ""}</a>
     ${
       prio.length
         ? `<div class="fo-sec"><span>Back up first</span><span class="meta">${prio.length}/${PRIORITY_MAX}</span></div><ol class="fo-prios">${prioRows}</ol>`
         : ""
     }
     <div class="fo-sec"><span>Folders on ${esc(host())}</span></div>
     <div class="fo-tree">${
       tree.has("")
         ? top.map((n) => navRow(n, 0)).join("") ||
           `<div class="fo-empty" style="--d:0">No backed-up folders. <a class="link-btn" href="#/settings/folders">Choose folders</a></div>`
         : '<div class="space-y-2 px-2 py-1"><div class="skeleton h-4"></div><div class="skeleton h-4 w-3/4"></div><div class="skeleton h-4 w-2/3"></div></div>'
     }</div>
     ${
       notBackedUp.length
         ? `<details class="fo-skip"><summary>${icon("info", "size-3.5")}Not backed up <span class="meta">${notBackedUp.length}</span></summary>
         <ul>${notBackedUp.map((f) => `<li><a class="fo-row" href="#/folders/f/${encodeURIComponent(f.name)}">${icon("folder")}<span class="min-w-0 flex-1 truncate">${esc(f.name)}</span><span class="meta">${num(f.files)}</span></a></li>`).join("")}</ul>
         <a class="link-btn sm ml-6" href="#/settings/folders">Choose folders</a></details>`
         : ""
     }`,
  );
}

// ---- backup priority: folders whose files go to Google first (saved in settings)
let prio: string[] = [];

async function loadPrio(): Promise<void> {
  const st = await get<Settings>("settings").catch(() => null);
  prio = st?.priority ?? [];
  renderNav();
  if (report) renderHead(report);
}

async function savePrio(next: string[], message: string): Promise<void> {
  const before = prio;
  prio = next;
  renderNav();
  if (report) renderHead(report);
  try {
    const st = await post<Settings>("settings", { priority: next });
    prio = st.priority ?? next;
    toast(message, "good");
  } catch (e) {
    prio = before;
    toast((e as Error).message, "bad");
  }
  renderNav();
  if (report) renderHead(report);
}

/** "Photos/trip/day2" -> "day2". */
const prioName = lastPart;

const RANGES: [number, string][] = [
  [7, "7 d"],
  [30, "30 d"],
  [90, "90 d"],
  [366, "1 y"],
];
const FILE_STATES: Record<string, [string, Tone, IconName]> = {
  backed_up: ["In Google", "good", "cloud"],
  waiting: ["Waiting", "info", "hourglass"],
  failed: ["Failed", "bad", "error"],
};

/** Photo/video filter asked for in the address (#/folders/f/~video), used when the page opens. */
let linkedKind = "all";

function parse(args: string[]): void {
  const last = args[args.length - 1] ?? "";
  linkedKind = last === "~photo" || last === "~video" ? last.slice(1) : "all";
  if (linkedKind !== "all") args = args.slice(0, -1);
  path = args[0] === "f" || args[0] === "d" ? args.slice(1).map(decodeURIComponent).join("/") : "";
}

/** Backup apps name files by a long code; a date says more about such a photo. */
function fileTitle(f: { name: string; mtime: number; kind: string }): string {
  const base = fileName(f.name);
  return /^[0-9a-f]{24,}\./i.test(base) && f.mtime
    ? `${f.kind === "video" ? "Video" : "Photo"} · ${date(f.mtime, true)}`
    : base;
}

function rangeText(): string {
  return days === 366 ? "the last year" : `the last ${days} days`;
}

/** "Today", "Yesterday", else "Sun, Nov 9", for a day key ("2026-11-09"). */
function dayTitle(day: string, today: string, long = false): string {
  const t = Date.parse(`${today}T12:00:00Z`);
  const d = Date.parse(`${day}T12:00:00Z`);
  if (day === today) return "Today";
  if (t - d === 86400000) return "Yesterday";
  return new Date(d).toLocaleDateString([], {
    timeZone: "UTC",
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(long && day.slice(0, 4) !== today.slice(0, 4) ? { year: "numeric" } : {}),
  });
}

// ---- loading the open folder

/** `background`: the 30 s refresh. A failed refresh keeps the report on screen, and new synced or
 * failed counts reload the photos. */
async function load(background = false): Promise<void> {
  const mine = ++request;
  const url = `folders?group=folders&path=${encodeURIComponent(path)}&days=${days}&tz=${tz}`;
  const seen = opened ? peek<FolderReport>(url) : undefined;
  opened = false;
  if (seen) {
    report = seen;
    render();
  }
  const r = await get<FolderReport>(url).catch(() => null);
  if (mine !== request) return; // the user moved on
  if (background && !r && report) return;
  const before = report?.total;
  report = r;
  if (r && !path) {
    storeLevel("", r);
    renderNav();
  } else if (r) {
    tree.set(path, r.children); // its folders, for the navigator too
    renderNav();
  }
  render();
  if (background && before && r && (before.synced !== r.total.synced || before.failed !== r.total.failed))
    void loadFiles(true, true);
}

/** `keep`: reload what is shown (as many as are loaded, up to 600) without jumping back to the top. */
async function loadFiles(reset: boolean, keep = false): Promise<void> {
  if (!reset && (fLoading || !files || shownFiles.length >= files.total)) return;
  const mine = ++fRequest;
  fLoading = true;
  const offset = reset ? 0 : shownFiles.length;
  const limit = keep ? Math.min(600, Math.max(60, shownFiles.length)) : 60;
  try {
    const url = `folders/files?path=${encodeURIComponent(path)}&group=folders&status=${fStatus}&kind=${fKind}&sort=${fSort}&offset=${offset}&limit=${limit}&day=${encodeURIComponent(fDay)}&tz=${tz}${fAcc ? `&account=${encodeURIComponent(fAcc)}` : ""}`;
    const seen = reset && filesOpened ? peek<FolderFiles>(url) : undefined;
    if (reset) filesOpened = false;
    if (seen) {
      files = seen;
      shownFiles = seen.items;
      renderFiles();
    }
    const r = await get<FolderFiles>(url);
    if (mine !== fRequest) return;
    files = r;
    shownFiles = reset ? r.items : shownFiles.concat(r.items);
    if (reset) renderFiles();
    else appendFiles(r.items);
  } catch {
    if (reset && !keep)
      html($("#fo-files"), `<div class="empty">${icon("warn")}Couldn't load the files</div>`);
  } finally {
    if (mine === fRequest) fLoading = false;
  }
}

// ---- the open folder: header, numbers, tabs

function crumbs(): string {
  const parts = path.split("/").filter(Boolean);
  const steps = parts.map((part, i) => {
    const to = parts.slice(0, i + 1).join("/");
    return i === parts.length - 1
      ? `<span class="text-ink-2">${esc(part)}</span>`
      : `<a class="hover:text-ink" href="#/folders/f/${enc(to)}">${esc(part)}</a>`;
  });
  const sep = `<span aria-hidden="true">/</span>`;
  return [`<a class="hover:text-ink" href="#/folders">${esc(host())}</a>`, ...steps].join(sep);
}

function renderHead(r: FolderReport): void {
  const t = r.total;
  const g = progressOf(t);
  const done = g ? percent(g.done, g.files) : 0;
  const [waiting, failed] = waitingFailed(r);
  const title = !path ? "Everything" : lastPart(path);
  const ic: IconName = !path ? "library" : "folder";
  html(
    $("#fo-head"),
    `<div class="flex items-center gap-2">
       <button type="button" class="btn sm lg:hidden" id="fo-browse" aria-expanded="${navOpen}" aria-controls="fo-nav-wrap">${icon("layers")}Folders</button>
       <nav class="meta flex min-w-0 flex-wrap items-center gap-1.5" aria-label="Where you are">${crumbs()}</nav>
     </div>
     <div class="mt-4 flex items-start gap-3">
       <span class="grid size-10 shrink-0 place-items-center rounded-[10px] bg-panel-2 text-ink-2">${icon(ic, "size-5")}</span>
       <div class="min-w-0 flex-1">
         <h2 class="truncate text-xl leading-7 font-medium">${esc(title)}</h2>
         <p class="mt-0.5 text-xs text-ink-3">${num(t.files)} files · ${bytes(t.bytes)}${!path ? ` · every backed-up folder on ${esc(host())}` : ""}</p>
       </div>
       ${path ? backupPriorityControl(path, prio.indexOf(path), waiting + failed > 0) : ""}
     </div>
     <div class="mt-4 flex items-center gap-3"><div class="bar flex-1"><i style="width:${done}%"></i></div><span class="meta shrink-0">${g ? `${done}% in ${fAcc ? esc(short(fAcc)) : "Google Photos"}` : `Not sent to ${esc(short(fAcc))}`}</span></div>
     <div id="fo-sends"></div>
     ${t.accounts?.length ? `<div class="mt-3 border-t border-line">${accountBreakdown(t.accounts, { pick: fAcc && SEGMENTS.some(([k]) => k === fStatus) ? { account: fAcc, part: fStatus } : null })}</div>` : ""}`,
  );
  sendsDrawn = ""; // the header was just rebuilt, with an empty slot
  renderSends();
  renderStats(r, waiting, failed);
}

/** "Sends to": the accounts the open folder's top folder goes to, changed with a tap, and any check
 * of an account just added to it. Redrawn with every status (the check's progress). */
function renderSends(): void {
  const box = document.getElementById("fo-sends");
  if (!box) return;
  const s = status();
  const top = path.split("/")[0] ?? "";
  const accs = s?.accounts ?? [];
  let markup = "";
  if (!top) {
    markup = accs.length
      ? `<div class="sends-to"><span class="text-xs text-ink-3">Choose which accounts each folder goes to</span><button type="button" class="btn sm ml-auto" data-folders-sheet="">${icon("folder")}Where each folder goes</button></div>`
      : "";
  } else if (s && top in (s.folders ?? {})) {
    const order = foldersMap(s)[top] ?? [];
    const checks = accs.flatMap((a) => prechecksFor(s, a, top));
    markup = `<div class="sends-to" data-sends-to="${esc(top)}" data-order="${esc(JSON.stringify(order))}">
        <span class="mr-1 text-xs text-ink-3">${path === top ? "Sends to" : `Sends to (all of ${esc(top)})`}</span><span class="contents" data-sends-chips>${folderChips(accs, order)}</span>
      </div>${checks.length ? `<div class="mt-3">${precheckLines(s, checks, true)}</div>` : ""}`;
  }
  if (markup === sendsDrawn) return;
  sendsDrawn = markup;
  html(box, markup);
}

/** The five numbers. The first four filter the photos below; Last added opens the Added tab. */
function renderStats(r: FolderReport, waiting: number, failed: number): void {
  const t = r.total;
  const cell = (key: string, label: string, ic: IconName, value: string, sub: string, on: boolean) =>
    `<button type="button" class="card metric-card lift flex min-w-0 flex-col p-4 text-left" data-stat="${key}" aria-pressed="${on}">
      <span class="flex min-w-0 items-center gap-2"><span class="metric-icon shrink-0">${icon(ic)}</span><span class="label truncate">${esc(label)}</span></span>
      <span class="num mt-4 truncate text-2xl">${value}</span>
      <span class="mt-1 truncate text-xs text-ink-3">${sub}</span></button>`;
  html(
    $("#fo-stats"),
    cell("photo", "Photos", "photo", num(t.photos), bytes(t.photo_bytes), fKind === "photo") +
      cell("video", "Videos", "video", num(t.videos), bytes(t.video_bytes), fKind === "video") +
      cell(
        "waiting",
        "Waiting",
        "hourglass",
        num(waiting),
        waiting ? (fAcc ? `Not in ${esc(short(fAcc))} yet` : "Not in Google yet") : "Nothing waiting",
        fStatus === "waiting",
      ) +
      cell(
        "failed",
        "Failed",
        "error",
        failed ? `<span class="text-bad">${num(failed)}</span>` : "0",
        failed ? "Tap to see them" : "None",
        fStatus === "failed",
      ) +
      cell(
        "added",
        "Last added",
        "clock",
        t.last_added ? esc(ago(t.last_added)) : "–",
        t.last_added ? `${esc(date(t.last_added))} · ${num(t.new_day)} today` : "Nothing yet",
        false,
      ),
  );
}

function renderTabs(r: FolderReport | null): void {
  const inside = (r?.children ?? []).filter((c) => c.name).length;
  const count = (n: number | undefined) => (n == null ? "" : `<span class="meta">${num(n)}</span>`);
  const tabs: [Tab, string, IconName, string][] = [
    ["photos", "Photos", "library", count(files?.counts.all ?? r?.total.files)],
    ["folders", "Folders inside", "folder", count(inside)],
    ["added", "Added", "chart", r ? `<span class="meta">${num(r.total.new)}</span>` : ""],
  ];
  if (tab === "folders" && !inside) tab = "photos";
  html(
    $("#fo-tabs"),
    tabs
      .filter(([k]) => k !== "folders" || inside)
      .map(
        ([k, l, ic, n]) =>
          `<button type="button" role="tab" data-tab="${k}" aria-selected="${k === tab}" class="whitespace-nowrap max-sm:px-2">${icon(ic, "size-4 max-sm:hidden")}<span>${k === "folders" ? '<span class="sm:hidden">Folders</span><span class="max-sm:hidden">Folders inside</span>' : l}</span>${n}</button>`,
      )
      .join(""),
  );
  for (const k of ["photos", "folders", "added"] as Tab[])
    document.getElementById(`fo-tab-${k}`)?.toggleAttribute("hidden", k !== tab);
}

// ---- tab: folders inside

function rowHtml(c: FolderGroup): string {
  const g = progressOf(c);
  const done = g ? percent(g.done, g.files) : 0;
  const to = c.folders[0] ?? (path ? `${path}/${c.name}` : c.name);
  const href = hrefOf(to);
  const ic: IconName = c.uploads ? "upload" : "folder";
  const thumbs = (c.recent ?? [])
    .map(
      (n) =>
        `<span class="block size-10 overflow-hidden rounded-lg bg-panel-2">${thumb(n, "size-full object-cover")}</span>`,
    )
    .join("");
  return `<a href="${href}" class="row clickable flex-wrap gap-y-2">
    <span class="grid size-10 shrink-0 place-items-center rounded-[10px] bg-panel-2 text-ink-2">${icon(ic, "size-5")}</span>
    <div class="min-w-0 flex-1 basis-40">
      <div class="flex items-center gap-2"><span class="truncate text-sm font-medium">${esc(c.name)}</span>${c.new ? `<span class="meta" title="Added in ${rangeText()}">+${num(c.new)}</span>` : ""}${c.last_added ? `<span class="fo-ago ml-auto ${Date.now() / 1000 - c.last_added > 30 * 86400 ? "old" : ""}" title="${esc(`${date(c.last_added, true)} ${clock(c.last_added)}`)}">${esc(ago(c.last_added))}</span>` : ""}</div>
      <div class="truncate text-xs text-ink-3">${c.uploads ? "Sent from the Upload page" : `${num(c.photos)} photos · ${num(c.videos)} videos`}</div>
      ${
        g
          ? `<div class="mt-2 flex items-center gap-2"><div class="bar max-w-48 flex-1"><i style="width:${done}%"></i></div><span class="meta">${done}%${fAcc ? ` in ${esc(short(fAcc))}` : ""}</span>${g.failed ? `<span class="text-xs text-bad">${num(g.failed)} failed</span>` : ""}</div>`
          : `<div class="mt-2 text-xs text-ink-3">Not sent to ${esc(short(fAcc))}</div>`
      }
    </div>
    ${thumbs ? `<div class="hidden gap-2 md:flex">${thumbs}</div>` : ""}
    <div class="hidden w-24 text-right sm:block"><div class="text-sm tabular-nums">${num(c.files)}</div><div class="text-xs text-ink-3">${bytes(c.bytes)}</div></div>
    <span class="text-ink-3">${icon("chevron", "size-4")}</span>
  </a>`;
}

// How Folders inside is sorted, kept on this device.
const SORT_KEY = "gp.foldersInsideSort";
const INSIDE_SORTS: [string, string][] = [
  ["files", "Most files"],
  ["recent", "Recently added"],
  ["name", "Name"],
];
let insideSort = "files";
try {
  insideSort = localStorage.getItem(SORT_KEY) ?? "files";
} catch {
  /* private mode */
}

function renderInside(r: FolderReport): void {
  const rows = r.children.filter((c) => c.name);
  if (insideSort === "recent") rows.sort((a, b) => b.last_added - a.last_added);
  else if (insideSort === "name")
    rows.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  html(
    $("#fo-inside"),
    `<div class="flex items-center gap-2 border-b border-line px-4 py-2.5"><span class="meta">${num(rows.length)} folders</span>
       <select class="input sm ml-auto w-auto" id="fo-isort" aria-label="Sort folders">${INSIDE_SORTS.map(([k, l]) => `<option value="${k}" ${k === insideSort ? "selected" : ""}>${l}</option>`).join("")}</select></div>
     ${rows.map(rowHtml).join("")}`,
  );
}

// ---- tab: photos

function fileTile(f: FolderFiles["items"][number]): string {
  const [label, tone, ic] = FILE_STATES[f.state] ?? [f.state, "info", "info"];
  return `<figure class="ph-tile group" ${openAttrs(f.name, f.state === "failed")} title="${esc(`${fileTitle(f)}\n${label} · ${bytes(f.size)}${f.error ? `\n${f.error}` : ""}`)}">
    <div class="shimmer relative aspect-square overflow-hidden rounded-lg">${thumb(f.name, "fade-img size-full object-cover", f.mtime)}
      <span class="ph-state ${tone}" aria-label="${esc(label)}">${icon(ic, "size-3")}</span>
      ${f.kind === "video" ? `<span class="ph-video">${icon("play", "size-3")}</span>` : ""}</div>
    <figcaption class="mt-1 flex justify-between gap-1 px-0.5 text-[11px] text-ink-3 tabular-nums"><span class="keep-line truncate">${clock(f.added)}</span><span class="keep-line shrink-0">${bytes(f.size)}</span></figcaption></figure>`;
}

/** Newest or oldest first: a heading at each new day, like a photo library. */
const byDay = () => fSort === "new" || fSort === "old";

function tilesHtml(items: FolderFiles["items"], after?: FolderFiles["items"][number]): string {
  let last = after ? dayKey(after.added) : "";
  const today = report?.today ?? dayKey(Date.now() / 1000);
  return items
    .map((f) => {
      const day = dayKey(f.added);
      const head =
        byDay() && day !== last
          ? `<div class="ph-day" data-key="day:${day}">${esc(dayTitle(day, today, true))}</div>`
          : "";
      last = day;
      return head + fileTile(f);
    })
    .join("");
}

function moreHtml(): string {
  if (!files) return "";
  return shownFiles.length < files.total
    ? `<button class="btn" id="fo-more">${icon("plus")}Show more (${num(files.total - shownFiles.length)} left)</button>`
    : shownFiles.length > 60
      ? `<span class="meta">All ${num(files.total)} shown</span>`
      : "";
}

function renderFilesTools(): void {
  const c = files?.counts;
  const n = (k: keyof NonNullable<typeof c>) => (c ? ` <span class="meta">${num(c[k])}</span>` : "");
  html(
    $("#fo-files-tools"),
    `<div class="seg" id="fo-fstatus">${(fAcc && c?.uploaded != null
      ? [
          ["all", "All", n("all")],
          ["uploaded", "Uploaded", n("uploaded")],
          ["exists", "Already in Google", n("exists")],
          ["waiting", "New to sync", n("waiting")],
          ["failed", "Failed", n("failed")],
        ]
      : [
          ["all", "All", n("all")],
          ["backed_up", "In Google", n("backed_up")],
          ["waiting", "Waiting", n("waiting")],
          ["failed", "Failed", n("failed")],
        ]
    )
      .filter(([k]) => k !== "failed" || !c || c.failed || fStatus === "failed")
      .map(([k, l, x]) => `<button data-s="${k}" aria-pressed="${k === fStatus}">${l}${x}</button>`)
      .join("")}</div>
     <div class="seg" id="fo-fkind">${[
       ["all", "Both"],
       ["photo", "Photos"],
       ["video", "Videos"],
     ]
       .map(([k, l]) => `<button data-k="${k}" aria-pressed="${k === fKind}">${l}</button>`)
       .join("")}</div>
     ${fDay ? `<button type="button" class="chip" aria-pressed="true" id="fo-clear-day" title="Show every day">${icon("calendar")}Added ${esc(dayTitle(fDay, report?.today ?? "", true))}${icon("x")}</button>` : ""}
     <select class="input ml-auto w-auto" id="fo-fsort" aria-label="Sort">${[
       ["new", "Newest added"],
       ["old", "Oldest added"],
       ["size", "Largest"],
       ["name", "Name"],
     ]
       .map(([k, l]) => `<option value="${k}" ${k === fSort ? "selected" : ""}>${l}</option>`)
       .join("")}</select>`,
  );
}

function renderFiles(): void {
  renderFilesTools();
  renderTabs(report);
  if (report) renderStats(report, ...waitingFailed(report));
  const body = document.getElementById("fo-files");
  if (!body) return;
  const filtered = fStatus !== "all" || fKind !== "all" || fDay;
  const label = fAcc
    ? `<p class="mb-3 text-xs text-ink-3">The files ${esc(fAcc)} should get, and where each one is in it. <button type="button" class="link-btn" data-acc-clear>Show every file</button></p>`
    : "";
  html(
    body,
    label +
      (shownFiles.length
        ? `<div class="ph-grid" id="fo-grid" data-gallery>${tilesHtml(shownFiles)}</div>
       <div class="mt-4 flex min-h-10 justify-center" id="fo-sentinel">${moreHtml()}</div>`
        : `<div class="empty">${icon("library")}${filtered ? "Nothing matches these filters" : "No photos or videos here"}${filtered ? '<button type="button" class="btn sm mt-2" id="fo-reset">Show everything</button>' : ""}</div>`),
  );
  watchEnd();
}

function waitingFailed(r: FolderReport): [number, number] {
  if (fAcc) {
    const a = accOf(r.total.accounts);
    return a ? [a.waiting, a.failed] : [0, 0];
  }
  const failed = r.total.failed ?? 0;
  return [Math.max(0, r.total.files - r.total.synced - failed), failed];
}

function appendFiles(more: FolderFiles["items"]): void {
  const grid = document.getElementById("fo-grid");
  if (!grid) return renderFiles();
  grid.insertAdjacentHTML("beforeend", tilesHtml(more, shownFiles[shownFiles.length - more.length - 1]));
  const body = document.getElementById("fo-files");
  if (body) touched(body);
  const end = document.getElementById("fo-sentinel");
  if (end) end.innerHTML = moreHtml();
}

/** Load the next page shortly before the end of the grid scrolls into view. */
function watchEnd(): void {
  observer?.disconnect();
  const end = document.getElementById("fo-sentinel");
  if (!end) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) void loadFiles(false);
    },
    { root: document.getElementById("view"), rootMargin: "900px 0px" },
  );
  observer.observe(end);
}

// ---- tab: added (per day; picking a day filters the photos)

function dayStat(label: string, value: string, sub: string, tone: string): string {
  return `<div class="day-stat ${tone}"><div class="text-xs text-ink-3">${esc(label)}</div><div class="num text-xl">${value}</div>${sub ? `<div class="truncate text-xs text-ink-3">${esc(sub)}</div>` : ""}</div>`;
}

function renderAdded(r: FolderReport): void {
  let rows = r.daily.map((d) => ({ ...d }));
  if (days > 90) {
    const weeks: typeof rows = [];
    for (let end = rows.length; end > 0; end -= 7) {
      const chunk = rows.slice(Math.max(0, end - 7), end);
      weeks.unshift({
        day: chunk[0]!.day,
        photos: chunk.reduce((a, d) => a + d.photos, 0),
        videos: chunk.reduce((a, d) => a + d.videos, 0),
        backed_up: chunk.reduce((a, d) => a + d.backed_up, 0),
        waiting: chunk.reduce((a, d) => a + d.waiting, 0),
        failed: chunk.reduce((a, d) => a + d.failed, 0),
      });
    }
    rows = weeks;
  }
  const max = Math.max(1, ...rows.map((d) => d.photos + d.videos));
  const every = rows.length <= 10 ? 1 : rows.length <= 31 ? 5 : 10;
  const nice = (day: string, long = false) =>
    new Date(day + "T00:00:00").toLocaleDateString(
      [],
      long ? { weekday: "short", day: "numeric", month: "short" } : { day: "numeric", month: "short" },
    );
  const weekly = days > 90; // a bar per week: not a single day to pick
  const picked = fDay || r.today;
  const day = r.daily.find((x) => x.day === picked) ?? {
    day: picked,
    photos: 0,
    videos: 0,
    backed_up: 0,
    waiting: 0,
    failed: 0,
  };
  const added = day.photos + day.videos;
  html(
    $("#fo-added"),
    `<div class="flex flex-wrap items-center gap-2">
       <div class="min-w-0 flex-1"><div class="label">${fDay ? "Picked day" : "Today"}</div>
         <div class="mt-1 text-lg font-medium">${esc(dayTitle(picked, r.today, true))} <span class="meta">${num(added)} added</span></div></div>
       ${added && !weekly ? `<button type="button" class="btn sm" data-show-day="${picked}">${icon("library")}Show these ${num(added)}</button>` : ""}
       <div class="seg" id="fo-range">${RANGES.map(([n, l]) => `<button data-d="${n}" aria-pressed="${n === days}">${l}</button>`).join("")}</div>
     </div>
     <div class="even mt-4">
       ${dayStat("Photos", num(day.photos), "", "s1")}
       ${dayStat("Videos", num(day.videos), "", "s2")}
       ${dayStat("In Google", num(day.backed_up), added ? `${percent(day.backed_up, added)}% of the day` : "", "")}
       ${dayStat(day.failed ? "Failed" : "Waiting", num(day.failed || day.waiting), day.failed ? `${num(day.waiting)} waiting` : "", day.failed ? "bad" : "")}
     </div>
     <div class="cols mt-6 flex h-32 items-end gap-[3px] sm:h-40 ${fDay ? "has-pick" : ""}">${rows
       .map((d) => {
         const head = weekly ? `Week of ${nice(d.day)}` : nice(d.day, true);
         const tip = `${head}\nPhotos: ${num(d.photos)}\nVideos: ${num(d.videos)}\nIn Google: ${num(d.backed_up)}\nWaiting: ${num(d.waiting)}${d.failed ? `\nFailed: ${num(d.failed)}` : ""}${weekly ? "" : "\nTap to pick this day"}`;
         const on = !weekly && d.day === picked;
         const bars = `${d.videos ? `<div class="col-bar rounded-t-[4px] bg-s2" style="height:${(d.videos / max) * 100}%"></div>` : ""}
           ${d.photos ? `<div class="col-bar ${d.videos ? "" : "rounded-t-[4px]"} bg-s1" style="height:${(d.photos / max) * 100}%"></div>` : ""}
           ${d.photos || d.videos ? "" : `<div class="h-[2px] rounded bg-panel-2"></div>`}`;
         return weekly
           ? `<div class="col flex h-full flex-1 flex-col justify-end gap-[2px]" data-tip="${esc(tip)}">${bars}</div>`
           : `<button type="button" class="col flex h-full flex-1 flex-col justify-end gap-[2px]" data-day="${d.day}" aria-pressed="${on}" aria-label="${esc(`${nice(d.day, true)}: ${num(d.photos)} photos, ${num(d.videos)} videos`)}" data-tip="${esc(tip)}">${bars}</button>`;
       })
       .join("")}</div>
     <div class="mt-2 flex gap-[3px] text-[11px] text-ink-3">${rows
       .map(
         (d, i) =>
           `<div class="flex-1 text-center whitespace-nowrap ${!weekly && d.day === picked ? "text-ink" : ""}">${(rows.length - 1 - i) % every === 0 ? esc(nice(d.day)) : ""}</div>`,
       )
       .join("")}</div>
     <div class="mt-3 flex flex-wrap items-center gap-3 text-xs text-ink-3"><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s1"></i>Photos</span><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s2"></i>Videos</span><span class="ml-auto">${weekly ? "One bar per week. Pick 90 d or less to open a day" : `${num(r.total.new)} added in ${rangeText()} · tap a day to pick it`}</span></div>`,
  );
}

function render(): void {
  renderNav();
  const r = report;
  if (!r) {
    html($("#fo-head"), `<div class="empty">${icon("warn")}Couldn't load this folder</div>`);
    return;
  }
  if (!r.ready) {
    html(
      $("#fo-head"),
      `<div class="empty">${icon("loader", "spin")}Counting files: the first scan is still running</div>`,
    );
    return;
  }
  renderHead(r);
  renderTabs(r);
  renderInside(r);
  renderAdded(r);
}

function skeleton(): void {
  html(
    $("#fo-head"),
    '<div class="skeleton h-4 w-40"></div><div class="skeleton mt-4 h-7 w-56"></div><div class="skeleton mt-4 h-2"></div>',
  );
  html($("#fo-stats"), '<div class="card skeleton h-28"></div>'.repeat(5));
  html($("#fo-files-tools"), "");
  html(
    $("#fo-files"),
    `<div class="ph-grid">${'<div class="shimmer aspect-square rounded-lg"></div>'.repeat(12)}</div>`,
  );
}

function setNav(open: boolean): void {
  navOpen = open;
  $("#fo-nav-wrap").classList.toggle("open", open);
  document.getElementById("fo-browse")?.setAttribute("aria-expanded", String(open));
}

function open(): void {
  opened = true; // a folder seen before draws at once, then its fresh numbers
  filesOpened = true;
  files = null;
  shownFiles = [];
  fStatus = "all";
  // Sent here from Sync with an account's part picked ("New to sync" for two@…).
  try {
    const pick = sessionStorage.getItem(PICK_KEY);
    if (pick) {
      sessionStorage.removeItem(PICK_KEY);
      fAcc = localStorage.getItem(ACC_KEY) ?? "";
      if (fAcc) fStatus = pick;
    }
  } catch {
    /* private mode */
  }
  fKind = linkedKind;
  fDay = "";
  if (linkedKind !== "all") tab = "photos";
  observer?.disconnect();
  revealPath();
  setNav(!path); // small screens: the navigator is the first screen; a folder folds it away
  skeleton();
  renderTabs(null);
  renderNav();
  void loadPrio();
  void load();
  void loadFiles(true);
  // Unfolded folders the navigator hasn't read yet (after a reload).
  for (const p of expanded) if (!tree.has(p) && p !== path) void loadLevel(p, true);
}

/** Every percentage for this account ("" = each folder's backup). status: also filter the photos to
 * that part (null: keep the filter where it still means something). */
function pickAccount(acc: string, status: string | null): void {
  setAcc(acc);
  const accountOnly = fStatus === "uploaded" || fStatus === "exists";
  if (status !== null) fStatus = status;
  else if ((!acc && accountOnly) || (acc && fStatus === "backed_up")) fStatus = "all";
  renderNav();
  if (report) {
    renderHead(report);
    renderInside(report);
  }
  if (status !== null) {
    tab = "photos";
    renderTabs(report);
  }
  void loadFiles(true);
}

/** Switch to the Photos tab with a filter (from a number or the Added tab). */
function filterPhotos(next: { status?: string; kind?: string; day?: string }): void {
  if (next.status !== undefined) fStatus = next.status;
  if (next.kind !== undefined) fKind = next.kind;
  if (next.day !== undefined) fDay = next.day;
  tab = "photos";
  renderTabs(report);
  void loadFiles(true);
}

const folders: View = {
  subtitle: "Every backed-up folder, and how much of each is in Google Photos",
  mount(el, args) {
    opened = true; // the first load draws the last answer at once (peek), then the fresh one
    filesOpened = true;
    parse(args);
    el.innerHTML = `
    <div class="fo-layout">
      <aside class="fo-nav-wrap" id="fo-nav-wrap" aria-label="Folders"><nav class="fo-nav scroll-thin" id="fo-nav"></nav></aside>
      <div class="min-w-0">
        <section class="card mb-4 p-4 sm:p-5" id="fo-head"></section>
        <section class="stat-row" id="fo-stats"></section>
        <div class="page-tabs mb-4" role="tablist" aria-label="Folder" id="fo-tabs"></div>
        <section class="card" id="fo-tab-photos" role="tabpanel">
          <div class="flex flex-wrap items-center gap-2 border-b border-line p-3" id="fo-files-tools"></div>
          <div class="p-3" id="fo-files"></div>
        </section>
        <section class="card overflow-hidden" id="fo-tab-folders" role="tabpanel" hidden><div id="fo-inside"></div></section>
        <section class="card p-4 sm:p-5" id="fo-tab-added" role="tabpanel" hidden><div id="fo-added"></div></section>
      </div>
    </div>`;

    el.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      // navigator
      const tw = t.closest<HTMLElement>("[data-twisty]");
      if (tw) {
        const p = tw.dataset.twisty!;
        if (expanded.has(p)) expanded.delete(p);
        else {
          expanded.add(p);
          if (!tree.has(p)) void loadLevel(p);
        }
        saveExpanded();
        renderNav();
        return;
      }
      if (t.closest("#fo-browse")) return setNav(!navOpen);
      if (t.closest(".fo-row") && window.matchMedia("(max-width: 1023px)").matches) setNav(false);
      // tabs and numbers
      const tb = t.closest<HTMLElement>("[data-tab]");
      if (tb) {
        tab = tb.dataset.tab as Tab;
        renderTabs(report);
        return;
      }
      const st = t.closest<HTMLElement>("[data-stat]");
      if (st) {
        const k = st.dataset.stat!;
        if (k === "added") {
          tab = "added";
          renderTabs(report);
          return;
        }
        if (k === "photo" || k === "video") filterPhotos({ kind: fKind === k ? "all" : k });
        else filterPhotos({ status: fStatus === k ? "all" : k });
        return;
      }
      // Sends to: a tap turns an account on or off for this folder (or makes it main), saved at once
      const sends = t.closest<HTMLElement>("[data-sends-to]");
      if (sends) {
        let order: string[] = [];
        try {
          order = JSON.parse(sends.dataset.order || "[]") as string[];
        } catch {
          /* keep [] */
        }
        const next = orderAfterTap(t, order);
        if (!next) return;
        const top = sends.dataset.sendsTo ?? "";
        sends.dataset.order = JSON.stringify(next);
        const chips = sends.querySelector("[data-sends-chips]");
        if (chips) html(chips, folderChips(status()?.accounts ?? [], next));
        void saveFolders({ ...foldersMap(status()), [top]: next }).then(() => {
          sendsDrawn = "";
          void load(true);
        });
        return;
      }
      // what each account has: a part shows those files, for that account
      const seg = t.closest<HTMLElement>("[data-seg]");
      if (seg) {
        const [acc = "", part = "all"] = seg.dataset.seg!.split("|");
        const same = fAcc === acc && fStatus === part;
        pickAccount(acc, same ? "all" : part);
        return;
      }
      if (t.closest("[data-acc-clear]")) return pickAccount("", "all");
      // photos
      if (t.closest("#fo-more")) return void loadFiles(false);
      if (t.closest("#fo-reset")) return filterPhotos({ status: "all", kind: "all", day: "" });
      if (t.closest("#fo-clear-day")) {
        filterPhotos({ day: "" });
        if (report) renderAdded(report);
        return;
      }
      const s = t.closest<HTMLElement>("#fo-fstatus button");
      if (s) return filterPhotos({ status: s.dataset.s ?? "all" });
      const k = t.closest<HTMLElement>("#fo-fkind button");
      if (k) {
        filterPhotos({ kind: k.dataset.k ?? "all" });
        // Kept in the address (#/folders/f/Photos/~video), so a link or reload opens the same.
        const base = location.hash.replace(/\/~(photo|video)$/, "");
        const next = fKind === "all" ? base : `${base}/~${fKind}`;
        if (next !== location.hash) history.replaceState(history.state, "", next);
        linkedKind = fKind;
        return;
      }
      // added
      const range = t.closest<HTMLButtonElement>("#fo-range button");
      if (range) {
        days = Number(range.dataset.d);
        void load();
        return;
      }
      const show = t.closest<HTMLElement>("[data-show-day]");
      if (show) return filterPhotos({ day: show.dataset.showDay ?? "" });
      const dayButton = t.closest<HTMLElement>("[data-day]");
      if (dayButton) {
        fDay = dayButton.dataset.day === fDay ? "" : (dayButton.dataset.day ?? "");
        if (report) renderAdded(report);
        void loadFiles(true);
        return;
      }
      // priority
      const b = t.closest<HTMLElement>("[data-prio-toggle], [data-prio-move], [data-prio-remove]");
      if (!b) return;
      e.preventDefault();
      if (b.dataset.prioToggle) {
        const p = b.dataset.prioToggle;
        if (prio.includes(p))
          void savePrio(
            prio.filter((x) => x !== p),
            `${prioName(p)} goes in the usual order again`,
          );
        else if (prio.length >= PRIORITY_MAX) {
          toast(`Up to ${PRIORITY_MAX} folders can go first. Remove one from Back up first.`, "bad");
          if (report) renderHead(report); // the switch stays off
        } else void savePrio([...prio, p], `${prioName(p)} backs up first`);
      } else if (b.dataset.prioRemove) {
        const p = b.dataset.prioRemove;
        void savePrio(
          prio.filter((x) => x !== p),
          `${prioName(p)} goes in the usual order again`,
        );
      } else if (b.dataset.prioMove) {
        const i = prio.indexOf(b.dataset.prioMove);
        const j = i + Number(b.dataset.dir);
        if (i < 0 || j < 0 || j >= prio.length) return;
        const next = [...prio];
        [next[i], next[j]] = [next[j]!, next[i]!];
        void savePrio(next, "Back up first order saved");
      }
    });
    el.addEventListener("change", (e) => {
      const t = e.target as HTMLSelectElement;
      if (t.id === "fo-fsort") {
        fSort = t.value;
        void loadFiles(true);
      } else if (t.id === "fo-acc") {
        pickAccount(t.value, null);
      } else if (t.id === "fo-isort") {
        insideSort = t.value;
        try {
          localStorage.setItem(SORT_KEY, insideSort);
        } catch {
          /* private mode */
        }
        if (report) renderInside(report);
      }
    });
    if (!tree.has("")) void loadLevel("");
    open();
    refreshTimer = window.setInterval(() => {
      void load(true);
      if (path) void loadLevel("", true);
      for (const p of expanded) if (p !== path && tree.has(p)) void loadLevel(p, true);
    }, 30000);
  },
  route(args) {
    parse(args);
    open();
    document.getElementById("view")?.scrollTo({ top: 0 });
  },
  update() {
    renderSends();
  },
  unmount() {
    sendsDrawn = "";
    clearInterval(refreshTimer);
    request++;
    fRequest++;
    observer?.disconnect();
    observer = null;
  },
};

export default folders;
