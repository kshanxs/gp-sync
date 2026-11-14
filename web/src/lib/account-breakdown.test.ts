import { describe, expect, it } from "vite-plus/test";
import type { FolderAccount } from "./api";
import { accountBreakdown, accountTotal, timeLeft } from "./account-breakdown";

const acc = (o: Partial<FolderAccount>): FolderAccount => ({
  account: "me@example.com",
  main: true,
  files: 0,
  done: 0,
  uploaded: 0,
  exists: 0,
  waiting: 0,
  failed: 0,
  ...o,
});

describe("account breakdown", () => {
  it("adds the four parts up to what the account should have", () => {
    expect(accountTotal(acc({ uploaded: 8, exists: 3, waiting: 2, failed: 1 }))).toBe(14);
  });

  it("works out time left from the last hour's pace", () => {
    expect(timeLeft(acc({ waiting: 0 }))).toBe("All in");
    expect(timeLeft(acc({ waiting: 0, failed: 2 }))).toBe(""); // failed ones are not "all in"
    expect(timeLeft(acc({ waiting: 30 }))).toBe("Not sending now");
    expect(timeLeft(acc({ waiting: 30, per_hour: 60 }))).toBe("About 30 min left");
    expect(timeLeft(acc({ waiting: 600, per_hour: 120 }))).toBe("About 5 h left");
  });

  it("draws a row per account, the parts as buttons when they can be picked", () => {
    const rows = accountBreakdown(
      [
        acc({ uploaded: 5, exists: 2, waiting: 1, files: 8, done: 7 }),
        acc({ account: "two@example.com", main: false, waiting: 8, files: 8 }),
      ],
      { pick: { account: "two@example.com", part: "waiting" } },
    );
    expect(rows.match(/class="acc-row"/g)).toHaveLength(2);
    expect(rows).toContain(">Backup<");
    expect(rows).toContain(">Also in<");
    expect(rows).toContain('data-seg="two@example.com|waiting" aria-pressed="true"');
    expect(rows).not.toContain("two@example.com|uploaded"); // nothing there: not a button
    expect(rows).not.toContain("Failed"); // no failures: no Failed part
  });

  it("leaves out the address when the card already names the account", () => {
    const row = accountBreakdown([acc({ waiting: 3, files: 3 })], { bare: true, eta: true });
    expect(row).not.toContain("me@example.com</span>");
    expect(row).toContain("Not sending now");
  });
});
