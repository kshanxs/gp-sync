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
  /** Tooltip on the name, instead of the name itself. */
  title?: string;
  /** Parked between steps (not being worked on): such items with the same step and note share one
   * row ("307 items"), so a long queue doesn't fill the card. */
  waiting?: boolean;
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

/** One colour per group, used by its chip, its part of the summary bar and its rows. Upload and
 * download keep the network chart's colours. */
const GROUP_COLOR: Record<string, string> = {
  uploading: "var(--s1)",
  downloading: "var(--s2)",
  copying: "var(--s3)",
  checking: "var(--s5)",
  replacing: "var(--s4)",
  reading: "var(--s3)",
  working: "var(--accent-fill)",
  waiting: "var(--ink-3)",
};
const colorOf = (g: string) => GROUP_COLOR[g] ?? "var(--accent-fill)";

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
    const g = groupOf(i);
    n.set(g, (n.get(g) ?? 0) + 1);
  }
  const rank = (g: string) => (GROUP_ORDER.includes(g) ? GROUP_ORDER.indexOf(g) : -1);
  return [...n].sort((a, b) => rank(a[0]) - rank(b[0]));
}

const groupOf = (i: WorkItem) => i.group ?? stepOf(i).group;

/** The summary bar's parts, one per group in the chips' order: its share of the items (width), and
 * how far its items are on average (fill, 0–100 of its part; ones without a share count as 0), or
 * null when none has a share. So the bar's filled length is the average over every item. */
export function segments(
  items: WorkItem[],
): { group: string; n: number; width: number; fill: number | null }[] {
  return groups(items).map(([group, n]) => {
    const known = items
      .filter((i) => groupOf(i) === group)
      .map(itemPct)
      .filter((p) => p != null);
    return {
      group,
      n,
      width: (n / items.length) * 100,
      fill: known.length ? known.reduce((a, p) => a + p, 0) / n : null,
    };
  });
}

/** Average share done over all items (those without a share count as 0), or null if none has one. */
export function average(items: WorkItem[]): number | null {
  const known = items.map(itemPct).filter((p) => p != null);
  return known.length ? known.reduce((a, p) => a + p, 0) / items.length : null;
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

/** The list's rows: one per item at work, then one per kind of wait (items parked on the same step
 * with the same note), with how many and their names in its tooltip. A wait of one keeps its row. */
export function listRows(items: WorkItem[]): WorkItem[] {
  const out: WorkItem[] = [];
  const parked = new Map<string, WorkItem[]>();
  for (const i of items) {
    if (!i.waiting) out.push(i);
    else {
      const k = `${i.step}\u0000${i.label ?? ""}\u0000${i.note ?? ""}`;
      parked.set(k, [...(parked.get(k) ?? []), i]);
    }
  }
  for (const [k, list] of parked) {
    const first = list[0]!;
    if (list.length === 1) out.push(first);
    else
      out.push({
        ...first,
        key: `waiting:${k}`,
        name: `${num(list.length)} items`,
        title: list
          .slice(0, 30)
          .map((i) => i.name)
          .join(", ")
          .concat(list.length > 30 ? ` and ${num(list.length - 30)} more` : ""),
        pct: list.every((i) => i.pct === first.pct) ? first.pct : undefined,
        href: undefined,
      });
  }
  return out;
}

function row(i: WorkItem): string {
  const s = stepOf(i);
  const p = itemPct(i);
  const ic = i.ic ?? s.ic;
  const spin = ic === "loader";
  const right = s.bytes && i.total ? `${bytes(i.done ?? 0)} of ${bytes(i.total)}` : "";
  const tag = i.href ? "a" : "div";
  return `<li data-key="${esc(i.key)}" style="--step:${colorOf(groupOf(i))}"><${tag} class="work-row"${i.href ? ` href="${esc(i.href)}"` : ""}>
    <span class="work-ic">${icon(ic, `size-4 ${spin ? "spin" : ""}`)}</span>
    <span class="min-w-0 flex-1">
      <span class="flex items-baseline gap-2"><span class="truncate text-sm font-medium" title="${esc(i.title ?? i.name)}">${esc(i.name)}</span>${p != null && i.pct == null ? `<span class="ml-auto shrink-0 text-xs font-medium tabular-nums">${Math.floor(p)}%</span>` : ""}</span>
      <span class="bar mt-1.5 block h-1.5 ${p == null ? "indeterminate" : "active"}"><i style="width:${p ?? 35}%"></i></span>
      ${
        i.note
          ? `<span class="mt-1 block truncate text-xs text-ink-3" title="${esc(i.note)}">${esc(i.label ?? s.label)} · ${esc(i.note)}</span>`
          : `<span class="mt-1 flex justify-between gap-2 text-xs text-ink-3 tabular-nums"><span class="min-w-0 truncate">${esc(i.label ?? s.label)}</span>${right ? `<span class="shrink-0">${esc(right)}</span>` : ""}</span>`
      }
    </span>
  </${tag}></li>`;
}

/** One bar for the whole card: a part per group in its colour, as wide as its share of the items
 * and filled as far as they are on average. A group with no share to show slides (or rests, when
 * waiting). */
function summaryBar(items: WorkItem[]): string {
  return `<span class="work-bar">${segments(items)
    .map((sg) => {
      const tip = `${num(sg.n)} ${sg.group}${sg.fill != null ? ` · ${Math.floor(sg.fill)}% on average` : ""}`;
      const fill =
        sg.fill != null
          ? `<i style="width:${sg.fill}%"></i>`
          : sg.group === "waiting"
            ? ""
            : '<i class="slide"></i>';
      return `<span style="--step:${colorOf(sg.group)};width:${sg.width}%" title="${esc(tip)}">${fill}</span>`;
    })
    .join("")}</span>`;
}

/** The summary and the scrolling list, to put inside a card. "" when nothing is under way. */
export function workBox(items: WorkItem[]): string {
  if (!items.length) return "";
  const g = groups(items);
  const t = totals(items);
  const avg = average(items);
  const under = [
    avg != null ? `<span>${Math.floor(avg)}% on average</span>` : "",
    t.total ? `<span>${bytes(t.done)} of ${bytes(t.total)}</span>` : "",
  ].join("");
  return `<div class="work-box">
    <div class="work-sum">
      <div class="even">${g
        .map(
          ([name, n]) =>
            `<span class="work-chip" style="--step:${colorOf(name)}">${icon(GROUP_IC[name] ?? "loader", "size-3.5 shrink-0")}<b class="tabular-nums">${num(n)}</b><span class="truncate">${esc(name)}</span></span>`,
        )
        .join("")}</div>
      <div class="mt-3">${summaryBar(items)}</div>
      ${under ? `<div class="mt-1.5 flex justify-between gap-2 text-xs text-ink-3 tabular-nums">${under}</div>` : ""}
    </div>
    <ul class="work-list scroll-thin">${listRows(items).map(row).join("")}</ul>
  </div>`;
}

/** The count in a work card's title, the same on every page: "12 running", or "300 waiting" when
 * all of it waits. */
export function workCount(items: WorkItem[]): string {
  const running = items.filter((i) => !i.waiting).length;
  if (running) return `<span class="badge info">${num(running)} running</span>`;
  return items.length
    ? `<span class="badge">${num(items.length)} waiting</span>`
    : `<span class="text-xs text-ink-3">Nothing running</span>`;
}

/** workBox in a card of its own, with a title (and buttons on the right). */
export function workPanel(title: string, ic: IconName, items: WorkItem[], extra = ""): string {
  if (!items.length) return "";
  return `<section class="card min-w-0 overflow-hidden">${cardHead(title, ic, `${extra}${workCount(items)}`)}${workBox(items)}</section>`;
}
