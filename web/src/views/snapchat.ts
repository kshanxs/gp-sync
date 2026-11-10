import { get, post, type SnapAction, type SnapCopy, type SnapItem, type SnapchatReport } from "../lib/api";
import { accountChips } from "../lib/account-menu";
import { confirmDialog } from "../lib/dialog";
import { $, animateChange, esc, html, toast } from "../lib/dom";
import { ago, fileName, num, pct } from "../lib/format";
import { icon } from "../lib/icons";
import { choices, matchBadge, missingNote, SNAP_ACTION_LABEL, SNAP_GROUPS } from "../lib/snapchat";
import { setPageStatus, setPauseControl, setRefreshControl, status } from "../lib/store";
import { badge, openAttrs, thumb, type Tone } from "../lib/ui";
import type { View } from "./view";

// Snapchat: memories from a Snapchat export (made into a folder by scripts/snapchat_export.py, with
// Snapchat's exact date and place in every file). Each is looked for in Google Photos first, so
// nothing is uploaded twice: ones Google has with the right date only go into the album, ones it
// has with a wrong date are replaced, the rest are uploaded. Nothing changes until Start.

let report: SnapchatReport | null = null;
let show: SnapAction = "replace";
let limit = 120;
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

function googleThumb(account: string, key: string, kind: string): string {
  return `<img class="thumb" loading="lazy" decoding="async" alt="" data-kind="${kind}" src="api/storage/thumb?account=${encodeURIComponent(account)}&key=${encodeURIComponent(key)}">`;
}

const ROLE: Record<SnapCopy["role"], [string, Tone]> = {
  keep: ["Stays", "good"],
  delete: ["Kept here, deleted from Google", "warn"],
  leave: ["From a camera: stays", ""],
};

/** One of Google's copies of a memory: its thumbnail, date and what happens to it. */
function copyTile(c: SnapCopy, i: SnapItem, r: SnapchatReport): string {
  const [label, tone] =
    c.state === "deleted" ? (["Deleted from Google · kept here", "good"] as const) : ROLE[c.role];
  const img =
    c.state === "deleted"
      ? `<span class="thumb grid place-items-center text-ink-3">${icon("trash")}</span>`
      : googleThumb(r.account, c.key, i.kind);
  const pic = c.url
    ? `<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer" title="Open in Google Photos">${img}</a>`
    : img;
  const late = !!c.taken && Math.abs(c.taken - i.taken) > 120;
  return `<div class="flex items-center gap-2 rounded-xl border border-line p-1.5 pr-3 text-xs">${pic}
    <div class="min-w-0"><span class="block text-ink-3">In Google${c.camera ? " · camera" : ""}</span><b class="${late ? "text-warn" : "text-ink-2"}">${esc(when(c.taken))}</b>
    <span class="mt-0.5 block">${badge(label, tone)}</span></div></div>`;
}

function stateBadge(i: SnapItem, r: SnapchatReport): string {
  if (r.current?.key === i.name)
    return badge(r.current.step === "deleting" ? "Replacing in Google" : "Working on it", "info", "loader");
  switch (i.state) {
    case "queued":
      return badge("Queued", "info", "hourglass");
    case "done":
      return badge(i.action === "album" ? "In the album" : "Done", "good", "ok");
    case "failed":
      return badge("Didn't work", "bad", "error");
    default:
      return "";
  }
}

function row(i: SnapItem, r: SnapchatReport): string {
  const [label, tone, tip] = matchBadge(i);
  const cur = r.current?.key === i.name ? r.current : null;
  const open = i.url || i.google?.url;
  const editable = !i.state || i.state === "failed";
  const select = editable
    ? `<select class="input w-auto py-1 text-xs" data-action="${esc(i.name)}" aria-label="What happens to ${esc(fileName(i.name))}">${choices(
        i,
      )
        .map(
          (a) => `<option value="${a}" ${a === i.action ? "selected" : ""}>${SNAP_ACTION_LABEL[a]}</option>`,
        )
        .join("")}</select>`
    : "";
  const place = i.place ? ` · ${i.place[0].toFixed(3)}, ${i.place[1].toFixed(3)}` : "";
  return `<div data-key="${esc(i.name)}" class="row flex-wrap items-start px-4 py-3 sm:flex-nowrap">
    <span class="flex shrink-0 gap-1.5">
      <span class="clickable" ${openAttrs(i.name)} title="The memory">${thumb(i.name)}</span>
    </span>
    <div class="min-w-0 flex-1 basis-48">
      <div class="flex flex-wrap items-center gap-2"><span class="truncate text-sm font-medium" title="${esc(i.name)}">${esc(fileName(i.name))}</span>${badge(label, tone, undefined, tip)}${stateBadge(i, r)}${i.decided ? badge("You chose", "warn") : ""}${i.kind === "video" ? `<span class="badge">${icon("video")}</span>` : ""}</div>
      <div class="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <div><span class="block text-ink-3">Snapchat</span><b class="text-ink-2">${esc(when(i.taken))}</b><span class="text-ink-3">${esc(place)}</span></div>
      </div>
      ${i.copies.length ? `<div class="mt-2 flex flex-wrap gap-2">${i.copies.map((c) => copyTile(c, i, r)).join("")}</div>` : ""}
      ${i.local ? `<div class="mt-1 truncate text-xs text-ink-3">Copy here: ${esc(i.local)}</div>` : ""}
      ${cur ? `<div class="bar mt-2"><i style="width:${pct(cur.done, cur.total)}%"></i></div>` : ""}
      ${i.error ? `<div class="mt-1 text-xs ${i.state === "failed" ? "text-bad" : "text-ink-3"}">${esc(i.error)}</div>` : ""}
    </div>
    <div class="row-actions flex w-full shrink-0 flex-wrap items-center justify-end gap-1.5 sm:w-auto">${select}${
      open
        ? `<a class="link-btn sm" href="${esc(open)}" target="_blank" rel="noopener noreferrer">${icon("external")}Open in Google Photos</a>`
        : ""
    }</div>
  </div>`;
}

function exportCard(r: SnapchatReport): string {
  const notes = [
    r.exporting.length
      ? `The export script is still adding memories to ${r.exporting.map(esc).join(", ")}: they wait until it's done.`
      : "",
    r.held
      ? `${num(r.held)} file${r.held === 1 ? " is" : "s are"} in the folder but not in its list, so ${r.held === 1 ? "it isn't" : "they aren't"} backed up. Run the script again to list them.`
      : "",
  ]
    .filter(Boolean)
    .map((n) => `<p class="mt-3 text-sm text-ink-2">${icon("info", "inline size-4 -mt-0.5")} ${n}</p>`)
    .join("");
  return r.manifests
    .map((m, n) => {
      const note = missingNote(m.export);
      return `<section class="card mb-4 p-4">
        <div class="flex flex-wrap items-center gap-3">
          <span class="grid size-10 place-items-center rounded-xl bg-panel-2 text-ink-2">${icon("folder", "size-5")}</span>
          <div class="min-w-0 flex-1"><h2 class="font-semibold">${num(m.export.found ?? r.memories)} memories in ${esc(m.folder)}</h2>
            <p class="text-xs text-ink-3">Snapchat's date, time and place are inside every file · album ${esc(r.album)} (<a class="underline" href="#/sources">change</a>)</p></div>
        </div>
        ${note ? `<p class="mt-3 text-sm text-warn">${icon("warn", "inline size-4 -mt-0.5")} ${esc(note)}. Download the missing parts from Snapchat and run the script again: only new memories are added.</p>` : ""}
        ${n === 0 ? notes : ""}
      </section>`;
    })
    .join("");
}

function searchCard(r: SnapchatReport): string {
  const steps = [
    ["Fingerprint the memories", r.fingerprinted, r.memories],
    ["Compare with Google's thumbnails", r.thumbs.done, r.thumbs.total],
    ["Match", r.checked, r.memories],
  ] as const;
  return `<section class="card mb-4 p-4" aria-live="polite">
    <h2 class="font-semibold">${icon("search", "inline size-4 -mt-0.5")} Looking for your memories in Google Photos</h2>
    <p class="mt-1 text-xs text-ink-3">${r.refreshed ? `Library read ${ago(r.refreshed)}` : "Reading your library from Google first"} · nothing changes in Google yet</p>
    ${r.slow_until ? `<p class="mt-2 text-xs text-warn">${icon("clock", "inline size-3.5 -mt-0.5")} Google asked GP Sync to slow down. Comparing goes on at ${esc(new Date(r.slow_until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}; what's done is kept.</p>` : ""}
    <div class="mt-3 space-y-2">${steps
      .map(
        ([label, done, total]) =>
          `<div class="text-xs"><div class="flex justify-between text-ink-2"><span>${label}</span><span>${total ? `${num(done)} of ${num(total)}` : "…"}</span></div><div class="bar mt-1"><i style="width:${total ? pct(done, total) : 0}%"></i></div></div>`,
      )
      .join("")}</div>
  </section>`;
}

function groupCards(r: SnapchatReport): string {
  return `<div class="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">${SNAP_GROUPS.map((g) => {
    const c = r.counts[g.id];
    const left = c.total - c.done - c.queued;
    const startable = g.id !== "skip" && left > 0 && !r.searching;
    return `<section class="card flex flex-col p-4 ${show === g.id ? "border-accent" : ""}">
      <button class="flex items-center gap-2 text-left" data-show="${g.id}">${icon(g.ic, "size-4 text-ink-3")}<span class="font-semibold">${g.title}</span></button>
      <div class="mt-1 text-2xl font-semibold tabular-nums">${num(c.total)}</div>
      <p class="mt-1 flex-1 text-xs text-ink-3">${g.hint}</p>
      ${
        g.id !== "skip" && c.total
          ? `<div class="mt-2 text-xs text-ink-3">${num(c.done)} done${c.queued ? ` · ${num(c.queued)} queued` : ""}${c.failed ? ` · <span class="text-bad">${num(c.failed)} didn't work</span>` : ""}</div><div class="bar mt-1"><i style="width:${pct(c.done, c.total)}%"></i></div>`
          : ""
      }
      ${startable ? `<button class="btn sm mt-3" data-start="${g.id}">${icon("play")}Start (${num(left)})</button>` : ""}
    </section>`;
  }).join("")}</div>`;
}

function render(): void {
  const r = report;
  const accs = status()?.accounts ?? [];
  html(
    $("#sc-chips"),
    r && r.memories
      ? accountChips(r.account, (a) =>
          a === r.account
            ? { line: "Memories go here", tone: "text-accent" }
            : { line: "Pick to send them here instead" },
        )
      : "",
  );
  if (!r) return;
  if (!accs.length) {
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
        <ol class="mt-3 list-decimal space-y-2 pl-5">
          <li>In Snapchat: Settings › My Data, tick <b>Memories</b> (with “Export your Memories”), and download every zip.</li>
          <li>On the server, run <code>python3 scripts/snapchat_export.py &lt;folder with the zips&gt; &lt;Photos/Snapchat&gt;</code> (needs exiftool and ffmpeg). It writes Snapchat's date, time and place into every file.</li>
          <li>The memories show up here. GP Sync looks for each one in Google Photos before anything is uploaded.</li>
        </ol>
      </section>`,
    );
    return;
  }
  const working = r.current != null || Object.values(r.counts).some((c) => c.queued > 0);
  setPageStatus(
    r.paused
      ? {
          tone: "warn",
          title: "Snapchat memories are paused",
          detail: `${r.account} · nothing is uploaded or deleted`,
        }
      : r.searching
        ? { tone: "run", title: "Looking for memories in Google Photos", detail: r.account }
        : working
          ? { tone: "run", title: "Sending Snapchat memories", detail: r.account }
          : { tone: "good", title: `${num(r.memories)} Snapchat memories`, detail: r.account },
  );
  setPauseControl(
    !(r.paused || working)
      ? null
      : {
          paused: r.paused,
          working,
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
  const left = (["album", "replace", "upload"] as const).reduce(
    (n, a) => n + r.counts[a].total - r.counts[a].done - r.counts[a].queued,
    0,
  );
  const group = SNAP_GROUPS.find((g) => g.id === show) ?? SNAP_GROUPS[0];
  html(
    $("#sc-body"),
    `${exportCard(r)}
    ${r.error && !r.slow_until ? `<section class="card mb-4 p-4 text-sm text-bad" role="alert">${esc(r.error)}</section>` : ""}
    ${r.searching ? searchCard(r) : ""}
    ${groupCards(r)}
    ${r.duplicates.total ? `<section class="card mb-4 flex flex-wrap items-center gap-3 p-4 text-sm"><span class="grid size-10 place-items-center rounded-xl bg-panel-2 text-ink-2">${icon("copy", "size-5")}</span><div class="min-w-0 flex-1"><b>${num(r.duplicates.total)} extra ${r.duplicates.total === 1 ? "copy" : "copies"} in Google</b><p class="text-xs text-ink-3">Each is kept on this server (Downloads, unless the same file is here) and then deleted from Google, so every memory is in Google once.</p></div><span class="text-xs text-ink-3">${num(r.duplicates.deleted)} deleted</span></section>` : ""}
    ${!r.searching && left ? `<div class="mb-4 flex justify-end"><button class="btn primary" data-start="all">${icon("play")}Start all (${num(left)})</button></div>` : ""}
    <section class="card min-w-0 overflow-hidden">
      <div class="flex flex-wrap items-center gap-3 border-b border-line p-4">
        <div class="min-w-0 flex-1 basis-64"><h2 class="font-semibold">${group.title}</h2>
          <p class="mt-1 text-xs text-ink-3">${group.hint} Pick another action per memory if a match looks wrong.</p></div>
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
    </section>`,
  );
}

async function start(which: SnapAction | "all"): Promise<void> {
  const r = report;
  if (!r) return;
  const actions = which === "all" ? (["album", "replace", "upload"] as SnapAction[]) : [which];
  const replace = actions.includes("replace") ? r.counts.replace.total - r.counts.replace.done : 0;
  const ok = await confirmDialog({
    title:
      which === "all"
        ? "Send all Snapchat memories?"
        : `${SNAP_GROUPS.find((g) => g.id === which)?.title}: start?`,
    icon: "sparkles",
    body: [
      `In ${r.account}: memories Google has with the right date go into the album ${r.album}; the others are uploaded with Snapchat's date and place, and added to it too.`,
      ...(r.duplicates.total - r.duplicates.deleted
        ? [
            `In all, ${num(r.duplicates.total - r.duplicates.deleted)} copies in Google are kept on this server, then deleted from Google, so no memory is there twice.`,
          ]
        : []),
      ...(replace
        ? [
            `${num(replace)} memories take the place of copies Google has with a later date: those copies are kept on this server, deleted from Google, and the memory is uploaded. Albums you made yourself don't carry over. Favourite, archive and your caption come back; shared links and comments don't.`,
          ]
        : []),
    ],
    confirm: "Start",
  });
  if (!ok) return;
  try {
    const res = await post<{ queued: number }>("snapchat/start", { account: r.account, actions });
    toast(
      res.queued ? `Started ${num(res.queued)} memories` : "Nothing to start",
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
  const b = (e.target as HTMLElement).closest<HTMLElement>("button");
  if (!b) return;
  if (b.dataset.acc) void pickAccount(b.dataset.acc);
  else if (b.dataset.start) void start(b.dataset.start as SnapAction | "all");
  else if (b.dataset.show) {
    if (b.dataset.show === show) return;
    show = b.dataset.show as SnapAction;
    limit = 120;
    void load().then(() => animateChange(document.getElementById("sc-list")));
  } else if (b.id === "sc-more") {
    limit += 120;
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
    limit = 120;
    el.innerHTML = `
    <div id="sc-chips"></div>
    <details class="mb-4 px-1 text-sm text-ink-3">
      <summary class="cursor-pointer font-medium text-ink-2">How it works</summary>
      <div class="mt-2 space-y-2 leading-relaxed">
        <p>Each memory is looked for in Google Photos: the same file, a photo or video taken in the same second (a snap saved to the camera roll), or one that looks the same (compared with Google's thumbnail).</p>
        <p>Snapchat's date wins. Copies Google has with another date are replaced by the memory; copies on this server aren't uploaded as well. Every memory goes into the album.</p>
      </div>
    </details>
    <div id="sc-body"><div class="card skeleton h-72"></div></div>`;
    el.addEventListener("click", onClick);
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
