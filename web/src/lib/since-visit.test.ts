import { describe, expect, it } from "vite-plus/test";
import { diff } from "./since-visit";

describe("since your last visit", () => {
  const base = { at: 1000, files: 100, synced: 90, failed: 1 };

  it("counts what was added, backed up and failed since", () => {
    expect(diff(base, { at: 5000, files: 142, synced: 130, failed: 3 })).toEqual({
      at: 1000,
      added: 42,
      backedUp: 40,
      failed: 2,
    });
  });

  it("says nothing when nothing changed, and never counts down", () => {
    expect(diff(base, { ...base, at: 9000 })).toBeNull();
    // Files deleted and failures retried: not "−5 added".
    expect(diff(base, { at: 9000, files: 95, synced: 90, failed: 0 })).toBeNull();
  });
});
