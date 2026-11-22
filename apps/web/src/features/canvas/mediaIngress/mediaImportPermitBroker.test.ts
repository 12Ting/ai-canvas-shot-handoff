import { afterEach, describe, expect, it, vi } from "vitest";
import { MediaImportPermitBroker, type MediaImportPermit } from "./mediaImportPermitBroker";

const signal = () => new AbortController().signal;

describe("MediaImportPermitBroker", () => {
  let broker: MediaImportPermitBroker;
  const permits: MediaImportPermit[] = [];

  afterEach(() => {
    for (const permit of permits.splice(0)) permit.release();
    broker?.dispose();
  });

  it("grants three immediately and the fourth in FIFO order", async () => {
    broker = new MediaImportPermitBroker();
    permits.push(await broker.acquire("one", signal()));
    permits.push(await broker.acquire("two", signal()));
    permits.push(await broker.acquire("three", signal()));
    const order: string[] = [];
    const fourth = broker.acquire("four", signal()).then((permit) => {
      order.push(permit.operationId);
      permits.push(permit);
      return permit;
    });
    const fifth = broker.acquire("five", signal()).then((permit) => {
      order.push(permit.operationId);
      permits.push(permit);
      return permit;
    });

    expect(broker.getSnapshot()).toEqual({
      runningOperationIds: ["one", "two", "three"],
      queuedOperationIds: ["four", "five"],
      queueHeld: false,
    });
    permits[1].release();
    await fourth;
    expect(order).toEqual(["four"]);
    permits[0].release();
    await fifth;
    expect(order).toEqual(["four", "five"]);
  });

  it("removes an aborted fourth waiter without consuming a permit", async () => {
    broker = new MediaImportPermitBroker();
    permits.push(await broker.acquire("one", signal()));
    permits.push(await broker.acquire("two", signal()));
    permits.push(await broker.acquire("three", signal()));
    const controller = new AbortController();
    const fourth = broker.acquire("four", controller.signal);

    controller.abort();

    await expect(fourth).rejects.toThrow("Aborted");
    expect(broker.getSnapshot().queuedOperationIds).toEqual([]);
    expect(broker.getSnapshot().runningOperationIds).toEqual(["one", "two", "three"]);
  });

  it("does not release a running permit when only its signal aborts", async () => {
    broker = new MediaImportPermitBroker(1);
    const controller = new AbortController();
    const running = await broker.acquire("one", controller.signal);
    permits.push(running);
    const queued = broker.acquire("two", signal());

    controller.abort();
    await Promise.resolve();

    expect(broker.getSnapshot().runningOperationIds).toEqual(["one"]);
    expect(broker.getSnapshot().queuedOperationIds).toEqual(["two"]);
    running.release();
    permits.push(await queued);
  });

  it("makes permit release idempotent", async () => {
    broker = new MediaImportPermitBroker(1);
    const running = await broker.acquire("one", signal());
    running.release();
    const next = await broker.acquire("two", signal());

    running.release();

    expect(broker.getSnapshot().runningOperationIds).toEqual(["two"]);
    permits.push(next);
  });

  it("rejects duplicate operation ids without polluting running or queued state", async () => {
    broker = new MediaImportPermitBroker(1);
    const first = await broker.acquire("same", signal());
    permits.push(first);

    await expect(broker.acquire("same", signal())).rejects.toThrow("already");
    expect(broker.getSnapshot()).toEqual({
      runningOperationIds: ["same"],
      queuedOperationIds: [],
      queueHeld: false,
    });

    const queued = broker.acquire("queued", signal());
    await expect(broker.acquire("queued", signal())).rejects.toThrow("already");
    expect(broker.getSnapshot().queuedOperationIds).toEqual(["queued"]);
    first.release();
    permits.push(await queued);
  });

  it("uses Set semantics for multiple queue holds and resumes only after every hold releases", async () => {
    broker = new MediaImportPermitBroker(1);
    broker.holdQueue("provider-a");
    broker.holdQueue("provider-a");
    broker.holdQueue("provider-b");
    const waiting = broker.acquire("one", signal());

    broker.releaseQueueHold("provider-a");
    await Promise.resolve();
    expect(broker.getSnapshot()).toEqual({
      runningOperationIds: [],
      queuedOperationIds: ["one"],
      queueHeld: true,
    });
    broker.releaseQueueHold("provider-b");
    permits.push(await waiting);
    expect(broker.getSnapshot().queueHeld).toBe(false);
  });

  it("rejects every queued waiter on dispose and never grants again", async () => {
    broker = new MediaImportPermitBroker(1);
    permits.push(await broker.acquire("running", signal()));
    const second = broker.acquire("second", signal());
    const third = broker.acquire("third", signal());

    broker.dispose();

    await expect(second).rejects.toThrow("disposed");
    await expect(third).rejects.toThrow("disposed");
    await expect(broker.acquire("later", signal())).rejects.toThrow("disposed");
  });

  it("cannot resurrect queue holds or grants after dispose", async () => {
    broker = new MediaImportPermitBroker(1);
    const running = await broker.acquire("running", signal());
    permits.push(running);
    const queued = broker.acquire("queued", signal());

    broker.dispose();
    broker.holdQueue("late-hold");
    broker.releaseQueueHold("late-hold");
    running.release();

    await expect(queued).rejects.toThrow("disposed");
    await expect(broker.acquire("late", signal())).rejects.toThrow(
      "disposed",
    );
    expect(broker.getSnapshot()).toEqual({
      runningOperationIds: [],
      queuedOperationIds: [],
      queueHeld: false,
    });
  });

  it("keeps snapshot references stable and notifies only for real changes", async () => {
    broker = new MediaImportPermitBroker(1);
    const listener = vi.fn();
    broker.subscribe(listener);
    const empty = broker.getSnapshot();
    expect(broker.getSnapshot()).toBe(empty);

    broker.holdQueue("hold");
    const held = broker.getSnapshot();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(held).not.toBe(empty);
    expect(broker.getSnapshot()).toBe(held);

    broker.holdQueue("hold");
    broker.releaseQueueHold("missing");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(broker.getSnapshot()).toBe(held);

    const waiting = broker.acquire("one", signal());
    const queued = broker.getSnapshot();
    expect(listener).toHaveBeenCalledTimes(2);
    expect(broker.getSnapshot()).toBe(queued);

    broker.releaseQueueHold("hold");
    permits.push(await waiting);
    expect(listener).toHaveBeenCalledTimes(3);
    expect(broker.getSnapshot()).toBe(broker.getSnapshot());
  });
});
