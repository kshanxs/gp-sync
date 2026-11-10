// Segmented controls (.seg) on small screens: when the choices don't fit on one line they become
// an even grid (style.css, .seg[data-wrap]) instead of hiding choices off to the side.

const PHONE = "(max-width: 639px)";

/** True when a row of choices is wider than the space it has. */
export function overflows(contentWidth: number, boxWidth: number): boolean {
  return contentWidth > boxWidth + 1;
}

export function fitSegs(root: ParentNode = document): void {
  const phone = window.matchMedia(PHONE).matches;
  for (const seg of root.querySelectorAll<HTMLElement>(".seg")) {
    delete seg.dataset.wrap; // measure as one line
    if (!phone) {
      // Wider screens: choices that don't fit go on a second line, never cut off.
      if (overflows(seg.scrollWidth, seg.clientWidth)) seg.dataset.wrap = "flex";
      continue;
    }
    // The number of choices picks the grid (style.css): five go 3 + 2, never 2 + 2 + 1.
    if (overflows(seg.scrollWidth, seg.clientWidth)) seg.dataset.wrap = String(seg.children.length);
  }
}

/** Keep every .seg fitted as pages render and the window changes size. */
export function watchSegs(): void {
  let queued = false;
  const run = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      fitSegs();
      fadeEdges();
    });
  };
  new MutationObserver(run).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("resize", run);
  // Rows that scroll sideways fade at the edge with more to see; updated as they scroll.
  document.addEventListener(
    "scroll",
    (e) => {
      if (e.target instanceof HTMLElement && e.target.matches(FADE)) fadeOne(e.target);
    },
    { capture: true, passive: true },
  );
  run();
}

const FADE = ".set-tabs, .fade-x";

function fadeOne(el: HTMLElement): void {
  const more = el.scrollWidth - el.clientWidth;
  el.dataset.fadeL = String(el.scrollLeft > 2);
  el.dataset.fadeR = String(more - el.scrollLeft > 2);
}

/** Mark every sideways-scrolling row with which edges have more beyond them (style.css fades them). */
export function fadeEdges(root: ParentNode = document): void {
  for (const el of root.querySelectorAll<HTMLElement>(FADE)) fadeOne(el);
}
