// What each Google account has of the files it should get: sent by GP Sync, found already in Google
// (nothing sent), still to send, and failed. The same rows on Sync (the whole library) and in a
// folder's header on Folders (that folder only).

import type { FolderAccount } from "./api";
import { esc } from "./dom";
import { num, remaining } from "./format";

export type Segment = "uploaded" | "exists" | "waiting" | "failed";

export const SEGMENTS: [Segment, string][] = [
  ["uploaded", "Uploaded"],
  ["exists", "Already in Google"],
  ["waiting", "New to sync"],
  ["failed", "Failed"],
];

const TIPS: Record<Segment, string> = {
  uploaded: "Sent by GP Sync",
  exists: "Google already had these (often your phone's own backup), so nothing was sent",
  waiting: "Not in this account yet",
  failed: "Couldn't be sent. Tap to see them",
};

/** Every file the account should have: the four parts added up. */
export function accountTotal(a: FolderAccount): number {
  return a.uploaded + a.exists + a.waiting + a.failed;
}

/** "About 40 min left", from the files this account finished in the last hour. */
export function timeLeft(a: FolderAccount): string {
  if (!a.waiting) return a.failed ? "" : "All in";
  if (!a.per_hour) return "Not sending now";
  return `About ${remaining((a.waiting / a.per_hour) * 3600)} left`;
}

export interface BreakdownOptions {
  /** Show each account's time left (the whole library: Sync). */
  eta?: boolean;
  /** The parts are buttons (data-seg="account|part"); the picked one is shown pressed. */
  pick?: { account: string; part: string } | null;
  /** Where a tap on a part goes, when the parts are links instead (Sync). */
  href?: (account: string, part: Segment) => string;
  /** Leave out the line with the address (the card around it already names the account). */
  bare?: boolean;
}

/** Folders opens with this account and part picked (read once by views/folders.ts). */
export const PICK_KEY = "gp.foldersPick";

/** Open Folders showing one account's files of one part ("uploaded", "waiting", …). */
export function openInFolders(account: string, part: string): void {
  try {
    localStorage.setItem("gp.foldersAccount", account);
    sessionStorage.setItem(PICK_KEY, part);
  } catch {
    /* private mode: Folders opens as it was */
  }
  location.hash = "#/folders";
}

/** The bar: the four parts side by side, in proportion. */
function bar(a: FolderAccount): string {
  const total = accountTotal(a);
  if (!total) return `<span class="acc-bar"><i class="w" style="flex:1"></i></span>`;
  return `<span class="acc-bar" aria-hidden="true">${SEGMENTS.filter(([k]) => a[k])
    .map(([k]) => `<i class="${k[0]}" style="flex:${a[k]}"></i>`)
    .join("")}</span>`;
}

function part(a: FolderAccount, k: Segment, label: string, o: BreakdownOptions): string {
  const n = a[k];
  if (k === "failed" && !n) return "";
  const body = `<i class="acc-key ${k[0]}"></i>${esc(label)} <span class="meta">${num(n)}</span>`;
  const tip = esc(TIPS[k]);
  if (o.href && n) return `<a class="acc-part" href="${o.href(a.account, k)}" title="${tip}">${body}</a>`;
  if (o.pick !== undefined && n) {
    const on = o.pick?.account === a.account && o.pick.part === k;
    return `<button type="button" class="acc-part" data-seg="${esc(`${a.account}|${k}`)}" aria-pressed="${on}" title="${tip}">${body}</button>`;
  }
  return `<span class="acc-part" title="${tip}">${body}</span>`;
}

/** One row per account, the folder's backup first. */
export function accountBreakdown(accs: FolderAccount[] | undefined, o: BreakdownOptions = {}): string {
  if (!accs?.length) return "";
  return `<ul class="acc-rows">${accs
    .map((a) => {
      const total = accountTotal(a);
      const left = o.eta ? timeLeft(a) : "";
      return `<li class="acc-row">
        ${
          o.bare
            ? ""
            : `<div class="flex min-w-0 items-center gap-2">
          <span class="min-w-0 truncate text-sm font-medium" title="${esc(a.account)}">${esc(a.account)}</span>
          <span class="badge shrink-0">${a.main ? "Backup" : "Also in"}</span>
          <span class="meta ml-auto shrink-0">${num(total)} files</span>
        </div>`
        }
        ${bar(a)}
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">${SEGMENTS.map(([k, l]) => part(a, k, l, o)).join("")}${
          left
            ? `<span class="ml-auto text-xs ${a.waiting && !a.per_hour ? "text-ink-3" : "text-ink-2"}" title="${a.per_hour ? esc(`${num(a.per_hour)} files in the last hour`) : ""}">${esc(left)}</span>`
            : ""
        }</div>
      </li>`;
    })
    .join("")}</ul>`;
}
