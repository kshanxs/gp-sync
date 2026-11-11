// Number, size, speed and time formatting used across the dashboard.

const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function bytes(n: number | null | undefined, digits = 1): string {
  let v = Math.max(0, n || 0);
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i++;
  }
  return i === 0 ? `${Math.round(v)} B` : `${v.toFixed(v >= 100 ? 0 : digits)} ${UNITS[i]}`;
}

/** Bytes per second as bits per second, the way connection speeds are usually quoted. */
export function speed(bps: number | null | undefined): string {
  const bits = Math.max(0, bps || 0) * 8;
  if (bits < 1e3) return `${Math.round(bits)} bps`;
  if (bits < 1e6) return `${(bits / 1e3).toFixed(bits < 1e4 ? 1 : 0)} Kbps`;
  if (bits < 1e9) return `${(bits / 1e6).toFixed(bits < 1e7 ? 1 : 0)} Mbps`;
  return `${(bits / 1e9).toFixed(2)} Gbps`;
}

export function num(n: number | null | undefined): string {
  return Math.round(n || 0).toLocaleString();
}

export function compact(n: number): string {
  if (n < 1000) return String(Math.round(n));
  if (n < 1e6) return `${(n / 1e3).toFixed(n < 1e4 ? 1 : 0)}k`;
  return `${(n / 1e6).toFixed(1)}M`;
}

export function pct(part: number, whole: number): number {
  return whole > 0 ? Math.min(100, (part / whole) * 100) : 0;
}

/** Length of time: "45 s", "3 min 20 s", "2 h 5 min", "3 d 4 h". */
export function duration(secs: number | null | undefined): string {
  const s = Math.max(0, Math.round(secs || 0));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
  if (s < 86400)
    return `${Math.floor(s / 3600)} h${s % 3600 >= 60 ? ` ${Math.floor((s % 3600) / 60)} min` : ""}`;
  return `${Math.floor(s / 86400)} d${s % 86400 >= 3600 ? ` ${Math.floor((s % 86400) / 3600)} h` : ""}`;
}

/** Rough time left, short enough for a card: "under a minute", "45 min", "2 h 15 min", "3 d 4 h".
 * Minutes round up, and past an hour to 5 minutes, so the estimate doesn't flicker. */
export function remaining(secs: number | null | undefined): string {
  const s = Math.max(0, secs || 0);
  if (s < 60) return "under a minute";
  if (s < 3600) return `${Math.ceil(s / 60)} min`;
  if (s < 86400) {
    let h = Math.floor(s / 3600);
    let m = Math.ceil((s % 3600) / 300) * 5;
    if (m === 60) [h, m] = [h + 1, 0];
    return m ? `${h} h ${m} min` : `${h} h`;
  }
  const d = Math.floor(s / 86400);
  const h = Math.round((s % 86400) / 3600);
  return h === 24 ? `${d + 1} d` : h ? `${d} d ${h} h` : `${d} d`;
}

export function ms(n: number | null | undefined): string {
  const v = n || 0;
  return v < 1000 ? `${Math.round(v)} ms` : duration(v / 1000);
}

/** "just now", "5 min ago", "3 h ago", "2 d ago". */
export function ago(unix: number | null | undefined, now = Date.now() / 1000): string {
  if (!unix) return "never";
  const d = now - unix;
  if (d < 45) return "just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  return `${Math.round(d / 86400)} d ago`;
}

/** "in 3 h", "in 12 min". */
export function until(unix: number, now = Date.now() / 1000): string {
  const d = unix - now;
  if (d <= 60) return "in a minute";
  return "in " + duration(d).replace(/ \d+ s$/, "");
}

// The time zone chosen in Settings (or the system default), from the status. Undefined: the
// browser's own zone, until the status arrives.
let zone: string | undefined;

export function setZone(name: string | undefined): void {
  try {
    if (name) new Intl.DateTimeFormat([], { timeZone: name }); // unknown to this browser: keep its own
    zone = name || undefined;
  } catch {
    zone = undefined;
  }
}

export function clock(unix: number): string {
  return new Date(unix * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: zone });
}

export function date(unix: number, withYear = false): string {
  return new Date(unix * 1000).toLocaleDateString([], {
    timeZone: zone,
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

/** The calendar day of a time in the chosen zone, as "2026-11-10" (the server's day keys). */
export function dayKey(unix: number): string {
  return new Date(unix * 1000).toLocaleDateString("sv", { timeZone: zone });
}

export function dayLabel(unix: number, now = new Date()): string {
  const d = new Date(unix * 1000);
  // The calendar day in the chosen zone, as a number of days.
  const dayOf = (x: Date) => {
    const [y, m, dd] = x.toLocaleDateString("sv", { timeZone: zone }).split("-").map(Number);
    return Date.UTC(y!, m! - 1, dd!) / 86400000;
  };
  const diff = dayOf(now) - dayOf(d);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return d.toLocaleDateString([], { weekday: "long", timeZone: zone });
  return d.toLocaleDateString([], {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: zone,
  });
}

export function hour(h: number): string {
  return `${String(((h % 24) + 24) % 24).padStart(2, "0")}:00`;
}

export function fileName(path: string): string {
  return path.split("/").pop() || path;
}

export function folderOf(path: string): string {
  const parts = path.split("/");
  return parts.length > 1 ? parts.slice(0, -1).join("/") : "";
}
