import { get, post, type ReclaimPrefs, type Settings } from "./api";
import { esc, toast } from "./dom";
import { icon } from "./icons";
import { atOnceLabel, atOnceText, type PaceNow } from "./pace";
import { openSheet } from "./sheet";

// "How many at once" and the choices that go with it, opened from the page they affect (Free
// storage, Copy accounts, Sync) instead of a card at the bottom or a trip to Settings. The same
// button and the same sheet on every page.

/** The button that opens a page's sheet: "Up to 7 (dynamic) at once", or "Items at once" when the
 * number isn't known on that page. `id` picks the sheet. */
export function paceButton(id: "move" | "upload", fixed: number | null, pace?: PaceNow | null): string {
  const text = fixed == null ? "Items at once" : `${atOnceText(fixed, pace)} at once`;
  return `<button type="button" class="pace-btn" data-pace="${id}" title="Change how many run at once">${icon("sliders", "size-3.5")}<span>${esc(text)}</span></button>`;
}

function row(id: string, on: boolean, title: string, hint: string): string {
  return `<div class="sheet-row"><span class="min-w-0 flex-1"><span class="block text-sm font-medium">${esc(title)}</span><span class="block text-xs text-ink-3">${esc(hint)}</span></span><button type="button" role="switch" class="switch shrink-0" data-opt="${id}" aria-checked="${on}" aria-label="${esc(title)}"></button></div>`;
}

function atOnceRow(id: string, title: string, value: number, hint: string): string {
  return `<div class="sheet-row flex-col items-stretch"><div class="flex items-center gap-3"><span class="min-w-0 flex-1 text-sm font-medium">${esc(title)}</span><span class="text-xs text-ink-3" data-now></span></div>
    <div class="pace-pick" role="radiogroup" aria-label="${esc(title)}" data-pick="${id}">${[
      ...new Set([0, 1, 2, 4, 6, 8, value]),
    ]
      .sort((a, b) => a - b)
      .map(
        (n) =>
          `<button type="button" role="radio" aria-checked="${n === value}" data-n="${n}">${n ? n : `${icon("zap", "size-3.5 shrink-0")}Dynamic`}</button>`,
      )
      .join("")}</div>
    <span class="text-xs text-ink-3">${esc(hint)}</span></div>`;
}

function wirePick(el: HTMLElement, onPick: (n: number) => void): void {
  el.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-pick] > button");
    if (!b) return;
    const group = b.parentElement!;
    group.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    onPick(Number(b.dataset.n));
  });
}

/** Free storage and Copy accounts: items at once, and Free storage's choices. */
export async function openMoveOptions(onSaved?: () => void, pace?: PaceNow | null): Promise<void> {
  const st = await get<Settings>("settings").catch(() => null);
  const p: ReclaimPrefs | undefined = st?.reclaim;
  if (!p) return toast("Couldn't load the settings", "bad");
  const save = async (change: Partial<ReclaimPrefs>, done: string) => {
    try {
      await post("settings", { reclaim: change });
      Object.assign(p, change);
      toast(done, "good");
      onSaved?.();
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };
  const sheet = openSheet({
    title: "Moving settings",
    icon: "sliders",
    sub: "Free storage and Copy accounts · every account",
    body: `${atOnceRow("workers", "Items at once", p.workers, "Dynamic runs more side by side while Google keeps up (up to 12; many small photos, few big videos) and slows down when Google asks. Each item needs its size free here while it moves.")}
      ${row("auto", !!p.auto, "Free new items automatically", "What your phones' Google Photos app backs up is freed as it arrives: checked every 15 minutes, in every account. Each new item waits 10 minutes first.")}
      ${row("confirm", p.confirm, "Ask before freeing", "Off: Free up starts at once with these choices.")}
      ${row("keep", p.keep, "Keep the downloaded copies", "On: each copy stays here, under Downloads.")}
      ${row("album", p.album, "Add freed items to an album", `“${p.album_name}” in Google Photos. Captions are never changed.`)}`,
  });
  const now = sheet.el.querySelector<HTMLElement>("[data-now]");
  if (now && !p.workers && pace) now.textContent = `${pace.now} now`;
  wirePick(
    sheet.el,
    (n) =>
      void save(
        { workers: n },
        n ? `Up to ${n} item${n === 1 ? "" : "s"} at once` : "Items at once: dynamic",
      ),
  );
  sheet.el.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-opt]");
    if (!b) return;
    const on = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(on));
    const key = b.dataset.opt as "auto" | "confirm" | "keep" | "album";
    const said = {
      auto: on ? "New items are freed automatically" : "Auto free storage is off",
      confirm: on ? "Free storage asks first" : "Free storage starts without asking",
      keep: on ? "Downloaded copies are kept" : "Downloaded copies are deleted after the upload",
      album: on ? `Freed items go into “${p.album_name}”` : "No album for freed items",
    }[key];
    void save({ [key]: on }, said);
  });
}

/** Sync (and Snapchat, which uploads the same way): files at once and the speed limit. */
export async function openUploadOptions(onSaved?: () => void, pace?: PaceNow | null): Promise<void> {
  const st = await get<Settings>("settings").catch(() => null);
  if (!st) return toast("Couldn't load the settings", "bad");
  const save = async (change: Partial<Settings>, done: string) => {
    try {
      await post("settings", change);
      toast(done, "good");
      onSaved?.();
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };
  const limit = st.speed_limit_mbps || 0;
  const speeds = [0, 5, 10, 25, 50];
  const sheet = openSheet({
    title: "Upload settings",
    icon: "sliders",
    sub: "Backup and Snapchat · every account",
    body: `${atOnceRow("upload", "Files at once", st.upload_workers ?? 0, "Per account: each account uploads this many side by side with the others. Dynamic runs more while Google keeps up (up to 12; fewer for big videos) and slows down when Google asks.")}
      <div class="sheet-row flex-col items-stretch"><span class="text-sm font-medium">Speed limit</span>
        <div class="pace-pick" role="radiogroup" aria-label="Speed limit" data-pick="speed">${[
          ...new Set([...speeds, limit]),
        ]
          .sort((a, b) => a - b)
          .map(
            (n) =>
              `<button type="button" role="radio" aria-checked="${n === limit}" data-n="${n}">${n ? `${n} Mbps` : "No limit"}</button>`,
          )
          .join("")}</div>
        <span class="text-xs text-ink-3">Shared by every account and every file going up at once. Upload hours and a custom limit are in <a class="underline" href="#/settings/sync" data-sheet-close>Settings</a>.</span></div>`,
  });
  const now = sheet.el.querySelector<HTMLElement>("[data-now]");
  if (now && !st.upload_workers && pace) now.textContent = `${pace.now} now`;
  sheet.el.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-pick] > button");
    if (!b) return;
    const group = b.parentElement!;
    group.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    const n = Number(b.dataset.n);
    if (group.dataset.pick === "speed")
      void save({ speed_limit_mbps: n }, n ? `Upload limited to ${n} Mbps` : "No speed limit");
    else
      void save(
        { upload_workers: n },
        `Files at once: ${atOnceLabel(n).replace(" (recommended)", "").toLowerCase()}`,
      );
  });
}
