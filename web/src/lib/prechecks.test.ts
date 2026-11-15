import { describe, expect, it } from "vite-plus/test";
import type { Precheck, Status } from "./api";
import { foldersMap } from "./folder-accounts";
import { precheckLines, prechecksFor } from "./prechecks";

const check = (o: Partial<Precheck>): Precheck => ({
  folder: "Photos",
  account: "two@example.com",
  state: "ready",
  done: 0,
  total: 0,
  matched: 0,
  new: 0,
  new_bytes: 0,
  error: "",
  added: 1,
  finished: 2,
  ...o,
});

const status = (o: Partial<Status>) => ({ net: { up_avg: 0 }, ...o }) as unknown as Status;

describe("folder accounts", () => {
  it("sends every folder, only with connected accounts, main first", () => {
    const s = status({
      accounts: ["me@example.com", "two@example.com"],
      folders: {
        Photos: {
          files: 1,
          bytes: 1,
          synced_files: 0,
          synced_bytes: 0,
          account: "me@example.com",
          accounts: ["me@example.com", "gone@example.com", "two@example.com"],
        },
        Videos: { files: 1, bytes: 1, synced_files: 0, synced_bytes: 0, account: "" },
      },
    });
    expect(foldersMap(s)).toEqual({ Photos: ["me@example.com", "two@example.com"], Videos: [] });
  });
});

describe("check first, then sync", () => {
  it("picks the account's checks, for one folder or all", () => {
    const s = status({
      prechecks: [check({}), check({ folder: "Videos" }), check({ account: "me@example.com" })],
    });
    expect(prechecksFor(s, "two@example.com")).toHaveLength(2);
    expect(prechecksFor(s, "two@example.com", "Videos")).toHaveLength(1);
  });

  it("says what it is doing while it checks, and that nothing is sent yet", () => {
    expect(precheckLines(null, [check({ state: "reading" })])).toContain("Nothing is sent yet");
    const comparing = precheckLines(null, [check({ state: "comparing", done: 3200, total: 12480 })]);
    expect(comparing).toContain("3,200 of 12,480");
  });

  it("offers Start syncing and Not now with what it found", () => {
    const s = status({ net: { up_avg: 1_000_000 } } as Partial<Status>);
    const ready = precheckLines(
      s,
      [check({ matched: 9100, total: 12480, new: 3380, new_bytes: 4_200_000_000 })],
      true,
    );
    expect(ready).toContain("two@example.com already has");
    expect(ready).toContain("9,100");
    expect(ready).toContain("3,380");
    expect(ready).toContain("at today's pace");
    expect(ready).toContain('data-precheck-start="two@example.com|Photos"');
    expect(ready).toContain("Not now");
  });

  it("lets a failed check be tried again or skipped", () => {
    const failed = precheckLines(null, [check({ state: "failed", error: "Google is busy" })]);
    expect(failed).toContain("Google is busy");
    expect(failed).toContain("data-precheck-retry");
    expect(failed).toContain("Sync without checking");
  });

  it("just says so when the account already has everything", () => {
    expect(precheckLines(null, [check({ matched: 5, total: 5, new: 0 })])).toContain(
      "already has every file",
    );
  });
});
