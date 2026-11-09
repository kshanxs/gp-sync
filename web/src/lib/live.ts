// What each process is doing right now, worked out from the status: for the Overview's process
// cards and the phone tab bar.

import type { ProcessKey, Status } from "./api";
import { num, pct } from "./format";
import { PROCESS } from "./processes";

export interface Live {
  key: ProcessKey;
  /** Work is moving right now. */
  running: boolean;
  /** The user paused it while items still wait. */
  paused: boolean;
  /** Items still waiting (0 when there is nothing to do). */
  waiting: number;
  /** Progress to draw (0–100), or null when there is nothing to measure. */
  pct: number | null;
  /** One short line: "3 of 40 this round", "7 waiting", "Paused · 7 waiting", "Idle". */
  detail: string;
  /** Short label for the tab bar while it runs ("Freeing"). */
  verb: string;
  href: string;
}

export const PROCESS_HREF: Record<ProcessKey, string> = {
  uploads: "#/sync",
  freed: "#/storage",
  copied: "#/copy",
  fixed: "#/dates",
};

const VERB: Record<ProcessKey, string> = {
  uploads: "Syncing",
  freed: "Freeing",
  copied: "Copying",
  fixed: "Fixing",
};

/** How far through the queue: items finished out of finished plus waiting. It only moves forward
 * as items finish, so it doesn't jump back each time a new item starts. */
function queuePct(done: number, waiting: number): number | null {
  return done + waiting > 0 ? pct(done, done + waiting) : null;
}

/** Average progress of the items moving now (each has done/total), or null. */
function itemsPct(items: { done?: number; total?: number }[]): number | null {
  const known = items.filter((i) => i.total);
  if (!known.length) return null;
  return known.reduce((a, i) => a + pct(i.done ?? 0, i.total ?? 0), 0) / known.length;
}

function line(running: boolean, paused: boolean, waiting: number, busy: string, idle: string): string {
  if (paused && waiting) return `Paused · ${num(waiting)} waiting`;
  if (running) return busy;
  return waiting ? `${num(waiting)} waiting` : idle;
}

export function liveProcesses(s: Status): Live[] {
  const jobs = s.jobs ?? [];
  const out: Live[] = [];

  // Normal sync: this round's files.
  const up = s.uploading ?? (s.current ? [s.current] : []);
  const upRunning = s.phase !== "paused" && (s.phase === "uploading" || up.length > 0);
  const left = Math.max(0, s.library.files - s.library.synced_files);
  const roundDone = Math.min(s.done + up.length, s.total);
  out.push({
    key: "uploads",
    running: upRunning,
    paused: !!s.paused,
    waiting: left,
    pct: upRunning && s.total ? pct(s.done, s.total) : null,
    detail:
      s.phase === "scanning"
        ? "Scanning your folders"
        : line(
            upRunning,
            !!s.paused,
            left,
            s.total ? `${num(roundDone)} of ${num(s.total)} this round` : "Uploading",
            "All backed up",
          ),
    verb: VERB.uploads,
    href: PROCESS_HREF.uploads,
  });

  // Free storage, every account added up. Paused when everything is paused, or every waiting
  // account is.
  const accs = Object.values(s.reclaim ?? {});
  const freeWaiting = accs.reduce((a, r) => a + r.queued, 0);
  const freeJobs = jobs.filter((j) => j.kind === "storage");
  const waitingAccs = accs.filter((r) => r.queued);
  const freePaused = waitingAccs.length > 0 && (!!s.paused || waitingAccs.every((r) => r.paused));
  out.push({
    key: "freed",
    running: freeJobs.length > 0,
    paused: freePaused,
    waiting: freeWaiting,
    pct: queuePct(
      accs.reduce((a, r) => a + r.done, 0),
      freeWaiting,
    ),
    detail: line(
      freeJobs.length > 0,
      freePaused,
      freeWaiting,
      `${num(freeJobs.length)} moving · ${num(freeWaiting)} waiting`,
      "Nothing queued",
    ),
    verb: VERB.freed,
    href: PROCESS_HREF.freed,
  });

  // Copy accounts, every pair.
  const copyJobs = jobs.filter((j) => j.kind === "copy");
  const copyWaiting = s.copying?.queued ?? 0;
  const copyPaused = (!!s.copying?.paused || !!s.paused) && copyWaiting > 0;
  out.push({
    key: "copied",
    running: copyJobs.length > 0,
    paused: copyPaused,
    waiting: copyWaiting,
    pct:
      s.copying?.done !== undefined
        ? queuePct(s.copying.done, copyWaiting + copyJobs.length)
        : itemsPct(copyJobs),
    detail: line(
      copyJobs.length > 0,
      copyPaused,
      copyWaiting,
      `${num(copyJobs.length)} moving · ${num(copyWaiting)} waiting`,
      s.copying?.checking ? `${num(s.copying.checking)} being confirmed` : "Nothing queued",
    ),
    verb: VERB.copied,
    href: PROCESS_HREF.copied,
  });

  // Fix dates.
  const dateJobs = jobs.filter((j) => j.kind === "dates");
  const dateWaiting = s.dates?.waiting ?? 0;
  out.push({
    key: "fixed",
    running: dateJobs.length > 0,
    paused: !!s.paused && dateWaiting > 0,
    waiting: dateWaiting,
    pct: itemsPct(dateJobs),
    detail: line(dateJobs.length > 0, !!s.paused, dateWaiting, "Fixing a date", "Nothing to fix"),
    verb: VERB.fixed,
    href: PROCESS_HREF.fixed,
  });
  return out;
}

/** What the Overview tab shows on phones: the background work running now (not uploads, which have
 * their own Sync tab). Several at once take turns, `turn` picking which. Null when nothing runs. */
export function tabLive(s: Status, turn = 0): (Live & { label: string }) | null {
  const busy = liveProcesses(s).filter((l) => l.key !== "uploads" && l.running);
  if (!busy.length) return null;
  const l = busy[turn % busy.length]!;
  return { ...l, label: PROCESS[l.key].label };
}
