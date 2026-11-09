// Small DOM helpers: escaping, querying and a toast.

export function esc(s: string | number | null | undefined): string {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

export function $<T extends Element = HTMLElement>(sel: string, root: ParentNode = document): T {
  const el = root.querySelector<T>(sel);
  if (!el) throw new Error("Missing element " + sel);
  return el;
}

export function $$<T extends Element = HTMLElement>(sel: string, root: ParentNode = document): T[] {
  return [...root.querySelectorAll<T>(sel)];
}

/** Show `markup` in `el`. Nothing happens when it is unchanged; otherwise the page is updated in
 * place (see morph), so rows, pictures, hover and focus stay put while numbers and bars move. */
export function html(el: Element, markup: string): void {
  const host = el as HTMLElement;
  // Skipped only if nobody replaced the content by hand since (its first node is still ours).
  if (host.dataset.html === markup && drawn.get(el) === el.firstChild) return;
  if (!el.firstChild) el.innerHTML = markup;
  else {
    const t = document.createElement("template");
    t.innerHTML = markup;
    morphChildren(el, t.content);
  }
  host.dataset.html = markup;
  drawn.set(el, el.firstChild);
}

const drawn = new WeakMap<Element, Node | null>();

// ---- updating in place
// Elements the new markup still has are kept and only their changed attributes and text are
// touched. Rows are matched by a key (data-key, data-open: a file, data-proc, or id), so a new row
// at the top of a list moves the others down instead of rewriting each one: their pictures never
// reload. Classes the page adds itself once a picture has loaded stay on.

const RUNTIME_CLASSES = ["loaded", "done"];

function keyOf(n: Node): string | null {
  if (n.nodeType !== 1) return null;
  const e = n as Element;
  return (
    e.getAttribute("data-key") ?? e.getAttribute("data-open") ?? e.getAttribute("data-proc") ?? (e.id || null)
  );
}

function patchAttributes(old: Element, next: Element): void {
  // A copy: attributes are removed while going through them.
  for (const a of Array.from(old.attributes)) {
    // Inline styles set by the page (the light under the mouse) stay unless the markup has its own.
    if (!next.hasAttribute(a.name) && a.name !== "style") old.removeAttribute(a.name);
  }
  for (const a of next.attributes) {
    let v = a.value;
    if (a.name === "class") {
      const keep = RUNTIME_CLASSES.filter((c) => old.classList.contains(c) && !next.classList.contains(c));
      if (keep.length) v = `${v} ${keep.join(" ")}`;
    }
    if (old.getAttribute(a.name) !== v) old.setAttribute(a.name, v);
  }
}

function morphNode(old: Node, next: Node): void {
  if (old.nodeType !== 1) {
    if (old.nodeValue !== next.nodeValue) old.nodeValue = next.nodeValue;
    return;
  }
  const o = old as Element;
  const n = next as Element;
  // A picture already on screen is never reloaded: same file, same element.
  if (o.tagName === "IMG" && o.getAttribute("src") === n.getAttribute("src")) {
    for (const name of ["alt", "title"]) {
      const v = n.getAttribute(name);
      if (v !== null && o.getAttribute(name) !== v) o.setAttribute(name, v);
    }
    return;
  }
  patchAttributes(o, n);
  // Charts draw their own insides (and keep them while the data is the same).
  if (o.hasAttribute("data-chart")) return;
  // Ticks follow the page's own state (Select all, a cleared pick), not only the user's clicks.
  if (o instanceof HTMLInputElement && (o.type === "checkbox" || o.type === "radio"))
    o.checked = n.hasAttribute("checked");
  // A field being typed in keeps what the user typed.
  if (
    (o.tagName === "INPUT" || o.tagName === "TEXTAREA" || o.tagName === "SELECT") &&
    o === document.activeElement
  )
    return;
  morphChildren(o, n);
}

export function morphChildren(old: Node, next: Node): void {
  const keyed = new Map<string, Node>();
  for (const k of old.childNodes) {
    const key = keyOf(k);
    if (key && !keyed.has(key)) keyed.set(key, k);
  }
  let cursor = old.firstChild;
  // A copy: new nodes move out of it.
  for (const n of Array.from(next.childNodes)) {
    const key = keyOf(n);
    let match: Node | null = null;
    if (key) {
      const k = keyed.get(key);
      if (k && k.nodeName === n.nodeName) {
        match = k;
        keyed.delete(key);
      }
    } else if (cursor && !keyOf(cursor) && cursor.nodeType === n.nodeType && cursor.nodeName === n.nodeName) {
      match = cursor;
    }
    if (!match) {
      old.insertBefore(n, cursor); // new: moved over from the parsed markup
      continue;
    }
    if (match === cursor) cursor = cursor.nextSibling;
    else old.insertBefore(match, cursor);
    morphNode(match, n);
  }
  while (cursor) {
    const after: ChildNode | null = cursor.nextSibling;
    old.removeChild(cursor);
    cursor = after;
  }
}

/** Mark `el` as changed by hand (rows added to the end, say): the next html() compares with what
 * is on screen instead of skipping because the markup looks the same as last time. */
export function touched(el: Element): void {
  (el as HTMLElement).dataset.html = "";
}

export function text(el: Element, value: string): void {
  if (el.textContent !== value) el.textContent = value;
}

/** Give a user-initiated filter change visible feedback without animating background refreshes. */
export function animateChange(el: HTMLElement | null): void {
  if (!el || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  el.classList.remove("content-swap");
  void el.offsetWidth;
  el.classList.add("content-swap");
  el.addEventListener("animationend", () => el.classList.remove("content-swap"), { once: true });
}

let toastTimer = 0;
const TOAST_ICONS = {
  good: '<path d="M20 6 9 17l-5-5"/>',
  bad: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
};

/** A short message at the bottom (above the phone tab bar). Errors stay longer; tap to close. */
export function toast(message: string, kind: "good" | "bad" | "info" = "info"): void {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    el.addEventListener("click", () => el?.classList.remove("show"));
    document.body.append(el);
  }
  el.setAttribute("role", kind === "bad" ? "alert" : "status");
  el.dataset.kind = kind;
  el.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${TOAST_ICONS[kind]}</svg><span>${esc(message)}</span>`;
  el.classList.remove("show");
  void el.offsetWidth; // restart the entrance animation
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el?.classList.remove("show"), kind === "bad" ? 7000 : 3500);
}

export async function copyText(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const t = document.createElement("textarea");
    t.value = value;
    document.body.append(t);
    t.select();
    document.execCommand("copy");
    t.remove();
  }
  toast("Copied", "good");
}
