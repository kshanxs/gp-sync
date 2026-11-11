import {
  get,
  post,
  type FreedReport,
  type ReclaimPrefs,
  type ReclaimProgress,
  type StorageItem,
  type StorageReport,
  peek,
} from "../lib/api";
import { $, esc, html, toast } from "../lib/dom";
import { ago, bytes, date, duration, num, pct, remaining, speed } from "../lib/format";
import { icon } from "../lib/icons";
import { host, setPageStatus, setPauseControl, setRefreshControl, status } from "../lib/store";
import { accountChips, freeingAccounts, openingAccount, rememberAccount } from "../lib/account-menu";
import { columns } from "../lib/charts";
import { confirmDialog } from "../lib/dialog";
import { atOnceText } from "../lib/pace";
import { openMoveOptions } from "../lib/pace-sheet";
import { workPanel, type WorkItem } from "../lib/work-panel";
import { accountLine, badge, cardHead, checkNote, sharp, tile, type Tone } from "../lib/ui";
import type { View } from "./view";

// Free up Google storage: items that count against it are downloaded (and proven to be Google's
// own copy), deleted in Google Photos (and its Trash), and uploaded again for free. Several items
// move at once per account, and every account works on its own.

let account = "";
let report: StorageReport | null = null;
let opened = false;
let freed: FreedReport | null = null;
/** The list of what counts, or the Freed page (#/storage/<account>/freed). */
let sub: "list" | "freed" | "time" = "list";
let timer = 0;
let ticks = 0;
let request = 0;
let shown = 50;
let freedShown = 60;
/** Which items the list and the Free buttons work on. */
let kind: "all" | "photo" | "video" = "all";
const picked = new Set<string>();
const KIND_NAME = { all: "photos and videos", photo: "photos", video: "videos" } as const;
const ACTIVE = ["queued", "downloaded", "in_google", "checking"];

async function load(): Promise<void> {
  const mine = ++request;
  const url = `storage?account=${encodeURIComponent(account)}&kind=${kind}`;
  const seen = opened ? peek<StorageReport>(url) : undefined;
  opened = false;
  if (seen) {
    report = seen;
    render();
  }
  const r = await get<StorageReport>(url).catch(() => null);
  if (mine !== request) return;
  report = r;
  if (r && !account) account = r.account;
  if (sub !== "list" && account) {
    freed = await get<FreedReport>(`storage/freed?account=${encodeURIComponent(account)}`).catch(() => freed);
    if (mine !== request) return;
  }
  rememberAccount("storage", account, sub === "list" ? "" : sub);
  render();
}

async function act(path: string, body: Record<string, unknown>, done?: string): Promise<void> {
  try {
    await post(`storage/${path}`, { account, ...body });
    if (done) toast(done, "good");
  } catch (e) {
    toast((e as Error).message, "bad");
  }
  void load();
}

/** Save Free storage choices (Settings: reclaim) and show them at once. */
async function savePrefs(change: Partial<ReclaimPrefs>, done?: string): Promise<void> {
  try {
    await post("settings", { reclaim: change });
    if (report?.prefs) report.prefs = { ...report.prefs, ...change };
    if (done) toast(done, "good");
  } catch (e) {
    toast((e as Error).message, "bad");
  }
  void load();
}

const thumbOf = (key: string, kind: string, cls = "thumb") => {
  const src = `api/storage/thumb?account=${encodeURIComponent(account)}&key=${encodeURIComponent(key)}`;
  return `<img class="${cls}" loading="lazy" decoding="async" alt="" data-kind="${kind}" src="${src}"${cls.includes("size-full") ? sharp(src) : ""}>`;
};

const STEP = {
  downloading: { label: "Downloading", ic: "download" },
  local: { label: "Copying from this server", ic: "server" }, // named with host() where shown
  deleting: { label: "Replacing in Google", ic: "loader" },
  uploading: { label: "Uploading", ic: "upload" },
} as const;

function stateOf(
  i: StorageItem,
  cur: ReclaimProgress | undefined,
): { label: string; tone: Tone; ic: Parameters<typeof icon>[0] } {
  if (cur)
    return {
      ...STEP[cur.step],
      ...(cur.step === "local" ? { label: `Copying from ${host()}` } : {}),
      tone: "info",
    };
  switch (i.state) {
    case "queued":
      return { label: "Queued", tone: "", ic: "hourglass" };
    case "downloaded":
      return { label: "Downloaded", tone: "info", ic: "hourglass" };
    case "in_google":
      return { label: "Retrying", tone: "warn", ic: "refresh" };
    case "checking":
      return { label: "Checking with Google", tone: "info", ic: "loader" };
    case "done":
      return { label: "Freed", tone: "good", ic: "ok" };
    case "counts":
    case "counted":
      return { label: "Still counts", tone: "bad", ic: "warn" };
    case "failed":
      return { label: "Didn't work", tone: "bad", ic: "error" };
    default:
      return { label: "", tone: "", ic: "info" };
  }
}

/** Refused because Google only serves a changed copy: it can be freed with that copy, if chosen. */
function onlyCopy(i: StorageItem): boolean {
  return i.state === "failed" && (i.error ?? "").includes("changed copy");
}

function actions(i: StorageItem, r: StorageReport, cur: ReclaimProgress | undefined): string {
  const cancel = `<button class="btn icon sm" data-cancel="${esc(i.key)}" title="Forget it (the downloaded copy is removed)" aria-label="Cancel">${icon("x")}</button>`;
  if (cur) return "";
  switch (i.state) {
    case "":
      return r.blocked
        ? ""
        : `<button class="btn sm" data-get="${esc(i.key)}">${icon("sparkles")}Free up</button>`;
    case "queued":
    case "downloaded":
    case "in_google":
      return cancel;
    case "failed":
      return onlyCopy(i)
        ? `<button class="btn sm" data-copy="${esc(i.key)}" title="Free it with the copy Google serves">${icon("copy")}Use Google's copy</button>`
        : `<button class="btn sm" data-get="${esc(i.key)}">${icon("retry")}Try again</button>${cancel}`;
    default:
      return "";
  }
}

/** An item's shape for the rows (width ÷ height), kept between 3:4 and 2:1 so a panorama or a tall
 * screenshot doesn't take a whole row or shrink to a sliver. Square when unknown. */
function shape(i: StorageItem): number {
  return i.width && i.height ? Math.min(2, Math.max(0.75, i.width / i.height)) : 1;
}

/** One item as a thumbnail tile, in rows at its own proportions (like Google Photos). */
function gridTile(i: StorageItem, r: StorageReport, working: Map<string, ReclaimProgress>): string {
  const cur = working.get(i.key);
  const s = stateOf(i, cur);
  const hint = ["downloaded", "in_google", "failed", "counts"].includes(i.state) ? (i.error ?? "") : "";
  const pickable = i.state === "" && !r.blocked;
  const chosen = picked.has(i.key);
  return `<figure data-key="${esc(i.key)}" style="--ar:${shape(i).toFixed(3)}" class="media-tile group relative overflow-hidden rounded-xl border ${chosen ? "border-accent" : "border-line"} bg-panel">
    <label class="shimmer relative block aspect-(--ar) overflow-hidden ${pickable ? "cursor-pointer" : "cursor-default"}">
      ${thumbOf(i.key, i.kind, "zoom-img fade-img size-full object-cover")}
      ${pickable ? `<input type="checkbox" class="peer sr-only" data-pick="${esc(i.key)}" ${chosen ? "checked" : ""} aria-label="Pick ${esc(i.name)}">` : ""}
      ${pickable ? `<span class="absolute inset-0 transition-colors peer-checked:bg-accent/25"></span><span class="absolute top-2 left-2 grid size-6 place-items-center rounded-full border border-white/70 bg-black/30 text-white opacity-0 transition-opacity group-hover:opacity-100 peer-checked:opacity-100 peer-checked:bg-accent peer-checked:border-accent">${icon("check", "size-3.5")}</span>` : ""}
      ${s.label ? `<span class="absolute top-2 right-2">${badge(s.label, s.tone, s.ic)}</span>` : ""}
      ${cur ? `<span class="absolute inset-x-2 bottom-2">${progressBar(cur.done, cur.total, cur.step !== "deleting")}</span>` : ""}
    </label>
    <figcaption class="p-2.5">
      <div class="truncate text-xs font-medium" title="${esc(i.name)}">${esc(i.name)}</div>
      <div class="truncate text-xs text-ink-3"><b class="text-ink-2">${bytes(i.quota)}</b> · ${i.taken ? date(i.taken, true) : "no date"}</div>
      ${hint ? `<div class="mt-1 line-clamp-2 text-xs ${i.state === "failed" || i.state === "counts" ? "text-bad" : "text-ink-3"}">${esc(hint)}</div>` : ""}
      <div class="row-actions mt-2 flex flex-wrap gap-1.5 empty:hidden">${actions(i, r, cur)}</div>
    </figcaption>
  </figure>`;
}

/** Every connected account at a glance: which one is freeing, paused or done. */
function accountStrip(r: StorageReport): string {
  const sum = r.summary ?? status()?.reclaim ?? {};
  return accountChips(account, (a) => {
    const { line, freed, tone, live } = accountLine(sum[a]);
    return { line, extra: freed, tone, live };
  });
}

/** A progress bar that moves: the share done with a shimmer, or a sliding bar for a step that has
 * no byte count (replacing in Google, or the first moment of a download). */
function progressBar(done: number, total: number, known = true): string {
  return known && total > 0 && done > 0
    ? `<span class="bar active mt-1.5 block" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.floor(pct(done, total))}"><i style="width:${pct(done, total)}%"></i></span>`
    : `<span class="bar indeterminate mt-1.5 block" role="progressbar" aria-busy="true"><i></i></span>`;
}

const WAITING_NOTE: Record<string, string> = {
  downloaded: "Replacing in Google next",
  in_google: "Trying again soon",
  checking: "Google confirms it is free",
};

/** Items under way, from download to Google's check, in the shared work panel. No "at once" button
 * here: the Moving now tile above opens that sheet. An item stays until
 * it is freed, so it can be followed step by step; new items don't wait for the check, so there can
 * be more rows than items at once. */
function workingCard(r: StorageReport): string {
  const w = new Map((r.working ?? []).map((c) => [c.key, c]));
  const items: WorkItem[] = [];
  // Biggest first, as the server lists them, so a row keeps its place as its item moves on.
  for (const i of r.items) {
    const c = w.get(i.key);
    if (c)
      items.push({
        key: c.key,
        name: c.name ?? i.name,
        step: c.step,
        done: c.done,
        total: c.total,
        label: c.step === "local" ? `Copying from ${host()}` : undefined,
      });
    else if (i.state in WAITING_NOTE)
      items.push({
        key: i.key,
        name: i.name,
        step: i.state,
        pct: i.state === "downloaded" ? 100 : undefined,
        // Why it waits, when there is a reason (a hold, or Google's error), else what comes next.
        note: i.error || WAITING_NOTE[i.state],
        waiting: true,
      });
  }
  for (const c of w.values())
    if (!items.some((x) => x.key === c.key))
      items.push({ key: c.key, name: c.name ?? c.key, step: c.step, done: c.done, total: c.total });
  const pausing = r.paused && w.size > 0;
  return items.length
    ? `<div class="mt-4">${workPanel(pausing ? "Pausing" : "Working now", pausing ? "pause" : "loader", items)}</div>`
    : "";
}

/** Share stored free, never rounded up to 100% while anything still counts. */
function freeShare(part: number, whole: number): string {
  const p = pct(part, whole);
  if (p >= 100) return "100%";
  return `${p >= 99 ? Math.floor(p * 100) / 100 : Math.floor(p * 10) / 10}%`;
}

/** Items Google only serves a changed copy of: they stay untouched unless the user chooses the copy. */
function copyNote(r: StorageReport): string {
  const only = r.items.filter(onlyCopy);
  if (!only.length) return "";
  const size = only.reduce((n, i) => n + (i.quota || 0), 0);
  return `<section class="card mb-4 flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
    <div class="flex min-w-0 flex-1 items-start gap-3">
      <span class="grid size-9 shrink-0 place-items-center rounded-xl bg-warn/15 text-warn">${icon("copy", "size-4")}</span>
      <div class="min-w-0 flex-1 text-sm"><div class="font-medium">${num(only.length)} item${only.length === 1 ? "" : "s"} (${bytes(size)}): Google only gives a changed copy</div>
        <div class="mt-0.5 text-ink-3">Not the file you uploaded, so they stay in Google untouched. You can free them with Google's copy instead.</div></div>
    </div>
    <button class="btn sm w-full shrink-0 justify-center sm:w-auto" id="st-copy-all">${icon("copy")}Use Google's copy</button>
  </section>`;
}

/** Ask, then free items with the copy Google serves (the original isn't available). */
async function useCopy(keys: string[]): Promise<void> {
  if (!keys.length) return;
  const what = keys.length === 1 ? "this item" : `${num(keys.length)} items`;
  const r = await confirmDialog({
    title: `Free ${what} with Google's copy?`,
    icon: "copy",
    body: [
      "Google doesn't give back the exact file you uploaded for these: something inside was changed (often the date or other details). GP Sync downloads the copy Google serves from its original link and uses it only if it is complete, about the original's size, and opens as a photo or video.",
      "That copy is then uploaded again for free, and the one in Google is deleted. What you keep is Google's copy, not the exact bytes you once uploaded.",
    ],
    confirm: "Use Google's copy",
    checkbox: {
      label: "Also keep the copy here, under Downloads",
      hint: "Off: it is deleted from this server after the upload.",
      checked: report?.prefs?.keep ?? false,
    },
  });
  if (!r) return;
  void act("use-copy", { keys, keep: r.checked }, `Freeing ${what} with Google's copy`);
}

/** All of yours that Google holds, and how much of it doesn't count against storage. */
function storedCard(r: StorageReport): string {
  const s = r.stored;
  if (!s?.count) return "";
  const counting = s.bytes - s.free_bytes;
  const share = freeShare(s.free_bytes, s.bytes);
  return `<section class="card mb-4 p-4">
    <div class="flex min-w-0 items-center gap-3">
      <span class="grid size-10 shrink-0 place-items-center rounded-xl bg-good/15 text-good">${icon("cloud", "size-5")}</span>
      <div class="min-w-0 flex-1">
        <div class="label">Yours in Google Photos</div>
        <div class="num truncate text-xl sm:text-2xl">${bytes(s.bytes)}</div>
        <div class="truncate text-xs text-ink-3">${num(s.count)} files</div>
      </div>
      <div class="shrink-0 text-right"><div class="num text-xl text-good sm:text-2xl">${share}</div><div class="text-xs text-ink-3">stored free</div></div>
    </div>
    <div class="mt-3 flex h-2 overflow-hidden rounded-full bg-panel-2" role="img" aria-label="${share} stored free">
      <span class="bg-good" style="width:${pct(s.free_bytes, s.bytes)}%"></span>
      ${counting > 0 ? `<span class="bg-warn" style="width:${pct(counting, s.bytes)}%;min-width:3px"></span>` : ""}
    </div>
    <div class="even mt-3">
      ${badge(`Photos · ${bytes(s.photo.bytes)}`, "", "photo", `${num(s.photo.count)} photos`)}
      ${badge(`Videos · ${bytes(s.video.bytes)}`, "", "video", `${num(s.video.count)} videos`)}
      ${badge(`Free · ${bytes(s.free_bytes)}`, "good", "check", `${num(s.free_count)} files stored free`)}
      ${badge(`Counting · ${bytes(counting)}`, counting > 0 ? "warn" : "", "cloud", `${num(s.count - s.free_count)} files count against storage`)}
    </div>
    <p class="mt-2 text-xs text-ink-3">Each file once at its full size. Free items don't use your Google storage${r.trash?.bytes ? `; ${bytes(r.trash.bytes)} more waits in Google's Trash` : ""}.</p>
  </section>`;
}

function renderList(r: StorageReport): void {
  const working = new Map((r.working ?? []).map((w) => [w.key, w]));
  const busy = r.items.filter((i) => ACTIVE.includes(i.state)).length;
  const oursCount = r.ours.counted > 0;
  const listed = r.items; // the server already returns only the chosen kind, under way first
  const fresh = listed.filter((i) => !i.state);
  const kinds = r.kinds ?? { photo: { count: 0, bytes: 0 }, video: { count: 0, bytes: 0 } };
  const ofKind =
    kind === "all"
      ? { count: kinds.photo.count + kinds.video.count, bytes: kinds.photo.bytes + kinds.video.bytes }
      : kinds[kind];
  const canFree = !(r.blocked || oursCount);
  const segs = (["all", "photo", "video"] as const)
    .map((k) => {
      const n = k === "all" ? kinds.photo.count + kinds.video.count : kinds[k].count;
      return `<button data-kind="${k}" aria-pressed="${k === kind}">${k === "all" ? "Both" : k === "photo" ? "Photos" : "Videos"} <span class="opacity-60">${num(n)}</span></button>`;
    })
    .join("");
  const workers = r.prefs?.workers ? r.prefs.workers : (r.pace?.now ?? 4);
  html(
    $("#st-body"),
    `${accountStrip(r)}
    ${
      r.blocked
        ? `<section class="card mb-4 flex flex-wrap items-start gap-3 border-bad/40 p-4">${icon("warn", "size-5 shrink-0 text-bad")}<div class="min-w-0 flex-1 text-sm"><div class="font-medium">Stopped: Google counted a re-upload against storage</div><div class="mt-0.5 text-ink-3">Nothing more is moved. Downloaded files stay here, and nothing was deleted.</div></div><button class="btn sm" id="st-unblock">Try again anyway</button></section>`
        : ""
    }
    ${r.error && !(oursCount && r.error.includes("own uploads")) ? `<section class="card mb-4 p-4 text-sm text-bad">${esc(r.error)}</section>` : ""}
    ${checkNote(r.check)}
    ${copyNote(r)}
    ${oursCount ? `<section class="card mb-4 flex items-start gap-3 p-4 text-sm">${icon("warn", "size-5 shrink-0 text-warn")}<div><div class="font-medium">GP Sync's uploads count against storage in this account</div><div class="mt-0.5 text-ink-3">Re-uploading wouldn't free anything, so nothing is deleted here.</div></div></section>` : ""}
    ${
      r.prefs
        ? `<section class="card mb-4 flex items-center gap-3 px-4 py-3"><span class="grid size-9 shrink-0 place-items-center rounded-xl bg-panel-2 ${r.prefs.auto ? "text-accent" : "text-ink-3"}">${icon("zap", "size-4")}</span>
        <span class="min-w-0 flex-1"><span class="block text-sm font-medium">Free new items automatically</span><span class="block text-xs text-ink-3">${r.prefs.auto ? "On: what your phones back up to Google Photos is freed as it arrives, in every account" : "Off: new phone backups wait here until you free them"}</span></span>
        <button type="button" role="switch" class="switch shrink-0" id="st-auto" aria-checked="${!!r.prefs.auto}" aria-label="Free new items automatically"></button></section>`
        : ""
    }
    ${storedCard(r)}
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      ${tile({
        label: "Using storage",
        // As Google One counts it: the Trash too, until it is emptied.
        value: bytes(r.counted_bytes + (r.trash?.bytes ?? 0)),
        ic: "cloud",
        sub: r.refreshing
          ? "Reading your library…"
          : r.trash?.bytes
            ? `${bytes(r.trash.bytes)} in Google's Trash`
            : `${num(r.counted)} of ${num(r.total)} items${r.refreshed ? ` · read ${ago(r.refreshed)}` : ""}`,
      })}
      ${tile({ label: "Freed by GP Sync", value: bytes(r.freed.bytes), ic: "sparkles", sub: r.freed.count ? `${num(r.freed.count)} item${r.freed.count === 1 ? "" : "s"} · see details` : "Nothing freed yet", tone: r.freed.count ? "good" : "", href: `#/storage/${encodeURIComponent(account)}/freed` })}
      ${tile({
        label: "Moving now",
        value: `${num(working.size)} of ${workers}`,
        ic: "hourglass",
        open: "move",
        sub: r.paused
          ? `Paused · ${num(r.left?.count ?? busy)} waiting`
          : r.left?.count
            ? `${num(r.left.count)} waiting in the queue`
            : r.prefs?.auto
              ? "Auto: new items freed as they arrive"
              : "Nothing waiting",
      })}
      ${tile({
        label: "Left to free",
        value: bytes(r.left?.bytes ?? 0),
        ic: "timer",
        // Time left first, kept short so it fits; the item count and speed are in the tooltip.
        sub: !r.left?.count
          ? "Nothing queued"
          : r.paused
            ? `Paused · ${num(r.left.count)} item${r.left.count === 1 ? "" : "s"}`
            : r.left.rate
              ? `<span data-tip="${num(r.left.count)} item${r.left.count === 1 ? "" : "s"} at ${speed(r.left.rate)} (the last hour's speed)">About ${remaining(r.left.bytes / r.left.rate)} left · ${num(r.left.count)} item${r.left.count === 1 ? "" : "s"}</span>`
              : `Timing the first ones · ${num(r.left.count)} item${r.left.count === 1 ? "" : "s"}`,
      })}
    </div>
    ${workingCard(r)}
    <section class="card mt-4 min-w-0 overflow-hidden">
      ${cardHead("Biggest first", "layers", `${num(ofKind.count)} · ${bytes(ofKind.bytes)}`)}
      <div class="flex flex-col gap-3 border-b border-line px-4 py-3 sm:flex-row sm:items-center">
        <div class="seg" id="st-kind" aria-label="Photos or videos">${segs}</div>
        ${
          canFree
            ? `<div class="grid grid-cols-3 gap-2 sm:ml-auto sm:flex">
          <button class="btn sm justify-center whitespace-nowrap" id="st-picked" ${picked.size ? "" : "disabled"}>${icon("check")}Picked (${picked.size})</button>
          <button class="btn sm justify-center whitespace-nowrap" id="st-next" ${fresh.length ? "" : "disabled"}>${icon("sparkles")}10 biggest</button>
          <button class="btn sm primary justify-center whitespace-nowrap" id="st-all" ${ofKind.count ? "" : "disabled"}>${icon("sparkles")}Free all</button></div>`
            : ""
        }
      </div>
      <p class="border-b border-line px-4 py-2.5 text-xs text-ink-3">Items under way are listed first. Other accounts run alongside, and copies too. Downloads need free space here: ${bytes(r.free_space)} left.</p>
      ${
        listed.length
          ? `<div class="rows-grid p-4" data-gallery>${listed
              .slice(0, shown)
              .map((i) => gridTile(i, r, working))
              .join(
                "",
              )}</div>${listed.length > shown ? `<div class="flex justify-center border-t border-line p-3"><button class="btn" id="st-more">${icon("plus")}Show more (${num(listed.length - shown)} left)</button></div>` : ""}`
          : `<div class="empty">${icon("shield")}No ${KIND_NAME[kind]} in this account count against storage</div>`
      }
    </section>
    <section class="card mt-4">${cardHead("How it works", "info")}
      <div class="card-b grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div class="flex gap-3"><span class="grid size-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs">1</span><span class="text-sm"><b>Downloaded</b> here and checked against the checksum Google gives for it.</span></div>
        <div class="flex gap-3"><span class="grid size-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs">2</span><span class="text-sm"><b>Deleted in Google</b>, Trash included, only after the copy here is checked again.</span></div>
        <div class="flex gap-3"><span class="grid size-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs">3</span><span class="text-sm"><b>Uploaded again</b> the free way, then Google is asked that it doesn't count.</span></div>
      </div>
      <div class="border-t border-line px-4 py-3 text-xs text-ink-3">${atOnceText(r.prefs?.workers ?? 0, r.pace).replace(/^u/, "U")} items at once (tap Moving now to change). Each item goes step by step and every step is saved, so an update or restart resumes safely. Keeps quality, date, favourite, archive and caption. Albums, shared links and comments are lost. A few videos download as a copy Google changed slightly (an edited date): that copy is used when it matches the checksum Google lists for it.</div>
    </section>`,
  );
}

/** What was freed in this account: totals, per day, and each item (open it in Google Photos). */
function renderFreed(): void {
  const f = freed;
  const back = `<a class="btn sm mb-4" href="#/storage/${encodeURIComponent(account)}">${icon("chevron", "size-4 rotate-180")}Free storage</a>`;
  if (!f) {
    html(
      $("#st-body"),
      `${back}<div class="grid grid-cols-2 gap-4 xl:grid-cols-4">${'<div class="card skeleton h-28"></div>'.repeat(4)}</div><div class="card skeleton mt-4 h-72"></div>`,
    );
    return;
  }
  const avg = f.timed ? f.seconds / f.timed : 0;
  // One column per day up to today: at least the last 14 days, so a single busy day is one bar
  // among others instead of filling the chart, and at most the last 30.
  const today = new Date();
  const span = f.first
    ? Math.max(14, Math.min(30, Math.floor((today.getTime() / 1000 - f.first) / 86400) + 1))
    : 14;
  const byDay = new Map(f.days.map((d) => [d.day, d]));
  const days = Array.from({ length: span }, (_, n) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (span - 1 - n));
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const label = d.toLocaleDateString([], { day: "numeric", month: "short" });
    return { key, label, v: byDay.get(key) };
  });
  const items = f.items.slice(0, freedShown);
  html(
    $("#st-body"),
    `${back}
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      ${tile({ label: "Freed", value: bytes(f.bytes), ic: "sparkles", sub: `${num(f.count)} item${f.count === 1 ? "" : "s"} in ${esc(account)}`, tone: f.count ? "good" : "" })}
      ${tile({ label: "Photos", value: num(f.kinds.photo.count), ic: "photo", sub: bytes(f.kinds.photo.bytes) })}
      ${tile({ label: "Videos", value: num(f.kinds.video.count), ic: "video", sub: bytes(f.kinds.video.bytes) })}
      ${tile({ label: "Time taken", value: f.timed ? duration(f.active ?? f.seconds) : "–", ic: "timer", sub: f.timed ? `About ${duration(avg)} per item · see details` : "Timed from 1.5.2 on", href: `#/storage/${encodeURIComponent(account)}/time` })}
    </div>
    <section class="card mt-4 min-w-0 overflow-hidden">
      ${cardHead("Freed per day", "chart", f.last ? `last ${ago(f.last)}` : "")}
      <div class="card-b">${
        f.count
          ? columns(
              days.map((d) => d.v?.bytes ?? 0),
              days.map((d) => d.label),
              {
                tip: (_v, i) => {
                  const d = days[i]!;
                  return d.v
                    ? `${d.label}: ${bytes(d.v.bytes)} freed · ${num(d.v.count)} item${d.v.count === 1 ? "" : "s"}`
                    : `${d.label}: nothing freed`;
                },
                every: days.length > 21 ? 5 : 3, // a date every few days: they don't fit under every bar
                height: 120,
              },
            )
          : `<div class="empty">${icon("sparkles")}Nothing freed in this account yet</div>`
      }</div>
    </section>
    <section class="card mt-4 min-w-0 overflow-hidden">
      ${cardHead("Freed items", "library", `${num(f.count)} · newest first`)}
      <p class="border-b border-line px-4 py-2.5 text-xs text-ink-3">Open an item in Google Photos to check it is there; its info there shows it takes no storage. Copies kept on this server play here.</p>
      ${
        items.length
          ? `<div class="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">${items
              .map(
                (
                  i,
                ) => `<figure data-key="${esc(i.key)}" class="media-tile group relative overflow-hidden rounded-xl border border-line bg-panel">
          <a class="shimmer relative block aspect-square overflow-hidden" href="${esc(i.google_url)}" target="_blank" rel="noopener" title="Open in Google Photos">
            ${thumbOf(i.key, i.kind, "zoom-img fade-img size-full object-cover")}
            <span class="absolute top-2 right-2">${badge("Freed", "good", "ok")}</span>
            ${i.kind === "video" ? `<span class="absolute bottom-2 left-2 grid size-7 place-items-center rounded-full bg-black/55 text-white">${icon("play", "size-3.5")}</span>` : ""}
          </a>
          <figcaption class="p-2.5">
            <div class="truncate text-xs font-medium" title="${esc(i.name)}">${esc(i.name)}</div>
            <div class="truncate text-xs text-ink-3"><b class="text-ink-2">${bytes(i.bytes)}</b> · ${i.finished ? ago(i.finished) : ""}${i.seconds != null ? ` · took ${duration(i.seconds)}` : ""}</div>
            <div class="mt-2 flex flex-wrap gap-1.5">
              <a class="btn sm" href="${esc(i.google_url)}" target="_blank" rel="noopener">${icon("external")}Google Photos</a>
              ${i.download ? `<a class="btn icon sm" href="api/downloads/file?id=${i.download}&inline=1" target="_blank" rel="noopener" title="Play the copy kept here" aria-label="Play the copy kept here">${icon("play")}</a>` : ""}
            </div>
          </figcaption>
        </figure>`,
              )
              .join(
                "",
              )}</div>${f.items.length > freedShown ? `<div class="flex justify-center border-t border-line p-3"><button class="btn" id="st-freed-more">${icon("plus")}Show more (${num(f.items.length - freedShown)} left)</button></div>` : ""}`
          : `<div class="empty">${icon("sparkles")}Freed items show up here</div>`
      }
    </section>`,
  );
}

/** How long freeing took in this account: running time, per item, each step, fastest and slowest. */
function renderTime(): void {
  const f = freed;
  const back = `<a class="btn sm mb-4" href="#/storage/${encodeURIComponent(account)}/freed">${icon("chevron", "size-4 rotate-180")}Freed</a>`;
  if (!f) {
    html($("#st-body"), `${back}<div class="card skeleton h-72"></div>`);
    return;
  }
  const t = f.timing;
  if (!f.timed || !t) {
    html(
      $("#st-body"),
      `${back}<div class="card empty">${icon("timer")}Items freed from 1.5.2 on are timed. Nothing timed yet in this account.</div>`,
    );
    return;
  }
  const active = f.active ?? f.seconds;
  const together = active ? f.seconds / active : 1;
  // Where an item's time goes, on average (items timed from 1.5.5 on).
  const steps = [
    { label: "Download and check", v: t.phases.download, color: "var(--s1)" },
    { label: "Replace in Google and upload", v: t.phases.upload, color: "var(--s2)" },
    { label: "Google confirms it is free", v: t.phases.check, color: "var(--s3)" },
  ];
  const stepTotal = steps.reduce((n, x) => n + (x.v ?? 0), 0);
  const rows = (list: typeof t.slowest) =>
    list
      .map(
        (i) =>
          `<li class="flex items-center gap-3 px-4 py-2.5 text-sm"><span class="min-w-0 flex-1 truncate" title="${esc(i.name)}">${esc(i.name)}</span><span class="shrink-0 text-xs text-ink-3">${bytes(i.bytes)}</span><span class="w-20 shrink-0 text-right font-medium tabular-nums">${duration(i.seconds)}</span></li>`,
      )
      .join("");
  const days = f.days.filter((d) => d.active).slice(-14);
  html(
    $("#st-body"),
    `${back}
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      ${tile({ label: "Running time", value: duration(active), ic: "timer", sub: `${num(f.timed)} item${f.timed === 1 ? "" : "s"}, several at once` })}
      ${tile({ label: "Per item", value: duration(t.average), ic: "hourglass", sub: `Median ${duration(t.median)}` })}
      ${tile({ label: "Speed", value: speed(t.rate), ic: "gauge", sub: `${bytes(t.rate * 3600)} freed per hour` })}
      ${tile({ label: "Items per hour", value: num(Math.round(t.per_hour)), ic: "layers", sub: together > 1.05 ? `About ${together.toFixed(1)} at a time` : "One at a time" })}
    </div>
    <section class="card mt-4 min-w-0 overflow-hidden">
      ${cardHead("Where the time goes", "chart", t.phased ? `average of ${num(t.phased)} item${t.phased === 1 ? "" : "s"}` : "")}
      <div class="card-b space-y-3">${
        t.phased && stepTotal
          ? `<div class="flex h-3 overflow-hidden rounded-full bg-panel-2">${steps
              .map(
                (x) =>
                  `<span style="width:${pct(x.v ?? 0, stepTotal)}%;background:${x.color}" data-tip="${esc(x.label)}: ${duration(x.v ?? 0)}"></span>`,
              )
              .join("")}</div>
          <ul class="grid gap-2 text-sm sm:grid-cols-3">${steps
            .map(
              (x) =>
                `<li class="flex items-center gap-2"><span class="size-2.5 shrink-0 rounded-full" style="background:${x.color}"></span><span class="min-w-0 flex-1 text-ink-2">${x.label}</span><b class="tabular-nums">${duration(x.v ?? 0)}</b></li>`,
            )
            .join("")}</ul>
          <p class="text-xs text-ink-3">Google lists a new upload about a minute after it arrives, so the last step is mostly waiting for that check.</p>`
          : `<p class="text-sm text-ink-3">Each step is timed from 1.5.5 on: the next items freed show the split here.</p>`
      }</div>
    </section>
    <div class="mt-4 grid gap-4 lg:grid-cols-2">
      <section class="card min-w-0 overflow-hidden">${cardHead("Slowest", "hourglass")}<ul class="divide-y divide-line">${rows(t.slowest)}</ul></section>
      <section class="card min-w-0 overflow-hidden">${cardHead("Fastest", "zap")}<ul class="divide-y divide-line">${rows(t.fastest)}</ul></section>
    </div>
    ${
      days.length
        ? `<section class="card mt-4 min-w-0 overflow-hidden">${cardHead("Running time per day", "calendar")}<ul class="divide-y divide-line">${days
            .reverse()
            .map((d) => {
              const label = new Date(`${d.day}T12:00:00`).toLocaleDateString([], {
                weekday: "short",
                day: "numeric",
                month: "short",
              });
              return `<li class="flex items-center gap-3 px-4 py-2.5 text-sm"><span class="min-w-0 flex-1">${label}</span><span class="text-xs text-ink-3">${num(d.count)} item${d.count === 1 ? "" : "s"} · ${bytes(d.bytes)}</span><span class="w-20 shrink-0 text-right font-medium tabular-nums">${duration(d.active ?? 0)}</span></li>`;
            })
            .join("")}</ul></section>`
        : ""
    }
    <p class="mt-4 px-1 text-xs text-ink-3">Running time counts the minutes Free storage was working, once, even when several items moved at the same time. Adding up every item's own time gives ${duration(f.seconds)}.</p>`,
  );
}

function render(): void {
  const r = report;
  if (r?.account) {
    // The control strip: this account's freeing (status, Pause/Resume) and reading its library.
    setRefreshControl({
      label: r.refreshing ? "Reading your library from Google…" : `Refresh ${account} from Google`,
      busy: r.refreshing,
      run: async () => {
        await post("storage/refresh", { account });
        void load();
        return "Reading your library from Google";
      },
    });
    const paused = !!r.paused;
    const moving = r.working?.length ?? 0;
    const left = r.left?.count ?? 0;
    setPageStatus(
      paused
        ? {
            tone: "warn",
            title: moving ? `Pausing · ${num(moving)} finishing their step` : "Freeing storage is paused",
            detail: `${account}${left ? ` · ${num(left)} waiting` : ""}`,
          }
        : moving
          ? {
              tone: "run",
              title: `Freeing storage · ${num(moving)} at once`,
              detail: `${account} · ${num(left)} left`,
            }
          : r.refreshing
            ? { tone: "run", title: "Reading your library from Google", detail: account }
            : left
              ? { tone: "idle", title: `${num(left)} waiting to be freed`, detail: account }
              : { tone: "good", title: "Nothing being freed", detail: account },
    );
    // Pause only when there is something to pause.
    setPauseControl(
      !(paused || moving || left)
        ? null
        : {
            paused,
            working: !!r.working?.length,
            label: paused ? `Resume freeing storage in ${account}` : `Pause freeing storage in ${account}`,
            toggle: async () => {
              await post("storage/pause", { account, paused: !paused });
              void load();
              return paused
                ? "Freeing storage resumed"
                : "Freeing storage paused: downloads stop now, uploads under way finish first";
            },
          },
    );
  }
  if (!r || !r.account) {
    html(
      $("#st-body"),
      `<div class="card empty">${icon("users")}Connect a Google account first (Settings › Accounts)</div>`,
    );
    return;
  }
  if (sub === "freed") {
    renderFreed();
    return;
  }
  if (sub === "time") {
    renderTime();
    return;
  }
  if (!r.listed) {
    html(
      $("#st-body"),
      `${accountStrip(r)}<section class="card p-6 text-center"><div class="mx-auto max-w-lg space-y-2">${icon("cloud", "mx-auto size-8 text-ink-3")}
        <div class="text-base font-medium">See what uses your Google storage</div>
        <p class="text-sm text-ink-3">GP Sync reads your library list (nothing changes) and shows what counts against storage. The first read can take a few minutes.</p>
        <button class="btn primary mt-2" id="st-read" ${r.refreshing ? "disabled" : ""}>${r.refreshing ? `${icon("loader", "spin")}Reading your library…` : `${icon("refresh")}Read my library`}</button>
        ${r.error ? `<p class="text-sm text-bad">${esc(r.error)}</p>` : ""}</div></section>`,
    );
    return;
  }
  renderList(r);
}

/** Ask (unless the user said not to), then queue: each item is downloaded, deleted in Google and
 * uploaded again free. Resolves with whether to keep the downloaded copies, or null if cancelled. */
async function ask(what: string, count: string): Promise<{ keep: boolean } | null> {
  const p = report?.prefs;
  if (p && !p.confirm) return { keep: p.keep };
  const workers = atOnceText(p?.workers ?? 0, report?.pace);
  const r = await confirmDialog({
    title: `Free up ${what}?`,
    icon: "sparkles",
    body: [
      `${count} in ${account}. Biggest first, ${workers} at once: each is downloaded here and checked, deleted in Google Photos (Trash included) and uploaded again for free.`,
      "Kept: quality, date, favourite, archive and caption. Lost: albums made by hand, shared links and comments.",
    ],
    confirm: "Free up",
    checkbox: {
      label: "Delete the downloaded copy after the upload",
      hint: "Off: the copies stay on this server, under Downloads, until you delete them.",
      checked: !(p?.keep ?? false),
    },
    remember: "Don't ask again (change it under Options)",
  });
  if (!r) return null;
  const keep = !r.checked;
  if (r.remember || keep !== (p?.keep ?? false))
    void savePrefs({ keep, ...(r.remember ? { confirm: false } : {}) });
  return { keep };
}

async function start(keys: string[]): Promise<boolean> {
  if (!keys.length) return false;
  const what = keys.length === 1 ? "this item" : `${num(keys.length)} items`;
  const r = await ask(what, keys.length === 1 ? "1 item" : `${num(keys.length)} items`);
  if (!r) return false;
  void act("download", { keys, keep: r.keep }, `Freeing ${what}`);
  return true;
}

function onClick(e: Event): void {
  const t = e.target as HTMLElement;
  const b = t.closest<HTMLElement>("button");
  if (!b) return;
  if (b.dataset.pace === "move") void openMoveOptions(() => void load(), report?.pace);
  else if (b.id === "st-auto") {
    const on = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(on));
    void savePrefs({ auto: on }, on ? "New items are freed automatically" : "Auto free storage is off");
  } else if (b.id === "st-read") void act("refresh", {}, "Reading your library from Google");
  else if (b.dataset.acc) {
    if (b.dataset.acc !== account) pickAccount(b.dataset.acc);
  } else if (b.id === "st-unblock") {
    if (confirm("Google counted a re-upload against storage last time. Try again anyway?"))
      void act("check", { unblock: true });
  } else if (b.dataset.get) void start([b.dataset.get]);
  else if (b.dataset.copy) void useCopy([b.dataset.copy]);
  else if (b.id === "st-copy-all") void useCopy((report?.items ?? []).filter(onlyCopy).map((i) => i.key));
  else if (b.dataset.cancel) void act("cancel", { keys: [b.dataset.cancel] });
  else if (b.id === "st-picked") {
    void start([...picked]).then((ok) => ok && picked.clear());
  } else if (b.id === "st-next") {
    void start(
      (report?.items ?? [])
        .filter((i) => !i.state && (kind === "all" || i.kind === kind))
        .slice(0, 10)
        .map((i) => i.key),
    );
  } else if (b.dataset.kind) {
    if (b.dataset.kind === kind) return;
    kind = b.dataset.kind as typeof kind;
    shown = 50;
    // Show the choice at once; the list reloads from the server for that kind.
    $("#st-kind")
      .querySelectorAll("button")
      .forEach((x) => x.setAttribute("aria-pressed", String((x as HTMLElement).dataset.kind === kind)));
    void load();
  } else if (b.id === "st-all") {
    const k = report?.kinds;
    const n = !k ? 0 : kind === "all" ? k.photo.count + k.video.count : k[kind].count;
    const size = !k ? 0 : kind === "all" ? k.photo.bytes + k.video.bytes : k[kind].bytes;
    const k_ = kind;
    void ask(`all ${KIND_NAME[k_]}`, `${num(n)} items, ${bytes(size)},`).then((r) => {
      if (r) void act("download", { kind: k_, keep: r.keep }, `Freeing all ${KIND_NAME[k_]}`);
    });
  } else if (b.id === "st-more") {
    shown += 50;
    render();
  } else if (b.id === "st-freed-more") {
    freedShown += 60;
    render();
  }
}

/** Another account chosen in the strip or the menu, or opened from a link (#/storage/<account>). */
function pickAccount(next: string): void {
  account = next;
  picked.clear();
  freed = null;
  rememberAccount("storage", account, sub === "list" ? "" : sub);
  void load();
}

function subOf(args: string[] | undefined): typeof sub {
  return args?.[1] === "freed" || args?.[1] === "time" ? args[1] : "list";
}

const storage: View = {
  subtitle: "What counts against your quota, and moving it off",
  mount(el, args) {
    opened = true; // the first load draws the last answer at once (peek), then the fresh one
    // Coming back opens the account that is freeing (or the one picked last), not always the main one.
    account = openingAccount("storage", args, freeingAccounts());
    sub = subOf(args);
    shown = 50;
    freedShown = 60;
    freed = null;
    el.innerHTML = `
    <div id="st-body"><div class="grid grid-cols-2 gap-4 xl:grid-cols-4">${'<div class="card skeleton h-28"></div>'.repeat(4)}</div><div class="card skeleton mt-4 h-72"></div></div>`;
    el.addEventListener("click", onClick);
    el.addEventListener("change", (e) => {
      const t = e.target as HTMLInputElement;
      if (t.dataset.pick) {
        if (t.checked) picked.add(t.dataset.pick);
        else picked.delete(t.dataset.pick);
        const btn = document.getElementById("st-picked") as HTMLButtonElement | null;
        if (btn) {
          btn.disabled = !picked.size;
          btn.innerHTML = `${icon("check")}Picked (${picked.size})`;
        }
      }
    });
    void load();
    timer = window.setInterval(() => {
      const r = report;
      const active =
        r?.refreshing || r?.working?.length || (!r?.paused && r?.items.some((i) => ACTIVE.includes(i.state)));
      const other = freeingAccounts().some((a) => a !== account); // keeps the account strip current
      if (active || ++ticks % (other ? 2 : 8) === 0) void load(); // busy: every 3 s, else every 24 s
    }, 3000);
  },
  route(args) {
    const next = openingAccount("storage", args, freeingAccounts());
    const nextSub = subOf(args);
    if (nextSub !== sub) {
      sub = nextSub;
      freed = null;
      account = next || account;
      document.getElementById("view")?.scrollTo?.(0, 0);
      render();
      void load();
    } else if (next && next !== account) pickAccount(next);
  },
  unmount() {
    clearInterval(timer);
    request++;
    setPauseControl(null);
    setRefreshControl(null);
  },
};

export default storage;
