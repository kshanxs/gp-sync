import { esc } from "./dom";

/** A backup-queue setting, not an action to add files from the current device. */
export const PRIORITY_MAX = 10;

/** The "Back up first" switch in a folder's header: its state under the name, the explanation in
 * its tooltip (and read out to screen readers). */
export function backupPriorityControl(path: string, position: number, hasWaiting: boolean): string {
  const active = position >= 0;
  const status = active ? `Priority #${position + 1}` : "Normal order";
  const description = `${
    hasWaiting
      ? "Move this folder's waiting files up in the backup queue."
      : "No files are waiting. Priority applies to future backups."
  } This doesn't add files. Up to ${PRIORITY_MAX} folders can go first. A scan may start; pause and upload hours still apply.`;
  const id = "prio-desc"; // one folder is open at a time
  return `<div class="prio-switch" data-tip="${esc(description)}">
    <span class="text-right leading-4"><span class="block text-xs font-medium text-ink">Back up first</span><span class="meta">${status}</span></span>
    <button type="button" class="switch" role="switch" data-prio-toggle="${esc(path)}" aria-checked="${active}" aria-label="Back up first: ${esc(path)}" aria-describedby="${id}"></button>
    <span class="sr-only" id="${id}">${description}</span>
  </div>`;
}
