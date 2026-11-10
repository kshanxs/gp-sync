import { describe, expect, it } from "vite-plus/test";
import { atOnceLabel, atOnceOptions, atOnceText } from "./pace";

describe("files at once", () => {
  it("offers dynamic first and fixed numbers up to 8", () => {
    expect(atOnceLabel(0)).toBe("Dynamic (recommended)");
    const html = atOnceOptions(0);
    expect(html).toContain('value="0" selected');
    expect(html).toContain('value="8"');
    expect(html).not.toContain('value="9"');
  });

  it("says how many run now", () => {
    expect(atOnceText(3)).toBe("up to 3");
    expect(atOnceText(0, { dynamic: true, now: 9, running: 9, calm: 0, slowdowns: 0 })).toBe(
      "up to 9 (dynamic)",
    );
    expect(atOnceText(0, { dynamic: true, now: 2, running: 1, calm: 40, slowdowns: 1 })).toBe(
      "up to 2 (dynamic, slowed down by Google)",
    );
    expect(atOnceText(0)).toBe("up to 4 (dynamic)");
  });
});
