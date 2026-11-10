import { esc } from "./dom";
import { bytes, num, pct } from "./format";
import { icon, type IconName } from "./icons";
import { cardHead } from "./ui";

// Work under way, the same on every page (Free storage, Copy accounts, Sync, Snapchat, Overview):
// a summary on top (how many are on each step, and how far they are together), then the items in
// a list that scrolls inside its own box, so many at once never stretch the page. Rows are keyed
// (data-key), so live updates move them in place and the list keeps its scroll position.

export interface WorkItem {
  key: string;
  name: string;
  /** The step it is on: one of STEPS, or any word (then `label` says it). */
  step: string;
  /** Bytes moved so far and in all, when the step counts bytes. */
  done?: number;
  total?: number;
  /** Shown instead of the step's own name ("Copying from umbrel"). */
  label?: string;
  /** Right-hand text instead of "2 MB of 5 MB" ("Trying again soon"). */
  note?: string;
  /** A share to draw (0–100) when it isn't bytes: 100 for an item downloaded and waiting. */
  pct?: number;
  /** The summary group, when not the step's own ("Free storage" on Overview). */
  group?: string;
  ic?: IconName;
  href?: string;
}

const STEPS: Record<string, { label: string; group: string; ic: IconName; bytes: boolean }> = {
  downloading: { label: "Downloading", group: "downloading", ic: "download", bytes: true },
  keeping: { label: "Saving a copy here", group: "downloading", ic: "download", bytes: true },
  local: { label: "Copying from this server", group: "copying", ic: "server", bytes: true },
  hashing: { label: "Checking", group: "checking", ic: "search", bytes: false },
  checking: { label: "Checking with Google", group: "checking", ic: "loader", bytes: false },
  uploading: { label: "Uploading", group: "uploading", ic: "upload", bytes: true },
  finalizing: { label: "Finishing", group: "uploading", ic: "upload", bytes: false },
  deleting: { label: "Replacing in Google", group: "replacing", ic: "loader", bytes: false },
  downloaded: { label: "Downloaded", group: "waiting", ic: "hourglass", bytes: false },
  in_google: { label: "Replacing in Google", group: "waiting", ic: "hourglass", bytes: false },
  waiting: { label: "Waiting", group: "waiting", ic: "hourglass", bytes: false },
  reading: { label: "Reading library", group: "reading", ic: "refresh", bytes: false },
  working: { label: "Working", group: "working", ic: "loader", bytes: false },
};
const GROUP_ORDER = [
  "uploading",
  "downloading",
  "copying",
  "checking",
  "replacing",
  "reading",
  "working",
  "waiting",
];
const GROUP_IC: Record<string, IconName> = {
  uploading: "upload",
  downloading: "download",
  copying: "server",
  checking: "search",
  replacing: "refresh",
  waiting: "hourglass",
  reading: "refresh",
  working: "loader",
};

function stepOf(i: WorkItem) {
  return STEPS[i.step] ?? { label: i.step, group: i.step, ic: "loader" as IconName, bytes: !!i.total };
}

/** Share drawn for one item, or null for a sliding bar (no byte count yet). */
export function itemPct(i: WorkItem): number | null {
  if (i.pct != null) return i.pct;
  const s = stepOf(i);
  return s.bytes && i.total && i.done ? pct(i.done, i.total) : null;
}

/** Counts per group, in a fixed order: [["uploading", 3], ["checking", 1]]. */
export function groups(items: WorkItem[]): [string, number][] {
  const n = new Map<string, number>();
  for (const i of items) {
    const g = i.group ?? stepOf(i).group;
    n.set(g, (n.get(g) ?? 0) + 1);
  }
  const rank = (g: string) => (GROUP_ORDER.includes(g) ? GROUP_ORDER.indexOf(g) : -1);
  return [...n].sort((a, b) => rank(a[0]) - rank(b[0]));
}

/** Bytes done and in all, over the items that count bytes now. */
export function totals(items: WorkItem[]): { done: number; total: number } {
  let done = 0;
  let total = 0;
  for (const i of items)
    if (stepOf(i).bytes && i.total) {
      done += i.done ?? 0;
      total += i.total;
    }
  return { done, total };
}

function row(i: WorkItem): string {
  const s = stepOf(i);
  const p = itemPct(i);
  const ic = i.ic ?? s.ic;
  const spin = ic === "loader";
  const right =
    i.note ?? (s.bytes && i.total ? `${bytes(i.done ?? 0)} of ${bytes(i.total)}` : s.bytes ? "" : "");
  const tag = i.href ? "a" : "div";
  return `<li data-key="${esc(i.key)}"><${tag} class="work-row"${i.href ? ` href="${esc(i.href)}"` : ""}>
    <span class="work-ic">${icon(ic, `size-4 ${spin ? "spin" : ""}`)}</span>
    <span class="min-w-0 flex-1">
      <span class="flex items-baseline gap-2"><span class="truncate text-sm font-medium" title="${esc(i.name)}">${esc(i.name)}</span>${p != null && i.pct == null ? `<span class="ml-auto shrink-0 text-xs font-semibold tabular-nums">${Math.floor(p)}%</span>` : ""}</span>
      <span class="bar mt-1.5 block h-1.5 ${p == null ? "indeterminate" : "active"}"><i style="width:${p ?? 35}%"></i></span>
      <span class="mt-1 flex justify-between gap-2 text-xs text-ink-3 tabular-nums"><span class="truncate">${esc(i.label ?? s.label)}</span>${right ? `<span class="shrink-0 truncate">${esc(right)}</span>` : ""}</span>
    </span>
  </${tag}></li>`;
}

/** The summary and the scrolling list, to put inside a card. "" when nothing is under way. */
export function workBox(items: WorkItem[]): string {
  if (!items.length) return "";
  const g = groups(items);
  const t = totals(items);
  return `<div class="work-box">
    <div class="work-sum">
      <div class="flex flex-wrap items-center gap-1.5">${g
        .map(
          ([name, n]) =>
            `<span class="work-chip">${icon(GROUP_IC[name] ?? "loader", "size-3.5")}<b class="tabular-nums">${num(n)}</b> ${esc(name)}</span>`,
        )
        .join("")}</div>
      ${
        t.total
          ? `<div class="mt-2 flex items-center gap-3"><span class="bar block h-1.5 flex-1"><i style="width:${pct(t.done, t.total)}%"></i></span><span class="shrink-0 text-xs text-ink-3 tabular-nums">${bytes(t.done)} of ${bytes(t.total)}</span></div>`
          : ""
      }
    </div>
    <ul class="work-list scroll-thin">${items.map(row).join("")}</ul>
  </div>`;
}

/** workBox in a card of its own, with a title (and buttons on the right). */
export function workPanel(title: string, ic: IconName, items: WorkItem[], extra = ""): string {
  if (!items.length) return "";
  return `<section class="card min-w-0 overflow-hidden">${cardHead(title, ic, `${extra}<span class="text-xs text-ink-3">${num(items.length)}</span>`)}${workBox(items)}</section>`;
}
