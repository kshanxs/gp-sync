// Check first, then sync (see queue_prechecks in sync.py): an account added to a folder that is
// already backed up is compared with its Google library before anything is sent. These lines show
// the check on Sync's account cards and in the open folder's header on Folders, with Start syncing
// once it's done. One click handler for every page (wired in main.ts).

import { post, type Precheck, type Status } from "./api";
import { esc, toast } from "./dom";
import { bytes, num, pct, remaining } from "./format";
import { icon } from "./icons";
import { openFoldersSheet } from "./folder-accounts";
import { refresh } from "./store";

const LATER = "gp.precheckLater";

function laterSet(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(LATER) || "[]") as string[]);
  } catch {
    return new Set();
  }
}

const keyOf = (c: Precheck) => `${c.folder}|${c.account}|${c.finished ?? 0}`;

/** The checks for an account (and folder), oldest first. */
export function prechecksFor(s: Status | null, account: string, folder = ""): Precheck[] {
  return (s?.prechecks ?? []).filter((c) => c.account === account && (!folder || c.folder === folder));
}

/** "about 2 h at today's pace": from the upload speed of the last minutes, when there is one. */
function etaText(c: Precheck, s: Status | null): string {
  const bps = s?.net.up_avg ?? 0;
  if (bps <= 1000 || !c.new_bytes) return "";
  const left = remaining(c.new_bytes / bps);
  return ` · ${left.startsWith("under") ? left : `about ${left}`} at today's pace`;
}

/** One line per check: what it is doing, or what it found with Start syncing. */
export function precheckLines(s: Status | null, checks: Precheck[], named = false): string {
  if (!checks.length) return "";
  const later = laterSet();
  return `<div class="pc-list">${checks
    .map((c) => {
      const who = named ? esc(c.account) : "This account";
      const where = esc(c.folder);
      const arg = esc(`${c.account}|${c.folder}`);
      if (c.state === "waiting" && s?.paused)
        return `<div class="pc-row">${icon("pause", "size-4 text-warn")}<span class="min-w-0 flex-1"><b>${where}</b> will be checked when you resume. Nothing is sent yet.</span></div>`;
      if (c.state === "waiting" || c.state === "reading")
        return `<div class="pc-row">${icon("loader", "size-4 spin text-accent")}<span class="min-w-0 flex-1"><b>Checking ${where}</b> · reading ${named ? `${esc(c.account.split("@")[0])}'s` : "its"} Google library. Nothing is sent yet.</span></div>`;
      if (c.state === "comparing")
        return `<div class="pc-row flex-col items-stretch">
          <div class="flex items-center gap-2">${icon("loader", "size-4 spin text-accent")}<span class="min-w-0 flex-1"><b>Checking ${where}</b> · <span class="meta">${num(c.done)} of ${num(c.total)}</span> compared</span></div>
          <span class="bar"><i style="width:${pct(c.done, c.total)}%"></i></span></div>`;
      if (c.state === "failed")
        return `<div class="pc-row">${icon("warn", "size-4 text-warn")}<span class="min-w-0 flex-1"><b>Couldn't check ${where}</b> · ${esc(c.error || "the library couldn't be read")}</span>
          <span class="pc-actions"><button type="button" class="btn sm" data-precheck-retry="${arg}">Try again</button><button type="button" class="link-btn sm" data-precheck-start="${arg}">Sync without checking</button></span></div>`;
      // ready
      const summary = `${who} already has <span class="num">${num(c.matched)}</span> of ${num(c.total)} files in ${where}. <span class="num">${num(c.new)}</span> new to sync · ${bytes(c.new_bytes)}${etaText(c, s)}`;
      if (!c.new)
        return `<div class="pc-row">${icon("ok", "size-4 text-good")}<span class="min-w-0 flex-1">${who} already has every file in ${where}.</span><span class="pc-actions"><button type="button" class="btn sm" data-precheck-start="${arg}">Done</button></span></div>`;
      if (later.has(keyOf(c)))
        return `<div class="pc-row">${icon("hourglass", "size-4 text-ink-3")}<span class="min-w-0 flex-1 text-ink-3">${where}: ${num(c.new)} new to sync, waiting for you</span><span class="pc-actions"><button type="button" class="link-btn sm" data-precheck-start="${arg}">Start syncing</button></span></div>`;
      return `<div class="pc-row pc-ready">${icon("ok", "size-4 text-accent")}<span class="min-w-0 flex-1">${summary}</span>
        <span class="pc-actions"><button type="button" class="btn sm primary" data-precheck-start="${arg}">${icon("cloudUp")}Start syncing</button><button type="button" class="btn sm" data-precheck-later="${esc(keyOf(c))}">Not now</button></span></div>`;
    })
    .join("")}</div>`;
}

/** Start syncing, Not now, Try again, and "Change" (the folders sheet), anywhere on the page. */
export function onPrecheckClick(e: MouseEvent): void {
  const t = e.target as HTMLElement;
  const sheet = t.closest<HTMLElement>("[data-folders-sheet]");
  if (sheet) {
    openFoldersSheet(sheet.dataset.foldersSheet ?? "");
    return;
  }
  const later = t.closest<HTMLElement>("[data-precheck-later]");
  if (later) {
    const set = laterSet();
    set.add(later.dataset.precheckLater ?? "");
    try {
      sessionStorage.setItem(LATER, JSON.stringify([...set]));
    } catch {
      /* private mode */
    }
    void refresh();
    return;
  }
  const b = t.closest<HTMLElement>("[data-precheck-start], [data-precheck-retry]");
  if (!b) return;
  const start = b.hasAttribute("data-precheck-start");
  const [account = "", folder = ""] = (b.dataset.precheckStart ?? b.dataset.precheckRetry ?? "").split("|");
  b.setAttribute("disabled", "");
  void post(start ? "prechecks/start" : "prechecks/retry", { account, folder })
    .then(() => {
      toast(start ? `Syncing ${folder} to ${account.split("@")[0]}` : `Checking ${folder} again`, "good");
      return refresh();
    })
    .catch((err: Error) => {
      b.removeAttribute("disabled");
      toast(err.message, "bad");
    });
}
