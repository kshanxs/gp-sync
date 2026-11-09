import { describe, expect, it } from "vite-plus/test";
import type { Status } from "./api";
import { liveProcesses, tabLive } from "./live";

function status(extra: Partial<Status> = {}): Status {
  return {
    phase: "idle",
    paused: false,
    done: 0,
    total: 0,
    library: { files: 10, synced_files: 10 },
    jobs: [],
    reclaim: {},
    ...extra,
  } as unknown as Status;
}

const by = (s: Status) => Object.fromEntries(liveProcesses(s).map((l) => [l.key, l]));

describe("live processes", () => {
  it("is idle when nothing runs or waits", () => {
    const l = by(status());
    expect(l.uploads!.running).toBe(false);
    expect(l.uploads!.detail).toBe("All backed up");
    expect(l.freed!.detail).toBe("Nothing queued");
    expect(tabLive(status())).toBeNull();
  });

  it("shows this round's uploads", () => {
    const s = status({
      phase: "uploading",
      done: 3,
      total: 40,
      library: { files: 50, synced_files: 10 },
    } as never);
    const u = by(s).uploads!;
    expect(u.running && u.pct).toBe(7.5);
    expect(u.detail).toBe("3 of 40 this round");
    expect(tabLive(s)).toBeNull(); // uploads have the Sync tab
  });

  it("adds up Free storage across accounts and averages the items moving", () => {
    const s = status({
      jobs: [
        { kind: "storage", account: "a", done: 50, total: 100 },
        { kind: "storage", account: "b", done: 100, total: 100 },
      ],
      reclaim: {
        a: { queued: 4, working: 1, done: 0, freed: 0, failed: 0, paused: false },
        b: { queued: 3, working: 1, done: 0, freed: 0, failed: 0, paused: false },
      },
    });
    const f = by(s).freed!;
    expect(f.running).toBe(true);
    expect(f.pct).toBe(75);
    expect(f.detail).toBe("2 moving · 7 waiting");
    expect(tabLive(s)?.verb).toBe("Freeing");
  });

  it("is paused only when every waiting account is paused", () => {
    const acc = (paused: boolean) => ({ queued: 2, working: 0, done: 0, freed: 0, failed: 0, paused });
    expect(by(status({ reclaim: { a: acc(true), b: acc(false) } })).freed!.paused).toBe(false);
    const all = by(status({ reclaim: { a: acc(true), b: acc(true) } })).freed!;
    expect(all.paused && all.detail).toBe("Paused · 4 waiting");
  });

  it("takes turns on the Overview tab when several run", () => {
    const s = status({
      jobs: [
        { kind: "storage", account: "a" },
        { kind: "copy", account: "a", to: "b" },
      ],
      copying: { queued: 5, checking: 0, paused: false },
    });
    expect(tabLive(s, 0)?.key).toBe("freed");
    expect(tabLive(s, 1)?.key).toBe("copied");
    expect(tabLive(s, 2)?.key).toBe("freed");
    expect(by(s).copied!.pct).toBeNull(); // no sizes known: the ring turns instead
  });
});
