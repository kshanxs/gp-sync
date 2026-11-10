import { describe, expect, it } from "vite-plus/test";
import { choices, matchBadge, missingNote, wrongDate } from "./snapchat";

describe("snapchat helpers", () => {
  it("says which memories aren't in the zips", () => {
    expect(missingNote({ missing: 0 })).toBe("");
    expect(missingNote({ missing: 1, missing_months: { "2024-05": 1 } })).toBe(
      "1 memory isn't in these zips (May 2024)",
    );
    expect(
      missingNote({ missing: 1162, missing_months: { "2025-02": 39, "2024-04": 126, "2024-11": 175 } }),
    ).toBe("1,162 memories aren't in these zips (Apr 2024 to Feb 2025)");
  });

  it("offers album and replace only when Google has a copy, upload only when it has none", () => {
    expect(choices({ copies: [] })).toEqual(["upload", "skip"]);
    const copy = {
      key: "K",
      taken: 1,
      how: "same_moment" as const,
      distance: null,
      camera: false,
      role: "keep" as const,
      state: "" as const,
      url: "",
    };
    expect(choices({ copies: [copy] })).toEqual(["album", "replace", "skip"]);
  });

  it("labels matches and wrong dates", () => {
    expect(matchBadge({ match: "looks_same", distance: 12 })[2]).toContain("12 of 256");
    expect(matchBadge({ match: "none", distance: null })[0]).toBe("Not in Google");
    expect(wrongDate({ taken: 1000, google: { key: "K", taken: 1100, url: "" } })).toBe(false);
    expect(wrongDate({ taken: 1000, google: { key: "K", taken: 90000, url: "" } })).toBe(true);
    expect(wrongDate({ taken: 1000, google: null })).toBe(false);
  });
});
