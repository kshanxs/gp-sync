// The parts every page is built from, so pages look and work alike:
//  - the header (intro, a Labs note, "How it works"): drawn by main.ts from the View's fields;
//  - statRow(): the summary numbers a page opens with;
//  - toolbar(): filters on the left, a mono count, search on the right.

import { esc, html } from "./dom";
import { icon, type IconName } from "./icons";
import { tile } from "./ui";

export type TileOpts = Parameters<typeof tile>[0];

/** A page's summary numbers: two per row on phones, four on wide screens. Fill with setTile(). */
export function statRow(items: TileOpts[]): string {
  return `<section class="stat-row">${items.map(tile).join("")}</section>`;
}

/** Put a value (and the line under it) into a tile made by tile() or statRow(). */
export function setTile(id: string, value: string, sub?: string): void {
  const t = document.getElementById(id);
  if (!t) return;
  const v = t.querySelector<HTMLElement>("[data-v]");
  const s = t.querySelector<HTMLElement>("[data-s]");
  if (v) html(v, value);
  if (s && sub !== undefined) html(s, sub);
}

/** The filter bar: the page's segmented controls first, a count, then search and selects. */
export function toolbar(opts: {
  lead?: string;
  /** Id of the count's element; the page fills it. */
  countId?: string;
  search?: { id: string; placeholder: string };
  tail?: string;
}): string {
  const search = opts.search
    ? `<label class="toolbar-search">${icon("search", "pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-3")}<input class="input pl-9" id="${opts.search.id}" placeholder="${esc(opts.search.placeholder)}" aria-label="${esc(opts.search.placeholder)}"></label>`
    : "";
  return `<section class="toolbar">
    ${opts.lead ?? ""}
    ${opts.countId ? `<span class="meta toolbar-count" id="${opts.countId}"></span>` : ""}
    <div class="toolbar-end">${search}${opts.tail ?? ""}</div>
  </section>`;
}

/** One step of "How it works". `text` is trusted HTML written in the code (it may hold <code>). */
export interface HowStep {
  ic: IconName;
  title: string;
  text: string;
}

/** Numbered steps, the same wherever a page explains itself. */
export function howSteps(steps: HowStep[], attrs = ""): string {
  return `<ol class="how"${attrs}>${steps
    .map(
      (s, i) =>
        `<li class="how-step"><span class="how-n">${i + 1}</span><div class="min-w-0"><div class="flex items-center gap-2 text-sm font-medium text-ink">${icon(s.ic, "size-4 text-ink-3")}${esc(s.title)}</div><p class="mt-1 text-xs leading-5 break-words text-ink-3">${s.text}</p></div></li>`,
    )
    .join("")}</ol>`;
}

/** The page header: what the page is for, Labs when it is one, and "How it works" (folded). */
export function pageHead(id: string, intro: string, opts: { labs?: boolean; how?: HowStep[] }): string {
  const open = opts.how ? howOpen(id) : false;
  const how = opts.how
    ? `<button type="button" class="link-btn sm ml-auto" data-how aria-expanded="${open}" aria-controls="how-${id}">${icon("info")}How it works</button>`
    : "";
  const labs = opts.labs
    ? `<span class="page-tag" data-tip="New or specialised: it works, and it may still change">Labs</span>`
    : "";
  const steps = opts.how ? howSteps(opts.how, ` id="how-${id}"${open ? "" : " hidden"}`) : "";
  if (!intro && !labs && !how) return "";
  return `<header class="page-head">${labs}<p class="page-intro">${esc(intro)}</p>${how}</header>${steps}`;
}

const HOW_KEY = "gpsync.how";

function howOpen(id: string): boolean {
  try {
    return (JSON.parse(localStorage.getItem(HOW_KEY) ?? "[]") as string[]).includes(id);
  } catch {
    return false;
  }
}

/** Fold or unfold "How it works"; the choice is kept per page on this device. */
export function toggleHow(id: string, btn: HTMLElement): void {
  const list = document.getElementById(`how-${id}`);
  if (!list) return;
  const open = list.hidden;
  list.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
  try {
    const set = new Set(JSON.parse(localStorage.getItem(HOW_KEY) ?? "[]") as string[]);
    if (open) set.add(id);
    else set.delete(id);
    localStorage.setItem(HOW_KEY, JSON.stringify([...set]));
  } catch {
    /* storage blocked: the fold just isn't remembered */
  }
}
