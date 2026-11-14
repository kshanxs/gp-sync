// Full-screen viewer for photos and videos (PhotoSwipe): it grows out of the thumbnail, pinch or
// double-tap to zoom, drag to pan, swipe between files, swipe down or Esc to close. The top bar has
// the file's name, Google Photos, download and Details (status, errors, retry), as before.
// PhotoSwipe is loaded the first time a file is opened, so pages that never open one don't pay for it.

import type PhotoSwipe from "photoswipe";
import type { SlideData } from "photoswipe";
import { get, mediaUrl, post, previewUrl, thumbUrl, type FileInfo } from "./api";
import { esc, toast } from "./dom";
import { ago, bytes, clock, date, fileName, folderOf, ms } from "./format";
import { icon } from "./icons";
import { refresh } from "./store";
import { badge, eventMeta, type Tone } from "./ui";

const STATUS: Record<string, [string, Tone]> = {
  uploaded: ["Backed up", "good"],
  exists: ["Already in Google", "info"],
  failed: ["Failed", "bad"],
  waiting: ["Waiting", ""],
  missing: ["No longer in the folder", "warn"],
};

const VIDEO = /\.(mp4|m4v|mov|webm)$/i;
/** Longest side of the server's preview (api/preview): the size a photo is shown at before its own
 * size is known. */
const PREVIEW = 1600;
/** Width of the Details panel beside the picture, on wider screens. */
const PANEL = 384;

interface Slide extends SlideData {
  name: string;
  video: boolean;
}

let pswp: PhotoSwipe | null = null;
let infoOpen = false;
let infoFor = ""; // the file the Details panel shows (or is loading)

/** A first size for a file, from its thumbnail on the page (same shape), fixed once it loads. */
function guessSize(el: Element | undefined): { width: number; height: number } {
  const img = el?.matches("img") ? (el as HTMLImageElement) : el?.querySelector("img");
  const w = img?.naturalWidth || 4;
  const h = img?.naturalHeight || 3;
  const k = PREVIEW / Math.max(w, h);
  return { width: Math.round(w * k), height: Math.round(h * k) };
}

/** The real size, once the picture or video has loaded: zoom limits and the shape follow it. */
function fitTo(p: PhotoSwipe, index: number, width: number, height: number): void {
  const data = p.options.dataSource as Slide[];
  const d = data[index];
  if (!d || !width || !height || (d.width === width && d.height === height)) return;
  d.width = width;
  d.height = height;
  const slide = p.mainScroll.itemHolders.map((h) => h.slide).find((s) => s?.index === index);
  if (!slide) return;
  slide.width = slide.content.width = width;
  slide.height = slide.content.height = height;
  slide.calculateSize();
  slide.zoomAndPanToInitial();
  slide.applyCurrentZoomPan();
  slide.updateContentSize(true);
}

function infoPanel(f: FileInfo): string {
  const [label, tone] = STATUS[f.status] ?? [f.status, ""];
  const rows: [string, string][] = [
    ["Folder", esc(folderOf(f.name) || "–")],
    ["Size", f.size ? bytes(f.size) : "–"],
    ["File date", f.modified ? `${date(f.modified, true)} ${clock(f.modified)}` : "–"],
    ["Google account", esc(f.account || "Not backed up")],
    ["Backed up", f.updated && (f.status === "uploaded" || f.status === "exists") ? ago(f.updated) : "–"],
    ["Upload took", f.upload_ms != null ? ms(f.upload_ms) : "–"],
  ];
  return `
    <div class="flex flex-wrap items-center gap-1.5">${badge(label, tone, tone === "good" ? "ok" : tone === "bad" ? "error" : "info")}${badge(f.kind === "video" ? "Video" : "Photo", "", f.kind === "video" ? "video" : "photo")}${f.attempts ? badge(`${f.attempts} of ${f.max_attempts} tries`, f.attempts >= f.max_attempts ? "bad" : "warn") : ""}</div>
    ${
      f.status === "failed"
        ? `<div class="mt-3 rounded-xl border border-bad/30 bg-bad/10 p-3"><div class="flex items-center gap-2 text-sm font-medium text-bad">${icon("warn")}Why it failed</div>
           <div class="mt-1.5 font-mono text-xs break-words text-ink-2">${esc(f.error || "Unknown error")}</div>
           <button class="btn primary mt-3 w-full" data-v="retry">${icon("retry")}Retry this file</button></div>`
        : ""
    }
    <div class="mt-3">${rows.map(([k, v]) => `<div class="kv"><span>${k}</span><span class="truncate">${v}</span></div>`).join("")}</div>
    ${
      f.history.length
        ? `<div class="label mt-4 mb-1">History</div>${f.history
            .map((h) => {
              const m = eventMeta[h.kind] ?? eventMeta.info!;
              return `<div class="flex items-start gap-2 border-b border-line py-2 text-xs last:border-0">${icon(m.icon, "mt-0.5 size-3.5 shrink-0 text-ink-3")}<div class="min-w-0 flex-1"><div class="break-words text-ink-2">${esc(h.text)}</div><div class="text-ink-3">${date(h.time)} ${clock(h.time)}</div></div></div>`;
            })
            .join("")}`
        : ""
    }
    ${f.sha1 ? `<div class="mt-3 truncate font-mono text-[11px] text-ink-3" title="SHA-1">${f.sha1}</div>` : ""}`;
}

function panel(p: PhotoSwipe): HTMLElement | null {
  return p.element?.querySelector<HTMLElement>(".vw-info") ?? null;
}

function setInfo(p: PhotoSwipe, open: boolean): void {
  infoOpen = open;
  panel(p)?.classList.toggle("hidden", !open);
  p.element?.querySelector(".vw-info-btn")?.setAttribute("aria-pressed", String(open));
  p.updateSize(true); // the picture makes room for the panel on wider screens
}

/** The current file's name, links and Details. */
async function showFile(p: PhotoSwipe): Promise<void> {
  const d = p.currSlide?.data as Slide | undefined;
  if (!d) return;
  const total = p.getNumItems();
  const title = p.element?.querySelector(".vw-title");
  if (title)
    title.innerHTML = `<div class="keep-line truncate text-sm font-medium">${esc(fileName(d.name))}</div><div class="keep-line truncate text-xs text-white/60">${esc(folderOf(d.name))}${total > 1 ? ` · ${p.currIndex + 1} of ${total}` : ""}</div>`;
  const dl = p.element?.querySelector<HTMLAnchorElement>(".vw-dl");
  if (dl) {
    dl.href = mediaUrl(d.name, true);
    dl.download = fileName(d.name);
    // iPhone home-screen apps have no back button: open the file in a sheet instead of in place.
    if ((navigator as Navigator & { standalone?: boolean }).standalone === true) dl.target = "_blank";
  }
  const g = p.element?.querySelector<HTMLAnchorElement>(".vw-google");
  g?.classList.add("hidden");
  const info = panel(p);
  if (!info) return;
  infoFor = d.name;
  info.innerHTML = `<div class="skeleton h-40"></div>`;
  const f = await get<FileInfo>("file?path=" + encodeURIComponent(d.name)).catch(() => null);
  if (infoFor !== d.name || pswp !== p) return;
  if (!f) {
    info.innerHTML = `<div class="text-sm text-ink-3">Couldn't load the details</div>`;
    return;
  }
  if (g && f.google_url) {
    g.href = f.google_url;
    g.classList.remove("hidden");
  }
  info.innerHTML = infoPanel(f);
  if (f.status === "failed" && !infoOpen) setInfo(p, true);
}

async function retry(p: PhotoSwipe, b: HTMLButtonElement): Promise<void> {
  b.disabled = true;
  const name = (p.currSlide?.data as Slide | undefined)?.name;
  if (!name) return;
  const r = await post<{ ok: boolean }>("retry", { path: name });
  toast(r.ok ? "This file will be tried again now" : "Nothing to retry", r.ok ? "good" : "info");
  void refresh();
  void showFile(p);
}

const barButton = (cls: string, label: string, ic: Parameters<typeof icon>[0]) => ({
  isButton: true,
  className: `vw-btn ${cls}`,
  title: label,
  ariaLabel: label,
  html: icon(ic, "size-5"),
});

/** Open the viewer at `start` within a list of files (relative to the media folder). `els` are the
 * thumbnails on the page, by index: the picture grows out of its own and shrinks back into it. */
export async function openViewer(
  list: string[],
  start = 0,
  withInfo = false,
  els: Element[] = [],
): Promise<void> {
  if (!list.length) return;
  const [{ default: PhotoSwipeClass }] = await Promise.all([
    import("photoswipe"),
    import("photoswipe/style.css"),
  ]);
  pswp?.destroy();
  const dataSource: Slide[] = list.map((name, i) => {
    const video = VIDEO.test(name);
    const el = els[i];
    return {
      name,
      video,
      ...guessSize(el),
      ...(video ? { type: "video" } : { src: previewUrl(name), msrc: thumbUrl(name) }),
      element: el as HTMLElement | undefined,
      thumbCropped: true,
      alt: fileName(name),
    };
  });
  infoOpen = withInfo || window.innerWidth >= 1280;
  const p = new PhotoSwipeClass({
    dataSource,
    index: Math.max(0, Math.min(start, list.length - 1)),
    bgOpacity: 1,
    counter: false, // the title says "3 of 40"
    loop: list.length > 2,
    showHideAnimationType: "zoom",
    errorMsg: "This file can't be shown here. Download it to open it.",
    paddingFn: (viewport) => ({
      top: 56,
      bottom: 16,
      left: 0,
      right: infoOpen && viewport.x >= 640 ? PANEL : 0,
    }),
  });
  pswp = p;

  // Videos: a player instead of a picture; dragging on it is left to its controls.
  p.on("contentLoad", (e) => {
    const { content } = e;
    const d = content.data as Slide;
    if (!d.video) return;
    e.preventDefault();
    const box = document.createElement("div");
    box.className = "pswp__content vw-video";
    const v = document.createElement("video");
    v.src = mediaUrl(d.name);
    v.poster = thumbUrl(d.name);
    v.controls = true;
    v.playsInline = true;
    v.preload = "metadata";
    v.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    v.addEventListener("loadedmetadata", () => fitTo(p, content.index, v.videoWidth, v.videoHeight));
    box.append(v);
    content.element = box;
  });
  p.on("loadComplete", ({ content }) => {
    const img = content.element;
    if (img instanceof HTMLImageElement && img.naturalWidth)
      fitTo(p, content.index, img.naturalWidth, img.naturalHeight);
  });
  p.on("change", () => {
    p.element?.querySelectorAll("video").forEach((v) => {
      if (!p.currSlide?.container.contains(v)) v.pause();
    });
    void showFile(p);
  });

  p.on("uiRegister", () => {
    p.ui?.registerElement({
      name: "vw-title",
      className: "vw-title",
      appendTo: "bar",
      order: 5,
      html: "",
    });
    p.ui?.registerElement({
      name: "vw-google",
      order: 7,
      tagName: "a",
      ...barButton("vw-google hidden", "Open in Google Photos", "external"),
      onInit: (el) => el.setAttribute("target", "_blank"),
    });
    p.ui?.registerElement({
      name: "vw-dl",
      order: 8,
      tagName: "a",
      ...barButton("vw-dl", "Download original", "download"),
    });
    p.ui?.registerElement({
      name: "vw-info-btn",
      order: 9,
      ...barButton("vw-info-btn", "Details", "info"),
      onClick: () => setInfo(p, !infoOpen),
    });
    p.ui?.registerElement({
      name: "vw-info",
      className: `vw-info scroll-thin ${infoOpen ? "" : "hidden"}`,
      appendTo: "root",
      onInit: (el) => {
        el.addEventListener("click", (ev) => {
          const b = (ev.target as HTMLElement).closest<HTMLButtonElement>("[data-v=retry]");
          if (b) void retry(p, b);
        });
        // Scrolling the panel isn't a swipe or a zoom.
        el.addEventListener("pointerdown", (ev) => ev.stopPropagation());
        el.addEventListener("wheel", (ev) => ev.stopPropagation());
      },
    });
  });

  const keys = (e: KeyboardEvent) => {
    if (e.key === "i" && !(e.target as HTMLElement).closest("input, textarea")) setInfo(p, !infoOpen);
  };
  document.addEventListener("keydown", keys);
  p.on("destroy", () => {
    document.removeEventListener("keydown", keys);
    if (pswp === p) pswp = null;
  });
  p.init();
  p.element?.querySelector(".vw-info-btn")?.setAttribute("aria-pressed", String(infoOpen));
  void showFile(p);
}
