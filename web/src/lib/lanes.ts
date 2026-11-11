// Folder backup per account. Each account uploads on its own, side by side, and they share the
// speed limit. "Backup" is a folder's main account; "Also in" are its extra accounts, which get a
// copy of the same files. (Copy accounts, library to library, is something else: views/copy.ts.)

import type { Current, Status } from "./api";

/** A chip tapped (turn the account on or off for a folder) or Make main (move it to the front).
 * The first account is the backup; the order is chosen here, never taken from the page. */
export function folderOrder(order: string[], acc: string, action: "toggle" | "main"): string[] {
  if (action === "main") return order.includes(acc) ? [acc, ...order.filter((a) => a !== acc)] : order;
  return order.includes(acc) ? order.filter((a) => a !== acc) : [...order, acc];
}

export type LanePhase = "uploading" | "waiting" | "stopped" | "behind" | "caught_up";

export interface AccountLane {
  account: string;
  phase: LanePhase;
  /** Files sending now for this account. */
  uploading: Current[];
  /** Folders this account is the backup for, and folders it is also in. */
  backup: string[];
  also: string[];
  /** Files of those folders in this account, and in all. */
  done: number;
  total: number;
  /** This round: files finished and in all for this account (0 and 0 outside a round). */
  roundDone: number;
  roundTotal: number;
}

/** One lane per connected account that has a folder or work this round, in account order. */
export function accountLanes(s: Status): AccountLane[] {
  const out: AccountLane[] = [];
  for (const account of s.accounts ?? []) {
    const backup: string[] = [];
    const also: string[] = [];
    let done = 0;
    let total = 0;
    for (const [name, f] of Object.entries(s.folders ?? {})) {
      const accs = f.accounts ?? (f.account ? [f.account] : []);
      const i = accs.indexOf(account);
      if (i < 0) continue;
      (i === 0 ? backup : also).push(name);
      total += f.files;
      done += i === 0 ? f.synced_files : (f.copies?.[account] ?? 0);
    }
    const lane = s.lanes?.[account];
    const uploading = (s.uploading ?? []).filter((u) => u.account === account);
    if (!backup.length && !also.length && !lane && !uploading.length) continue;
    const left = lane ? lane.total - lane.done : 0;
    const phase: LanePhase =
      uploading.length || (lane?.phase === "uploading" && left > 0)
        ? "uploading"
        : lane?.phase === "waiting"
          ? "waiting"
          : lane?.phase === "stopped" && left > 0
            ? "stopped"
            : done < total
              ? "behind"
              : "caught_up";
    out.push({
      account,
      phase,
      uploading,
      backup,
      also,
      done,
      total,
      roundDone: lane?.done ?? 0,
      roundTotal: lane?.total ?? 0,
    });
  }
  return out;
}

/** What a lane is doing, in words. */
export function lanePhaseText(l: AccountLane, s: Status): string {
  switch (l.phase) {
    case "uploading":
      return `Uploading · ${l.uploading.length} file${l.uploading.length === 1 ? "" : "s"} at once`;
    case "waiting":
      return "Waiting to start";
    case "stopped":
      return s.paused
        ? "Paused"
        : !s.schedule.allowed
          ? "Waiting for upload hours"
          : s.login_failed.includes(l.account)
            ? "Login rejected"
            : "Stopped this round";
    case "behind":
      return s.paused ? "Paused" : "Waiting for the next scan";
    default:
      return "Caught up";
  }
}

/** Accounts uploading now and files sending across them, for the control strip and the badge. */
export function uploadSummary(s: Status): { accounts: number; files: number } {
  const ups = s.uploading ?? (s.current ? [s.current] : []);
  const accs = new Set(ups.map((u) => u.account));
  for (const [a, l] of Object.entries(s.lanes ?? {})) if (l.phase === "uploading") accs.add(a);
  return { accounts: accs.size, files: ups.length };
}

/** "Uploading · 2 accounts · 7 files", or "Uploading · 3 at once" with one account. */
export function uploadTitle(s: Status): string {
  const u = uploadSummary(s);
  if (u.accounts > 1)
    return `Uploading · ${u.accounts} accounts · ${u.files} file${u.files === 1 ? "" : "s"}`;
  return `Uploading · ${Math.max(1, u.files)} at once`;
}

/** Folder copies ("Also in") still to go: [[account, files left]], most first. */
export function copiesLeft(s: Status): [string, number][] {
  return Object.entries(s.copies_pending_by ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
}

/** "two (12), three (4)": who still gets copies. */
export function copiesText(s: Status): string {
  return copiesLeft(s)
    .map(([a, n]) => `${a.split("@")[0]} (${n})`)
    .join(", ");
}
