import { describe, expect, it } from "vite-plus/test";
import type { ReclaimSummary } from "./api";
import { accountLine, tile } from "./ui";

describe("metric tiles", () => {
  it("keeps the shared styling and live-update hooks", () => {
    const markup = tile({ id: "metric", label: "Upload", value: "–", ic: "up", chart: "" });
    expect(markup).toContain('class="card metric-card lift');
    expect(markup).toContain('id="metric"');
    expect(markup).toContain("data-v");
    expect(markup).toContain("data-s");
    expect(markup).toContain("data-c");
  });

  it("escapes the label and omits the chart container when not requested", () => {
    const markup = tile({ label: "<Files>", value: "0", ic: "photo" });
    expect(markup).toContain("&lt;Files&gt;");
    expect(markup).not.toContain("data-c");
  });
});

describe("Free storage account line", () => {
  const acc = (v: Partial<ReclaimSummary>): ReclaimSummary => ({
    queued: 0,
    working: 0,
    done: 0,
    freed: 0,
    failed: 0,
    paused: false,
    ...v,
  });
  it("says what is left to free, in the right colour", () => {
    expect(accountLine(acc({ counted: 0 }))).toMatchObject({ line: "Nothing to free", tone: "text-good" });
    expect(accountLine(acc({ counted: 2 * 1024 ** 3 })).line).toMatch(/to free$/);
    expect(accountLine(acc({ counted: null })).line).toBe("Library not read yet");
    expect(accountLine(acc({ queued: 7, working: 3, counted: 9 }))).toMatchObject({
      line: "Freeing · 3 at once · 7 left",
      tone: "text-run",
      live: true,
    });
    expect(accountLine(acc({ queued: 7, paused: true }))).toMatchObject({
      line: "Paused · 7 waiting",
      tone: "text-warn",
    });
  });
  it("adds what was freed, and never says 'Nothing freed yet'", () => {
    const v = accountLine(acc({ done: 4, freed: 392 * 1024 ** 2, counted: 0 }));
    expect(v.freed).toMatch(/freed$/);
    expect(v.line).toBe("Nothing to free");
    expect(accountLine(undefined).line).not.toMatch(/Nothing freed/);
  });
});
