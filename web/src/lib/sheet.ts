import { esc } from "./dom";
import { icon, type IconName } from "./icons";

// A panel over the page for settings that belong to what is on screen: a sheet from the bottom on
// phones, a centred window on wider screens. Escape, the close button or a tap outside closes it.

export interface Sheet {
  el: HTMLElement;
  close(): void;
}

export function openSheet(o: {
  title: string;
  icon: IconName;
  sub?: string;
  body: string;
  onClose?: () => void;
}): Sheet {
  document.querySelector(".sheet-wrap")?.remove(); // one at a time
  const box = document.createElement("div");
  box.className = "sheet-wrap";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-labelledby", "sheet-title");
  box.innerHTML = `<div class="sheet card">
    <div class="flex items-center gap-3 border-b border-line px-4 py-3">
      <span class="grid size-9 shrink-0 place-items-center rounded-xl bg-panel-2 text-accent">${icon(o.icon, "size-4")}</span>
      <div class="min-w-0 flex-1"><h2 id="sheet-title" class="truncate font-semibold">${esc(o.title)}</h2>${o.sub ? `<p class="truncate text-xs text-ink-3">${esc(o.sub)}</p>` : ""}</div>
      <button class="btn icon sm" data-sheet-close aria-label="Close">${icon("x")}</button>
    </div>
    <div class="sheet-body">${o.body}</div>
  </div>`;
  const close = () => {
    box.classList.add("closing");
    document.removeEventListener("keydown", onKey, true);
    setTimeout(() => box.remove(), 160);
    o.onClose?.();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") close();
  };
  box.addEventListener("click", (e) => {
    if (e.target === box || (e.target as HTMLElement).closest("[data-sheet-close]")) close();
  });
  document.addEventListener("keydown", onKey, true);
  document.body.append(box);
  box.querySelector<HTMLElement>("[data-sheet-close]")?.focus();
  return { el: box, close };
}
