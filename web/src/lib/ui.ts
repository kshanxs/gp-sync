// Shared bits of markup: phase badges, stat tiles, card headers.

import type { CheckInfo, ReclaimSummary, Status } from "./api";
import { esc } from "./dom";
import { ago, bytes, duration, hour, num, speed, until } from "./format";
import { icon, type IconName } from "./icons";
import { copiesText, uploadTitle } from "./lanes";

export type Tone = "good" | "warn" | "bad" | "info" | "";

export interface PhaseInfo {
  label: string;
  tone: Tone;
  icon: IconName;
  detail: string;
}

export function phaseInfo(s: Status): PhaseInfo {
  const left = s.library.files - s.library.synced_files;
  switch (s.phase) {
    case "uploading":
      return {
        // Several files (and accounts) go at once, so no "file n of m": the same words as the strip.
        label: uploadTitle(s),
        tone: "info",
        icon: "cloudUp",
        detail: s.total ? `${s.done} of ${s.total} done this round` : "Sending files",
      };
    case "scanning":
      return { label: "Scanning", tone: "info", icon: "loader", detail: "Looking for new photos and videos" };
    case "paused":
      return {
        label: "Paused",
        tone: "warn",
        icon: "pause",
        detail: left > 0 ? `${left} file(s) wait until you resume` : "Nothing is waiting",
      };
    case "scheduled":
      return {
        label: "Waiting",
        tone: "warn",
        icon: "clock",
        detail: `Upload hours start ${until(s.schedule.next_window, s.now)}`,
      };
    case "setup":
      return { label: "Set up", tone: "warn", icon: "key", detail: "Connect a Google account in Settings" };
    case "error":
      return { label: "Error", tone: "bad", icon: "error", detail: s.error || "Something went wrong" };
    case "starting":
      return { label: "Starting", tone: "info", icon: "loader", detail: "Starting up" };
    default:
      if (left > 0)
        return {
          label: `${left} waiting`,
          tone: "",
          icon: "hourglass",
          detail: "Uploading at the next scan",
        };
      if (s.copies_pending)
        return {
          label: "Main library in",
          tone: "good",
          icon: "ok",
          detail: `Still copying to ${copiesText(s) || "your other accounts"}`,
        };
      return { label: "Up to date", tone: "good", icon: "ok", detail: "Everything is backed up" };
  }
}

export function badge(label: string, tone: Tone = "", ic?: IconName, tip?: string): string {
  return `<span class="badge ${tone}"${tip ? ` data-tip="${esc(tip)}"` : ""}>${ic ? icon(ic) : ""}${esc(label)}</span>`;
}

/** The one badge that says what the app is doing. Moving icons while it works. */
export function phaseBadge(s: Status): string {
  const p = phaseInfo(s);
  const motion =
    s.phase === "scanning" || s.phase === "starting" ? "spin" : s.phase === "uploading" ? "bob" : "";
  return `<span class="badge ${p.tone} phase" data-tip="${esc(p.detail)}">${icon(p.icon, motion)}${esc(p.label)}</span>`;
}

/** A file row or tile that opens the viewer (see main.ts). */
export function openAttrs(name: string, info = false): string {
  return `data-open="${esc(name)}"${info ? ' data-info="1"' : ""} tabindex="0" role="button"`;
}

/** Seconds until the next automatic scan. */
export function nextScan(s: Status): number {
  return Math.max(0, s.last_scan + s.interval * 60 - s.now);
}

/** Only what matters right now: problems, limits that are on, the next scan. Defaults stay quiet. */
export function syncChips(s: Status): string {
  const sch = s.schedule;
  const out: string[] = [];
  if (s.login_failed.length) out.push(badge("Login rejected", "bad", "warn", s.login_failed.join(", ")));
  if (s.counts.failed) out.push(badge(`${s.counts.failed} failed`, "bad", "error", "See Activity → Failed"));
  if (s.phase === "uploading" && s.net.up_avg > 1000)
    out.push(badge(speed(s.net.up_avg), "info", "up", "Upload speed (last 30 s)"));
  if (sch.enabled)
    out.push(
      badge(
        `${hour(sch.start)}–${hour(sch.end)}`,
        sch.allowed ? "good" : "warn",
        "clock",
        sch.allowed ? "Inside upload hours" : "Outside upload hours",
      ),
    );
  if (s.speed_limit_mbps)
    out.push(badge(`${s.speed_limit_mbps} Mbps`, "info", "gauge", "Upload speed limit"));
  if (s.phase !== "scanning" && s.phase !== "uploading" && s.last_scan) {
    const n = nextScan(s);
    out.push(
      badge(
        n > 30 ? `Next scan ${until(s.now + n, s.now)}` : "Scanning soon",
        "",
        "search",
        `Last scan ${ago(s.last_scan, s.now)} · every ${s.interval} min`,
      ),
    );
  }
  return out.join("");
}

/** "1.2 TB free" with a tone: warn under 10% free, bad under 3%. */
export function spaceTone(d: { total: number; free: number }): "good" | "warn" | "bad" {
  const left = d.total ? d.free / d.total : 1;
  return left < 0.03 ? "bad" : left < 0.1 ? "warn" : "good";
}

export function cardHead(title: string, ic: IconName, extra = ""): string {
  return `<div class="card-h">${icon(ic, "size-4 text-ink-3")}<span>${esc(title)}</span><div class="sub">${extra}</div></div>`;
}

export function tile(opts: {
  label: string;
  value: string;
  ic: IconName;
  sub?: string;
  chart?: string;
  id?: string;
  tone?: Tone;
  /** Opens this page when the tile is clicked. */
  href?: string;
  /** Opens this sheet when the tile is clicked (data-pace: its settings, see lib/pace-sheet.ts). */
  open?: string;
}): string {
  const tag = opts.href ? "a" : opts.open ? "button" : "div";
  const attrs = opts.href
    ? ` href="${opts.href}"`
    : opts.open
      ? ` type="button" data-pace="${opts.open}"`
      : "";
  return `<${tag} class="card metric-card lift flex min-w-0 flex-col p-4${opts.href ? " tile-link" : ""}${opts.open ? " tile-link tile-open" : ""}"${attrs}${opts.id ? ` id="${opts.id}"` : ""}>
    <div class="flex min-w-0 items-center gap-2.5"><span class="metric-icon grid size-9 shrink-0 place-items-center rounded-xl">${icon(opts.ic, "size-4")}</span><span class="label line-clamp-2 min-w-0 [overflow-wrap:anywhere]" title="${esc(opts.label)}">${esc(opts.label)}</span>${opts.open ? `<span class="tile-hint" title="Settings">${icon("sliders", "size-4")}</span>` : ""}</div>
    <div class="num mt-3 truncate text-xl sm:text-2xl" data-v>${opts.value}</div>
    <div class="mt-0.5 truncate text-xs text-ink-3" data-s>${opts.sub ?? ""}</div>
    ${opts.chart !== undefined ? `<div class="mt-auto pt-2" data-c>${opts.chart}</div>` : ""}
  </${tag}>`;
}

export function etaText(s: Status): string {
  const left = s.library.bytes - s.library.synced_bytes;
  if (left <= 0) return "";
  const rate = s.net.up_avg;
  if (s.phase !== "uploading" || rate < 10_000) return "";
  return duration(left / rate);
}

export function kindIcon(name: string): IconName {
  return /\.(mp4|mov|m4v|3gp|3g2|avi|mkv|webm|mts|m2ts|wmv|mpg|mpeg)$/i.test(name) ? "video" : "photo";
}

export const eventMeta: Record<string, { tone: Tone; icon: IconName; label: string }> = {
  uploaded: { tone: "good", icon: "cloudUp", label: "Uploaded" },
  fixed: { tone: "good", icon: "calendar", label: "Date fixed" },
  freed: { tone: "good", icon: "sparkles", label: "Re-uploaded free" },
  copied: { tone: "good", icon: "copy", label: "Copied" },
  deleted: { tone: "warn", icon: "trash", label: "Deleted in Google" },
  downloaded: { tone: "info", icon: "download", label: "Downloaded" },
  exists: { tone: "info", icon: "verified", label: "Already in Google" },
  failed: { tone: "bad", icon: "error", label: "Failed" },
  error: { tone: "bad", icon: "warn", label: "Error" },
  scan: { tone: "", icon: "search", label: "Scan" },
  pause: { tone: "warn", icon: "pause", label: "Paused" },
  resume: { tone: "good", icon: "play", label: "Resumed" },
  account: { tone: "", icon: "users", label: "Account" },
  album: { tone: "", icon: "library", label: "Album" },
  received: { tone: "info", icon: "phone", label: "From phone" },
  info: { tone: "", icon: "info", label: "Info" },
};

/** Thumbnail with a photo/video icon if it can't be shown (see the error handler in main.ts).
 *  With the file's time (`v`) the picture can be kept by the browser for good. */
export function thumb(name: string, cls = "thumb", v?: number): string {
  const src = `api/thumb?path=${encodeURIComponent(name)}${v ? `&v=${v}` : ""}`;
  return `<img class="${cls}" loading="lazy" decoding="async" src="${src}"${cls.includes("size-full") ? sharp(src) : ""} alt="" data-kind="${kindIcon(name)}">`;
}

/** About how wide a grid tile is shown: phones two across, wider screens a few hundred pixels. */
const TILE_SIZES = "(min-width: 1280px) 300px, (min-width: 640px) 260px, 50vw";

/** A tile's picture in two sizes (320 and 640 pixels): the browser takes the sharp one on dense
 * screens and big tiles, the small one elsewhere. Small row icons keep the one size. */
export function sharp(src: string): string {
  return ` srcset="${src} 320w, ${src}&w=640 640w" sizes="${TILE_SIZES}"`;
}

export function thumbFallback(img: HTMLImageElement): void {
  img.parentElement?.classList.add("done");
  const span = document.createElement("span");
  span.className = `${img.className} loaded grid place-items-center text-ink-3`;
  span.innerHTML = icon(img.dataset.kind === "video" ? "video" : "photo", "size-1/2 max-h-6 max-w-6");
  img.replaceWith(span);
}

/** A note when Google didn't answer the last check of new uploads ("" otherwise). Work goes on meanwhile. */
export function checkNote(c: CheckInfo | undefined, now = Date.now() / 1000): string {
  if (!c?.error || !c.waiting) return "";
  const mins = Math.max(1, Math.ceil(((c.next ?? now) - now) / 60));
  return `<section class="card mb-4 p-4 text-sm text-warn">Google didn't answer the last check, so ${c.waiting} new upload${c.waiting === 1 ? " waits" : "s wait"} to be confirmed as free. Trying again in about ${mins} min. Other items keep moving meanwhile.</section>`;
}

/** The line under an account: what counts against storage there and how freeing goes, plus what
 * was freed so far. */
export function accountLine(v: ReclaimSummary | undefined): {
  line: string;
  freed: string;
  tone: string;
  live: boolean;
} {
  const freed = v?.done ? `${bytes(v.freed)} freed` : "";
  if (v?.queued && v.paused)
    return { line: `Paused · ${num(v.queued)} waiting`, freed, tone: "text-warn", live: false };
  if (v?.queued)
    return {
      line: `Freeing · ${num(v.working)} at once · ${num(v.queued)} left`,
      freed,
      tone: "text-run",
      live: true,
    };
  if (v?.counted == null) return { line: "Library not read yet", freed, tone: "text-ink-3", live: false };
  if (!v.counted) return { line: "Nothing to free", freed, tone: "text-good", live: false };
  return { line: `${bytes(v.counted)} to free`, freed, tone: "text-ink-3", live: false };
}
