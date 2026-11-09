import {
  get,
  post,
  type FreedReport,
  type ReclaimPrefs,
  type ReclaimProgress,
  type StorageItem,
  type StorageReport,
} from "../lib/api";
import { $, esc, html, toast } from "../lib/dom";
import { ago, bytes, date, duration, num, pct, remaining, speed } from "../lib/format";
import { icon } from "../lib/icons";
import { host, setPauseControl, setRefreshControl, status } from "../lib/store";
import { avatar, freeingAccounts, openingAccount, rememberAccount } from "../lib/account-menu";
import { columns } from "../lib/charts";
import { confirmDialog } from "../lib/dialog";
import { accountLine, badge, cardHead, checkNote, pauseBtn, tile, type Tone } from "../lib/ui";
import type { View } from "./view";

// Free up Google storage: items that count against it are downloaded (and proven to be Google's
// own copy), deleted in Google Photos (and its Trash), and uploaded again for free. Several items
// move at once per account, and every account works on its own.

let account = "";
let report: StorageReport | null = null;
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
const WORKER_CHOICES = [1, 2, 3, 4]; // more makes Google throttle and fail

async function load(): Promise<void> {
  const mine = ++request;
  const r = await get<StorageReport>(`storage?account=${encodeURIComponent(account)}&kind=${kind}`).catch(
    () => null,
  );
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

const thumbOf = (key: string, kind: string, cls = "thumb") =>
  `<img class="${cls}" loading="lazy" decoding="async" alt="" data-kind="${kind}" src="api/storage/thumb?account=${encodeURIComponent(account)}&key=${encodeURIComponent(key)}">`;

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
      return `<button class="btn sm" data-get="${esc(i.key)}">${icon("retry")}Try again</button>${cancel}`;
    default:
      return "";
  }
}

/** One item as a thumbnail tile, like the Library grid. */
function gridTile(i: StorageItem, r: StorageReport, working: Map<string, ReclaimProgress>): string {
  const cur = working.get(i.key);
  const s = stateOf(i, cur);
  const hint = ["downloaded", "in_google", "failed", "counts"].includes(i.state) ? (i.error ?? "") : "";
  const pickable = i.state === "" && !r.blocked;
  const chosen = picked.has(i.key);
  return `<figure data-key="${esc(i.key)}" class="media-tile group relative overflow-hidden rounded-xl border ${chosen ? "border-accent" : "border-line"} bg-panel">
    <label class="shimmer relative block aspect-square overflow-hidden ${pickable ? "cursor-pointer" : "cursor-default"}">
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
  const accs = status()?.accounts ?? [];
  if (accs.length < 2) return "";
  const sum = r.summary ?? status()?.reclaim ?? {};
  // A list on phones (one account per row, nothing cut off), columns on wider screens.
  return `<div class="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3" role="tablist" aria-label="Accounts">${accs
    .map((a, n) => {
      const { line, freed, tone, live } = accountLine(sum[a]);
      const on = a === account;
      return `<button type="button" role="tab" data-acc="${esc(a)}" aria-selected="${on}" class="acc-chip card flex min-w-0 items-center gap-3 px-3 py-2.5 text-left ${on ? "border-accent" : ""}">
        ${avatar(a, "size-8")}
        <span class="min-w-0 flex-1"><span class="flex min-w-0 items-center gap-1.5"><span class="truncate text-sm font-medium" title="${esc(a)}">${esc(a)}</span>${n === 0 ? '<span class="shrink-0 rounded-full bg-panel-2 px-1.5 text-[10px] font-medium text-ink-3">Main</span>' : ""}</span>
        <span class="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs"><span class="flex min-w-0 items-center gap-1.5 ${tone}">${live ? '<span class="dot live shrink-0"></span>' : ""}<span class="truncate">${esc(line)}</span></span>${freed ? `<span class="text-ink-3">· ${esc(freed)}</span>` : ""}</span></span>
        ${on ? icon("check", "size-4 shrink-0 text-accent") : ""}
      </button>`;
    })
    .join("")}</div>`;
}

/** A progress bar that moves: the share done with a shimmer, or a sliding bar for a step that has
 * no byte count (replacing in Google, or the first moment of a download). */
function progressBar(done: number, total: number, known = true): string {
  return known && total > 0 && done > 0
    ? `<span class="bar active mt-1.5 block" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.floor(pct(done, total))}"><i style="width:${pct(done, total)}%"></i></span>`
    : `<span class="bar indeterminate mt-1.5 block" role="progressbar" aria-busy="true"><i></i></span>`;
}

/** Items being worked on right now, each with its step and a moving progress bar. */
function workingCard(r: StorageReport): string {
  const w = r.working ?? [];
  if (!w.length) return "";
  const pausing = r.paused;
  return `<section class="card mt-4 min-w-0 overflow-hidden">
    ${cardHead(pausing ? "Pausing" : "Working now", pausing ? "pause" : "loader", pausing ? `${num(w.length)} finishing their step` : `${num(w.length)} at once ${pauseBtn(false)}`)}
    <ul class="divide-y divide-line">${w
      .map((c) => {
        const step = c.step === "local" ? { ...STEP.local, label: `Copying from ${host()}` } : STEP[c.step];
        const known = c.step !== "deleting";
        const share = Math.floor(pct(c.done, c.total));
        return `<li data-key="${esc(c.key)}" class="flex items-center gap-3 px-4 py-3">
        <span class="grid size-9 shrink-0 place-items-center rounded-lg bg-panel-2 text-accent">${icon(step.ic, `size-4 ${known ? "" : "spin"}`)}</span>
        <span class="min-w-0 flex-1">
          <span class="flex items-baseline gap-2"><span class="truncate text-sm font-medium" title="${esc(c.name ?? c.key)}">${esc(c.name ?? c.key)}</span><span class="ml-auto shrink-0 text-sm font-semibold tabular-nums">${known ? `${share}%` : ""}</span></span>
          ${progressBar(c.done, c.total, known)}
          <span class="mt-1 flex justify-between text-xs text-ink-3 tabular-nums"><span>${step.label}</span><span>${known ? `${bytes(c.done)} of ${bytes(c.total)}` : "Waiting for Google"}</span></span>
        </span>
      </li>`;
      })
      .join("")}</ul>
  </section>`;
}

function sw(id: string, on: boolean, label: string): string {
  return `<button type="button" role="switch" class="switch shrink-0" id="${id}" aria-checked="${on}" aria-label="${esc(label)}"></button>`;
}

/** Choices that used to be asked every time: set once here. */
function optionsCard(p: ReclaimPrefs): string {
  const row = (id: string, on: boolean, title: string, hint: string) =>
    `<div class="flex items-start gap-3 px-4 py-3"><span class="min-w-0 flex-1"><span class="block text-sm font-medium">${esc(title)}</span><span class="block text-xs text-ink-3">${esc(hint)}</span></span>${sw(id, on, title)}</div>`;
  return `<section class="card mt-4 min-w-0 overflow-hidden">
    ${cardHead("Options", "sliders", "Saved for every account")}
    <div class="grid divide-y divide-line md:grid-cols-2 md:divide-y-0">
      <div class="divide-y divide-line md:border-r md:border-line">
        ${row("st-opt-confirm", p.confirm, "Ask before freeing", "Off: Free up starts at once, with the choices here.")}
        ${row("st-opt-keep", p.keep, "Keep the downloaded copies", "On: each copy stays on this server, under Downloads. Off: it is deleted once Google has the free upload.")}
      </div>
      <div class="divide-y divide-line">
        <div class="flex items-start gap-3 px-4 py-3"><span class="min-w-0 flex-1"><span class="block text-sm font-medium">Items at once</span><span class="block text-xs text-ink-3">Per account, for Free storage and Copy accounts. More is faster on a fast connection; each needs its size free here while it moves.</span></span>
          <select id="st-opt-workers" class="input w-20 shrink-0" aria-label="Items at once">${WORKER_CHOICES.map((n) => `<option value="${n}" ${n === p.workers ? "selected" : ""}>${n}</option>`).join("")}</select></div>
        ${row("st-opt-album", p.album, "Add freed items to an album", `Album “${p.album_name}” in Google Photos: an easy place to check them. Captions are never changed.`)}
      </div>
    </div>
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
  const workers = r.prefs?.workers ?? 4;
  html(
    $("#st-body"),
    `${accountStrip(r)}
    ${
      r.paused
        ? `<section class="card mb-4 flex gap-3 border-warn/40 p-4">${icon("pause", "mt-0.5 size-5 shrink-0 text-warn")}<div class="min-w-0 flex-1 text-sm"><div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1"><span class="font-medium">Freeing storage is paused in this account</span>${pauseBtn(true)}</div><p class="mt-1.5 text-ink-3">${r.left?.count ? `${num(r.left.count)} item${r.left.count === 1 ? "" : "s"} wait. ` : ""}Downloads stop at once and wait in the queue. An item already deleted in Google finishes its upload first, so nothing is lost.</p></div></section>`
        : ""
    }
    ${
      r.blocked
        ? `<section class="card mb-4 flex flex-wrap items-start gap-3 border-bad/40 p-4">${icon("warn", "size-5 shrink-0 text-bad")}<div class="min-w-0 flex-1 text-sm"><div class="font-medium">Stopped: Google counted a re-upload against storage</div><div class="mt-0.5 text-ink-3">Nothing more is moved. Downloaded files stay here, and nothing was deleted.</div></div><button class="btn sm" id="st-unblock">Try again anyway</button></section>`
        : ""
    }
    ${r.error ? `<section class="card mb-4 p-4 text-sm text-bad">${esc(r.error)}</section>` : ""}
    ${checkNote(r.check)}
    ${oursCount ? `<section class="card mb-4 flex items-start gap-3 p-4 text-sm">${icon("warn", "size-5 shrink-0 text-warn")}<div><div class="font-medium">GP Sync's uploads count against storage in this account</div><div class="mt-0.5 text-ink-3">Re-uploading wouldn't free anything, so nothing is deleted here.</div></div></section>` : ""}
    <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
      ${tile({ label: "Counts against storage", value: bytes(r.counted_bytes), ic: "cloud", sub: r.refreshing ? "Reading your library…" : `${num(r.counted)} of ${num(r.total)} items${r.refreshed ? ` · read ${ago(r.refreshed)}` : ""}` })}
      ${tile({ label: "Freed by GP Sync", value: bytes(r.freed.bytes), ic: "sparkles", sub: r.freed.count ? `${num(r.freed.count)} item${r.freed.count === 1 ? "" : "s"} · see details` : "Nothing freed yet", tone: r.freed.count ? "good" : "", href: `#/storage/${encodeURIComponent(account)}/freed` })}
      ${tile({
        label: "Moving now",
        value: `${num(working.size)} of ${workers}`,
        ic: "hourglass",
        sub: r.paused
          ? `Paused · ${num(r.left?.count ?? busy)} waiting`
          : r.left?.count
            ? `${num(r.left.count)} waiting in the queue`
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
          ? `<div class="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6" data-gallery>${listed
              .slice(0, shown)
              .map((i) => gridTile(i, r, working))
              .join(
                "",
              )}</div>${listed.length > shown ? `<div class="flex justify-center border-t border-line p-3"><button class="btn" id="st-more">${icon("plus")}Show more (${num(listed.length - shown)} left)</button></div>` : ""}`
          : `<div class="empty">${icon("shield")}No ${KIND_NAME[kind]} in this account count against storage</div>`
      }
    </section>
    ${r.prefs ? optionsCard(r.prefs) : ""}
    <section class="card mt-4">${cardHead("How it works", "info")}
      <div class="card-b grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div class="flex gap-3"><span class="grid size-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs">1</span><span class="text-sm"><b>Downloaded</b> here and checked against the checksum Google gives for it.</span></div>
        <div class="flex gap-3"><span class="grid size-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs">2</span><span class="text-sm"><b>Deleted in Google</b>, Trash included, only after the copy here is checked again.</span></div>
        <div class="flex gap-3"><span class="grid size-6 shrink-0 place-items-center rounded-full bg-panel-2 text-xs">3</span><span class="text-sm"><b>Uploaded again</b> the free way, then Google is asked that it doesn't count.</span></div>
      </div>
      <div class="border-t border-line px-4 py-3 text-xs text-ink-3">Up to ${workers} items at once (Options). Each item goes step by step and every step is saved, so an update or restart resumes safely. Keeps quality, date, favourite, archive and caption. Albums, shared links and comments are lost. A few videos download as a copy Google changed slightly (an edited date): that copy is used when it matches the checksum Google lists for it.</div>
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
    // The header's refresh button reads this account's library from Google here (no second button).
    setRefreshControl({
      label: r.refreshing ? "Reading your library from Google…" : `Refresh ${account} from Google`,
      busy: r.refreshing,
      run: async () => {
        await post("storage/refresh", { account });
        void load();
        return "Reading your library from Google";
      },
    });
    // The page's Pause/Resume buttons pause freeing in this account.
    const paused = !!r.paused;
    setPauseControl({
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
    });
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
  const workers = p?.workers ?? 4;
  const r = await confirmDialog({
    title: `Free up ${what}?`,
    icon: "sparkles",
    body: [
      `${count} in ${account}. Biggest first, up to ${workers} at once: each is downloaded here and checked, deleted in Google Photos (Trash included) and uploaded again for free.`,
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

function toggleOption(b: HTMLElement): void {
  const on = b.getAttribute("aria-checked") !== "true";
  b.setAttribute("aria-checked", String(on));
  if (b.id === "st-opt-confirm")
    void savePrefs({ confirm: on }, on ? "Free storage asks first" : "Free storage starts without asking");
  else if (b.id === "st-opt-keep")
    void savePrefs(
      { keep: on },
      on ? "Downloaded copies are kept" : "Downloaded copies are deleted after the upload",
    );
  else
    void savePrefs(
      { album: on },
      on
        ? `Freed items go into “${report?.prefs?.album_name ?? "Freed by GP Sync"}”`
        : "No album for freed items",
    );
}

function onClick(e: Event): void {
  const t = e.target as HTMLElement;
  const b = t.closest<HTMLElement>("button");
  if (!b) return;
  if (b.id === "st-read") void act("refresh", {}, "Reading your library from Google");
  else if (b.dataset.acc) {
    if (b.dataset.acc !== account) pickAccount(b.dataset.acc);
  } else if (b.id.startsWith("st-opt-")) toggleOption(b);
  else if (b.id === "st-unblock") {
    if (confirm("Google counted a re-upload against storage last time. Try again anyway?"))
      void act("check", { unblock: true });
  } else if (b.dataset.get) void start([b.dataset.get]);
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
      if (t.id === "st-opt-workers") {
        const n = Number(t.value);
        void savePrefs({ workers: n }, `Up to ${n} item${n === 1 ? "" : "s"} at once`);
      } else if (t.dataset.pick) {
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
