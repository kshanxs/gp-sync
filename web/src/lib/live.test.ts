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

  it("adds up Free storage across accounts, with progress through the queue", () => {
    const s = status({
      jobs: [
        { kind: "storage", account: "a", done: 50, total: 100 },
        { kind: "storage", account: "b", done: 100, total: 100 },
      ],
      reclaim: {
        a: { queued: 4, working: 1, done: 20, freed: 0, failed: 0, paused: false },
        b: { queued: 3, working: 1, done: 1, freed: 0, failed: 0, paused: false },
      },
    });
    const f = by(s).freed!;
    expect(f.running).toBe(true);
    expect(f.pct).toBe(75); // 21 done of 28
    expect(f.detail).toBe("2 moving · 7 waiting");
    expect(tabLive(s)?.verb).toBe("Freeing");
  });

  it("shows Snapchat memories being sent, and the phone tab says so", () => {
    const s = status({
      jobs: [{ kind: "snapchat", account: "a", step: "uploading", done: 1, total: 2 }],
      snapchat: { a: { queued: 25, done: 75, failed: 0, total: 100, paused: false } },
    });
    const c = by(s).snapchat!;
    expect(c.running && c.detail).toBe("1 moving · 25 waiting");
    expect(c.pct).toBe(75);
    expect(c.href).toBe("#/snapchat");
    expect(tabLive(s)?.verb).toBe("Sending");
    const idle = by(
      status({ snapchat: { a: { queued: 0, done: 100, failed: 0, total: 100, paused: false } } }),
    );
    expect(idle.snapchat!.running).toBe(false);
    expect(idle.snapchat!.detail).toBe("100 memories in Google");
    const paused = by(status({ snapchat: { a: { queued: 5, done: 0, failed: 0, total: 5, paused: true } } }));
    expect(paused.snapchat!.paused && paused.snapchat!.detail).toBe("Paused · 5 waiting");
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

  it("shows every job as paused when everything is paused", () => {
    // Pause everything sets each job's own pause.
    const s = status({
      paused: true,
      all_paused: true,
      jobs: [{ kind: "storage", account: "a", done: 1, total: 2 }],
      reclaim: { a: { queued: 5, working: 1, done: 0, freed: 0, failed: 0, paused: true } },
      copying: { queued: 2, checking: 0, paused: true },
    } as never);
    const l = by(s);
    expect(l.freed!.paused).toBe(true);
    expect(l.freed!.detail).toBe("Paused · 5 waiting");
    expect(l.copied!.paused).toBe(true);
  });
});
