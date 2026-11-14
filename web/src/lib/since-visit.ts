// "Since your last visit": what changed while the dashboard was closed. Kept on this device only.
// Every status this tab sees is saved (at most once a minute) as the last visit; the first one a
// new session finds is held for the session as the point to compare with.

import type { Status } from "./api";

export interface Visit {
  at: number;
  files: number;
  synced: number;
  failed: number;
}

const LAST = "gp.lastVisit";
const BASE = "gp.visitBase";
/** Shorter than this away is the same visit: no line. */
export const MIN_AWAY = 10 * 60;

function read(store: Storage, key: string): Visit | null {
  try {
    const v = JSON.parse(store.getItem(key) || "null") as Visit | null;
    return v && typeof v.at === "number" ? v : null;
  } catch {
    return null;
  }
}

function write(store: Storage, key: string, v: Visit | null): void {
  try {
    if (v) store.setItem(key, JSON.stringify(v));
    else store.removeItem(key);
  } catch {
    /* private mode: no line */
  }
}

export function snapshot(s: Status, now = Date.now() / 1000): Visit {
  return { at: now, files: s.library.files, synced: s.library.synced_files, failed: s.counts.failed };
}

let savedAt = 0;

/** The first time this session asks: the last visit becomes the base to compare with. */
function ensureBase(): boolean {
  try {
    if (sessionStorage.getItem(BASE) === null)
      sessionStorage.setItem(BASE, localStorage.getItem(LAST) ?? "null");
    return true;
  } catch {
    return false;
  }
}

/** Called with every status: fixes this session's base once, then keeps the last visit current. */
export function noteVisit(s: Status, now = Date.now() / 1000): void {
  if (!s.library.files && !s.last_scan) return; // before the first scan: nothing to compare yet
  if (!ensureBase() || now - savedAt < 60) return;
  savedAt = now;
  write(localStorage, LAST, snapshot(s, now));
}

export interface SinceVisit {
  at: number;
  added: number;
  backedUp: number;
  failed: number;
}

/** What changed since the visit before this session, or null when there is nothing to say. */
export function sinceVisit(s: Status, now = Date.now() / 1000): SinceVisit | null {
  if (!ensureBase()) return null;
  const base = read(sessionStorage, BASE);
  if (!base || now - base.at < MIN_AWAY) return null;
  return diff(base, snapshot(s, now));
}

export function diff(base: Visit, cur: Visit): SinceVisit | null {
  const out = {
    at: base.at,
    added: Math.max(0, cur.files - base.files),
    backedUp: Math.max(0, cur.synced - base.synced),
    failed: Math.max(0, cur.failed - base.failed),
  };
  return out.added || out.backedUp || out.failed ? out : null;
}

/** "Got it": compare with now from here on. */
export function dismissVisit(s: Status): void {
  write(sessionStorage, BASE, snapshot(s));
}
