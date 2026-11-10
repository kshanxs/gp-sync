import type { SnapAction, SnapExport, SnapItem, SnapMatch } from "./api";
import type { IconName } from "./icons";
import type { Tone } from "./ui";

/** The four groups on the Snapchat page, in order. */
export const SNAP_GROUPS: { id: SnapAction; title: string; hint: string; ic: IconName }[] = [
  {
    id: "album",
    title: "Already in Google",
    hint: "One copy stays (in your own albums too) and goes into the Snapchat album: the original (older, or from a camera), or one with the right date. Any other copies are duplicates: kept on this server, deleted from Google.",
    ic: "library",
  },
  {
    id: "replace",
    title: "In Google, wrong date",
    hint: "Google only has copies dated later (camera-roll saves backed up afterwards). They're kept on this server and deleted from Google, and the memory is uploaded with Snapchat's date, time and place built in. Albums you made yourself don't carry over.",
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

/** "1,162 memories aren't in these zips (Apr 2024 to Feb 2025)", or "" when none are missing. */
export function missingNote(e: SnapExport): string {
  const missing = e.missing ?? 0;
  if (!missing) return "";
  const months = Object.keys(e.missing_months ?? {}).sort();
  const fmt = (m: string) => {
    const [y, mo] = m.split("-").map(Number);
    return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString("en-GB", {
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    });
  };
  const span = months.length
    ? months.length === 1
      ? ` (${fmt(months[0])})`
      : ` (${fmt(months[0])} to ${fmt(months[months.length - 1])})`
    : "";
  return `${missing.toLocaleString("en-US")} ${missing === 1 ? "memory isn't" : "memories aren't"} in these zips${span}`;
}

/** Google's date is off from Snapchat's by more than two minutes. */
export function wrongDate(i: Pick<SnapItem, "google" | "taken">): boolean {
  return !!i.google?.taken && Math.abs(i.google.taken - i.taken) > 120;
}
