import { describe, expect, it } from "vite-plus/test";
import { choices, matchBadge, missingNote, offset, wrongDate } from "./snapchat";

describe("snapchat helpers", () => {
  it("says which memories aren't in the zips", () => {
    expect(missingNote({ missing: 0 })).toBe("");
    expect(missingNote({ missing: 1, missing_months: { "2024-05": 1 } })).toBe(
      "1 memory isn't in these zips (May 2024)",
    );
    expect(
      missingNote({
        missing: 1162,
        missing_months: {
          "2023-06": 6,
          "2024-04": 126,
          "2024-05": 133,
          "2024-06": 61,
          "2024-07": 121,
          "2024-08": 126,
          "2024-09": 110,
          "2024-10": 136,
          "2024-11": 175,
          "2024-12": 50,
          "2025-01": 69,
          "2025-02": 39,
          "2025-08": 11,
          "2026-08": 10,
        },
      }),
    ).toBe("1,162 memories aren't in these zips: 1,146 from Apr 2024 to Feb 2025, and 16 in other months");
    expect(missingNote({ missing: 30, missing_months: { "2024-04": 10, "2024-05": 20 } })).toBe(
      "30 memories aren't in these zips (Apr 2024 to May 2024)",
    );
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

  it("says how far Google's date is from Snapchat's", () => {
    expect(offset(240)).toBe("+4 min");
    expect(offset(4 * 86400)).toBe("+4 days");
    expect(offset(86400)).toBe("+1 day");
    expect(offset(-7200)).toBe("−2 h");
    expect(offset(-2 * 365 * 86400)).toBe("−2 yr");
  });
});
