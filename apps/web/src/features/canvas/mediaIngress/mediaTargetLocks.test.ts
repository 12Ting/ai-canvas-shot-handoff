import { describe, expect, it, vi } from "vitest";
import { MediaTargetLocks } from "./mediaTargetLocks";

describe("MediaTargetLocks", () => {
  it("serializes the same target while allowing different targets in parallel", () => {
    const locks = new MediaTargetLocks();
    const first = locks.acquire("one", "node-a");
    const other = locks.acquire("two", "node-b");

    expect(first).not.toBeNull();
    expect(other).not.toBeNull();
    expect(locks.acquire("three", "node-a")).toBeNull();
    expect(locks.getSnapshot().protectedNodeIds).toEqual(["node-a", "node-b"]);
  });

  it("does not let an old lease release the replacement lease", () => {
    const locks = new MediaTargetLocks();
    const oldLease = locks.acquire("old", "node-a");
    oldLease?.release();
    const replacement = locks.acquire("new", "node-a");

    oldLease?.release();

    expect(replacement).not.toBeNull();
    expect(locks.acquire("third", "node-a")).toBeNull();
  });

  it("makes release idempotent", () => {
    const locks = new MediaTargetLocks();
    const listener = vi.fn();
    locks.subscribe(listener);
    const lease = locks.acquire("one", "node-a");

    lease?.release();
    lease?.release();

    expect(listener).toHaveBeenCalledTimes(2);
    expect(locks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("sorts protected ids, caches snapshots, and notifies only for real changes", () => {
    const locks = new MediaTargetLocks();
    const listener = vi.fn();
    locks.subscribe(listener);
    const empty = locks.getSnapshot();
    expect(locks.getSnapshot()).toBe(empty);

    const z = locks.acquire("one", "node-z");
    const zSnapshot = locks.getSnapshot();
    expect(locks.getSnapshot()).toBe(zSnapshot);
    expect(listener).toHaveBeenCalledTimes(1);

    expect(locks.acquire("duplicate", "node-z")).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(locks.getSnapshot()).toBe(zSnapshot);

    const a = locks.acquire("two", "node-a");
    expect(locks.getSnapshot().protectedNodeIds).toEqual(["node-a", "node-z"]);
    expect(listener).toHaveBeenCalledTimes(2);
    expect(locks.getSnapshot()).toBe(locks.getSnapshot());

    a?.release();
    z?.release();
    expect(locks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(listener).toHaveBeenCalledTimes(4);
  });
});
