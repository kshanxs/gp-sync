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
import { sparkline } from "../lib/charts";
import { backupPriorityControl, PRIORITY_MAX } from "../lib/backup-priority";
import { $, $$, esc, html, toast, touched } from "../lib/dom";
import { ago, bytes, clock, date, dayKey, fileName, num, pct } from "../lib/format";
import { icon, type IconName } from "../lib/icons";
import { host } from "../lib/store";
import { badge, cardHead, openAttrs, thumb, type Tone } from "../lib/ui";
import type { View } from "./view";

// #/folders              → one row per device (the folders phone backup apps make)
// #/folders/d/Photos/x   → one device: its photos and videos (and folders, if it has real ones)
// #/folders/f            → every top folder
// #/folders/f/Photos/x   → any folder: what's inside it, and its photos and videos

/** Whole percent; 99 until the very last file is in, so "100%" always means done. */
const percent = (part: number, whole: number) => {
  const p = Math.round(pct(part, whole));
  return p === 100 && part < whole ? 99 : p;
};

let group: "folders" | "devices" = "devices";
let path = "";
let days = 30;
let report: FolderReport | null = null;
let opened = false;
let filesOpened = false; // the same for the photos below
let request = 0;
let refreshTimer = 0;

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

/** Where a folder's files go: "Backup: me 80%", then "Also in: two 40%" per extra account. */
function accountChips(accs: FolderAccount[] | undefined): string {
  if (!accs?.length) return "";
  const chip = (a: FolderAccount) => {
    const p = percent(a.done, a.files);
    return `<span class="badge ${p === 100 ? "good" : ""}" title="${esc(`${a.main ? "Backup" : "Also in"} ${a.account}: ${num(a.done)} of ${num(a.files)} files`)}">${esc(a.account.split("@")[0]!)} ${p}%</span>`;
  };
  const main = accs.filter((a) => a.main);
  const also = accs.filter((a) => !a.main);
  return `<span class="flex min-w-0 flex-wrap items-center gap-1 text-xs text-ink-3">${main.length ? `<span>Backup</span>${main.map(chip).join("")}` : ""}${also.length ? `<span>${main.length ? "· " : ""}Also in</span>${also.map(chip).join("")}` : ""}</span>`;
}

/** A device's folders, from the device list ("Pixel" -> Photos/Pixel, Photos/Pixel (2)), so an open
 * device can link to its other folders. Kept for this tab, so a refresh still has them. */
const DEVICE_KEY = "gp.deviceFolders";
const deviceFolders = new Map<string, string[]>();

function rememberDevices(children: { name: string; folders: string[] }[]): void {
  for (const c of children) if (c.folders.length > 1) deviceFolders.set(c.name, c.folders);
  try {
    sessionStorage.setItem(DEVICE_KEY, JSON.stringify(Object.fromEntries(deviceFolders)));
  } catch {
    /* private mode, or storage full */
  }
}

try {
  const saved = JSON.parse(sessionStorage.getItem(DEVICE_KEY) || "{}") as Record<string, unknown>;
  for (const [name, folders] of Object.entries(saved))
    if (Array.isArray(folders) && folders.every((f) => typeof f === "string") && folders.length > 1)
      deviceFolders.set(name, folders);
} catch {
  /* ignore a bad saved list */
}

// ---- upload order: folders whose files go to Google first (saved in settings)
let prio: string[] = [];

async function loadPrio(): Promise<void> {
  const st = await get<Settings>("settings").catch(() => null);
  prio = st?.priority ?? [];
  renderPrio();
  if (report) render();
}

async function savePrio(next: string[], message: string): Promise<void> {
  const before = prio;
  prio = next;
  renderPrio();
  if (report) render();
  try {
    const st = await post<Settings>("settings", { priority: next });
    prio = st.priority ?? next;
    toast(message, "good");
  } catch (e) {
    prio = before;
    toast((e as Error).message, "bad");
  }
  renderPrio();
  if (report) render();
}

/** "Photos/I2220 (2)" -> "I2220", "Photos/trip/day2" -> "day2". */
function prioName(p: string): string {
  const parts = p.split("/");
  return parts.length === 2 ? deviceName(parts[1]!) : (parts.at(-1) ?? p);
}

function prioBadge(p: string): string {
  const i = prio.indexOf(p);
  return i < 0 ? "" : badge(`Priority #${i + 1}`, "good", "up");
}

function renderPrio(): void {
  const card = document.getElementById("fo-prio-card");
  if (!card) return;
  const show = prio.length > 0;
  card.classList.toggle("hidden", !show);
  if (!show) return;
  html(
    card,
    `${cardHead("Backup priority", "up", `<span class="text-xs text-ink-3">Preferred folders in the backup queue</span>`)}
     <ol class="divide-y divide-line">${prio
       .map(
         (p, i) => `<li class="flex items-center gap-3 px-4 py-2.5">
           <span class="grid size-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs tabular-nums">${i + 1}</span>
           <a class="min-w-0 flex-1" href="#/folders/${p.split("/").length === 2 ? "d" : "f"}/${p.split("/").map(encodeURIComponent).join("/")}"><span class="block truncate text-sm font-medium">${esc(prioName(p))}</span><span class="block truncate text-xs text-ink-3">${esc(p)}</span></a>
           <button class="btn icon sm" data-prio-move="${esc(p)}" data-dir="-1" ${i === 0 ? "disabled" : ""} aria-label="Earlier">${icon("up")}</button>
           <button class="btn icon sm" data-prio-move="${esc(p)}" data-dir="1" ${i === prio.length - 1 ? "disabled" : ""} aria-label="Later">${icon("down")}</button>
           <button class="btn icon sm" data-prio-remove="${esc(p)}" aria-label="Back to the usual order">${icon("x")}</button></li>`,
       )
       .join("")}</ol>
     <p class="border-t border-line px-4 py-2.5 text-xs text-ink-3">Others follow in the usual order. Up to ${PRIORITY_MAX} folders.</p>`,
  );
}

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
  group = args[0] === "f" ? "folders" : "devices";
  path = args[0] === "f" || args[0] === "d" ? args.slice(1).map(decodeURIComponent).join("/") : "";
}

/** A link inside the current view: devices stay devices, folders stay folders. */
function folderHref(p: string): string {
  const kind = group === "devices" ? "d" : "f";
  if (!p) return group === "devices" ? "#/folders" : "#/folders/f";
  return `#/folders/${kind}/${p.split("/").map(encodeURIComponent).join("/")}`;
}

/** "I2220 (2)" -> "I2220": a phone that paired again is still the same device. */
const deviceName = (folder: string) => folder.replace(/ \(\d+\)$/, "");

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

// ---- loading

/** `background`: the 30 s refresh. A failed refresh keeps the report on screen, and new synced or
 * failed counts reload the photos below. */
async function load(background = false): Promise<void> {
  const mine = ++request;
  const kind = path ? "folders" : group; // inside a device it is a folder like any other
  const url = `folders?group=${kind}&path=${encodeURIComponent(path)}&days=${days}&tz=${tz}`;
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
  if (r && group === "devices" && !path) rememberDevices(r.children);
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
    const url = `folders/files?path=${encodeURIComponent(path)}&group=${group}&status=${fStatus}&kind=${fKind}&sort=${fSort}&offset=${offset}&limit=${limit}&day=${encodeURIComponent(fDay)}&tz=${tz}`;
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

// ---- top bar

function renderBar(): void {
  $$("button", $("#fo-group")).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.g === group)));
  const sep = `<span class="text-ink-3">${icon("chevron", "size-3.5")}</span>`;
  const parts = path.split("/").filter(Boolean);
  // A device starts at its folder (Photos/iPhone); show it by name, then what's inside it.
  const from = group === "devices" ? Math.min(2, parts.length) : 0;
  const steps = parts.slice(from).map((part, i, all) => {
    const to = parts.slice(0, from + i + 1).join("/");
    return i === all.length - 1
      ? `<span class="font-medium">${esc(part)}</span>`
      : `<a class="link-btn" href="${folderHref(to)}">${esc(part)}</a>`;
  });
  if (group === "devices" && from) {
    const dev = deviceName(parts[from - 1] ?? "");
    steps.unshift(
      steps.length
        ? `<a class="link-btn" href="${folderHref(parts.slice(0, from).join("/"))}">${icon("phone", "size-4")}${esc(dev)}</a>`
        : `<span class="flex items-center gap-1.5 font-medium">${icon("phone", "size-4 text-ink-3")}${esc(dev)}</span>`,
    );
  }
  const root =
    group === "devices"
      ? path
        ? `<a class="link-btn" href="#/folders">${icon("devices", "size-4")}Devices</a>`
        : `<span class="flex items-center gap-1.5 font-medium">${icon("devices", "size-4 text-ink-3")}Devices on ${esc(host())}</span>`
      : path
        ? `<a class="link-btn" href="#/folders/f">${icon("folder", "size-4")}${esc(host())}</a>`
        : `<span class="flex items-center gap-1.5 font-medium">${icon("folder", "size-4 text-ink-3")}Folders on ${esc(host())}</span>`;
  const crumbs = [root, ...steps].join(sep);
  html($("#fo-crumbs"), `<div class="flex min-w-0 flex-wrap items-center gap-1.5 text-sm">${crumbs}</div>`);
}

// ---- the main card: the folder's totals, the chosen day (today unless a bar was picked), the chart

function stat(label: string, value: string, sub = ""): string {
  return `<div class="min-w-0"><div class="label truncate">${esc(label)}</div><div class="num mt-1 truncate text-lg sm:text-xl">${value}</div>${sub ? `<div class="truncate text-xs text-ink-3">${sub}</div>` : ""}</div>`;
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

function renderSummary(r: FolderReport): void {
  const t = r.total;
  const done = percent(t.synced, t.files);
  const failed = t.failed ?? 0;
  const waiting = Math.max(0, t.files - t.synced - failed);
  const parts = path.split("/").filter(Boolean);
  const siblings =
    group === "devices" && parts.length === 2
      ? (deviceFolders.get(deviceName(parts[1]!)) ?? []).filter((f) => f !== path)
      : [];
  const title = !path
    ? group === "devices"
      ? "All devices"
      : "All folders"
    : group === "devices" && parts.length === 2
      ? deviceName(parts[1]!)
      : (parts.at(-1) ?? path);
  html(
    $("#fo-summary"),
    `<div class="flex flex-wrap items-center gap-x-3 gap-y-1"><span class="truncate text-base font-medium">${esc(title)}</span>
       <span class="text-xs text-ink-3">${num(t.files)} files · ${bytes(t.bytes)}</span>
       ${path ? prioBadge(path) : ""}
       <span class="ml-auto text-xs text-ink-3 tabular-nums">${done}% in Google Photos</span></div>
     <div class="bar mt-2"><i style="width:${done}%"></i></div>
     ${t.accounts?.length ? `<div class="mt-2">${accountChips(t.accounts)}</div>` : ""}
     ${siblings.length ? `<div class="mt-2 text-xs text-ink-3">Also from this device: ${siblings.map((f) => `<a class="link-btn" href="${folderHref(f)}">${esc(f)}</a>`).join(" · ")}</div>` : ""}
     <div class="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
       ${stat("Photos", num(t.photos), bytes(t.photo_bytes))}
       ${stat("Videos", num(t.videos), bytes(t.video_bytes))}
       ${stat("Last added", t.last_added ? ago(t.last_added) : "–", t.last_added ? date(t.last_added, true) : "Nothing yet")}
       ${stat("In Google", num(t.synced), `${done}% of the files`)}
       ${stat("Waiting", num(waiting), waiting ? "Not sent yet" : "Nothing waiting")}
       ${stat("Failed", failed ? `<span class="text-bad">${num(failed)}</span>` : "0", failed ? '<a class="link-btn" href="#/activity/failed">See why</a>' : "None")}
     </div>
     ${path ? backupPriorityControl(path, prio.indexOf(path), waiting + failed > 0) : ""}`,
  );
}

/** The chosen day's numbers, above the chart: today unless a bar was picked. */
function renderDay(r: FolderReport): void {
  const day = fDay || r.today;
  const d = r.daily.find((x) => x.day === day) ?? {
    day,
    photos: 0,
    videos: 0,
    backed_up: 0,
    waiting: 0,
    failed: 0,
  };
  const added = d.photos + d.videos;
  const inGoogle = percent(d.backed_up, added);
  html(
    $("#fo-day"),
    `<div class="flex flex-wrap items-center gap-2">
       <div class="min-w-0 flex-1"><div class="label">${fDay ? "Picked day" : "Added today"}</div>
         <div class="mt-0.5 flex flex-wrap items-baseline gap-x-2"><span class="text-lg font-medium">${esc(dayTitle(day, r.today, true))}</span>${day === r.today ? `<span class="text-xs text-ink-3">${esc(dayTitle(day, "", true))}</span>` : ""}</div></div>
       ${fDay ? `<button class="btn sm" id="fo-clear-day">${icon("calendar")}Back to today</button>` : ""}
       <div class="seg" id="fo-range">${RANGES.map(([n, l]) => `<button data-d="${n}" aria-pressed="${n === days}" class="max-sm:px-2.5">${l}</button>`).join("")}</div>
     </div>
     <div class="even mt-3">
       ${dayStat("Added", num(added), added ? `${inGoogle}% in Google` : "Nothing", "")}
       ${dayStat("Photos", num(d.photos), "", "s1")}
       ${dayStat("Videos", num(d.videos), "", "s2")}
       ${dayStat("In Google", num(d.backed_up), "", "good")}
       ${dayStat(d.failed ? "Failed" : "Waiting", num(d.failed || d.waiting), d.failed ? `${num(d.waiting)} waiting` : "", d.failed ? "bad" : "")}
     </div>`,
  );
}

function dayStat(label: string, value: string, sub: string, tone: string): string {
  return `<div class="day-stat ${tone}"><div class="text-[11px] text-ink-3">${esc(label)}</div><div class="num text-xl">${value}</div>${sub ? `<div class="truncate text-[11px] text-ink-3">${esc(sub)}</div>` : ""}</div>`;
}

// ---- devices and subfolders

function rowTarget(c: FolderGroup): string {
  // Several folders (the phone paired again): each one is its own link, not a jump to the first.
  if (group === "devices" && !path) return c.folders.length === 1 ? folderHref(c.folders[0]!) : "";
  const to = c.folders[0] ?? path;
  // A phone's folder opens as that device, whichever way it was reached.
  if (c.device) return `#/folders/d/${to.split("/").map(encodeURIComponent).join("/")}`;
  return folderHref(to);
}

function rowHtml(c: FolderGroup): string {
  const done = percent(c.synced, c.files);
  const devices = group === "devices" && !path;
  const ic = devices ? (c.uploads ? "upload" : "phone") : c.device ? "phone" : "folder";
  const sub = devices
    ? c.uploads
      ? "Sent from the Upload page"
      : c.folders.length > 1
        ? c.folders
            .map(
              (f) =>
                `<a class="link-btn" href="${folderHref(f)}" title="${esc(f)}">${esc(f.split("/").pop() ?? f)}</a>`,
            )
            .join(" · ")
        : esc(c.folders[0] ?? "")
    : `${c.device ? "Device · " : ""}${num(c.photos)} photos · ${num(c.videos)} videos`;
  const href = rowTarget(c);
  const tag = href ? "a" : "div";
  const thumbs = (c.recent ?? [])
    .map(
      (n) =>
        `<span class="block size-10 overflow-hidden rounded-lg bg-panel-2">${thumb(n, "size-full object-cover")}</span>`,
    )
    .join("");
  return `<${tag} ${href ? `href="${href}"` : ""} class="row ${href ? "clickable" : ""} flex-wrap gap-y-2 px-4 py-3">
    <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-panel-2 text-ink-2">${icon(ic, "size-5")}</span>
    <div class="min-w-0 flex-1 basis-40">
      <div class="flex items-center gap-2"><span class="truncate text-sm font-medium">${esc(c.name)}</span>${c.folders.length === 1 ? prioBadge(c.folders[0]!) : ""}${c.new ? badge(`+${num(c.new)} new`, "info") : ""}</div>
      <div class="truncate text-xs text-ink-3">${sub}</div>
      ${c.accounts?.length ? `<div class="mt-1">${accountChips(c.accounts)}</div>` : ""}
      <div class="mt-1.5 flex items-center gap-2"><div class="bar h-1.5 max-w-48 flex-1"><i style="width:${done}%"></i></div><span class="text-xs text-ink-3 tabular-nums">${done}%</span></div>
    </div>
    ${thumbs ? `<div class="hidden gap-1.5 md:flex">${thumbs}</div>` : ""}
    <div class="hidden w-28 text-right text-sm tabular-nums sm:block">${num(c.files)}<div class="text-xs text-ink-3">${bytes(c.bytes)}</div></div>
    <div class="hidden w-28 lg:block" data-tip="${esc(`${num(c.new)} added in ${rangeText()}\n${num(c.new_week)} this week\nLast added ${c.last_added ? ago(c.last_added) : "never"}`)}">${sparkline(c.series, c.new ? "var(--s1)" : "var(--line)", 28)}</div>
    <span class="text-ink-3">${href ? icon("chevron", "size-4") : ""}</span>
  </${tag}>`;
}

function renderList(r: FolderReport): void {
  const rows = r.children.filter((c) => c.name); // loose files are in the photos below
  const card = $("#fo-list-card");
  card.classList.toggle("hidden", !rows.length && !!path); // a folder without folders inside: just its photos
  html(
    $("#fo-list-head"),
    cardHead(
      group === "devices" && !path ? "Devices" : path ? "Folders inside" : "Folders",
      group === "devices" && !path ? "devices" : "layers",
      `<span class="text-xs text-ink-3">${num(rows.length)}</span>`,
    ),
  );
  const skipped = !path ? (r.not_backed_up ?? []) : [];
  const note = skipped.length
    ? `<div class="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 py-3 text-xs text-ink-3">${icon("info", "size-3.5")}<span>Not backed up, so not counted here: ${skipped
        .map(
          (f) =>
            `<a class="link-btn" href="#/folders/f/${encodeURIComponent(f.name)}">${esc(f.name)}</a> (${num(f.files)})`,
        )
        .join(", ")}.</span><a class="link-btn" href="#/settings/folders">Choose folders</a></div>`
    : "";
  html(
    $("#fo-list"),
    rows.length
      ? rows.map(rowHtml).join("") + note
      : `<div class="empty">${icon(group === "devices" ? "phone" : "folder")}${
          group === "devices" && !path
            ? "No device folders yet. Phone backup apps make one folder per device inside a backed-up folder (for example Photos/iPhone)."
            : "No folders here"
        }</div>`,
  );
}

// ---- photos and videos of the open folder

function fileTile(f: FolderFiles["items"][number]): string {
  const [label, tone, ic] = FILE_STATES[f.state] ?? [f.state, "info", "info"];
  return `<figure class="ph-tile group" ${openAttrs(f.name, f.state === "failed")} title="${esc(`${fileTitle(f)}\n${label} · ${bytes(f.size)}${f.error ? `\n${f.error}` : ""}`)}">
    <div class="shimmer relative aspect-square overflow-hidden rounded-lg">${thumb(f.name, "zoom-img fade-img size-full object-cover", f.mtime)}
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
      ? `<span class="text-xs text-ink-3">All ${num(files.total)} shown</span>`
      : "";
}

function renderFilesHead(): void {
  const c = files?.counts;
  const n = (k: keyof NonNullable<typeof c>) => (c ? ` · ${num(c[k])}` : "");
  html(
    $("#fo-files-tools"),
    `<div class="seg" id="fo-fstatus">${[
      ["all", "All", n("all")],
      ["backed_up", "In Google", n("backed_up")],
      ["waiting", "Waiting", n("waiting")],
      ["failed", "Failed", n("failed")],
    ]
      .filter(([k]) => k !== "failed" || !c || c.failed)
      .map(([k, l, x]) => `<button data-s="${k}" aria-pressed="${k === fStatus}">${l}${x}</button>`)
      .join("")}</div>
     <div class="seg" id="fo-fkind">${[
       ["all", "Both"],
       ["photo", "Photos"],
       ["video", "Videos"],
     ]
       .map(([k, l]) => `<button data-k="${k}" aria-pressed="${k === fKind}">${l}</button>`)
       .join("")}</div>
     <select class="input w-auto" id="fo-fsort">${[
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
  renderFilesHead();
  const body = document.getElementById("fo-files");
  if (!body) return;
  html(
    body,
    shownFiles.length
      ? `<div class="ph-grid" id="fo-grid" data-gallery>${tilesHtml(shownFiles)}</div>
       <div class="mt-4 flex min-h-10 justify-center" id="fo-sentinel">${moreHtml()}</div>`
      : `<div class="empty">${icon("library")}${fStatus === "all" && fKind === "all" ? "No photos or videos here" : "Nothing matches"}</div>`,
  );
  watchEnd();
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

// ---- chart

/** Stacked columns of photos and videos added; a year is shown per week so bars stay readable. */
function renderChart(r: FolderReport): void {
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
  html(
    $("#fo-chart"),
    `<div class="cols flex h-24 items-end gap-[3px] sm:h-32 ${fDay ? "has-pick" : ""}">${rows
      .map((d) => {
        const head = weekly ? `Week of ${nice(d.day)}` : nice(d.day, true);
        const tip = `${head}\nPhotos: ${num(d.photos)}\nVideos: ${num(d.videos)}\nIn Google: ${num(d.backed_up)}\nWaiting: ${num(d.waiting)}${d.failed ? `\nFailed: ${num(d.failed)}` : ""}${weekly ? "" : "\nTap to see this day"}`;
        const on = !weekly && d.day === picked;
        const bars = `${d.videos ? `<div class="col-bar rounded-t-[4px] bg-s2" style="height:${(d.videos / max) * 100}%"></div>` : ""}
          ${d.photos ? `<div class="col-bar ${d.videos ? "" : "rounded-t-[4px]"} bg-s1" style="height:${(d.photos / max) * 100}%"></div>` : ""}
          ${d.photos || d.videos ? "" : `<div class="h-[2px] rounded bg-panel-2"></div>`}`;
        return weekly
          ? `<div class="col flex h-full flex-1 flex-col justify-end gap-[2px]" data-tip="${esc(tip)}">${bars}</div>`
          : `<button type="button" class="col flex h-full flex-1 flex-col justify-end gap-[2px]" data-day="${d.day}" aria-pressed="${on}" aria-label="${esc(`${nice(d.day, true)}: ${num(d.photos)} photos, ${num(d.videos)} videos`)}" data-tip="${esc(tip)}">${bars}</button>`;
      })
      .join("")}</div>
    <div class="mt-1.5 flex gap-[3px] text-[11px] text-ink-3">${rows
      .map(
        (d, i) =>
          `<div class="flex-1 text-center whitespace-nowrap ${!weekly && d.day === picked ? "font-medium text-ink" : ""}">${(rows.length - 1 - i) % every === 0 ? esc(nice(d.day)) : ""}</div>`,
      )
      .join("")}</div>
    <div class="mt-2 flex items-center gap-3 text-xs text-ink-3"><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s1"></i>Photos</span><span class="flex items-center gap-1.5"><i class="size-2 rounded-full bg-s2"></i>Videos</span><span class="ml-auto">${weekly ? "One bar per week: a trend only. Pick 90 d or less to open a day" : "Tap a day to see it"}</span></div>`,
  );
  renderDay(r);
}

function render(): void {
  renderBar();
  const r = report;
  if (!r) {
    html($("#fo-summary"), `<div class="empty">${icon("warn")}Couldn't load this folder</div>`);
    return;
  }
  if (!r.ready) {
    html(
      $("#fo-summary"),
      `<div class="empty">${icon("loader", "spin")}Counting files: the first scan is still running</div>`,
    );
    return;
  }
  renderSummary(r);
  renderChart(r);
  renderList(r);
}

function skeleton(): void {
  html(
    $("#fo-summary"),
    '<div class="skeleton h-5 w-40"></div><div class="skeleton mt-3 h-2"></div><div class="skeleton mt-4 h-12"></div>',
  );
  html($("#fo-list"), '<div class="skeleton m-4 h-12"></div>'.repeat(3));
  html($("#fo-day"), '<div class="skeleton h-5 w-32"></div><div class="skeleton mt-3 h-14"></div>');
  html($("#fo-chart"), '<div class="skeleton h-28"></div>');
  html($("#fo-files-tools"), "");
  html(
    $("#fo-files"),
    `<div class="ph-grid">${'<div class="shimmer aspect-square rounded-lg"></div>'.repeat(12)}</div>`,
  );
}

function filesSub(): string {
  if (fDay)
    return `Added ${dayTitle(fDay, report?.today ?? "", true)}${path ? " · including the folders inside" : ""}`;
  if (path) return "Including the folders inside";
  if (group === "devices") return "Newest first, from every device";
  return "Newest first, from every backed-up folder";
}

function open(): void {
  opened = true; // a folder seen before draws at once, then its fresh numbers
  filesOpened = true;
  files = null;
  shownFiles = [];
  fStatus = "all";
  fKind = linkedKind;
  fDay = "";
  observer?.disconnect();
  skeleton();
  renderBar();
  renderPrio();
  void loadPrio();
  html($("#fo-files-sub"), filesSub());
  void load();
  void loadFiles(true);
  // #/folders/f/~photo (from the Sync page's Photos and videos): straight to the photos.
  if (linkedKind !== "all")
    requestAnimationFrame(() =>
      document.getElementById("fo-files-card")?.scrollIntoView({ block: "start", behavior: "smooth" }),
    );
}

const folders: View = {
  subtitle: "Files per device and folder",
  mount(el, args) {
    opened = true; // the first load draws the last answer at once (peek), then the fresh one
    filesOpened = true;
    parse(args);
    el.innerHTML = `
    <section class="card mb-4 flex flex-wrap items-center gap-2 p-3">
      <div class="seg" id="fo-group"><button data-g="devices">${icon("devices")}Devices</button><button data-g="folders">${icon("folder")}Folders</button></div>
      <div class="order-first w-full min-w-0 px-1 pb-1 sm:order-none sm:w-auto sm:flex-1 sm:pb-0" id="fo-crumbs"></div>
    </section>
    <section class="card mb-4 overflow-hidden">
      <div class="p-4" id="fo-summary"></div>
      <div class="border-t border-line p-4" id="fo-day"></div>
      <div class="px-4 pb-4" id="fo-chart"></div>
    </section>
    <section class="card mb-4 hidden overflow-hidden" id="fo-prio-card"></section>
    <section class="card mb-4 overflow-hidden" id="fo-list-card"><div id="fo-list-head"></div><div class="divide-y divide-line" id="fo-list"></div></section>
    <section class="card mb-4" id="fo-files-card">
      ${cardHead("Photos and videos", "library", `<span class="hidden text-xs text-ink-3 sm:inline" id="fo-files-sub"></span>`)}
      <div class="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3" id="fo-files-tools"></div>
      <div class="p-3" id="fo-files"></div>
    </section>`;
    $("#fo-group").addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (b) location.hash = b.dataset.g === "devices" ? "#/folders" : "#/folders/f";
    });

    el.addEventListener("click", (e) => {
      const range = (e.target as HTMLElement).closest<HTMLButtonElement>("#fo-range button");
      if (range) {
        days = Number(range.dataset.d);
        void load();
        return;
      }
      // A bar picks its day (the card above shows it, the photos below are that day's); the same
      // bar again, or Back to today, goes back.
      const dayButton = (e.target as HTMLElement).closest<HTMLElement>("[data-day], #fo-clear-day");
      if (dayButton) {
        fDay = dayButton.dataset.day === fDay ? "" : (dayButton.dataset.day ?? "");
        html($("#fo-files-sub"), filesSub());
        if (report) renderChart(report);
        void loadFiles(true);
        return;
      }
      const b = (e.target as HTMLElement).closest<HTMLElement>(
        "[data-prio-toggle], [data-prio-move], [data-prio-remove]",
      );
      if (!b) return;
      e.preventDefault();
      if (b.dataset.prioToggle) {
        const p = b.dataset.prioToggle;
        const focused = document.activeElement === b;
        if (prio.includes(p))
          void savePrio(
            prio.filter((x) => x !== p),
            `Backup priority removed for ${prioName(p)}`,
          );
        else if (prio.length >= PRIORITY_MAX) {
          toast(`Up to ${PRIORITY_MAX} folders can go first. Remove one from Backup priority first.`, "bad");
          if (report) render(); // the switch stays off
        } else void savePrio([...prio, p], `Backup priority set for ${prioName(p)}`);
        if (focused) document.querySelector<HTMLButtonElement>("#fo-summary [data-prio-toggle]")?.focus();
      } else if (b.dataset.prioRemove) {
        const p = b.dataset.prioRemove;
        void savePrio(
          prio.filter((x) => x !== p),
          `Backup priority removed for ${prioName(p)}`,
        );
      } else if (b.dataset.prioMove) {
        const i = prio.indexOf(b.dataset.prioMove);
        const j = i + Number(b.dataset.dir);
        if (i < 0 || j < 0 || j >= prio.length) return;
        const next = [...prio];
        [next[i], next[j]] = [next[j]!, next[i]!];
        void savePrio(next, "Backup priority order saved");
      }
    });
    const card = $("#fo-files-card");
    card.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
      if (!b) return;
      if (b.id === "fo-more") void loadFiles(false);
      else if (b.dataset.s) {
        fStatus = b.dataset.s;
        void loadFiles(true);
      } else if (b.dataset.k) {
        fKind = b.dataset.k;
        // Kept in the address (#/folders/f/Photos/~video), so a link or reload opens the same.
        const base = location.hash.replace(/\/~(photo|video)$/, "");
        const next = fKind === "all" ? base : `${base}/~${fKind}`;
        if (next !== location.hash) history.replaceState(history.state, "", next);
        linkedKind = fKind;
        void loadFiles(true);
      }
    });
    card.addEventListener("change", (e) => {
      const t = e.target as HTMLSelectElement;
      if (t.id === "fo-fsort") {
        fSort = t.value;
        void loadFiles(true);
      }
    });
    open();
    refreshTimer = window.setInterval(() => void load(true), 30000);
  },
  route(args) {
    parse(args);
    open();
    if (linkedKind === "all") document.getElementById("view")?.scrollTo({ top: 0 });
  },
  unmount() {
    clearInterval(refreshTimer);
    request++;
    fRequest++;
    observer?.disconnect();
    observer = null;
  },
};

export default folders;
