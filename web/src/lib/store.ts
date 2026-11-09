// Live status. Pushed by the app over server-sent events (/api/stream) the moment something
// happens; if the stream can't be used, the page asks every 2 seconds instead.

import { get, setTzOffset, type Status } from "./api";
import { setZone } from "./format";

type Listener = (s: Status) => void;

let current: Status | null = null;
const listeners = new Set<Listener>();
const modeListeners = new Set<(live: boolean) => void>();
let timer = 0;
let failures = 0;
let source: EventSource | null = null;
let lastPush = 0;

export function status(): Status | null {
  return current;
}

/** The machine's name for messages: HOST_NAME, "Umbrel" on Umbrel, otherwise "this server". */
export function host(): string {
  return current?.host || "this server";
}

export function onStatus(fn: Listener): () => void {
  listeners.add(fn);
  if (current) fn(current);
  return () => listeners.delete(fn);
}

/** Called with true while updates are pushed live, false while polling. */
export function onMode(fn: (live: boolean) => void): void {
  modeListeners.add(fn);
  fn(isLive());
}

export function isLive(): boolean {
  return Date.now() - lastPush < 6000;
}

let pageVersion = "";

/** The app was updated while this page was open: load the new page (once per version). */
function reloadIfUpdated(s: Status): boolean {
  if (!pageVersion) pageVersion = s.version;
  if (!s.version || s.version === pageVersion) return false;
  if (sessionStorage.getItem("reloaded-for") === s.version) return false; // tried already
  sessionStorage.setItem("reloaded-for", s.version);
  location.reload();
  return true;
}

function publish(s: Status): void {
  if (reloadIfUpdated(s)) return;
  current = s;
  failures = 0;
  // Days and times follow the time zone chosen in Settings (or the system default).
  setZone(s.tz?.name);
  setTzOffset(s.tz?.offset_min);
  document.documentElement.dataset.offline = "";
  listeners.forEach((fn) => {
    try {
      fn(s);
    } catch (e) {
      console.error(e);
    }
  });
}

export async function refresh(): Promise<void> {
  try {
    publish(await get<Status>("status"));
  } catch {
    failures++;
    if (failures > 2) document.documentElement.dataset.offline = "1";
  }
}

function connect(): void {
  if (!("EventSource" in window) || source) return;
  source = new EventSource("api/stream");
  source.onmessage = (e: MessageEvent<string>) => {
    const wasLive = isLive();
    lastPush = Date.now();
    if (!wasLive) modeListeners.forEach((fn) => fn(true));
    try {
      publish(JSON.parse(e.data) as Status);
    } catch {
      /* ignore a broken message */
    }
  };
  source.onerror = () => {
    // The browser reconnects by itself; polling covers the gap.
    modeListeners.forEach((fn) => fn(false));
  };
}

function disconnect(): void {
  source?.close();
  source = null;
  lastPush = 0;
}

export function startPolling(): void {
  const tick = async () => {
    if (!document.hidden && !isLive()) await refresh();
    timer = window.setTimeout(tick, 2000);
  };
  clearTimeout(timer);
  void tick();
  connect();
  document.addEventListener("visibilitychange", () => {
    // No live connection in the background: saves battery on phones.
    if (document.hidden) disconnect();
    else {
      connect();
      void refresh();
    }
  });
}

// ---- The page's control strip, under the header: what the page's job is doing, with Pause or
// Resume and Refresh. One place per page (no header buttons, no repeats inside cards). Pages set
// these as they render and the shell clears them when another page opens.

/** The strip's status line. */
export interface PageStatus {
  /** "run" (working), "warn" (paused), "good" (done or all set), "idle". */
  tone: "run" | "warn" | "good" | "idle";
  title: string;
  detail?: string;
}

/** The page's Pause/Resume: its own job only (uploads, Free storage in one account, copies…). */
export interface PauseControl {
  paused: boolean;
  /** Said in full on hover: "Pause freeing storage in a@x.com". */
  label: string;
  /** Work is running right now. */
  working?: boolean;
  /** Flips it; resolves with the toast to show. */
  toggle: () => Promise<string>;
}

/** The page's Refresh: a scan, or reading the library from Google. */
export interface RefreshControl {
  /** "Refresh a@x.com from Google"… */
  label: string;
  /** Reading right now: the button spins. */
  busy: boolean;
  /** Starts it; resolves with the toast to show. */
  run: () => Promise<string>;
}

let pageStatus: PageStatus | null = null;
let pauseCtl: PauseControl | null = null;
let refreshCtl: RefreshControl | null = null;
const controlListeners = new Set<() => void>();
const changed = () => controlListeners.forEach((fn) => fn());

export function setPageStatus(c: PageStatus | null): void {
  pageStatus = c;
  changed();
}

export function setPauseControl(c: PauseControl | null): void {
  pauseCtl = c;
  changed();
}

export function setRefreshControl(c: RefreshControl | null): void {
  refreshCtl = c;
  changed();
}

export function pageControls(): {
  status: PageStatus | null;
  pause: PauseControl | null;
  refresh: RefreshControl | null;
} {
  return { status: pageStatus, pause: pauseCtl, refresh: refreshCtl };
}

export function onPageControls(fn: () => void): void {
  controlListeners.add(fn);
}
