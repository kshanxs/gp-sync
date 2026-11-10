/** How many files or items run at once (Settings: upload_workers, reclaim.workers). 0 is dynamic:
 * the server starts at 4, adds more while Google keeps up (up to 12, fewer for big videos) and
 * halves when Google asks it to slow down. A fixed number goes up to 8. */
export const AT_ONCE_CHOICES = [0, 1, 2, 3, 4, 5, 6, 7, 8];

export interface PaceNow {
  dynamic: boolean;
  /** How many may run at once right now. */
  now: number;
  running: number;
  /** Seconds before dynamic grows again after Google asked to slow down. */
  calm: number;
  slowdowns: number;
}

export function atOnceLabel(n: number): string {
  if (n === 0) return "Dynamic (recommended)";
  if (n === 4) return "4 (steady)";
  return String(n);
}

/** <option>s for a "… at once" select. */
export function atOnceOptions(selected: number): string {
  return AT_ONCE_CHOICES.map(
    (n) => `<option value="${n}" ${n === selected ? "selected" : ""}>${atOnceLabel(n)}</option>`,
  ).join("");
}

/** "up to 4", or "up to 7 (dynamic)" with the number the server allows right now. */
export function atOnceText(fixed: number, pace?: PaceNow | null): string {
  if (fixed > 0) return `up to ${fixed}`;
  return `up to ${pace?.now ?? 4} (dynamic${pace?.calm ? ", slowed down by Google" : ""})`;
}
