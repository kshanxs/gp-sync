import type { SnapAction, SnapExport, SnapItem, SnapMatch } from "./api";
import type { IconName } from "./icons";
import type { Tone } from "./ui";

/** The four groups on the Snapchat page, in order. */
export const SNAP_GROUPS: { id: SnapAction; title: string; hint: string; ic: IconName }[] = [
  {
    id: "album",
    title: "Already in Google",
    hint: "One copy stays (in your own albums too) and goes into the Snapchat album: the original (older, or from a camera), or one with the right date. Any other copies are duplicates and are deleted from Google; the memory here is the same picture (a less certain match is downloaded first).",
    ic: "library",
  },
  {
    id: "replace",
    title: "In Google, wrong date",
    hint: "Google only has copies dated later (camera-roll saves backed up afterwards). They're deleted from Google (a less certain match is downloaded first), and the memory is uploaded with Snapchat's date, time and place built in. Albums you made yourself don't carry over.",
    ic: "calendar",
  },
  {
    id: "upload",
    title: "Not in Google",
    hint: "Uploaded with Snapchat's date and place.",
    ic: "cloudUp",
  },
  { id: "skip", title: "Left out", hint: "Nothing happens to these.", ic: "x" },
];

export const SNAP_ACTION_LABEL: Record<SnapAction, string> = {
  album: "Add to album",
  replace: "Replace with the memory",
  upload: "Upload",
  skip: "Leave out",
};

/** How the memory was found in Google, for a badge. */
export function matchBadge(i: Pick<SnapItem, "match" | "distance">): [string, Tone, string] {
  const labels: Record<SnapMatch, [string, Tone, string]> = {
    same_file: ["Same file", "good", "Google has exactly this file"],
    same_moment: ["Same moment", "good", "Google has a photo or video taken in the same second"],
    looks_same: [
      "Looks the same",
      "info",
      `Google's copy looks the same${i.distance != null ? ` (difference ${i.distance} of 256)` : ""}`,
    ],
    local: ["Copy on this server", "info", "A copy here isn't in Google yet: the memory goes up instead"],
    none: ["Not in Google", "", "No copy found in Google Photos"],
  };
  return labels[i.match];
}

/** The actions a memory can take: album and replace when Google has copies (uploading as well
 * would make a duplicate), upload when it has none. */
export function choices(i: Pick<SnapItem, "copies">): SnapAction[] {
  return i.copies.length ? ["album", "replace", "skip"] : ["upload", "skip"];
}

/** "1,162 memories aren't in these zips: 1,135 from Apr 2024 to Feb 2025, and 27 in other
 * months", or "" when none are missing. The biggest run of months in a row is the missing part. */
export function missingNote(e: SnapExport): string {
  const missing = e.missing ?? 0;
  if (!missing) return "";
  const months = Object.entries(e.missing_months ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const n = (x: number) => x.toLocaleString("en-US");
  const head = `${n(missing)} ${missing === 1 ? "memory isn't" : "memories aren't"} in these zips`;
  if (!months.length) return head;
  const index = (m: string) => {
    const [y, mo] = m.split("-").map(Number);
    return y * 12 + mo;
  };
  const fmt = (m: string) => {
    const [y, mo] = m.split("-").map(Number);
    return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString("en-GB", {
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
  };
  // Runs of months in a row; the one with the most memories is the missing part of the export.
  const runs: { from: string; to: string; count: number }[] = [];
  for (const [m, c] of months) {
    const last = runs[runs.length - 1];
    if (last && index(m) === index(last.to) + 1) {
      last.to = m;
      last.count += c;
    } else runs.push({ from: m, to: m, count: c });
  }
  const main = runs.reduce((a, b) => (b.count > a.count ? b : a));
  const span = main.from === main.to ? fmt(main.from) : `${fmt(main.from)} to ${fmt(main.to)}`;
  const rest = missing - main.count;
  if (!rest) return `${head} (${span})`;
  return `${head}: ${n(main.count)} from ${span}, and ${n(rest)} in other months`;
}

/** Google's date is off from Snapchat's by more than two minutes. */
export function wrongDate(i: Pick<SnapItem, "google" | "taken">): boolean {
  return !!i.google?.taken && Math.abs(i.google.taken - i.taken) > 120;
}
