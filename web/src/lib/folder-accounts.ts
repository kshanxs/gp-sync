// Which Google accounts each top folder goes to, chosen where you look at them: Folders (the open
// folder's "Sends to" chips), Sync (each account's "Change"), Settings, and right after connecting an
// account. The first account is the folder's backup; the others get copies. An account added to a
// folder that is already backed up is checked against its Google library first (lib/prechecks.ts).

import { post, type Status } from "./api";
import { $$, esc, html, toast } from "./dom";
import { icon } from "./icons";
import { folderOrder } from "./lanes";
import { openSheet } from "./sheet";
import { refresh, status } from "./store";

/** Every top folder and its accounts (main first), only connected ones: what Settings saves. The
 * whole map is always sent, because a folder left out of it would stop being backed up. */
export function foldersMap(s: Status | null): Record<string, string[]> {
  const accs = s?.accounts ?? [];
  return Object.fromEntries(
    Object.entries(s?.folders ?? {}).map(([name, f]) => [
      name,
      (f.accounts ?? (f.account ? [f.account] : [])).filter((a) => accs.includes(a)),
    ]),
  );
}

/** Save the folders map. Returns the accounts that will be checked first (added to a folder that
 * was already backed up), so the message can say so. */
export async function saveFolders(
  next: Record<string, string[]>,
  before = foldersMap(status()),
): Promise<boolean> {
  const checked = new Set<string>();
  for (const [folder, accs] of Object.entries(next)) {
    const old = before[folder] ?? [];
    for (const a of accs) if (old.length && !old.includes(a)) checked.add(a);
  }
  try {
    await post("settings", { folders: next });
    toast(
      checked.size
        ? `Saved. Checking what ${[...checked].map((a) => a.split("@")[0]).join(" and ")} already has in Google first: nothing is sent until you start.`
        : "Saved. Scanning now.",
      "good",
    );
    void refresh();
    return true;
  } catch (e) {
    toast((e as Error).message, "bad");
    return false;
  }
}

/** One chip per connected account: on or off for the folder, "main" on the backup, Make main on
 * the others that are on. */
export function folderChips(accs: string[], order: string[]): string {
  if (!accs.length) return '<span class="text-xs text-ink-3">Connect a Google account first</span>';
  return accs
    .map((a) => {
      const i = order.indexOf(a);
      const main = i === 0 && order.length > 1;
      return `<span class="inline-flex items-center gap-1"><button type="button" class="chip" data-acc="${esc(a)}" aria-pressed="${i >= 0}">${icon("ok", "size-3.5")}${esc(a)}${main ? '<span class="opacity-70">· main</span>' : ""}</button>${i > 0 ? `<button type="button" class="link-btn" data-main="${esc(a)}" title="Make ${esc(a)} the backup. The others get copies of the same files.">Make main</button>` : ""}</span>`;
    })
    .join("");
}

/** The order after a tap inside a chips row (a chip or Make main), or null for any other tap. */
export function orderAfterTap(target: HTMLElement, order: string[]): string[] | null {
  const main = target.closest<HTMLElement>("[data-main]");
  const chip = target.closest<HTMLElement>(".chip[data-acc]");
  if (main) return folderOrder(order, main.dataset.main ?? "", "main");
  if (chip) return folderOrder(order, chip.dataset.acc ?? "", "toggle");
  return null;
}

function readOrder(row: HTMLElement): string[] {
  try {
    return JSON.parse(row.dataset.order || "[]") as string[];
  } catch {
    return [];
  }
}

/** Every folder with its account chips, in a sheet: one screen to choose where everything goes.
 * `focus`: an account just connected, or the one whose card opened it (named in the title). */
export function openFoldersSheet(focus = ""): void {
  const s = status();
  const accs = s?.accounts ?? [];
  const map = foldersMap(s);
  const names = Object.keys(map);
  const short = focus.split("@")[0];
  const sheet = openSheet({
    title: focus ? `Folders for ${short}` : "Where each folder goes",
    icon: "folder",
    sub: focus
      ? `Tap ${focus} on the folders it should get`
      : "The first account is the backup; the others get a copy",
    body: names.length
      ? `<div>${names
          .map(
            (name) => `<div class="border-b border-line px-4 py-3 last:border-b-0">
              <div class="flex items-center gap-2 text-sm font-medium">${icon("folder", "size-4 text-ink-3")}${esc(name)}</div>
              <div class="mt-2 flex flex-wrap gap-1.5" data-sheet-folder="${esc(name)}" data-order="${esc(JSON.stringify(map[name]))}">${folderChips(accs, map[name] ?? [])}</div></div>`,
          )
          .join("")}</div>
        <div class="px-4 py-3"><p class="text-xs text-ink-3">An account added to a folder that is already backed up is checked against its Google library first. Nothing is sent to it until you start.</p>
        <div class="mt-4 flex justify-end gap-2"><button type="button" class="btn" data-sheet-close>Cancel</button><button type="button" class="btn primary" data-sheet-save>${icon("ok")}Save</button></div></div>`
      : `<div class="empty">${icon("folderOpen")}No folders found. Mount folders under /media.</div>`,
  });
  sheet.el.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    const row = t.closest<HTMLElement>("[data-sheet-folder]");
    if (row) {
      const order = orderAfterTap(t, readOrder(row));
      if (!order) return;
      row.dataset.order = JSON.stringify(order);
      html(row, folderChips(accs, order));
      return;
    }
    if (t.closest("[data-sheet-save]")) {
      const next: Record<string, string[]> = {};
      $$<HTMLElement>("[data-sheet-folder]", sheet.el).forEach((r) => {
        next[r.dataset.sheetFolder ?? ""] = readOrder(r);
      });
      void saveFolders(next, map).then((ok) => ok && sheet.close());
    }
  });
}
