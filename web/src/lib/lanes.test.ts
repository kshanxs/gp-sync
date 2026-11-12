import { describe, expect, it } from "vite-plus/test";
import type { Current, Status } from "./api";
import { accountLanes, copiesText, folderOrder, lanePhaseText, uploadSummary, uploadTitle } from "./lanes";
import { phaseInfo } from "./ui";

const up = (name: string, account: string): Current => ({
  name,
  account,
  bytes_done: 1,
  bytes_total: 2,
  step: "uploading",
  kind: "photo",
});

function status(v: Partial<Status>): Status {
  return {
    phase: "idle",
    accounts: ["me@x.com", "two@x.com"],
    paused: false,
    login_failed: [],
    schedule: { enabled: false, start: 0, end: 0, allowed: true, next_window: 0 },
    library: { files: 10, bytes: 0, synced_files: 10, synced_bytes: 0 },
    folders: {
      Photos: {
        files: 10,
        bytes: 0,
        synced_files: 10,
        synced_bytes: 0,
        account: "me@x.com",
        accounts: ["me@x.com", "two@x.com"],
        copies: { "two@x.com": 4 },
      },
    },
    done: 0,
    total: 0,
    ...v,
  } as Status;
}

describe("folder accounts", () => {
  it("adds and removes accounts without moving the main one", () => {
    expect(folderOrder(["a"], "b", "toggle")).toEqual(["a", "b"]);
    expect(folderOrder(["a", "b"], "a", "toggle")).toEqual(["b"]);
  });

  it("Make main moves an account to the front, whatever order it was picked in", () => {
    expect(folderOrder(["a", "b", "c"], "c", "main")).toEqual(["c", "a", "b"]);
    expect(folderOrder(["a"], "z", "main")).toEqual(["a"]); // not picked: nothing changes
  });
});

describe("account lanes", () => {
  it("gives each account its own lane, files and progress", () => {
    const s = status({
      phase: "uploading",
      uploading: [
        up("Photos/a.jpg", "me@x.com"),
        up("Photos/a.jpg", "two@x.com"),
        up("Photos/b.jpg", "two@x.com"),
      ],
      lanes: {
        "me@x.com": { phase: "uploading", done: 1, total: 3, copies: 0, copies_done: 0 },
        "two@x.com": { phase: "uploading", done: 0, total: 6, copies: 6, copies_done: 0 },
      },
    });
    const [me, two] = accountLanes(s);
    expect(me).toMatchObject({
      account: "me@x.com",
      phase: "uploading",
      backup: ["Photos"],
      done: 10,
      total: 10,
    });
    expect(two).toMatchObject({ account: "two@x.com", also: ["Photos"], done: 4, total: 10, roundTotal: 6 });
    expect(two!.uploading).toHaveLength(2);
    expect(lanePhaseText(two!, s)).toBe("Uploading · 2 files at once");
    expect(uploadSummary(s)).toEqual({ accounts: 2, files: 3 });
    expect(uploadTitle(s)).toBe("Uploading · 2 accounts · 3 files");
  });

  it("no file n of m while several go at once: the badge says what the strip says", () => {
    const s = status({
      phase: "uploading",
      done: 4,
      total: 9,
      uploading: [up("Photos/a.jpg", "me@x.com"), up("Photos/b.jpg", "me@x.com")],
    });
    expect(phaseInfo(s).label).toBe("Uploading · 2 at once");
    expect(phaseInfo(s).detail).toBe("4 of 9 done this round");
  });

  it("an account with copies left is not caught up, and nobody says everything is backed up", () => {
    const s = status({ copies_pending: 6, copies_pending_by: { "two@x.com": 6 } });
    const lanes = accountLanes(s);
    expect(lanes.map((l) => l.phase)).toEqual(["caught_up", "behind"]);
    expect(copiesText(s)).toBe("two (6)");
    expect(phaseInfo(s).label).toBe("Main library in");
    expect(phaseInfo(s).tone).toBe("info");
    expect(phaseInfo(s).detail).toContain("two (6)");
  });
});
