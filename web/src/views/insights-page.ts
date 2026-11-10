import { icon, type IconName } from "../lib/icons";
import activity from "./history";
import patterns from "./insights";
import type { View } from "./view";

// Insights: what every process did, in two tabs on one page. Activity is the day-by-day record
// (12 months, per day, per month); Patterns is when things happen and the uploads in detail. They
// were two pages (History and Insights) reading the same numbers. #/history still lands here.

const TABS: { id: string; label: string; ic: IconName; view: View }[] = [
  { id: "activity", label: "Activity", ic: "calendar", view: activity },
  { id: "patterns", label: "Patterns", ic: "chart", view: patterns },
];

let current: (typeof TABS)[number] | null = null;
let host: HTMLElement | null = null;

function show(id?: string): void {
  const t = TABS.find((x) => x.id === id) ?? TABS[0]!;
  if (t === current || !host) return;
  current?.view.unmount?.();
  current = t;
  document.querySelectorAll<HTMLElement>("[data-ins-tab]").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.insTab === t.id));
  });
  host.innerHTML = "";
  const page = document.createElement("div");
  host.append(page);
  t.view.mount(page, []);
}

const insightsPage: View = {
  subtitle: "What every process did, and when",
  mount(el, args) {
    el.innerHTML = `<div class="page-tabs mb-4" role="tablist" aria-label="Insights">${TABS.map(
      (t) =>
        `<a href="#/insights/${t.id}" role="tab" aria-selected="false" data-ins-tab="${t.id}">${icon(t.ic, "size-4")}<span>${t.label}</span></a>`,
    ).join("")}</div><div id="ins-host"></div>`;
    host = el.querySelector<HTMLElement>("#ins-host");
    current = null;
    show(args[0]);
  },
  route(args) {
    show(args[0]);
  },
  update(s) {
    current?.view.update?.(s);
  },
  unmount() {
    current?.view.unmount?.();
    current = null;
    host = null;
  },
};

export default insightsPage;
