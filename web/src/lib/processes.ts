// Each process GP Sync runs, for pages that show them side by side (History, Insights).

import type { IconName } from "./icons";
import type { ProcessHistory, ProcessKey } from "./api";

export const PROCESS: Record<ProcessKey, { label: string; noun: string; icon: IconName; color: string }> = {
  uploads: { label: "Uploads", noun: "uploaded", icon: "upload", color: "var(--s1)" },
  freed: { label: "Free storage", noun: "freed", icon: "cloud", color: "var(--s2)" },
  copied: { label: "Copies", noun: "copied", icon: "users", color: "var(--s3)" },
  fixed: { label: "Date fixes", noun: "fixed", icon: "clock", color: "var(--s4)" },
};
export const PROCESS_KEYS = Object.keys(PROCESS) as ProcessKey[];

/** Day ("YYYY-MM-DD") -> items, for one process or several added together. */
export function countsByDay(list: ProcessHistory[], metric: "files" | "data" = "files"): Map<string, number> {
  const out = new Map<string, number>();
  for (const p of list)
    for (const d of p.days) out.set(d.day, (out.get(d.day) ?? 0) + (metric === "files" ? d.count : d.bytes));
  return out;
}

/** Days in a row with something done: the current run (up to today, or yesterday if today has
 * nothing yet) and the longest. */
export function streaks(active: Set<string>, today: string): { current: number; longest: number } {
  const key = (d: Date) => d.toLocaleDateString("sv");
  const day = new Date(`${today}T00:00:00`);
  if (!active.has(today)) day.setDate(day.getDate() - 1);
  let current = 0;
  while (active.has(key(day))) {
    current++;
    day.setDate(day.getDate() - 1);
  }
  let longest = 0;
  let run = 0;
  let prev: Date | null = null;
  for (const k of [...active].sort()) {
    const d = new Date(`${k}T00:00:00`);
    run = prev && Math.round((d.getTime() - prev.getTime()) / 86400000) === 1 ? run + 1 : 1;
    longest = Math.max(longest, run);
    prev = d;
  }
  return { current, longest };
}
