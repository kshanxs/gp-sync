// Account pickers for pages that work on one Google account at a time (Fix dates, Free storage):
// chips for every account when there are several, or a button with a menu.

import { esc } from "./dom";
import { icon } from "./icons";
import { status } from "./store";

/** First letter of an address, for the round avatar. */
export function initial(email: string): string {
  return (email.trim()[0] ?? "?").toUpperCase();
}

/** The account's state, for menus and lists. */
export function accountState(email: string, failed: string[] = status()?.login_failed ?? []): "ok" | "bad" {
  return failed.includes(email) ? "bad" : "ok";
}

export function avatar(email: string, size = "size-7"): string {
  return `<span class="acc-av ${size}" aria-hidden="true">${esc(initial(email))}</span>`;
}

/** Markup: the button (current account) and its menu. `note(account)` adds a short line per account. */
export function accountMenu(id: string, current: string, note?: (account: string) => string): string {
  const s = status();
  // Before the first status arrives, show the page's account on its own.
  const accs = s?.accounts?.length ? s.accounts : current ? [current] : [];
  if (!accs.length) return `<span class="text-sm text-ink-3">No account connected</span>`;
  const items = accs
    .map((a, i) => {
      const extra = [
        i === 0 && accs.length > 1 ? "Main" : "",
        accountState(a) === "bad" ? "Login rejected" : "",
      ]
        .concat(note ? [note(a)] : [])
        .filter(Boolean)
        .join(" · ");
      return `<button type="button" role="menuitemradio" class="acc-item" data-account="${esc(a)}" aria-checked="${a === current}">
        ${avatar(a)}<span class="min-w-0 flex-1 text-left"><span class="block truncate text-sm font-medium">${esc(a)}</span>${extra ? `<span class="block truncate text-xs ${accountState(a) === "bad" ? "text-bad" : "text-ink-3"}">${esc(extra)}</span>` : ""}</span>
        ${icon("check", "acc-tick size-4")}</button>`;
    })
    .join("");
  return `<div class="acc-pick" id="${id}">
    <button type="button" class="acc-btn" aria-haspopup="menu" aria-expanded="false" ${accs.length < 2 ? "disabled" : ""}>
      ${avatar(current)}<span class="min-w-0 truncate text-sm font-medium">${esc(current)}</span>${accs.length > 1 ? icon("chevron", "size-4 shrink-0 rotate-90 text-ink-3") : ""}
    </button>
    <div class="acc-menu" role="menu" hidden>${items}</div>
  </div>`;
}

/** Open and close the menu; `onPick` runs when another account is chosen. */
export function wireAccountMenu(id: string, onPick: (account: string) => void): void {
  const root = document.getElementById(id);
  if (!root || root.dataset.wired) return;
  root.dataset.wired = "";
  const btn = root.querySelector<HTMLButtonElement>(".acc-btn")!;
  const menu = root.querySelector<HTMLElement>(".acc-menu")!;
  const close = () => {
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", onKey, true);
  };
  const outside = (e: Event) => {
    if (!root.contains(e.target as Node)) close();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      close();
      btn.focus();
    }
  };
  btn.addEventListener("click", () => {
    if (!menu.hidden) return close();
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", onKey, true);
    menu.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  });
  menu.addEventListener("click", (e) => {
    const item = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-account]");
    if (!item) return;
    close();
    if (item.getAttribute("aria-checked") !== "true") onPick(item.dataset.account ?? "");
  });
}

const lastPick: Record<string, string> = {};

/** The account a page opens on: the one in the address, else the one last picked on that page,
 * else one with work under way (`busy`, most active first), else the main account. Coming back
 * to a page so shows the account that is working, not always the main one. */
export function openingAccount(page: string, args: string[] | undefined, busy: string[] = []): string {
  const accs = status()?.accounts ?? [];
  const want = decodeURIComponent(args?.[0] ?? "");
  if (accs.includes(want)) return want;
  const last = lastPick[page];
  if (last && accs.includes(last)) return last;
  return busy.find((a) => accs.includes(a)) ?? accs[0] ?? "";
}

/** Remember the page's account (for coming back), and put it in the address without a reload. */
export function rememberAccount(page: string, account: string, rest = ""): void {
  if (!account) return;
  lastPick[page] = account;
  const hash = `#/${page}/${encodeURIComponent(account)}${rest ? `/${rest}` : ""}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
}

/** Accounts freeing storage right now, most items under way first (paused ones left out). */
export function freeingAccounts(): string[] {
  const r = status()?.reclaim ?? {};
  return Object.entries(r)
    .filter(([, v]) => v.queued && !v.paused)
    .sort((a, b) => b[1].working - a[1].working)
    .map(([a]) => a);
}

/** Accounts with a job of this kind running now (status.jobs). */
export function busyAccounts(kind: string): string[] {
  return [...new Set((status()?.jobs ?? []).filter((j) => j.kind === kind).map((j) => j.account))];
}

/** What an account chip says under the address. */
export interface ChipLine {
  line: string;
  /** Extra in grey after the line ("1.8 GB freed"). */
  extra?: string;
  tone?: string;
  live?: boolean;
}

/** Every connected account as a row of chips, the current one ticked (Free storage, Fix dates): a
 * list on phones, columns on wider screens. Clicks carry data-acc. "" with only one account. */
export function accountChips(current: string, lineFor: (account: string) => ChipLine): string {
  const accs = status()?.accounts ?? [];
  if (accs.length < 2) return "";
  return `<div class="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2 ${accs.length > 2 ? "xl:grid-cols-3" : ""}" role="tablist" aria-label="Accounts">${accs
    .map((a, n) => {
      const { line, extra, tone = "text-ink-3", live } = lineFor(a);
      const on = a === current;
      return `<button type="button" role="tab" data-acc="${esc(a)}" aria-selected="${on}" class="acc-chip card flex min-w-0 items-center gap-3 px-3 py-2.5 text-left ${on ? "border-accent" : ""}">
        ${avatar(a, "size-8")}
        <span class="min-w-0 flex-1"><span class="flex min-w-0 items-center gap-1.5"><span class="truncate text-sm font-medium" title="${esc(a)}">${esc(a)}</span>${n === 0 ? '<span class="shrink-0 rounded-full bg-panel-2 px-1.5 text-[10px] font-medium text-ink-3">Main</span>' : ""}</span>
        <span class="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs"><span class="flex min-w-0 items-center gap-1.5 ${tone}">${live ? '<span class="dot live shrink-0"></span>' : ""}<span class="truncate">${esc(line)}</span></span>${extra ? `<span class="text-ink-3">· ${esc(extra)}</span>` : ""}</span></span>
        ${on ? icon("check", "size-4 shrink-0 text-accent") : ""}
      </button>`;
    })
    .join("")}</div>`;
}
