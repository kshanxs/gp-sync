import { describe, expect, it } from "vite-plus/test";
import { average, groups, itemPct, listRows, segments, totals, workBox, workPanel } from "./work-panel";

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

  it("splits the summary bar by step, filled to the average of all items", () => {
    const s = segments(items);
    expect(s.map((x) => [x.group, x.width])).toEqual([
      ["uploading", 40],
      ["downloading", 20],
      ["checking", 20],
      ["waiting", 20],
    ]);
    expect(s.find((x) => x.group === "checking")!.fill).toBeNull(); // no share yet: it slides
    // a 50, b 0, c 25, d unknown (0), e 100: 175 / 5
    expect(average(items)).toBe(35);
    expect(s.reduce((a, x) => a + ((x.fill ?? 0) * x.width) / 100, 0)).toBeCloseTo(35);
    expect(workBox(items)).toContain("35% on average");
  });

  it("puts items waiting the same way in one row, and keeps every item in the counts", () => {
    const parked = Array.from({ length: 300 }, (_, n) => ({
      key: `p${n}`,
      name: `IMG_${n}.JPG`,
      step: "downloaded",
      pct: 100,
      note: "Replacing in Google next",
      waiting: true,
    }));
    const odd = { key: "x", name: "x.jpg", step: "in_google", note: "Trying again soon", waiting: true };
    const all = [...items.slice(0, 2), ...parked, odd];
    const rows = listRows(all);
    expect(rows.map((r) => r.name)).toEqual(["a.jpg", "b.jpg", "300 items", "x.jpg"]);
    expect(rows[2]!.pct).toBe(100);
    expect(rows[2]!.title).toContain("IMG_0.JPG, IMG_1.JPG");
    expect(rows[2]!.title).toContain("and 270 more");
    expect(groups(all)).toEqual([
      ["uploading", 2],
      ["waiting", 301],
    ]);
    expect(workBox(all).match(/<li /g)).toHaveLength(4);
  });

  it("is empty when nothing runs", () => {
    expect(workBox([])).toBe("");
    expect(workPanel("Working now", "loader", [])).toBe("");
  });
});
