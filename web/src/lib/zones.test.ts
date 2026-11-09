import { describe, expect, it } from "vite-plus/test";
import { offsetLabel, zoneOffset, zoneOptions, zoneTitle } from "./zones";

describe("time zones", () => {
  it("knows India and daylight saving", () => {
    expect(zoneOffset("Asia/Kolkata")).toBe(330);
    expect(zoneOffset("America/New_York", new Date("2026-01-15T12:00:00Z"))).toBe(-300);
    expect(zoneOffset("America/New_York", new Date("2026-07-15T12:00:00Z"))).toBe(-240);
    expect(zoneOffset("UTC")).toBe(0);
    expect(zoneOffset("Mars/Olympus")).toBeNull();
  });
  it("labels zones plainly", () => {
    expect(offsetLabel(330)).toBe("UTC+05:30");
    expect(offsetLabel(-240)).toBe("UTC−04:00");
    expect(zoneTitle("America/Argentina/Buenos_Aires")).toBe("Buenos Aires (America)");
  });
  it("lists zones west to east", () => {
    const list = zoneOptions(new Date("2026-01-15T12:00:00Z"));
    const kolkata = list.find((z) => z.zone === "Asia/Kolkata");
    expect(kolkata?.label).toBe("(UTC+05:30) Kolkata (Asia) · India Standard Time");
    expect(list.some((z) => z.zone === "Asia/Calcutta")).toBe(false); // listed once, by its current name
    expect(list.every((z, i) => i === 0 || list[i - 1]!.offset <= z.offset)).toBe(true);
  });
});
