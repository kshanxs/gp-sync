import { describe, expect, it } from "vite-plus/test";
import { groups, itemPct, totals, workBox, workPanel } from "./work-panel";

const items = [
  { key: "a", name: "a.jpg", step: "uploading", done: 50, total: 100 },
  { key: "b", name: "b.jpg", step: "uploading", done: 0, total: 300 },
  { key: "c", name: "c.mp4", step: "downloading", done: 25, total: 100 },
  { key: "d", name: "d.jpg", step: "checking" },
  { key: "e", name: "e.jpg", step: "downloaded", pct: 100, note: "Replacing in Google next" },
];

describe("work panel", () => {
  it("counts each step, in a fixed order", () => {
    expect(groups(items)).toEqual([
      ["uploading", 2],
      ["downloading", 1],
      ["checking", 1],
      ["waiting", 1],
    ]);
  });

  it("adds up only the bytes that are moving", () => {
    expect(totals(items)).toEqual({ done: 75, total: 500 });
  });

  it("draws a share for byte steps, a sliding bar for the rest", () => {
    expect(itemPct(items[0]!)).toBe(50);
    expect(itemPct(items[1]!)).toBeNull(); // nothing moved yet
    expect(itemPct(items[3]!)).toBeNull();
    expect(itemPct(items[4]!)).toBe(100);
  });

  it("keys every row, so live updates keep the list's scroll position", () => {
    const html = workBox(items);
    for (const i of items) expect(html).toContain(`data-key="${i.key}"`);
    expect(html).toContain("work-list");
    expect(html).toContain("75 B of 500 B");
  });

  it("is empty when nothing runs", () => {
    expect(workBox([])).toBe("");
    expect(workPanel("Working now", "loader", [])).toBe("");
  });
});
