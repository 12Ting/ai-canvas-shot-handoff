import { startTransition, StrictMode, Suspense, useContext } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ArtifactRevision } from "../../../api/client";
import { useCanvasStore } from "../canvasStore";
import type { CanvasNode, NodeKind } from "../canvasTypes";
import {
  createMediaImportRuntimeDependencies,
  useMediaImport,
  type MediaImportControls,
} from "../useMediaImport";
import type { MediaImportAttempt, MediaImportCore } from "./mediaImportCore";
import { MediaIngressContext, type MediaIngressRuntimePort } from "./mediaIngressContext";
import { MediaIngressProvider } from "./MediaIngressProvider";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
let currentRuntime: MediaIngressRuntimePort | null = null;
let nodeControls: MediaImportControls | null = null;

function Probe() {
  currentRuntime = useContext(MediaIngressContext);
  return null;
}

function NodeProbe() {
  nodeControls = useMediaImport("media-node");
  return null;
}

const NEVER_SETTLES = new Promise<void>(() => undefined);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}

type TrackedAttempt = MediaImportAttempt & {
  readonly filename: string;
  signal: AbortSignal | null;
  readonly releaseFile: ReturnType<typeof vi.fn>;
};

function MaybeSuspend({ suspend }: Readonly<{ suspend: boolean }>) {
  if (suspend) throw NEVER_SETTLES;
  return null;
}

function load(epochProject = PROJECT_ID): number {
  useCanvasStore.getState().load({
    projectId: epochProject,
    nodes: [],
    edges: [],
    shots: [],
    revisionId: "revision-1",
  });
  return useCanvasStore.getState().loadEpoch;
}

async function renderForceRefreshScenario(
  confirmForceRefresh: () => boolean,
  onForceRefreshApproved: () => void,
) {
  useCanvasStore.getState().load({
    projectId: PROJECT_ID,
    nodes: [{
      id: "media-node",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    }],
    edges: [],
    shots: [],
    revisionId: "revision-1",
  });
  const epoch = useCanvasStore.getState().loadEpoch;
  const base = createMediaImportRuntimeDependencies();
  const events: string[] = [];
  const attempts: TrackedAttempt[] = [];
  const core: MediaImportCore = {
    discardImport: async () => ({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "not_applicable",
    }),
    createAttempt: (input) => {
      const tracked: TrackedAttempt = {
        operationId: input.operationId,
        declaration: input.declaration,
        filename: input.declaration.filename,
        signal: null,
        getImportId: () =>
          input.declaration.filename.startsWith("batch-")
            ? `import-${input.operationId}`
            : null,
        wasCreateDispatched: () => true,
        wasContentPutDispatched: () =>
          input.declaration.filename.startsWith("batch-"),
        recoverImportId: async () => {
          const importId = tracked.getImportId();
          if (!importId) throw new Error("import id unavailable");
          return importId;
        },
        execute: ({ signal, onPhase }) => {
          tracked.signal = signal;
          onPhase("initializing");
          return new Promise(() => undefined);
        },
        reconcileCancellation: async () => ({
          kind: "unconfirmed",
          reason: "observer_failed",
          errorMessage: "offline",
        }),
        releaseFile: vi.fn(() => events.push(`release:${tracked.filename}`)),
      };
      attempts.push(tracked);
      return tracked;
    },
  };
  const shared = { ...base, core };
  const originalDispose = shared.permits.dispose.bind(shared.permits);
  vi.spyOn(shared.permits, "dispose").mockImplementation(() => {
    events.push("broker.dispose");
    originalDispose();
  });
  const reports: number[] = [];
  render(
    <MediaIngressProvider
      confirmForceRefresh={confirmForceRefresh}
      dependencies={shared}
      generation={`p:${epoch}`}
      loadEpoch={epoch}
      onForceRefreshApproved={() => {
        events.push("approved");
        onForceRefreshApproved();
      }}
      onPendingCountChange={(_generation, count) => reports.push(count)}
      projectId={PROJECT_ID}
    >
      <Probe />
      <NodeProbe />
    </MediaIngressProvider>,
  );
  act(() =>
    nodeControls?.selectFile(
      new File(["node"], "node.png", { type: "image/png" }),
    ),
  );
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  const controller = currentRuntime?.controller;
  if (!controller) throw new Error("controller unavailable");
  let batchId = "";
  act(() => {
    const admission = controller.enqueue({
      projectId: PROJECT_ID,
      loadEpoch: epoch,
      source: "multi_picker",
      candidates: Array.from({ length: 3 }, (_, index) => ({
        kind: "file" as const,
        file: new File([`${index}`], `batch-${index}.png`, {
          type: "image/png",
        }),
        displayName: `batch-${index}.png`,
      })),
      anchor: { x: 0, y: 0 },
      targetMode: "create",
      targetBinding: null,
    });
    expect(admission.kind).toBe("accepted");
    if (admission.kind === "accepted") batchId = admission.batchId;
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  const firstItem = controller.getSnapshot().batches
    .find((batch) => batch.batchId === batchId)!
    .slots[0].currentItem;
  act(() => controller.cancelItem(batchId, firstItem.itemId));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(
    controller.getSnapshot().batches
      .find((batch) => batch.batchId === batchId)!
      .slots[0].currentItem,
  ).toMatchObject({
    state: "CANCELLING",
    canRetryCancellation: true,
    canForceRefresh: true,
  });
  return { attempts, batchId, controller, events, reports, shared };
}

function obstacleNode(
  kind: NodeKind,
  measured: Readonly<{ width: number; height: number }>,
): CanvasNode {
  const data =
    kind === "media_input"
      ? { kind, artifactRevisionId: null, mediaKind: null }
      : kind === "shot"
        ? { kind, shotId: "shot-1" }
        : { kind, text: "" };
  return {
    id: `obstacle-${kind}`,
    type: kind,
    position: { x: 0, y: 0 },
    measured,
    data,
  } as CanvasNode;
}

function reservationPosition(
  kind: NodeKind,
  measured: Readonly<{ width: number; height: number }>,
  anchor: Readonly<{ x: number; y: number }>,
) {
  useCanvasStore.getState().load({
    projectId: PROJECT_ID,
    nodes: [obstacleNode(kind, measured)],
    edges: [],
    shots: [],
    revisionId: "revision-1",
  });
  const epoch = useCanvasStore.getState().loadEpoch;
  const rendered = render(
    <MediaIngressProvider
      generation={`p:${epoch}`}
      loadEpoch={epoch}
      projectId={PROJECT_ID}
    >
      <Probe />
    </MediaIngressProvider>,
  );
  const controller = currentRuntime?.controller;
  if (!controller) throw new Error("controller unavailable");
  act(() => {
    const admission = controller.enqueue({
      projectId: PROJECT_ID,
      loadEpoch: epoch,
      source: "multi_picker",
      candidates: [{
        kind: "entry_error",
        displayName: "local error",
        code: "file_unavailable",
        message: "local error",
      }],
      anchor,
      targetMode: "create",
      targetBinding: null,
    });
    expect(admission.kind).toBe("accepted");
  });
  const position =
    controller.getSnapshot().batches[0].slots[0].reservation.position;
  rendered.unmount();
  currentRuntime = null;
  return position;
}

describe("MediaIngressProvider", () => {
  afterEach(() => {
    cleanup();
    currentRuntime = null;
    nodeControls = null;
    vi.useRealTimers();
  });

  it.each([
    "media_input",
    "shot",
    "text_source",
    "prompt",
    "note",
  ] as const)("prefers finite positive measured dimensions for %s nodes", (kind) => {
    expect(
      reservationPosition(kind, { width: 123, height: 77 }, { x: 143, y: 0 }),
    ).toEqual({ x: 143, y: 0 });
    expect(
      reservationPosition(kind, { width: 123, height: 77 }, { x: 0, y: 97 }),
    ).toEqual({ x: 0, y: 97 });
  });

  it.each([
    ["media_input", 312, 360],
    ["shot", 280, 180],
    ["text_source", 248, 180],
    ["prompt", 248, 180],
    ["note", 248, 180],
  ] as const)(
    "falls back by %s node kind for zero, negative, NaN, and infinite measurements",
    (kind, fallbackWidth, fallbackHeight) => {
      for (const invalid of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
        expect(
          reservationPosition(
            kind,
            { width: invalid, height: invalid },
            { x: 20, y: 20 },
          ),
        ).not.toEqual({ x: 20, y: 20 });
        expect(
          reservationPosition(
            kind,
            { width: invalid, height: invalid },
            { x: fallbackWidth + 20, y: 0 },
          ),
        ).toEqual({ x: fallbackWidth + 20, y: 0 });
        expect(
          reservationPosition(
            kind,
            { width: invalid, height: invalid },
            { x: 0, y: fallbackHeight + 20 },
          ),
        ).toEqual({ x: 0, y: fallbackHeight + 20 });
      }
    },
  );

  it("deduplicates controller operations while counting a B1 node queued on the shared broker", async () => {
    useCanvasStore.getState().load({
      projectId: PROJECT_ID,
      nodes: [{
        id: "media-node",
        type: "media_input",
        position: { x: 0, y: 0 },
        data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
      }],
      edges: [],
      shots: [],
      revisionId: "revision-1",
    });
    const epoch = useCanvasStore.getState().loadEpoch;
    const shared = createMediaImportRuntimeDependencies();
    const pendingCore: MediaImportCore = {
      discardImport: async () => ({
        httpStatus: 200,
        outcome: "discarded",
        contentCleanup: "not_applicable",
      }),
      createAttempt: (input) => ({
        operationId: input.operationId,
        declaration: input.declaration,
        getImportId: () => null,
        wasCreateDispatched: () => false,
        wasContentPutDispatched: () => false,
        recoverImportId: () => Promise.reject(new Error("not used")),
        execute: ({ onPhase }) => {
          onPhase("initializing");
          return new Promise(() => undefined);
        },
        reconcileCancellation: () => Promise.reject(new Error("not used")),
        releaseFile: () => undefined,
      } satisfies MediaImportAttempt),
    };
    const reports: number[] = [];
    render(
      <MediaIngressProvider
        dependencies={{ ...shared, core: pendingCore }}
        generation={`p:${epoch}`}
        loadEpoch={epoch}
        onPendingCountChange={(_generation, count) => reports.push(count)}
        projectId={PROJECT_ID}
      >
        <Probe />
        <NodeProbe />
      </MediaIngressProvider>,
    );

    expect(currentRuntime?.controller?.getSnapshot().batches).toEqual([]);
    act(() =>
      nodeControls?.selectFile(
        new File(["node"], "node.png", { type: "image/png" }),
      ),
    );
    await act(async () => Promise.resolve());
    expect(currentRuntime?.controller?.getSnapshot().batches).toEqual([]);

    act(() => {
      currentRuntime?.controller?.enqueue({
        projectId: PROJECT_ID,
        loadEpoch: epoch,
        source: "multi_picker",
        candidates: ["a", "b", "c"].map((name) => ({
          kind: "file" as const,
          file: new File([name], `${name}.png`, { type: "image/png" }),
          displayName: `${name}.png`,
        })),
        anchor: { x: 0, y: 0 },
        targetMode: "create",
        targetBinding: null,
      });
    });
    await act(async () => Promise.resolve());

    expect(shared.permits.getSnapshot().queuedOperationIds).toHaveLength(1);
    expect(reports.at(-1)).toBe(4);
  });

  it("keeps a cleanup-pending discard in the provider aggregate navigation guard", async () => {
    const epoch = load();
    const shared = createMediaImportRuntimeDependencies();
    let rejectImport!: (reason: unknown) => void;
    let finishDiscard!: (result: {
      httpStatus: 202;
      outcome: "discarded";
      contentCleanup: "pending";
    }) => void;
    const discardImport = vi.fn(
      () =>
        new Promise<{
          httpStatus: 202;
          outcome: "discarded";
          contentCleanup: "pending";
        }>((resolve) => {
          finishDiscard = resolve;
        }),
    );
    const core: MediaImportCore = {
      discardImport,
      createAttempt: (input) => ({
        operationId: input.operationId,
        declaration: input.declaration,
        getImportId: () => "99999999-9999-4999-8999-999999999999",
        wasCreateDispatched: () => true,
        wasContentPutDispatched: () => true,
        recoverImportId: async () =>
          "99999999-9999-4999-8999-999999999999",
        execute: ({ onPhase }) => {
          onPhase("initializing");
          return new Promise((_resolve, reject) => {
            rejectImport = reject;
          });
        },
        reconcileCancellation: () => Promise.reject(new Error("not used")),
        releaseFile: () => undefined,
      }),
    };
    const reports: number[] = [];
    render(
      <MediaIngressProvider
        dependencies={{ ...shared, core }}
        generation={`p:${epoch}`}
        loadEpoch={epoch}
        onPendingCountChange={(_generation, count) => reports.push(count)}
        projectId={PROJECT_ID}
      >
        <Probe />
      </MediaIngressProvider>,
    );
    const controller = currentRuntime?.controller;
    if (!controller) throw new Error("controller unavailable");
    let batchId = "";
    act(() => {
      const admission = controller.enqueue({
        projectId: PROJECT_ID,
        loadEpoch: epoch,
        source: "multi_picker",
        candidates: [{
          kind: "file",
          file: new File(["x"], "cleanup.png", { type: "image/png" }),
          displayName: "cleanup.png",
        }],
        anchor: { x: 0, y: 0 },
        targetMode: "create",
        targetBinding: null,
      });
      if (admission.kind === "accepted") batchId = admission.batchId;
    });
    await act(async () => {
      await Promise.resolve();
      rejectImport(new Error("import failed"));
      await Promise.resolve();
    });
    const slot = controller.getSnapshot().batches[0].slots[0];

    await act(async () => {
      controller.ignoreSlot(batchId, slot.slotId);
      await Promise.resolve();
    });
    expect(reports.at(-1)).toBe(1);
    expect(
      controller.getSnapshot().guardState.pendingOperationCount,
    ).toBe(1);

    await act(async () => {
      finishDiscard({
        httpStatus: 202,
        outcome: "discarded",
        contentCleanup: "pending",
      });
      await Promise.resolve();
    });

    expect(reports.at(-1)).toBe(1);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem,
    ).toMatchObject({
      state: "DISCARDING",
      canRetryDiscard: true,
    });
  });

  it("reports SavedUnplaced separately from operation count and only for guard changes", async () => {
    const epoch = load();
    const completed = deferred<ArtifactRevision>();
    const shared = createMediaImportRuntimeDependencies();
    const core: MediaImportCore = {
      discardImport: async () => ({
        httpStatus: 200,
        outcome: "discarded",
        contentCleanup: "not_applicable",
      }),
      createAttempt: (input) => ({
        operationId: input.operationId,
        declaration: input.declaration,
        getImportId: () => "99999999-9999-4999-8999-999999999999",
        wasCreateDispatched: () => true,
        wasContentPutDispatched: () => true,
        recoverImportId: async () =>
          "99999999-9999-4999-8999-999999999999",
        execute: ({ onPhase }) => {
          onPhase("initializing");
          return completed.promise;
        },
        reconcileCancellation: () => Promise.reject(new Error("not used")),
        releaseFile: () => undefined,
      }),
    };
    const guards: Array<{
      generation: string;
      pendingOperationCount: number;
      hasSavedUnplaced: boolean;
    }> = [];
    render(
      <MediaIngressProvider
        dependencies={{ ...shared, core }}
        generation={`p:${epoch}`}
        loadEpoch={epoch}
        onGuardStateChange={(generation, state) =>
          guards.push({ generation, ...state })
        }
        projectId={PROJECT_ID}
      >
        <Probe />
      </MediaIngressProvider>,
    );
    guards.length = 0;
    const controller = currentRuntime?.controller;
    if (!controller) throw new Error("controller unavailable");

    act(() => {
      const admission = controller.enqueue({
        projectId: PROJECT_ID,
        loadEpoch: epoch,
        source: "multi_picker",
        candidates: [{
          kind: "file",
          file: new File(["x"], "saved.png", { type: "image/png" }),
          displayName: "saved.png",
        }],
        anchor: { x: 0, y: 0 },
        targetMode: "create",
        targetBinding: null,
      });
      expect(admission.kind).toBe("accepted");
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    load();
    await act(async () => {
      completed.resolve({
        revision_id: "77777777-7777-4777-8777-777777777777",
        media_kind: "image",
      } as ArtifactRevision);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(guards).toEqual([
      {
        generation: `p:${epoch}`,
        pendingOperationCount: 1,
        hasSavedUnplaced: false,
      },
      {
        generation: `p:${epoch}`,
        pendingOperationCount: 0,
        hasSavedUnplaced: true,
      },
    ]);
  });

  it("keeps both runtimes, the broker hold, and the aggregate guard unchanged when force refresh is refused", async () => {
    const confirmForceRefresh = vi.fn(() => false);
    const approved = vi.fn();
    const scenario = await renderForceRefreshScenario(
      confirmForceRefresh,
      approved,
    );
    const brokerBefore = scenario.shared.permits.getSnapshot();
    const controllerBefore = scenario.controller.getSnapshot();
    const signalsBefore = scenario.attempts.map((attempt) => attempt.signal);
    const abortedBefore = signalsBefore.map((signal) => signal?.aborted ?? null);
    const reportsBefore = [...scenario.reports];

    act(() => scenario.controller.requestForceRefresh());

    expect(confirmForceRefresh).toHaveBeenCalledOnce();
    expect(approved).not.toHaveBeenCalled();
    expect(scenario.shared.permits.getSnapshot()).toBe(brokerBefore);
    expect(scenario.controller.getSnapshot()).toBe(controllerBefore);
    expect(scenario.attempts.map((attempt) => attempt.signal)).toEqual(
      signalsBefore,
    );
    expect(
      scenario.attempts.map((attempt) => attempt.signal?.aborted ?? null),
    ).toEqual(abortedBefore);
    expect(scenario.reports).toEqual(reportsBefore);
  });

  it("does not report an unchanged zero when forced abandonment has nothing left to clear", () => {
    const epoch = load();
    const reports: number[] = [];
    render(
      <MediaIngressProvider
        generation={`p:${epoch}`}
        loadEpoch={epoch}
        onPendingCountChange={(_generation, count) => reports.push(count)}
        projectId={PROJECT_ID}
      >
        <Probe />
      </MediaIngressProvider>,
    );
    reports.length = 0;
    const controller = currentRuntime?.controller as unknown as {
      abandonAllNow(): void;
    } | null;
    if (!controller) throw new Error("controller unavailable");

    act(() => controller.abandonAllNow());

    expect(reports).toEqual([]);
  });

  it("abandons the generation, disposes the broker before both runtimes, reports aggregate zero, and only then approves refresh", async () => {
    const approved = vi.fn();
    let scenario!: Awaited<ReturnType<typeof renderForceRefreshScenario>>;
    scenario = await renderForceRefreshScenario(
      () => true,
      () => {
        expect(scenario.shared.permits.getSnapshot()).toMatchObject({
          runningOperationIds: [],
          queuedOperationIds: [],
          queueHeld: false,
        });
        expect(scenario.controller.getSnapshot().guardState.pendingOperationCount).toBe(0);
        const runtime = currentRuntime;
        if (!runtime) throw new Error("Expected a committed media runtime");
        expect(
          (
            runtime.nodeImport as unknown as {
              getPendingOperationCount(): number;
            }
          ).getPendingOperationCount(),
        ).toBe(0);
        expect(scenario.reports.at(-1)).toBe(0);
        approved();
      },
    );
    const createdBefore = scenario.attempts.map(
      (attempt) => attempt.filename,
    );
    expect(createdBefore).toContain("node.png");
    expect(createdBefore.filter((name) => name.startsWith("batch-"))).toHaveLength(2);
    expect(createdBefore).not.toContain("batch-2.png");

    act(() => scenario.controller.requestForceRefresh());

    expect(approved).toHaveBeenCalledOnce();
    expect(scenario.events[0]).toBe("broker.dispose");
    expect(scenario.events.at(-1)).toBe("approved");
    expect(
      scenario.attempts.every(
        (attempt) => attempt.signal === null || attempt.signal.aborted,
      ),
    ).toBe(true);
    expect(
      scenario.attempts.every(
        (attempt) => attempt.releaseFile.mock.calls.length === 1,
      ),
    ).toBe(true);
    expect(scenario.attempts.map((attempt) => attempt.filename)).toEqual(
      createdBefore,
    );
  });

  it("keys the unified runtime by projectId and loadEpoch", () => {
    const firstEpoch = load();
    const onPendingCountChange = vi.fn();
    const rendered = render(
      <MediaIngressProvider
        generation={`p:${firstEpoch}`}
        loadEpoch={firstEpoch}
        onPendingCountChange={onPendingCountChange}
        projectId={PROJECT_ID}
      >
        <Probe />
      </MediaIngressProvider>,
    );
    const first = currentRuntime;
    expect(first?.projectId).toBe(PROJECT_ID);
    expect(first?.loadEpoch).toBe(firstEpoch);

    const secondEpoch = load();
    rendered.rerender(
      <MediaIngressProvider
        generation={`p:${secondEpoch}`}
        loadEpoch={secondEpoch}
        onPendingCountChange={onPendingCountChange}
        projectId={PROJECT_ID}
      >
        <Probe />
      </MediaIngressProvider>,
    );
    expect(currentRuntime).not.toBe(first);
    expect(currentRuntime?.loadEpoch).toBe(secondEpoch);
  });

  it("survives StrictMode and does not dispose externally injected shared dependencies", async () => {
    vi.useFakeTimers();
    const epoch = load();
    const dependencies = createMediaImportRuntimeDependencies();
    const dispose = vi.spyOn(dependencies.permits, "dispose");
    const actualSubscribe = dependencies.targetLocks.subscribe.bind(
      dependencies.targetLocks,
    );
    const lockListeners: ReturnType<typeof vi.fn<() => void>>[] = [];
    const lockCleanups: ReturnType<typeof vi.fn<() => void>>[] = [];
    vi.spyOn(dependencies.targetLocks, "subscribe").mockImplementation(
      (listener) => {
        const trackedListener = vi.fn<() => void>(listener);
        lockListeners.push(trackedListener);
        const unsubscribe = actualSubscribe(trackedListener);
        const trackedCleanup = vi.fn<() => void>(unsubscribe);
        lockCleanups.push(trackedCleanup);
        return trackedCleanup;
      },
    );
    const rendered = render(
      <StrictMode>
        <MediaIngressProvider
          dependencies={dependencies}
          generation={`p:${epoch}`}
          loadEpoch={epoch}
          projectId={PROJECT_ID}
        >
          <Probe />
        </MediaIngressProvider>
      </StrictMode>,
    );

    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(dispose).not.toHaveBeenCalled();
    expect(currentRuntime).not.toBeNull();

    rendered.unmount();
    expect(dispose).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(dispose).not.toHaveBeenCalled();
    expect(lockCleanups.length).toBeGreaterThan(0);
    for (const cleanupSubscription of lockCleanups) {
      expect(cleanupSubscription).toHaveBeenCalledOnce();
    }
    const callsAfterCleanup = lockListeners.map(
      (listener) => listener.mock.calls.length,
    );
    const lease = dependencies.targetLocks.acquire(
      "post-unmount-operation",
      "post-unmount-node",
    );
    lease?.release();
    expect(lockListeners.map((listener) => listener.mock.calls.length)).toEqual(
      callsAfterCleanup,
    );
  });

  it("suppresses an unchanged old-provider zero after a new generation becomes active", async () => {
    vi.useFakeTimers();
    const firstEpoch = load();
    const reports: Array<[string, number]> = [];
    const report = (generation: string, count: number) => reports.push([generation, count]);
    const rendered = render(
      <MediaIngressProvider
        generation="old"
        loadEpoch={firstEpoch}
        onPendingCountChange={report}
        projectId={PROJECT_ID}
      >
        <Probe />
      </MediaIngressProvider>,
    );

    const secondEpoch = load();
    rendered.rerender(
      <MediaIngressProvider
        generation="new"
        loadEpoch={secondEpoch}
        onPendingCountChange={report}
        projectId={PROJECT_ID}
      >
        <Probe />
      </MediaIngressProvider>,
    );
    reports.length = 0;
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(reports).toEqual([]);
  });

  it("commits reload callback changes only after the provider update commits", () => {
    const epoch = load();
    const committedReload = vi.fn();
    const abandonedReload = vi.fn();
    const view = (onRequestReload: () => void, suspend: boolean) => (
      <Suspense fallback={<p>loading</p>}>
        <MediaIngressProvider
          generation={`p:${epoch}`}
          loadEpoch={epoch}
          onRequestReload={onRequestReload}
          projectId={PROJECT_ID}
        >
          <Probe />
          <MaybeSuspend suspend={suspend} />
        </MediaIngressProvider>
      </Suspense>
    );
    const rendered = render(view(committedReload, false));

    act(() => currentRuntime?.nodeImport.requestReload());
    expect(committedReload).toHaveBeenCalledOnce();

    act(() => {
      startTransition(() => rendered.rerender(view(abandonedReload, true)));
    });
    act(() => currentRuntime?.nodeImport.requestReload());
    expect(committedReload).toHaveBeenCalledTimes(2);
    expect(abandonedReload).not.toHaveBeenCalled();

    rendered.rerender(view(abandonedReload, false));
    act(() => currentRuntime?.nodeImport.requestReload());
    expect(abandonedReload).toHaveBeenCalledOnce();
  });
});
