import { get, peek, post, type GoneItem, type GoneReport } from "../lib/api";
import { confirmDialog } from "../lib/dialog";
import { $, esc, html, toast } from "../lib/dom";
import { ago, bytes, date, fileName, folderOf, num } from "../lib/format";
import { icon } from "../lib/icons";
import { badge, cardHead, openAttrs, thumb } from "../lib/ui";
import type { View } from "./view";

// Files GP Sync sent to Google Photos that were deleted there since, in one account or all of them.
// They are never uploaded again. Here: delete them on this device, in the accounts that still have
// them, or both; or keep them.

/** Read the Google libraries again by themselves when the oldest read is older than this. */
const STALE = 6 * 3600;

type Filter = "all" | "everywhere" | "some";
const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["everywhere", "Gone everywhere"],
  ["some", "Still in an account"],
];

let report: GoneReport | null = null;
let opened = false;
let asked = false;
let filter: Filter = "all";
const picked = new Set<string>();

async function load(): Promise<void> {
  const seen = opened ? peek<GoneReport>("gone") : undefined;
  opened = false;
  if (seen) {
    report = seen;
    render();
  }
  report = await get<GoneReport>("gone").catch(() => null);
  const live = new Set(report?.items.map((i) => i.path));
  for (const p of picked) if (!live.has(p)) picked.delete(p);
  render();
  maybeRefresh();
}

/** Opening the page checks Google again when its lists are old (once per visit). */
function maybeRefresh(): void {
  const r = report;
  if (!r || asked || r.refreshing.length) return;
  const reads = Object.values(r.read);
  const now = Date.now() / 1000;
  if (reads.length && reads.some((t) => t && now - t > STALE)) {
    asked = true;
    void post("gone/refresh", {}).then(load);
  }
}

const short = (a: string) => a.split("@")[0] ?? a;

function chips(i: GoneItem, multi: boolean): string {
  return i.accounts
    .map((a) => {
      const who = multi ? ` · ${short(a.account)}` : "";
      if (a.state === "trash")
        return badge(`${a.by_gp ? "Moved to Trash" : "In Google's Trash"}${who}`, "warn", "trash",
          a.trash ? `Since ${date(a.trash, true)}: Google Photos can still restore it for a while` : undefined); // prettier-ignore
      if (a.state === "deleted") return badge(`Deleted in Google${who}`, "bad", "cloudOff");
      if (a.state === "present") return badge(`Still in ${short(a.account)}`, "", "cloud");
      if (a.state === "ours")
        return badge(
          `Handled by GP Sync${who}`,
          "",
          "verified",
          "Removed or replaced there by GP Sync, not by you",
        );
      return badge(`Not checked yet${who}`, "", "clock");
    })
    .join("");
}

function shown(r: GoneReport): GoneItem[] {
  if (filter === "everywhere") return r.items.filter((i) => i.everywhere);
  if (filter === "some") return r.items.filter((i) => i.in_google.length);
  return r.items;
}

/** `mixed`: some files can be deleted here and some can't, so each row says which. */
function row(i: GoneItem, multi: boolean, mixed: boolean): string {
  const canAct = i.writable || i.in_google.length > 0;
  const note =
    !i.writable && mixed
      ? `<div class="mt-1 text-xs text-ink-3">This folder is read-only for GP Sync${i.in_google.length ? "" : ": delete it yourself, or keep it"}.</div>`
      : "";
  return `<div class="row flex-wrap items-start px-4 py-3 sm:flex-nowrap" data-key="${esc(i.path)}">
    ${canAct ? `<input type="checkbox" class="mt-3 size-4 accent-[var(--accent)]" data-pick="${esc(i.path)}" ${picked.has(i.path) ? "checked" : ""} aria-label="Pick ${esc(i.name)}">` : '<span class="w-4 shrink-0"></span>'}
    <span class="clickable" ${openAttrs(i.path)}>${thumb(i.path, "thumb", Math.round(i.mtime))}</span>
    <div class="min-w-0 flex-1 basis-48">
      <div class="flex flex-wrap items-center gap-2"><span class="truncate text-sm font-medium" title="${esc(i.path)}">${esc(fileName(i.path))}</span>${i.kind === "video" ? `<span class="badge">${icon("video")}</span>` : ""}</div>
      <div class="truncate text-xs text-ink-3">${esc(folderOf(i.path) || "Top folder")} · ${bytes(i.size)}${i.seen ? ` · noticed ${ago(i.seen)}` : ""}</div>
      <div class="gone-chips mt-1.5 flex min-w-0 flex-wrap gap-1.5">${chips(i, multi)}</div>
      ${note}
    </div>
    <div class="row-actions flex w-full shrink-0 items-center justify-end gap-1.5 sm:w-auto">
      <button class="btn sm" data-keep="${esc(i.path)}">${icon("check")}Keep</button>
      ${canAct ? `<button class="btn sm danger" data-del="${esc(i.path)}">${icon("trash")}Delete</button>` : ""}
    </div></div>`;
}

function readLine(r: GoneReport): string {
  if (r.refreshing.length) return `${icon("refresh", "inline size-3.5 spin")} Checking Google Photos…`;
  const reads = Object.values(r.read).filter(Boolean);
  if (!reads.length) return "Google Photos hasn't been checked yet: press Check now";
  return `Google Photos checked ${ago(Math.min(...reads))} · checked again every few hours`;
}

function render(): void {
  const el = $("#gone-body");
  const r = report;
  if (!r) return html(el, `<div class="card empty">${icon("warn")}Couldn't load this page</div>`);
  const multi = Object.keys(r.read).length > 1;
  const list = shown(r);
  const pickable = list.filter((i) => i.writable || i.in_google.length);
  const head = `<section class="card flex flex-wrap items-center gap-3 p-4">
      <div class="min-w-0 basis-full sm:basis-0 sm:flex-1">
        <div class="text-base font-medium">${r.total ? `${num(r.total)} ${r.total === 1 ? "file" : "files"} deleted in Google Photos · ${bytes(r.bytes)} here` : "Nothing deleted in Google Photos"}</div>
        <div class="text-xs text-ink-3">${readLine(r)}${r.kept ? ` · ${num(r.kept)} kept <button class="link-btn" data-unkeep>Show again</button>` : ""}</div>
      </div>
      <button class="btn sm" data-refresh ${r.refreshing.length ? "disabled" : ""}>${icon("refresh")}Check now</button>
      ${r.total ? `<button class="btn sm danger" data-del-picked ${picked.size ? "" : "disabled"}>${icon("trash")}${picked.size ? `Delete ${num(picked.size)}` : "Delete selected"}</button>` : ""}
    </section>`;
  if (!r.total) {
    return html(
      el,
      `${head}<section class="card mt-4 p-6 text-center"><div class="mx-auto max-w-md space-y-2">${icon("check", "mx-auto size-8 text-ink-3")}
        <div class="text-base font-medium">Everything here is still in Google Photos</div>
        <p class="text-sm text-ink-3">When you delete a photo or video in Google Photos that GP Sync backed up, in any of your accounts, it shows up here. Only your own deletions count: items GP Sync removed or replaced itself (Live Photos, Free storage, Snapchat copies) never do. GP Sync won't upload it again, and you can delete it on this device and in your other accounts too.</p></div></section>`,
    );
  }
  const roNote =
    r.writable < r.total
      ? `<p class="card mt-4 p-4 text-sm text-ink-2">${icon("info", "mr-1 inline size-4 text-ink-3")}GP Sync reads your photo folders read-only, so it can only delete files here that are in ${r.upload_folder ? `the upload folder (${esc(r.upload_folder)})` : "folders it can write to"}. ${num(r.total - r.writable)} of these are elsewhere: delete them yourself, or keep them.</p>`
      : "";
  const tabs = multi
    ? `<div class="seg max-w-full overflow-x-auto whitespace-nowrap" id="gone-filter">${FILTERS.map(([k, l]) => `<button data-filter="${k}" aria-pressed="${k === filter}">${l}${k === "everywhere" ? ` · ${num(r.everywhere)}` : k === "some" ? ` · ${num(r.in_google)}` : ""}</button>`).join("")}</div>`
    : "";
  const more =
    r.total > r.items.length
      ? `<p class="border-t border-line px-4 py-2.5 text-xs text-ink-3">Showing ${num(r.items.length)} of ${num(r.total)}.</p>`
      : "";
  html(
    el,
    `${head}${roNote}
    ${tabs ? `<div class="mt-4">${tabs}</div>` : ""}
    <section class="card mt-4 min-w-0 overflow-hidden">${cardHead("Newest first", "trash", pickable.length ? `<button class="link-btn" data-all>${pickable.every((i) => picked.has(i.path)) ? "Select none" : "Select all"}</button>` : "")}
      <div class="divide-y divide-line">${list.length ? list.map((i) => row(i, multi, r.writable > 0)).join("") : `<div class="empty p-6 text-sm text-ink-3">Nothing here</div>`}</div>${more}
      <p class="border-t border-line px-4 py-2.5 text-xs text-ink-3">GP Sync never uploads these again, to any account. Deleting here removes the file from ${r.read_only ? "the upload folder" : "this device"} for good; deleting in an account moves it to that account's Trash in Google Photos.</p>
    </section>`,
  );
}

async function remove(paths: string[]): Promise<void> {
  const items = (report?.items ?? []).filter((i) => paths.includes(i.path));
  if (!items.length) return;
  const here = items.filter((i) => i.writable);
  const elsewhere = items.filter((i) => i.in_google.length);
  const accounts = [...new Set(elsewhere.flatMap((i) => i.in_google))];
  const one = items.length === 1;
  const what = one ? fileName(items[0]!.path) : `${num(items.length)} files`;
  const size = here.reduce((n, i) => n + i.size, 0);
  const body = [
    one
      ? `It was deleted in Google Photos${
          items[0]!.everywhere
            ? ""
            : ` in ${items[0]!.accounts
                .filter((a) => a.state === "deleted" || a.state === "trash")
                .map((a) => a.account)
                .join(", ")}`
        }.`
      : `They were deleted in Google Photos in at least one account.`,
    here.length
      ? `${here.length === items.length ? (one ? "The file" : "The files") : `${num(here.length)} of them`} (${bytes(size)}) ${here.length === 1 ? "is" : "are"} deleted on this device for good.`
      : "Nothing is deleted on this device (its folder is read-only for GP Sync).",
    "GP Sync checks Google once more first, and leaves anything that is back in Google.",
  ];
  const ok = await confirmDialog({
    title: here.length ? `Delete ${what}?` : `Delete ${what} in Google Photos?`,
    icon: "trash",
    body,
    confirm: here.length ? "Delete" : "Move to Trash",
    danger: true,
    checkbox:
      elsewhere.length && here.length
        ? {
            label: `Also delete in ${accounts.join(", ")}`,
            hint: `${num(elsewhere.length)} ${elsewhere.length === 1 ? "is" : "are"} still there; ${elsewhere.length === 1 ? "it goes" : "they go"} to that account's Trash in Google Photos.`,
            checked: true,
          }
        : undefined,
  });
  if (!ok) return;
  const google = elsewhere.length > 0 && (!here.length || ok.checked);
  const res = await post<{
    deleted: number;
    bytes: number;
    trashed: number;
    failed: { path: string; error: string }[];
  }>("gone/delete", { paths, local: here.length > 0, google }).catch((e: Error) => {
    toast(e.message, "bad");
    return null;
  });
  if (!res) return;
  for (const p of paths) picked.delete(p);
  const done = [
    res.deleted ? `deleted ${num(res.deleted)} here (${bytes(res.bytes)})` : "",
    res.trashed ? `moved ${num(res.trashed)} to Google's Trash` : "",
  ].filter(Boolean);
  if (done.length)
    toast(
      done.join(", ").replace(/^./, (c) => c.toUpperCase()),
      "good",
    );
  const failed = res.failed.filter((f) => !/read-only/.test(f.error) || !google);
  if (failed.length) toast(`${num(failed.length)} not done: ${failed[0]!.error}`, "bad");
  void load();
}

async function onClick(e: Event): Promise<void> {
  const t = e.target as HTMLElement;
  const b = t.closest<HTMLElement>(
    "[data-refresh],[data-del],[data-del-picked],[data-keep],[data-all],[data-unkeep],[data-filter]",
  );
  if (!b) return;
  if (b.dataset.refresh !== undefined) {
    await post("gone/refresh", {});
    void load();
  } else if (b.dataset.del) {
    await remove([b.dataset.del]);
  } else if (b.dataset.delPicked !== undefined) {
    if (picked.size) await remove([...picked]);
  } else if (b.dataset.keep) {
    const p = b.dataset.keep;
    await post("gone/keep", { paths: [p] });
    picked.delete(p);
    toast(`Keeping ${fileName(p)} here. It still won't be uploaded again`, "good");
    void load();
  } else if (b.dataset.all !== undefined && report) {
    const all = shown(report).filter((i) => i.writable || i.in_google.length);
    if (all.every((i) => picked.has(i.path))) all.forEach((i) => picked.delete(i.path));
    else all.forEach((i) => picked.add(i.path));
    render();
  } else if (b.dataset.unkeep !== undefined) {
    await post("gone/keep", { all: true, keep: false });
    void load();
  } else if (b.dataset.filter) {
    filter = b.dataset.filter as Filter;
    render();
  }
}

function onChange(e: Event): void {
  const box = (e.target as HTMLElement).closest<HTMLInputElement>("[data-pick]");
  if (!box) return;
  const p = box.dataset.pick ?? "";
  if (box.checked) picked.add(p);
  else picked.delete(p);
  render();
}

const gone: View = {
  subtitle: "Deleted in Google Photos, still on this device",
  how: [
    {
      ic: "cloud",
      title: "Watches Google",
      text: "Every few hours GP Sync reads each account's Google Photos library and looks for the files it backed up.",
    },
    {
      ic: "trash",
      title: "Notices what you deleted",
      text: "Anything you deleted there, or put in its Trash, is noted in Activity and never uploaded again.",
    },
    {
      ic: "check",
      title: "You decide",
      text: "Delete it on this device, in your other accounts, or both. Or keep it. Nothing is deleted on its own.",
    },
  ],
  mount(el) {
    opened = true;
    asked = false;
    filter = "all";
    picked.clear();
    el.innerHTML = `<div id="gone-body"><div class="card skeleton h-40"></div></div>`;
    el.addEventListener("click", (e) => void onClick(e));
    el.addEventListener("change", onChange);
    void load();
  },
  update() {
    if (report?.refreshing.length) void load();
  },
};

export default gone;
