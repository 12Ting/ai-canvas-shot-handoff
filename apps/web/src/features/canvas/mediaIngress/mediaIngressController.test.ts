import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  ArtifactDeclarationValidationError,
  ArtifactImportConflictError,
  ArtifactImportFailedError,
  SessionExpiredError,
  type ArtifactImportDeclaration,
  type ArtifactRevision,
} from "../../../api/client";
import type { MediaKind, SaveStatus } from "../canvasTypes";
import type {
  CancelReconciliation,
  CreateMediaImportAttemptInput,
  DiscardMediaImportInput,
  MediaImportAttempt,
  MediaImportCore,
  MediaImportNetworkPhase,
  LargeUploadProgressUpdate,
} from "./mediaImportCore";
import { MediaImportPermitBroker } from "./mediaImportPermitBroker";
import { MediaTargetLocks } from "./mediaTargetLocks";
import {
  createMediaIngressController,
  type MediaIngressCanvasAdapter,
} from "./mediaIngressController";
import type { MediaIngressObstacle } from "./mediaIngressLayout";
import type { IngressCandidate, MediaIngressRequest } from "./mediaIngressTypes";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const IMPORT_ID = "99999999-9999-4999-8999-999999999999";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
}

function file(name: string, size = 4, type = "image/png"): File {
  return { name, size, type } as File;
}

function candidate(name: string, size = 4, type = "image/png"): IngressCandidate {
  const selected = file(name, size, type);
  return { kind: "file", file: selected, displayName: selected.name };
}

function entryError(index: number): IngressCandidate {
  return {
    kind: "entry_error",
    displayName: `folder-${index}`,
    code: "folder_not_supported",
    message: "不支持导入文件夹",
  };
}

function request(candidates: readonly IngressCandidate[]): MediaIngressRequest {
  return {
    projectId: PROJECT_ID,
    loadEpoch: 7,
    source: "multi_picker",
    candidates,
    anchor: { x: 100, y: 200 },
    targetMode: "create",
    targetBinding: null,
  };
}

function fillRequest(
  targetNodeId: string,
  candidates: readonly IngressCandidate[] = [candidate("fill.png")],
): MediaIngressRequest {
  return {
    ...request(candidates),
    source: "drop",
    targetMode: "fill_existing",
    targetBinding: { targetNodeId, expectedRevisionId: null },
  };
}

function choiceRequest(
  targetNodeId: string,
  expectedRevisionId: string,
  candidates: readonly IngressCandidate[] = [candidate("replace.png")],
): MediaIngressRequest {
  return {
    ...request(candidates),
    source: "drop",
    targetMode: "choose_create_or_replace",
    targetBinding: { targetNodeId, expectedRevisionId },
  };
}

function revision(index: number, mediaKind: "image" | "video" = "image"): ArtifactRevision {
  return {
    revision_id: `revision-${index}`,
    artifact_id: `artifact-${index}`,
    source_project_id: PROJECT_ID,
    source_kind: "import",
    media_kind: mediaKind,
    byte_size: 4,
  } as ArtifactRevision;
}

class FakeAttempt implements MediaImportAttempt {
  readonly operationId: string;
  readonly declaration;
  readonly result = deferred<ArtifactRevision>();
  released = false;
  signal: AbortSignal | null = null;
  importId: string | null = null;
  createDispatched = false;
  contentPutDispatched = false;
  phases: MediaImportNetworkPhase[] = [];
  reconciliationCalls: Array<{
    signal: AbortSignal;
    maxPolls: number;
    deadlineMs: number;
  }> = [];
  reconciliations: Array<ReturnType<typeof deferred<CancelReconciliation>>> =
    [];
  recoveryCalls: AbortSignal[] = [];
  recoveries: Array<ReturnType<typeof deferred<string>>> = [];
  private onPhaseCallback: ((phase: MediaImportNetworkPhase) => void) | null = null;
  private onProgressCallback: ((progress: LargeUploadProgressUpdate) => void) | null = null;

  constructor(input: CreateMediaImportAttemptInput) {
    this.operationId = input.operationId;
    this.declaration = input.declaration;
  }
  getImportId(): string | null {
    return this.importId;
  }
  wasCreateDispatched(): boolean {
    return this.createDispatched;
  }
  wasContentPutDispatched(): boolean {
    return this.contentPutDispatched;
  }
  execute({
    signal,
    onPhase,
    onProgress,
  }: {
    signal: AbortSignal;
    onPhase(phase: MediaImportNetworkPhase): void;
    onProgress?(progress: LargeUploadProgressUpdate): void;
  }) {
    this.signal = signal;
    this.onPhaseCallback = onPhase;
    this.onProgressCallback = onProgress ?? null;
    onPhase("initializing");
    this.phases.push("initializing");
    return this.result.promise;
  }
  report(phase: MediaImportNetworkPhase): void {
    this.phases.push(phase);
    this.onPhaseCallback?.(phase);
  }
  reportProgress(progress: LargeUploadProgressUpdate): void {
    this.onProgressCallback?.(progress);
  }
  queueReconciliation() {
    const next = deferred<CancelReconciliation>();
    this.reconciliations.push(next);
    return next;
  }
  queueRecovery() {
    const next = deferred<string>();
    this.recoveries.push(next);
    return next;
  }
  recoverImportId({ signal }: { signal: AbortSignal }): Promise<string> {
    this.recoveryCalls.push(signal);
    return (
      this.recoveries.shift()?.promise ??
      Promise.reject(new Error("recovery result was not configured"))
    );
  }
  reconcileCancellation(input: {
    signal: AbortSignal;
    maxPolls: number;
    deadlineMs: number;
  }): Promise<CancelReconciliation> {
    this.reconciliationCalls.push(input);
    return (
      this.reconciliations.shift()?.promise ??
      Promise.resolve({
        kind: "unconfirmed",
        reason: "observer_failed",
        errorMessage: "observer result was not configured",
      })
    );
  }
  releaseFile(): void {
    this.released = true;
  }
}

class FakeCore implements MediaImportCore {
  inputs: CreateMediaImportAttemptInput[] = [];
  attempts: FakeAttempt[] = [];
  throwNext: Error | null = null;
  createCalls = 0;
  throwOnCalls = new Set<number>();
  discardCalls: DiscardMediaImportInput[] = [];
  discards: Array<
    ReturnType<
      typeof deferred<{
        httpStatus: 200 | 202;
        outcome: "discarded" | "already_discarded";
        contentCleanup:
          | "removed"
          | "not_present"
          | "retained_shared"
          | "not_applicable"
          | "pending";
      }>
    >
  > = [];

  createAttempt(input: CreateMediaImportAttemptInput): MediaImportAttempt {
    const call = ++this.createCalls;
    if (this.throwNext) {
      const error = this.throwNext;
      this.throwNext = null;
      throw error;
    }
    if (this.throwOnCalls.delete(call)) {
      throw new Error(`Core creation failed on call ${call}`);
    }
    this.inputs.push(input);
    const attempt = new FakeAttempt(input);
    this.attempts.push(attempt);
    return attempt;
  }

  queueDiscard() {
    const next = deferred<{
      httpStatus: 200 | 202;
      outcome: "discarded" | "already_discarded";
      contentCleanup:
        | "removed"
        | "not_present"
        | "retained_shared"
        | "not_applicable"
        | "pending";
    }>();
    this.discards.push(next);
    return next;
  }

  discardImport(input: DiscardMediaImportInput) {
    this.discardCalls.push(input);
    return (
      this.discards.shift()?.promise ??
      Promise.resolve({
        httpStatus: 200 as const,
        outcome: "discarded" as const,
        contentCleanup: "removed" as const,
      })
    );
  }
}

function harness() {
  const graph = {
    projectId: PROJECT_ID as string | null,
    loadEpoch: 7,
    conflict: false,
    status: "clean" as SaveStatus,
    editSequence: 3,
    savedSequence: 3,
    baseRevisionId: "graph-revision-3" as string | null,
  };
  const mediaTargets = new Map<
    string,
    { revisionId: string | null; mediaKind: MediaKind | null }
  >([
    ["empty-media", { revisionId: null, mediaKind: null }],
    [
      "filled-media",
      { revisionId: "revision-current", mediaKind: "image" },
    ],
    [
      "duplicate-media",
      { revisionId: "revision-current", mediaKind: "image" },
    ],
    ["second-media", { revisionId: "revision-second", mediaKind: "image" }],
  ]);
  const attached: Array<{
    projectId: string;
    loadEpoch: number;
    nodeId: string;
    expectedRevisionId: string | null;
    revisionId: string;
    mediaKind: MediaKind;
  }> = [];
  const promoted: Parameters<MediaIngressCanvasAdapter["promote"]>[0][] = [];
  let promoteHook: (() => void) | null = null;
  let promoteResult = true;
  const core = new FakeCore();
  const permits = new MediaImportPermitBroker(3);
  const targetLocks = new MediaTargetLocks();
  const scheduled: (() => void)[] = [];
  const obstacles: MediaIngressObstacle[] = [];
  const getBinding = vi.fn(() => ({ ...graph }));
  const idFactory = vi.fn();
  let id = 0;
  idFactory.mockImplementation(() => `id-${++id}`);
  const controller = createMediaIngressController({
    projectId: PROJECT_ID,
    loadEpoch: 7,
    core,
    permits,
    targetLocks,
    idFactory,
    schedulePlacement: (callback) => scheduled.push(callback),
    canvas: {
      getBinding,
      getMediaTarget: (nodeId: string) => {
        const target = mediaTargets.get(nodeId);
        return target ? { ...target } : null;
      },
      getObstacles: () => obstacles,
      attach: (input: (typeof attached)[number]) => {
        attached.push(input);
        const target = mediaTargets.get(input.nodeId);
        if (!target || target.revisionId !== input.expectedRevisionId) {
          return false;
        }
        mediaTargets.set(input.nodeId, {
          revisionId: input.revisionId,
          mediaKind: input.mediaKind,
        });
        return true;
      },
      promote: (input) => {
        promoted.push(input);
        promoteHook?.();
        if (promoteResult) {
          obstacles.push({
            x: input.position.x,
            y: input.position.y,
            width: 312,
            height: 360,
          });
        }
        return promoteResult;
      },
    },
  });
  return {
    controller,
    core,
    permits,
    targetLocks,
    graph,
    mediaTargets,
    attached,
    promoted,
    scheduled,
    obstacles,
    getBinding,
    idFactory,
    setPromoteHook: (hook: (() => void) | null) => {
      promoteHook = hook;
    },
    setPromoteResult: (result: boolean) => {
      promoteResult = result;
    },
  };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("MediaIngressController", () => {
  it("exposes server-acknowledged B4 upload progress on the active ingress item", async () => {
    const { controller, core } = harness();
    const totalByteSize = 65 * 1024 * 1024;
    const admission = controller.enqueue(
      request([candidate("progress.mov", totalByteSize, "video/quicktime")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();

    core.attempts[0].reportProgress({
      receivedByteSize: 8 * 1024 * 1024,
      totalByteSize,
    });

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem,
    ).toMatchObject({
      largeUploadProgress: {
        receivedByteSize: 8 * 1024 * 1024,
        totalByteSize,
      },
    });
  });

  beforeEach(() => vi.restoreAllMocks());

  it("does not create a batch for an empty selection", () => {
    const { controller, core, idFactory } = harness();
    expect(controller.enqueue(request([]))).toEqual({ kind: "empty" });
    expect(controller.getSnapshot().batches).toEqual([]);
    expect(idFactory).not.toHaveBeenCalled();
    expect(core.inputs).toEqual([]);
  });

  it("rejects a pre-existing graph conflict before ids, files, batches, reservations, or attempts", () => {
    const { controller, core, graph, idFactory } = harness();
    graph.conflict = true;

    expect(controller.enqueue(request([candidate("private.png")]))).toEqual({
      kind: "rejected",
      message: "请先处理画布版本冲突",
    });
    expect(idFactory).not.toHaveBeenCalled();
    expect(core.inputs).toEqual([]);
    expect(controller.getSnapshot().batches).toEqual([]);
  });

  it("re-gates a normalized paste before retaining its File or creating ids, reservations, or attempts", () => {
    const { controller, core, graph, idFactory } = harness();
    const pasted = candidate("private-clipboard.png");
    graph.conflict = true;

    expect(
      controller.enqueue({
        ...request([pasted]),
        source: "paste",
      }),
    ).toEqual({
      kind: "rejected",
      message: "请先处理画布版本冲突",
    });
    expect(idFactory).not.toHaveBeenCalled();
    expect(core.inputs).toEqual([]);
    expect(controller.getSnapshot().batches).toEqual([]);
  });

  it("never lets a malformed multi-image paste fill the first selected target", () => {
    const { controller, core, idFactory, mediaTargets } = harness();

    const admission = controller.enqueue({
      ...fillRequest("empty-media", [
        candidate("first.png"),
        candidate("second.png"),
      ]),
      source: "paste",
    });

    expect(admission.kind).toBe("rejected");
    expect(idFactory).not.toHaveBeenCalled();
    expect(core.inputs).toEqual([]);
    expect(controller.getSnapshot().batches).toEqual([]);
    expect(mediaTargets.get("empty-media")?.revisionId).toBeNull();
  });

  it("prioritizes a graph conflict over aggregate count and byte-limit rejection", () => {
    for (const candidates of [
      Array.from({ length: 21 }, (_, index) => entryError(index)),
      [candidate("too-large-batch.png", 536_870_913)],
    ]) {
      const { controller, core, graph, idFactory } = harness();
      graph.conflict = true;

      expect(controller.enqueue(request(candidates))).toEqual({
        kind: "rejected",
        message: "请先处理画布版本冲突",
      });
      expect(idFactory).not.toHaveBeenCalled();
      expect(core.inputs).toEqual([]);
      expect(controller.getSnapshot().batches).toEqual([]);
    }
  });

  it("prioritizes stale or disposed controller gates over aggregate rejection", () => {
    const scenarios: Array<
      (
        context: ReturnType<typeof harness>,
        requestValue: MediaIngressRequest,
      ) => MediaIngressRequest
    > = [
      (_context, requestValue) => ({
        ...requestValue,
        projectId: "22222222-2222-4222-8222-222222222222",
      }),
      (_context, requestValue) => ({ ...requestValue, loadEpoch: 8 }),
      (context, requestValue) => {
        context.graph.projectId = "22222222-2222-4222-8222-222222222222";
        return requestValue;
      },
      (context, requestValue) => {
        context.graph.loadEpoch = 8;
        return requestValue;
      },
      (context, requestValue) => {
        context.controller.dispose();
        return requestValue;
      },
    ];

    for (const arrange of scenarios) {
      const context = harness();
      const staleRequest = arrange(
        context,
        request([candidate("too-large-batch.png", 536_870_913)]),
      );

      expect(context.controller.enqueue(staleRequest)).toEqual({
        kind: "rejected",
        message: "当前项目已发生变化",
      });
      expect(context.idFactory).not.toHaveBeenCalled();
      expect(context.core.inputs).toEqual([]);
      expect(context.controller.getSnapshot().batches).toEqual([]);
    }
  });

  it("performs 20 item and 100 GiB aggregate admission before creating any ids or attempts", () => {
    const allowedSizes = [
      ...Array.from({ length: 20 }, () => 5_368_709_120),
    ];
    const accepted = harness();
    expect(
      accepted.controller.enqueue(
        request(allowedSizes.map((size, index) => candidate(`${index}.png`, size))),
      ).kind,
    ).toBe("accepted");
    expect(accepted.controller.getSnapshot().batches[0].counts.total).toBe(20);

    for (const rejectedCandidates of [
      Array.from({ length: 21 }, (_, index) => candidate(`${index}.png`, 1)),
      [candidate("too-much.png", 107_374_182_401)],
    ]) {
      const rejected = harness();
      expect(rejected.controller.enqueue(request(rejectedCandidates)).kind).toBe("rejected");
      expect(rejected.idFactory).not.toHaveBeenCalled();
      expect(rejected.core.inputs).toEqual([]);
      expect(rejected.controller.getSnapshot().batches).toEqual([]);
    }
  });

  it("counts local entry errors toward all-or-none admission before ids or Core", () => {
    const { controller, core, idFactory } = harness();

    const admission = controller.enqueue(
      request(Array.from({ length: 21 }, (_, index) => entryError(index))),
    );

    expect(admission.kind).toBe("rejected");
    if (admission.kind === "rejected") {
      expect(admission.message).toContain("21");
      expect(admission.message).toContain("20");
      expect(admission.message).toContain("拆分导入");
    }
    expect(idFactory).not.toHaveBeenCalled();
    expect(core.inputs).toEqual([]);
    expect(controller.getSnapshot().batches).toEqual([]);
  });

  it("admits an empty-node fill with a distinct fallback reservation and null replacement declaration", async () => {
    const { controller, core } = harness();

    const admission = controller.enqueue(fillRequest("empty-media"));

    expect(admission.kind).toBe("accepted");
    const slot = controller.getSnapshot().batches[0].slots[0];
    expect(slot.targetNodeId).toBe("empty-media");
    expect(slot.reservation.nodeId).not.toBe("empty-media");
    expect(core.inputs).toEqual([]);
    await flush();
    expect(core.inputs).toHaveLength(1);
    expect(core.inputs[0].declaration.replaceRevisionId).toBeNull();
  });

  it("holds a filled-node single file as a choice without batch, reservation, attempt, or permit", () => {
    const { controller, core, permits, targetLocks, idFactory } = harness();

    const admission = controller.enqueue(
      choiceRequest("filled-media", "revision-current"),
    );

    expect(admission).toEqual({
      kind: "choice_required",
      choiceId: expect.any(String),
    });
    expect(controller.getSnapshot().batches).toEqual([]);
    expect(controller.getSnapshot().pendingTargetChoices).toEqual([
      expect.objectContaining({
        choiceId:
          admission.kind === "choice_required" ? admission.choiceId : "",
        displayName: "replace.png",
        targetNodeId: "filled-media",
        requestedMediaKind: "image",
        currentMediaKind: "image",
      }),
    ]);
    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(1);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "filled-media",
    ]);
    expect(core.inputs).toEqual([]);
    expect(permits.getSnapshot().runningOperationIds).toEqual([]);
    expect(permits.getSnapshot().queuedOperationIds).toEqual([]);
    expect(idFactory).toHaveBeenCalledTimes(1);
  });

  it("keeps an empty target locked through null-CAS attach and marks only that target PLACED", async () => {
    const {
      controller,
      core,
      targetLocks,
      attached,
      promoted,
      scheduled,
    } = harness();

    expect(controller.enqueue(fillRequest("empty-media")).kind).toBe(
      "accepted",
    );
    const slot = controller.getSnapshot().batches[0].slots[0];
    expect(slot.reservation.nodeId).not.toBe("empty-media");
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "empty-media",
    ]);
    await flush();

    core.attempts[0].result.resolve(revision(1));
    await flush();
    expect(controller.getSnapshot().batches[0].slots[0].currentItem.state).toBe(
      "PLACING",
    );
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "empty-media",
    ]);

    scheduled.shift()?.();

    expect(attached).toEqual([
      {
        projectId: PROJECT_ID,
        loadEpoch: 7,
        nodeId: "empty-media",
        expectedRevisionId: null,
        revisionId: "revision-1",
        mediaKind: "image",
      },
    ]);
    expect(promoted).toEqual([]);
    expect(controller.getSnapshot().batches[0].slots[0].currentItem.state).toBe(
      "PLACED",
    );
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("keeps a distinct fallback when a target CAS turns stale and releases the target lock", async () => {
    const {
      controller,
      core,
      mediaTargets,
      targetLocks,
      attached,
      promoted,
      scheduled,
    } = harness();
    controller.enqueue(fillRequest("empty-media"));
    const fallback =
      controller.getSnapshot().batches[0].slots[0].reservation;
    await flush();
    mediaTargets.set("empty-media", {
      revisionId: "revision-other",
      mediaKind: "image",
    });

    core.attempts[0].result.resolve(revision(1));
    await flush();
    scheduled.shift()?.();

    const slot = controller.getSnapshot().batches[0].slots[0];
    expect(attached).toHaveLength(0);
    expect(promoted).toEqual([]);
    expect(slot.currentItem.state).toBe("SAVED_UNPLACED");
    expect(slot.currentItem.errorMessage).toContain(
      "目标已变化，成功后需决定是否作为新节点放置",
    );
    expect(slot.reservation).toEqual(fallback);
    expect(slot.reservation.nodeId).not.toBe("empty-media");
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("cancels a pending choice after graph and target mutations without consulting those gates", () => {
    const { controller, core, graph, mediaTargets, targetLocks } = harness();
    const admission = controller.enqueue(
      choiceRequest("filled-media", "revision-current"),
    );
    expect(admission.kind).toBe("choice_required");
    if (admission.kind !== "choice_required") return;
    graph.projectId = "22222222-2222-4222-8222-222222222222";
    graph.loadEpoch = 99;
    graph.conflict = true;
    mediaTargets.delete("filled-media");

    expect(
      controller.resolveTargetChoice(admission.choiceId, "cancel"),
    ).toEqual({ kind: "empty" });
    expect(
      controller.resolveTargetChoice(admission.choiceId, "cancel"),
    ).toEqual({ kind: "empty" });
    expect(controller.getSnapshot().pendingTargetChoices).toEqual([]);
    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(0);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(controller.getSnapshot().batches).toEqual([]);
    expect(core.inputs).toEqual([]);
  });

  it("cancels one FIFO choice without reading graph gates needed only by a later choice", () => {
    const { controller, getBinding, targetLocks } = harness();
    const first = controller.enqueue(
      choiceRequest("filled-media", "revision-current", [
        candidate("first.png"),
      ]),
    );
    const second = controller.enqueue(
      choiceRequest("second-media", "revision-second", [
        candidate("second.png"),
      ]),
    );
    expect(first.kind).toBe("choice_required");
    expect(second.kind).toBe("choice_required");
    if (first.kind !== "choice_required") return;
    getBinding.mockImplementation(() => {
      throw new Error("cancel must not read the graph gate");
    });

    expect(
      controller.resolveTargetChoice(first.choiceId, "cancel"),
    ).toEqual({ kind: "empty" });
    expect(
      controller
        .getSnapshot()
        .pendingTargetChoices.map((pending) => pending.displayName),
    ).toEqual(["second.png"]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "second-media",
    ]);
  });

  it("resolves create into an unbound fallback batch and releases the choice target", async () => {
    const { controller, core, targetLocks } = harness();
    const admission = controller.enqueue(
      choiceRequest("filled-media", "revision-current"),
    );
    expect(admission.kind).toBe("choice_required");
    if (admission.kind !== "choice_required") return;

    const resolved = controller.resolveTargetChoice(
      admission.choiceId,
      "create",
    );

    expect(resolved.kind).toBe("accepted");
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    const slot = controller.getSnapshot().batches[0].slots[0];
    expect(slot.targetNodeId).toBeNull();
    expect(slot.reservation.nodeId).not.toBe("filled-media");
    expect(core.inputs).toEqual([]);
    await flush();
    expect(core.inputs).toHaveLength(1);
    expect(core.inputs[0].declaration.replaceRevisionId).toBeNull();
  });

  it("resolves replace with the exact old revision and holds the target through exact CAS attach", async () => {
    const {
      controller,
      core,
      mediaTargets,
      targetLocks,
      attached,
      promoted,
      scheduled,
    } = harness();
    const admission = controller.enqueue(
      choiceRequest("filled-media", "revision-current"),
    );
    expect(admission.kind).toBe("choice_required");
    if (admission.kind !== "choice_required") return;

    expect(
      controller.resolveTargetChoice(admission.choiceId, "replace").kind,
    ).toBe("accepted");
    expect(core.inputs).toEqual([]);
    await flush();
    expect(core.inputs[0].declaration.replaceRevisionId).toBe(
      "revision-current",
    );
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "filled-media",
    ]);
    core.attempts[0].result.resolve(revision(2));
    await flush();
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "filled-media",
    ]);
    scheduled.shift()?.();

    expect(attached).toEqual([
      expect.objectContaining({
        nodeId: "filled-media",
        expectedRevisionId: "revision-current",
        revisionId: "revision-2",
      }),
    ]);
    expect(mediaTargets.get("filled-media")?.revisionId).toBe("revision-2");
    expect(mediaTargets.get("duplicate-media")?.revisionId).toBe(
      "revision-current",
    );
    expect(promoted).toEqual([]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("disables a cross-kind replacement while keeping create available", () => {
    const { controller, core, targetLocks } = harness();
    const admission = controller.enqueue(
      choiceRequest("filled-media", "revision-current", [
        candidate("clip.webm", 4, "video/webm"),
      ]),
    );
    expect(admission.kind).toBe("choice_required");
    if (admission.kind !== "choice_required") return;

    expect(controller.getSnapshot().pendingTargetChoices[0]).toMatchObject({
      requestedMediaKind: "video",
      currentMediaKind: "image",
      canReplace: false,
      replacementBlockedReason: "仅可替换为相同媒体类型",
    });
    expect(
      controller.resolveTargetChoice(admission.choiceId, "replace").kind,
    ).toBe("rejected");
    expect(controller.getSnapshot().pendingTargetChoices).toEqual([]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(core.inputs).toEqual([]);
  });

  it("keeps independent choices FIFO and rejects a second choice for the same target", () => {
    const { controller } = harness();
    const first = controller.enqueue(
      choiceRequest("filled-media", "revision-current", [
        candidate("first.png"),
      ]),
    );
    const duplicate = controller.enqueue(
      choiceRequest("filled-media", "revision-current", [
        candidate("duplicate.png"),
      ]),
    );
    const second = controller.enqueue(
      choiceRequest("second-media", "revision-second", [
        candidate("second.png"),
      ]),
    );

    expect(first.kind).toBe("choice_required");
    expect(duplicate).toEqual({
      kind: "rejected",
      message: "该媒体节点正在处理另一项导入",
    });
    expect(second.kind).toBe("choice_required");
    expect(
      controller
        .getSnapshot()
        .pendingTargetChoices.map((pending) => pending.displayName),
    ).toEqual(["first.png", "second.png"]);
  });

  it("re-gates create and replace choices and atomically clears stale guards", () => {
    const scenarios: Array<{
      name: string;
      mutate(context: ReturnType<typeof harness>): void;
    }> = [
      {
        name: "project",
        mutate: ({ graph }) => {
          graph.projectId =
            "22222222-2222-4222-8222-222222222222";
        },
      },
      {
        name: "load epoch",
        mutate: ({ graph }) => {
          graph.loadEpoch = 8;
        },
      },
      {
        name: "conflict",
        mutate: ({ graph }) => {
          graph.conflict = true;
          graph.status = "conflict";
        },
      },
      {
        name: "deleted target",
        mutate: ({ mediaTargets }) => {
          mediaTargets.delete("filled-media");
        },
      },
      {
        name: "changed target revision",
        mutate: ({ mediaTargets }) => {
          mediaTargets.set("filled-media", {
            revisionId: "revision-later",
            mediaKind: "image",
          });
        },
      },
    ];

    for (const selected of ["create", "replace"] as const) {
      for (const scenario of scenarios) {
        const context = harness();
        const admission = context.controller.enqueue(
          choiceRequest("filled-media", "revision-current", [
            candidate(`${scenario.name}-${selected}.png`),
          ]),
        );
        expect(admission.kind).toBe("choice_required");
        if (admission.kind !== "choice_required") continue;
        scenario.mutate(context);

        expect(
          context.controller.resolveTargetChoice(
            admission.choiceId,
            selected,
          ).kind,
        ).toBe("rejected");
        expect(
          context.controller.getSnapshot().pendingTargetChoices,
        ).toEqual([]);
        expect(
          context.controller.getSnapshot().guardState.pendingOperationCount,
        ).toBe(0);
        expect(
          context.targetLocks.getSnapshot().protectedNodeIds,
        ).toEqual([]);
        expect(context.controller.getSnapshot().batches).toEqual([]);
        expect(context.core.inputs).toEqual([]);
      }
    }
  });

  it("requires a clean saved graph with a base revision before replacement", () => {
    const scenarios: Array<{
      arrange(graph: ReturnType<typeof harness>["graph"]): void;
      reason: string;
    }> = [
      {
        arrange: (graph) => {
          graph.status = "dirty";
        },
        reason: "请先保存画布后再替换",
      },
      {
        arrange: (graph) => {
          graph.editSequence = 4;
          graph.savedSequence = 3;
        },
        reason: "请先保存画布后再替换",
      },
      {
        arrange: (graph) => {
          graph.baseRevisionId = null;
        },
        reason: "当前画布没有可用于替换的已保存版本",
      },
    ];

    for (const scenario of scenarios) {
      const context = harness();
      scenario.arrange(context.graph);
      const admission = context.controller.enqueue(
        choiceRequest("filled-media", "revision-current"),
      );
      expect(admission.kind).toBe("choice_required");
      if (admission.kind !== "choice_required") continue;
      expect(
        context.controller.getSnapshot().pendingTargetChoices[0],
      ).toMatchObject({
        canReplace: false,
        replacementBlockedReason: scenario.reason,
      });

      expect(
        context.controller.resolveTargetChoice(
          admission.choiceId,
          "replace",
        ),
      ).toEqual({ kind: "rejected", message: scenario.reason });
      expect(
        context.targetLocks.getSnapshot().protectedNodeIds,
      ).toEqual([]);
      expect(context.core.inputs).toEqual([]);
    }
  });

  it("leaves an empty target unchanged and releases its lock when import fails", async () => {
    const { controller, core, mediaTargets, targetLocks, attached } =
      harness();
    controller.enqueue(fillRequest("empty-media"));
    await flush();

    core.attempts[0].result.reject(new Error("upload failed"));
    await flush();

    expect(mediaTargets.get("empty-media")).toEqual({
      revisionId: null,
      mediaKind: null,
    });
    expect(attached).toEqual([]);
    expect(controller.getSnapshot().batches[0].slots[0].currentItem.state).toBe(
      "FAILED",
    );
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("keeps a pending File private and releases its choice lock on dispose", () => {
    const { controller, targetLocks, core } = harness();
    const admission = controller.enqueue(
      choiceRequest("filled-media", "revision-current", [
        candidate("C:/private/pending-secret.png"),
      ]),
    );
    expect(admission.kind).toBe("choice_required");
    const serialized = JSON.stringify(controller.getSnapshot());
    expect(serialized).not.toContain("C:/private");
    expect(serialized).not.toContain("content");
    expect(serialized).toContain("pending-secret.png");

    controller.dispose();

    expect(controller.getSnapshot().pendingTargetChoices).toEqual([]);
    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(0);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(core.inputs).toEqual([]);
  });

  it("isolates an admitted entry error while the valid slot continues in order", async () => {
    const { controller, core } = harness();

    expect(
      controller.enqueue(
        request([entryError(1), candidate("valid.png")]),
      ).kind,
    ).toBe("accepted");

    const batch = controller.getSnapshot().batches[0];
    expect(batch.counts).toMatchObject({
      total: 2,
      failed: 1,
      queued: 1,
    });
    expect(batch.slots.map((slot) => slot.currentItem.state)).toEqual([
      "FAILED",
      "QUEUED",
    ]);
    expect(batch.slots.map((slot) => slot.currentItem.displayName)).toEqual([
      "folder-1",
      "valid.png",
    ]);
    expect(core.inputs).toEqual([]);
    await flush();
    expect(core.inputs).toHaveLength(1);
    expect(core.inputs[0].declaration.filename).toBe("valid.png");
    expect(
      controller
        .getSnapshot()
        .batches[0].slots.map((slot) => slot.currentItem.state),
    ).toEqual(["FAILED", "INITIALIZING"]);
  });

  it("isolates a first-slot synchronous Core creation throw and keeps its failed reservation recoverable", async () => {
    const { controller, core, permits, targetLocks } = harness();
    core.throwOnCalls.add(1);
    let admission: ReturnType<typeof controller.enqueue> | undefined;

    expect(() => {
      admission = controller.enqueue(
        request([
          candidate("C:/private/throws-first.png"),
          candidate("continues.png"),
        ]),
      );
    }).not.toThrow();

    expect(admission?.kind).toBe("accepted");
    const originalBatch = controller.getSnapshot().batches[0];
    await flush();
    expect(
      controller
        .getSnapshot()
        .batches[0].slots.map((slot) => slot.currentItem.state),
    ).toEqual(["FAILED", "INITIALIZING"]);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.errorMessage,
    ).toBe("媒体导入失败，请重试");
    expect(
      controller.getSnapshot().batches[0].slots[0].canReselect,
    ).toBe(true);
    expect(core.attempts).toHaveLength(1);
    expect(core.inputs.map((input) => input.declaration.filename)).toEqual([
      "continues.png",
    ]);
    expect(JSON.stringify(controller.getSnapshot())).not.toContain(
      "C:/private",
    );

    controller.enqueue(request([candidate("later.png")]));
    const laterPosition =
      controller.getSnapshot().batches[0].slots[0].reservation.position;
    expect(laterPosition).not.toEqual(
      originalBatch.slots[0].reservation.position,
    );
    expect(laterPosition).not.toEqual(
      originalBatch.slots[1].reservation.position,
    );
    expect(
      controller.getSnapshot().batches[1].slots[1].currentItem.state,
    ).toBe("INITIALIZING");

    controller.dispose();
    await flush();
    expect(core.attempts.every((attempt) => attempt.released)).toBe(true);
    expect(permits.getSnapshot().runningOperationIds).toEqual([]);
    expect(permits.getSnapshot().queuedOperationIds).toEqual([]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("isolates a later synchronous Core creation throw while earlier and later peers continue", async () => {
    const { controller, core, permits } = harness();
    core.throwOnCalls.add(2);
    let admission: ReturnType<typeof controller.enqueue> | undefined;

    expect(() => {
      admission = controller.enqueue(
        request([
          candidate("before.png"),
          candidate("throws-middle.png"),
          candidate("after.png"),
        ]),
      );
    }).not.toThrow();

    expect(admission?.kind).toBe("accepted");
    await flush();
    expect(
      controller
        .getSnapshot()
        .batches[0].slots.map((slot) => slot.currentItem.state),
    ).toEqual(["INITIALIZING", "FAILED", "INITIALIZING"]);
    expect(core.attempts).toHaveLength(2);
    expect(core.inputs.map((input) => input.declaration.filename)).toEqual([
      "before.png",
      "after.png",
    ]);

    expect(permits.getSnapshot().runningOperationIds).toHaveLength(2);

    controller.dispose();
    expect(core.attempts.every((attempt) => attempt.released)).toBe(true);
    expect(permits.getSnapshot().runningOperationIds).toEqual([]);
  });

  it("turns a target-bound synchronous Core creation throw into a failed slot and releases its lease", async () => {
    const { controller, core, permits, targetLocks } = harness();
    core.throwOnCalls.add(1);
    let admission: ReturnType<typeof controller.enqueue> | undefined;

    expect(() => {
      admission = controller.enqueue(
        fillRequest("empty-media", [
          candidate("C:/private/target-throws.png"),
        ]),
      );
    }).not.toThrow();

    expect(admission?.kind).toBe("accepted");
    if (admission?.kind !== "accepted") return;
    await flush();
    const failed = controller.getSnapshot().batches[0].slots[0];
    expect(failed.currentItem).toMatchObject({
      state: "FAILED",
      errorMessage: "媒体导入失败，请重试",
    });
    expect(failed.targetNodeId).toBe("empty-media");
    expect(core.attempts).toEqual([]);
    expect(permits.getSnapshot().runningOperationIds).toEqual([]);
    expect(permits.getSnapshot().queuedOperationIds).toEqual([]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(JSON.stringify(controller.getSnapshot())).not.toContain(
      "C:/private",
    );

    controller.enqueue(request([candidate("blocked-by-failure.png")]));
    expect(
      controller.getSnapshot().batches[0].slots[0].reservation.position,
    ).toEqual({ x: 460, y: 200 });

    controller.ignoreSlot(admission.batchId, failed.slotId);
    controller.enqueue(request([candidate("released-after-ignore.png")]));
    expect(
      controller.getSnapshot().batches[0].slots[0].reservation.position,
    ).toEqual(failed.reservation.position);

    controller.dispose();
    await flush();
    expect(core.attempts.every((attempt) => attempt.released)).toBe(true);
    expect(permits.getSnapshot().runningOperationIds).toEqual([]);
    expect(permits.getSnapshot().queuedOperationIds).toEqual([]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("fails only oversized or unknown slots after admission while valid slots continue", async () => {
    const { controller, core } = harness();
    expect(
      controller.enqueue(
        request([
          candidate("large.png", 10_737_418_241),
          candidate("unknown.bin", 4, "application/octet-stream"),
          candidate("valid.png"),
        ]),
      ).kind,
    ).toBe("accepted");

    const batch = controller.getSnapshot().batches[0];
    expect(batch.slots.map((slot) => slot.currentItem.state)).toEqual([
      "FAILED",
      "FAILED",
      "QUEUED",
    ]);
    expect(core.inputs).toEqual([]);
    await flush();
    expect(core.inputs).toHaveLength(1);
    expect(controller.getSnapshot().batches[0].slots[2].currentItem.state).toBe(
      "INITIALIZING",
    );
  });

  it("creates four stable reservations immediately, runs only three, and gives every item independent ids", async () => {
    const { controller, core } = harness();
    expect(
      controller.enqueue(
        request(Array.from({ length: 4 }, (_, index) => candidate(`${index}.png`))),
      ).kind,
    ).toBe("accepted");

    const immediate = controller.getSnapshot();
    expect(immediate.batches[0].slots).toHaveLength(4);
    expect(immediate.batches[0].slots.map((slot) => slot.reservation.position)).toEqual([
      { x: 100, y: 200 },
      { x: 452, y: 200 },
      { x: 100, y: 600 },
      { x: 452, y: 600 },
    ]);
    expect(new Set(immediate.batches[0].slots.map((slot) => slot.slotId)).size).toBe(4);
    expect(new Set(immediate.batches[0].slots.map((slot) => slot.reservation.nodeId)).size).toBe(4);
    expect(core.inputs).toEqual([]);

    await flush();
    expect(core.inputs).toHaveLength(3);
    expect(new Set(core.inputs.map((input) => input.operationId)).size).toBe(3);
    expect(
      new Set(
        core.inputs.map(
          (input) => (input.declaration as ArtifactImportDeclaration).clientImportId,
        ),
      ).size,
    ).toBe(3);
    expect(
      controller
        .getSnapshot()
        .batches[0].slots.map((slot) => slot.currentItem.state),
    ).toEqual(["INITIALIZING", "INITIALIZING", "INITIALIZING", "QUEUED"]);
  });

  it("cancels a queued target import synchronously without creating Core or retaining private resources", () => {
    const { controller, core, permits, targetLocks } = harness();
    permits.holdQueue("earlier-unknown-cancellation");
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;

    const slot = controller.getSnapshot().batches[0].slots[0];
    expect(slot.currentItem.state).toBe("QUEUED");
    expect(core.inputs).toEqual([]);
    expect(permits.getSnapshot().queuedOperationIds).toEqual([
      slot.currentItem.itemId,
    ]);

    controller.cancelItem(admission.batchId, slot.currentItem.itemId);

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("CANCELLED");
    expect(core.inputs).toEqual([]);
    expect(permits.getSnapshot().queuedOperationIds).toEqual([]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    permits.releaseQueueHold("earlier-unknown-cancellation");
  });

  it("releases a cancelled target fallback reservation for the next batch", () => {
    const { controller, permits } = harness();
    permits.holdQueue("keep-target-import-queued");
    const targetAdmission = controller.enqueue(fillRequest("empty-media"));
    expect(targetAdmission.kind).toBe("accepted");
    if (targetAdmission.kind !== "accepted") return;
    const targetSlot = controller.getSnapshot().batches[0].slots[0];

    controller.cancelItem(
      targetAdmission.batchId,
      targetSlot.currentItem.itemId,
    );
    const nextAdmission = controller.enqueue(
      request([candidate("reuse-target-fallback.png")]),
    );
    expect(nextAdmission.kind).toBe("accepted");

    expect(
      controller.getSnapshot().batches[0].slots[0].reservation.position,
    ).toEqual(targetSlot.reservation.position);
    controller.dispose();
    permits.releaseQueueHold("keep-target-import-queued");
  });

  it("cancels a running attempt directly when no import id exists and PUT never started", async () => {
    const { controller, core, permits, targetLocks } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();

    const item = controller.getSnapshot().batches[0].slots[0].currentItem;
    const attempt = core.attempts[0];
    expect(attempt.importId).toBeNull();
    expect(attempt.contentPutDispatched).toBe(false);

    controller.cancelItem(admission.batchId, item.itemId);

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("CANCELLED");
    expect(attempt.signal?.aborted).toBe(true);
    expect(attempt.released).toBe(true);
    expect(permits.getSnapshot()).toMatchObject({
      runningOperationIds: [],
      queuedOperationIds: [],
      queueHeld: false,
    });
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("reconciles a dispatched large create with no returned upload id before release", async () => {
    const { controller, core, permits } = harness();
    const admission = controller.enqueue(
      request([
        candidate(
          "create-response-lost.mov",
          64 * 1024 * 1024 + 1,
          "video/quicktime",
        ),
      ]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();

    const item = controller.getSnapshot().batches[0].slots[0].currentItem;
    const attempt = core.attempts[0];
    attempt.createDispatched = true;
    const observation = attempt.queueReconciliation();

    controller.cancelItem(admission.batchId, item.itemId);

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("CANCELLING");
    expect(attempt.reconciliationCalls).toHaveLength(1);
    expect(attempt.released).toBe(false);
    expect(permits.getSnapshot().runningOperationIds).toContain(item.itemId);

    observation.resolve({
      kind: "cancelled",
      serverState: "ABORTED",
      reason: "aborted",
    });
    await flush();

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("CANCELLED");
    expect(attempt.released).toBe(true);
    expect(permits.getSnapshot().runningOperationIds).not.toContain(item.itemId);
  });

  it("holds all new grants while cancellation is unknown, retries once, then saves an exact completed revision", async () => {
    const { controller, core, permits, promoted, scheduled } = harness();
    const admission = controller.enqueue(
      request(Array.from({ length: 4 }, (_, index) => candidate(`${index}.png`))),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    expect(core.attempts).toHaveLength(3);

    const firstItem =
      controller.getSnapshot().batches[0].slots[0].currentItem;
    const firstAttempt = core.attempts[0];
    firstAttempt.importId = "server-import-1";
    firstAttempt.contentPutDispatched = true;
    const firstObservation = firstAttempt.queueReconciliation();

    controller.cancelItem(admission.batchId, firstItem.itemId);
    await flush();

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("CANCELLING");
    expect(firstAttempt.signal?.aborted).toBe(true);
    expect(firstAttempt.reconciliationCalls).toHaveLength(1);
    expect(firstAttempt.reconciliationCalls[0]).toMatchObject({
      maxPolls: 40,
      deadlineMs: 10_000,
    });
    expect(firstAttempt.reconciliationCalls[0].signal).not.toBe(
      firstAttempt.signal,
    );
    expect(permits.getSnapshot().queueHeld).toBe(true);
    expect(permits.getSnapshot().runningOperationIds).toContain(
      firstItem.itemId,
    );

    core.attempts[1].result.reject(new Error("peer failed"));
    await flush();
    expect(core.attempts).toHaveLength(3);
    expect(permits.getSnapshot().queueHeld).toBe(true);

    firstObservation.resolve({
      kind: "pending",
      serverState: "CREATED",
    });
    await flush();
    const unknown =
      controller.getSnapshot().batches[0].slots[0].currentItem;
    expect(unknown.state).toBe("CANCELLING");
    expect(unknown.errorMessage).toBeTruthy();
    expect(unknown.canRetryCancellation).toBe(true);
    expect(unknown.canForceRefresh).toBe(true);
    expect(permits.getSnapshot().queueHeld).toBe(true);

    const retryObservation = firstAttempt.queueReconciliation();
    controller.retryCancellation(admission.batchId, firstItem.itemId);
    controller.retryCancellation(admission.batchId, firstItem.itemId);
    await flush();
    expect(firstAttempt.reconciliationCalls).toHaveLength(2);

    retryObservation.resolve({
      kind: "completed",
      revision: revision(70),
    });
    await flush();

    const saved = controller.getSnapshot().batches[0].slots[0];
    expect(saved.currentItem.state).toBe("SAVED_UNPLACED");
    expect(saved.canPlaceSavedUnplaced).toBe(true);
    expect(saved.canIgnore).toBe(true);
    expect(firstAttempt.released).toBe(true);
    expect(permits.getSnapshot().queueHeld).toBe(false);
    expect(permits.getSnapshot().runningOperationIds).not.toContain(
      firstItem.itemId,
    );
    expect(core.attempts).toHaveLength(4);
    expect(scheduled).toEqual([]);
    expect(promoted).toEqual([]);
  });

  it("does not offer batch stop when cancellation confirmation is the only remaining work", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(
      request([candidate("unknown-only.png")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    const attempt = core.attempts[0];
    attempt.importId = "unknown-only-import";
    attempt.contentPutDispatched = true;
    const observation = attempt.queueReconciliation();
    const item = controller.getSnapshot().batches[0].slots[0].currentItem;

    controller.cancelItem(admission.batchId, item.itemId);
    observation.resolve({
      kind: "unconfirmed",
      reason: "observer_failed",
      errorMessage: "network unavailable",
    });
    await flush();

    const batchSnapshot = controller.getSnapshot().batches[0];
    expect(batchSnapshot.slots[0].currentItem.state).toBe("CANCELLING");
    expect(batchSnapshot.canStop).toBe(false);
  });

  it("stops only the selected batch, synchronously cancels its queued and running work, and leaves placed work intact", async () => {
    const { controller, core, scheduled } = harness();
    const target = controller.enqueue(
      request(Array.from({ length: 5 }, (_, index) => candidate(`target-${index}.png`))),
    );
    const other = controller.enqueue(request([candidate("other.png")]));
    expect(target.kind).toBe("accepted");
    expect(other.kind).toBe("accepted");
    if (target.kind !== "accepted" || other.kind !== "accepted") return;
    await flush();

    core.attempts[0].result.resolve(revision(80));
    await flush();
    scheduled.shift()?.();
    expect(
      controller.getSnapshot().batches
        .find((batch) => batch.batchId === target.batchId)
        ?.slots[0].currentItem.state,
    ).toBe("PLACED");

    controller.stopBatch(target.batchId);
    const stopped = controller
      .getSnapshot()
      .batches.find((batch) => batch.batchId === target.batchId);
    expect(stopped?.slots.map((slot) => slot.currentItem.state)).toEqual([
      "PLACED",
      "CANCELLED",
      "CANCELLED",
      "CANCELLED",
      "CANCELLED",
    ]);
    expect(
      controller.getSnapshot().batches
        .find((batch) => batch.batchId === other.batchId)
        ?.slots[0].currentItem.state,
    ).toBe("QUEUED");
    await flush();
    expect(
      controller.getSnapshot().batches
        .find((batch) => batch.batchId === other.batchId)
        ?.slots[0].currentItem.state,
    ).toBe("INITIALIZING");
  });

  it("releases network resources and starts the fourth import while a completed revision awaits placement", async () => {
    const { controller, core, permits, scheduled } = harness();
    controller.enqueue(
      request(Array.from({ length: 4 }, (_, index) => candidate(`release-${index}.png`))),
    );
    await flush();
    const firstItem =
      controller.getSnapshot().batches[0].slots[0].currentItem.itemId;

    core.attempts[0].result.resolve(revision(81));
    await flush();

    const slots = controller.getSnapshot().batches[0].slots;
    expect(slots[0].currentItem.state).toBe("PLACING");
    expect(core.attempts[0].released).toBe(true);
    expect(permits.getSnapshot().runningOperationIds).not.toContain(firstItem);
    expect(slots[3].currentItem.state).toBe("INITIALIZING");
    expect(scheduled).toHaveLength(1);
  });

  it("cancels before the scheduled graph callback without creating a node and keeps the saved revision actionable", async () => {
    const { controller, core, promoted, scheduled } = harness();
    const admission = controller.enqueue(request([candidate("saved-before-place.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.resolve(revision(82));
    await flush();
    const item = controller.getSnapshot().batches[0].slots[0].currentItem;
    expect(item.state).toBe("PLACING");

    controller.cancelItem(admission.batchId, item.itemId);
    const saved = controller.getSnapshot().batches[0].slots[0];
    expect(saved.currentItem.state).toBe("SAVED_UNPLACED");
    expect(saved.canPlaceSavedUnplaced).toBe(true);
    expect(saved.canIgnore).toBe(true);
    scheduled.shift()?.();
    expect(promoted).toEqual([]);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("SAVED_UNPLACED");
  });

  it("offers batch stop before the placement callback starts and prevents its late graph write", async () => {
    const { controller, core, promoted, attached, scheduled } = harness();
    const admission = controller.enqueue(
      request([candidate("stop-before-place.png")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.resolve(revision(821));
    await flush();

    expect(controller.getSnapshot().batches[0]).toMatchObject({
      canStop: true,
      slots: [{ currentItem: { state: "PLACING", canCancel: true } }],
    });
    controller.stopBatch(admission.batchId);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("SAVED_UNPLACED");

    scheduled.shift()?.();
    expect(promoted).toEqual([]);
    expect(attached).toEqual([]);
  });

  it("treats cancellation as a no-op once synchronous promotion has begun", async () => {
    const { controller, core, promoted, scheduled, setPromoteHook } = harness();
    const admission = controller.enqueue(request([candidate("atomic-place.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.resolve(revision(83));
    await flush();
    const item = controller.getSnapshot().batches[0].slots[0].currentItem;
    setPromoteHook(() => controller.cancelItem(admission.batchId, item.itemId));

    scheduled.shift()?.();

    expect(promoted).toHaveLength(1);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("PLACED");
  });

  it("places an already saved revision despite a new graph conflict", async () => {
    const {
      controller,
      core,
      graph,
      promoted,
      targetLocks,
    } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    graph.loadEpoch = 8;
    core.attempts[0].result.resolve(revision(84));
    await flush();
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);

    graph.loadEpoch = 7;
    graph.conflict = true;
    const saved = controller.getSnapshot().batches[0].slots[0];
    controller.placeSavedUnplaced(admission.batchId, saved.slotId);

    expect(promoted).toHaveLength(1);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("PLACED");
  });

  it("reuses the stable target fallback reservation when placing a saved revision", async () => {
    const {
      controller,
      core,
      graph,
      obstacles,
      promoted,
      attached,
      idFactory,
    } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const original = controller.getSnapshot().batches[0].slots[0].reservation;
    await flush();
    graph.loadEpoch = 8;
    core.attempts[0].result.resolve(revision(841));
    await flush();
    graph.loadEpoch = 7;
    obstacles.push({
      x: original.position.x,
      y: original.position.y,
      width: original.width,
      height: original.height,
    });
    const idsBeforePlacement = idFactory.mock.calls.length;
    const saved = controller.getSnapshot().batches[0].slots[0];

    controller.placeSavedUnplaced(admission.batchId, saved.slotId);

    expect(attached).toEqual([]);
    expect(promoted).toEqual([
      expect.objectContaining({
        nodeId: original.nodeId,
        position: original.position,
        revisionId: "revision-841",
      }),
    ]);
    expect(idFactory).toHaveBeenCalledTimes(idsBeforePlacement);
    expect(
      controller.getSnapshot().batches[0].slots[0],
    ).toMatchObject({
      reservation: original,
      currentItem: { state: "PLACED" },
    });
  });

  it("keeps a saved revision and its stable fallback when promotion is rejected", async () => {
    const {
      controller,
      core,
      graph,
      promoted,
      idFactory,
      setPromoteResult,
    } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const original = controller.getSnapshot().batches[0].slots[0].reservation;
    await flush();
    graph.loadEpoch = 8;
    core.attempts[0].result.resolve(revision(842));
    await flush();
    graph.loadEpoch = 7;
    setPromoteResult(false);
    const idsBeforePlacement = idFactory.mock.calls.length;
    const saved = controller.getSnapshot().batches[0].slots[0];

    controller.placeSavedUnplaced(admission.batchId, saved.slotId);

    expect(promoted).toEqual([
      expect.objectContaining({
        nodeId: original.nodeId,
        position: original.position,
        revisionId: "revision-842",
      }),
    ]);
    expect(idFactory).toHaveBeenCalledTimes(idsBeforePlacement);
    expect(
      controller.getSnapshot().batches[0].slots[0],
    ).toMatchObject({
      reservation: original,
      currentItem: { state: "SAVED_UNPLACED" },
      canPlaceSavedUnplaced: true,
    });
  });

  it("re-gates saved placement after a project or load change", async () => {
    const { controller, core, graph, promoted } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    graph.loadEpoch = 8;
    core.attempts[0].result.resolve(revision(843));
    await flush();

    controller.placeSavedUnplaced(
      admission.batchId,
      controller.getSnapshot().batches[0].slots[0].slotId,
    );

    expect(promoted).toEqual([]);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("SAVED_UNPLACED");
  });

  it("keeps a saved revision and reservation until its exact local result deletion is confirmed", async () => {
    const { controller, core, graph } = harness();
    const admission = controller.enqueue(request([candidate("ignore-saved.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const slot = controller.getSnapshot().batches[0].slots[0];
    await flush();
    graph.loadEpoch = 8;
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.resolve(revision(85));
    await flush();
    const deletion = core.queueDiscard();

    controller.ignoreSlot(admission.batchId, slot.slotId);

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("DISCARDING");
    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(1);
    expect(core.discardCalls).toEqual([
      expect.objectContaining({
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId: "id-5",
        expectedRevisionId: "revision-85",
      }),
    ]);
    graph.loadEpoch = 7;
    controller.enqueue(request([candidate("avoid-live-position.png")]));
    expect(
      controller.getSnapshot().batches[0].slots[0].reservation.position,
    ).not.toEqual(slot.reservation.position);

    deletion.resolve({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });
    await flush();

    expect(
      controller
        .getSnapshot()
        .batches.find((batch) => batch.batchId === admission.batchId)
        ?.slots[0].currentItem.state,
    ).toBe("IGNORED");
  });

  it("uses a latched import id and null revision precondition when deleting a server-backed failure", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("failed.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.reject(new Error("probe failed"));
    await flush();
    const slot = controller.getSnapshot().batches[0].slots[0];
    const deletion = core.queueDiscard();

    controller.ignoreSlot(admission.batchId, slot.slotId);

    expect(core.discardCalls).toEqual([
      expect.objectContaining({
        importId: IMPORT_ID,
        clientImportId: slot.currentItem.clientImportId,
        expectedRevisionId: null,
      }),
    ]);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem,
    ).toMatchObject({
      state: "DISCARDING",
      errorMessage: "媒体导入失败，请重试",
      discardErrorMessage: null,
      canRetryDiscard: false,
    });

    deletion.resolve({
      httpStatus: 200,
      outcome: "already_discarded",
      contentCleanup: "retained_shared",
    });
    await flush();
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("IGNORED");
  });

  it("recovers the exact import id before deleting when the create response was lost", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("lost-create.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    const attempt = core.attempts[0];
    attempt.createDispatched = true;
    attempt.result.reject(new TypeError("create response lost"));
    await flush();
    const slot = controller.getSnapshot().batches[0].slots[0];
    const recovered = attempt.queueRecovery();
    const deletion = core.queueDiscard();

    controller.ignoreSlot(admission.batchId, slot.slotId);

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("DISCARDING");
    expect(core.discardCalls).toEqual([]);
    expect(attempt.recoveryCalls).toHaveLength(1);

    recovered.resolve(IMPORT_ID);
    await flush();
    expect(core.discardCalls).toEqual([
      expect.objectContaining({
        importId: IMPORT_ID,
        clientImportId: slot.currentItem.clientImportId,
        expectedRevisionId: null,
      }),
    ]);

    deletion.resolve({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });
    await flush();
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("IGNORED");
  });

  it("keeps an unconfirmed deletion guarded and retries the same idempotent owner request", async () => {
    const { controller, core, graph } = harness();
    const admission = controller.enqueue(request([candidate("pending.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const reservation =
      controller.getSnapshot().batches[0].slots[0].reservation;
    await flush();
    graph.loadEpoch = 8;
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.resolve(revision(86));
    await flush();
    const first = core.queueDiscard();
    const second = core.queueDiscard();

    controller.ignoreSlot(
      admission.batchId,
      controller.getSnapshot().batches[0].slots[0].slotId,
    );
    first.resolve({
      httpStatus: 202,
      outcome: "discarded",
      contentCleanup: "pending",
    });
    await flush();

    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(1);
    expect(controller.getSnapshot().batches[0].slots[0]).toMatchObject({
      reservation,
      currentItem: {
        state: "DISCARDING",
        discardErrorMessage: expect.stringContaining("清理待完成"),
        canRetryDiscard: true,
      },
    });

    controller.retryDiscard(
      admission.batchId,
      controller.getSnapshot().batches[0].slots[0].slotId,
    );
    expect(core.discardCalls).toHaveLength(2);
    expect(
      core.discardCalls.map(({ signal: _signal, ...call }) => call),
    ).toEqual([
      {
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId:
          controller.getSnapshot().batches[0].slots[0].currentItem
            .clientImportId,
        expectedRevisionId: "revision-86",
      },
      {
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId:
          controller.getSnapshot().batches[0].slots[0].currentItem
            .clientImportId,
        expectedRevisionId: "revision-86",
      },
    ]);
    second.resolve({
      httpStatus: 200,
      outcome: "already_discarded",
      contentCleanup: "not_present",
    });
    await flush();
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("IGNORED");
  });

  it.each([
    [
      "network",
      new TypeError("network unavailable"),
      "无法连接本地服务",
    ],
    [
      "404",
      new ApiRequestError(404, "not found"),
      "未找到当前项目中的精确导入结果",
    ],
    [
      "409 result_referenced",
      new ArtifactImportConflictError(IMPORT_ID, "result_referenced"),
      "仍被项目历史或其他结果引用",
    ],
    [
      "invalid response",
      new ApiRequestError(
        502,
        "Invalid API response from local service",
        true,
      ),
      "无效或不可用的删除响应",
    ],
    [
      "5xx",
      new ApiRequestError(503, "unavailable"),
      "暂时无法完成删除",
    ],
  ])("never releases the slot when %s leaves deletion unconfirmed", async (_label, error, safeMessage) => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("failure.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.reject(new Error("import failed"));
    await flush();
    const slot = controller.getSnapshot().batches[0].slots[0];
    const deletion = core.queueDiscard();
    controller.ignoreSlot(admission.batchId, slot.slotId);

    deletion.reject(error);
    await flush();

    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(1);
    expect(controller.getSnapshot().batches[0].slots[0]).toMatchObject({
      reservation: slot.reservation,
      currentItem: {
        state: "DISCARDING",
        discardErrorMessage: expect.stringContaining(safeMessage),
        canRetryDiscard: true,
      },
    });
    expect(JSON.stringify(controller.getSnapshot())).not.toContain(
      error.message,
    );
  });

  it("treats a malformed successful discard payload as unconfirmed", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("malformed.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.reject(new Error("failed"));
    await flush();
    const slot = controller.getSnapshot().batches[0].slots[0];
    const deletion = core.queueDiscard();
    controller.ignoreSlot(admission.batchId, slot.slotId);

    deletion.resolve({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "unexpected",
    } as never);
    await flush();

    expect(controller.getSnapshot().batches[0].slots[0]).toMatchObject({
      reservation: slot.reservation,
      currentItem: {
        state: "DISCARDING",
        canRetryDiscard: true,
        discardErrorMessage: expect.stringContaining(
          "无效或不可用的删除响应",
        ),
      },
    });
  });

  it("closes a server-backed failed batch only after every deletion is confirmed", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("close.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.reject(new Error("failed"));
    await flush();
    const deletion = core.queueDiscard();

    controller.closeBatch(admission.batchId);
    expect(controller.getSnapshot().batches).toHaveLength(1);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("DISCARDING");

    deletion.resolve({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });
    await flush();
    expect(controller.getSnapshot().batches).toEqual([]);
  });

  it("deletes a server-backed failed result before starting a reselected file with fresh ids", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("old.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.reject(new Error("failed"));
    await flush();
    const before = controller.getSnapshot().batches[0].slots[0];
    const deletion = core.queueDiscard();

    controller.reselect(
      admission.batchId,
      before.slotId,
      file("private-retry.png"),
    );

    expect(core.inputs).toHaveLength(1);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("DISCARDING");
    expect(JSON.stringify(controller.getSnapshot())).not.toContain(
      "private-retry.png",
    );

    deletion.resolve({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });
    await flush();

    const retried = controller.getSnapshot().batches[0].slots[0];
    expect(retried.reservation).toEqual(before.reservation);
    expect(retried.currentItem).toMatchObject({
      state: "INITIALIZING",
      displayName: "private-retry.png",
    });
    expect(retried.currentItem.itemId).not.toBe(before.currentItem.itemId);
    expect(retried.currentItem.clientImportId).not.toBe(
      before.currentItem.clientImportId,
    );
    expect(retried.previousAttempts).toEqual([before.currentItem]);
    expect(core.inputs).toHaveLength(2);
  });

  it("moves a later batch as a whole around every earlier live reservation", () => {
    const { controller } = harness();
    controller.enqueue(request([candidate("first.png"), candidate("second.png")]));
    const earlier = controller.getSnapshot().batches[0];

    controller.enqueue(request([candidate("later-a.png"), candidate("later-b.png")]));
    const [later, stillEarlier] = controller.getSnapshot().batches;

    expect(stillEarlier.slots.map((slot) => slot.reservation.position)).toEqual(
      earlier.slots.map((slot) => slot.reservation.position),
    );
    expect(later.slots.map((slot) => slot.reservation.position)).toEqual([
      { x: 100, y: 600 },
      { x: 452, y: 600 },
    ]);
  });

  it("keeps a failed reservation live until its ordinary failed batch is closed", () => {
    const { controller } = harness();
    const firstAdmission = controller.enqueue(
      request([candidate("unknown.bin", 4, "application/octet-stream")]),
    );
    expect(firstAdmission.kind).toBe("accepted");
    if (firstAdmission.kind !== "accepted") return;

    controller.enqueue(request([candidate("blocked.png")]));
    expect(controller.getSnapshot().batches[0].slots[0].reservation.position).toEqual({
      x: 460,
      y: 200,
    });

    controller.closeBatch(firstAdmission.batchId);
    controller.enqueue(request([candidate("released.png")]));
    expect(controller.getSnapshot().batches[0].slots[0].reservation.position).toEqual({
      x: 100,
      y: 200,
    });
  });

  it("does not publish or notify when an attempt repeats the same network phase", async () => {
    const { controller, core } = harness();
    controller.enqueue(request([candidate("phase.png")]));
    await flush();
    const listener = vi.fn();
    controller.subscribe(listener);

    core.attempts[0].report("receiving");
    const receiving = controller.getSnapshot();
    expect(listener).toHaveBeenCalledOnce();

    core.attempts[0].report("receiving");
    expect(controller.getSnapshot()).toBe(receiving);
    expect(listener).toHaveBeenCalledOnce();

    core.attempts[0].report("validating");
    const validating = controller.getSnapshot();
    expect(validating).not.toBe(receiving);
    expect(listener).toHaveBeenCalledTimes(2);

    core.attempts[0].report("validating");
    expect(controller.getSnapshot()).toBe(validating);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("exposes PLACING before atomically promoting the exact reservation to PLACED", async () => {
    const { controller, core, promoted, scheduled } = harness();
    controller.enqueue(request([candidate("placed.png")]));
    await flush();

    core.attempts[0].result.resolve(revision(1));
    await flush();
    const placing = controller.getSnapshot().batches[0].slots[0];
    expect(placing.currentItem.state).toBe("PLACING");
    expect(promoted).toEqual([]);
    expect(scheduled).toHaveLength(1);

    scheduled.shift()?.();
    const placed = controller.getSnapshot().batches[0].slots[0];
    expect(placed.currentItem.state).toBe("PLACED");
    expect(promoted).toEqual([
      {
        projectId: PROJECT_ID,
        loadEpoch: 7,
        nodeId: placing.reservation.nodeId,
        position: placing.reservation.position,
        revisionId: "revision-1",
        mediaKind: "image",
      },
    ]);
    expect(core.attempts[0].released).toBe(true);
  });

  it("lets a promoted formal node replace its reservation without retaining a ghost obstacle", async () => {
    const { controller, core, obstacles, scheduled } = harness();
    controller.enqueue(request([candidate("placed.png")]));
    await flush();
    core.attempts[0].result.resolve(revision(1));
    await flush();
    scheduled.shift()?.();

    obstacles[0] = { x: 2_000, y: 2_000, width: 312, height: 360 };
    controller.enqueue(request([candidate("at-old-anchor.png")]));

    expect(controller.getSnapshot().batches[0].slots[0].reservation.position).toEqual({
      x: 100,
      y: 200,
    });
  });

  it("isolates an ordinary failure and starts the next queued slot without promotion", async () => {
    const { controller, core, promoted } = harness();
    controller.enqueue(
      request(Array.from({ length: 4 }, (_, index) => candidate(`${index}.png`))),
    );
    await flush();

    core.attempts[0].result.reject(new Error("upload failed"));
    await flush();
    const slots = controller.getSnapshot().batches[0].slots;
    expect(slots[0].currentItem.state).toBe("FAILED");
    expect(slots[0].currentItem.errorMessage).toBe("媒体导入失败，请重试");
    expect(slots[3].currentItem.state).toBe("INITIALIZING");
    expect(promoted).toEqual([]);
    expect(core.attempts[0].released).toBe(true);
  });

  it("records a completed revision as SAVED_UNPLACED if project/load epoch changed", async () => {
    const { controller, core, graph, promoted, scheduled } = harness();
    controller.enqueue(request([candidate("late.png")]));
    await flush();
    graph.loadEpoch = 8;

    core.attempts[0].result.resolve(revision(1));
    await flush();
    expect(scheduled).toHaveLength(0);
    expect(controller.getSnapshot().batches[0].slots[0].currentItem.state).toBe(
      "SAVED_UNPLACED",
    );
    expect(promoted).toEqual([]);
  });

  it("keeps SAVED_UNPLACED as a live obstacle without moving its stable reservation", async () => {
    const { controller, core, graph } = harness();
    controller.enqueue(request([candidate("saved.png")]));
    const savedPosition =
      controller.getSnapshot().batches[0].slots[0].reservation.position;
    await flush();
    graph.loadEpoch = 8;
    core.attempts[0].result.resolve(revision(1));
    await flush();
    expect(controller.getSnapshot().batches[0].slots[0].reservation.position).toEqual(
      savedPosition,
    );

    graph.loadEpoch = 7;
    controller.enqueue(request([candidate("later.png")]));
    expect(controller.getSnapshot().batches[0].slots[0].reservation.position).toEqual({
      x: 460,
      y: 200,
    });
    expect(controller.getSnapshot().batches[1].slots[0].reservation.position).toEqual(
      savedPosition,
    );
  });

  it("keeps unchanged snapshots referentially stable and publishes only frozen safe projections", () => {
    const { controller } = harness();
    const empty = controller.getSnapshot();
    expect(controller.getSnapshot()).toBe(empty);
    controller.enqueue(request([candidate("C:/private/token-secret.png")]));
    const snapshot = controller.getSnapshot();
    expect(controller.getSnapshot()).toBe(snapshot);
    expect(snapshot).not.toBe(empty);
    expect(Object.isFrozen(snapshot)).toBe(true);
    expect(Object.isFrozen(snapshot.batches)).toBe(true);
    expect(Object.isFrozen(snapshot.batches[0].slots[0].currentItem)).toBe(true);
    const json = JSON.stringify(snapshot);
    expect(json).not.toContain("AbortController");
    expect(json).not.toContain("C:/private");
    expect(json).not.toContain("content");
    expect(json).toContain("token-secret.png");
  });

  it("redacts local paths, ffprobe commands, and stack fragments from failed import messages", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("unsafe.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();

    core.attempts[0].result.reject(
      new Error(
        "ffprobe.exe C:\\Users\\creator\\secret.mp4\n    at D:\\tools\\probe.ts:12:4",
      ),
    );
    await flush();

    const errorMessage = controller.getSnapshot().batches[0].slots[0].currentItem
      .errorMessage;
    expect(errorMessage).toBe("媒体导入失败，请重试");
    expect(errorMessage).not.toContain("C:\\Users");
    expect(errorMessage).not.toContain("ffprobe");
    expect(errorMessage).not.toContain(" at ");
  });

  it.each([
    [
      "a file URI in a generic Error",
      new Error("internal failure:file:///C:/Users/alice/private/clip.mp4"),
      "file:///C:/Users/alice/private/clip.mp4",
    ],
    [
      "an unspaced Unix path in an API Error",
      new ApiRequestError(500, "internal failure:/var/private/clip.mp4"),
      "/var/private/clip.mp4",
    ],
  ])("never exposes %s from an unknown import failure", async (_label, error, secret) => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("unsafe-path.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();

    core.attempts[0].result.reject(error);
    await flush();

    const errorMessage = controller.getSnapshot().batches[0].slots[0].currentItem
      .errorMessage;
    expect(errorMessage).toBe("媒体导入失败，请重试");
    expect(errorMessage).not.toContain(secret);
  });

  it.each([
    [
      "a declaration validation failure",
      new ArtifactDeclarationValidationError("untrusted declaration detail"),
      "所选文件的名称或媒体类型声明无效，请重新选择文件。",
    ],
    [
      "a typed artifact failure",
      new ArtifactImportFailedError(IMPORT_ID, "probe_timeout"),
      "素材检查超时，可以重试。",
    ],
    [
      "a typed import conflict",
      new ArtifactImportConflictError(IMPORT_ID, "upload_in_progress"),
      "同一文件仍在处理中，请稍候。",
    ],
    [
      "an expired session",
      new SessionExpiredError(),
      "本地会话已失效，请从 AI Canvas 启动器重新打开。",
    ],
  ])("keeps the safe semantic message for %s", async (_label, error, expected) => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("typed-failure.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();

    core.attempts[0].result.reject(error);
    await flush();

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.errorMessage,
    ).toBe(expected);
  });

  it("keeps protected target snapshots live and clears pending public state on dispose", async () => {
    const { controller, core } = harness();
    const before = controller.getSnapshot();
    controller.enqueue(request([candidate("pending.png")]));
    await flush();
    expect(core.attempts).toHaveLength(1);
    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(1);

    controller.dispose();

    expect(controller.getSnapshot()).not.toBe(before);
    expect(controller.getSnapshot().batches).toEqual([]);
    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(0);
  });

  it("releases files, controllers, permits, target leases, and subscriptions directly on dispose", async () => {
    const { controller, core, permits, targetLocks } = harness();
    const actualSubscribe = targetLocks.subscribe.bind(targetLocks);
    let unsubscribeCalls = 0;
    vi.spyOn(targetLocks, "subscribe").mockImplementation((listener) => {
      const cleanup = actualSubscribe(listener);
      return () => {
        unsubscribeCalls += 1;
        cleanup();
      };
    });
    controller.mount();
    const fill = controller.enqueue(fillRequest("empty-media"));
    const batch = controller.enqueue(
      request([
        candidate("dispose-a.png"),
        candidate("dispose-b.png"),
        candidate("dispose-c.png"),
      ]),
    );
    expect(fill.kind).toBe("accepted");
    expect(batch.kind).toBe("accepted");
    if (fill.kind !== "accepted" || batch.kind !== "accepted") return;
    await flush();
    expect(permits.getSnapshot().runningOperationIds).toHaveLength(3);
    expect(permits.getSnapshot().queuedOperationIds).toHaveLength(1);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual(["empty-media"]);

    const activeItem = controller.getSnapshot().batches
      .find((candidateBatch) => candidateBatch.batchId === fill.batchId)!
      .slots[0].currentItem;
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].contentPutDispatched = true;
    core.attempts[0].queueReconciliation();
    controller.cancelItem(fill.batchId, activeItem.itemId);
    await flush();
    expect(permits.getSnapshot().queueHeld).toBe(true);

    controller.dispose();

    expect(core.attempts.every((attempt) => attempt.released)).toBe(true);
    expect(core.attempts.every((attempt) => attempt.signal?.aborted)).toBe(true);
    expect(permits.getSnapshot()).toEqual({
      runningOperationIds: [],
      queuedOperationIds: [],
      queueHeld: false,
    });
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(unsubscribeCalls).toBe(1);
    expect(controller.getSnapshot()).toMatchObject({
      batches: [],
      pendingTargetChoices: [],
      protectedTargetNodeIds: [],
      guardState: { pendingOperationCount: 0, hasSavedUnplaced: false },
    });
  });

  it("publishes target-lock acquire and release changes without a batch transition", () => {
    const { controller, targetLocks } = harness();
    controller.mount();
    const initial = controller.getSnapshot();
    const lease = targetLocks.acquire("b1-operation", "existing-media");
    expect(controller.getSnapshot()).not.toBe(initial);
    expect(controller.getSnapshot().protectedTargetNodeIds).toEqual([
      "existing-media",
    ]);

    lease?.release();
    expect(controller.getSnapshot().protectedTargetNodeIds).toEqual([]);
  });

  it("reselects a failed slot in place with new ids and immutable safe history", async () => {
    const { controller, core, scheduled, promoted } = harness();
    const admission = controller.enqueue(
      request([candidate("broken.bin", 4, "application/octet-stream")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;

    const before = controller.getSnapshot().batches[0].slots[0];
    expect(before.currentItem.state).toBe("FAILED");
    expect(before.canReselect).toBe(true);
    expect(before.canIgnore).toBe(true);

    controller.reselect(
      admission.batchId,
      before.slotId,
      file("C:/private/replacement.png"),
    );

    const retried = controller.getSnapshot().batches[0].slots[0];
    expect(retried.reservation).toEqual(before.reservation);
    expect(retried.currentItem.itemId).not.toBe(before.currentItem.itemId);
    expect(retried.currentItem.clientImportId).not.toBe(
      before.currentItem.clientImportId,
    );
    expect(retried.previousAttempts).toEqual([before.currentItem]);
    expect(retried.attemptCount).toBe(2);
    expect(Object.isFrozen(retried.previousAttempts)).toBe(true);
    expect(Object.isFrozen(retried.previousAttempts[0])).toBe(true);
    expect(JSON.stringify(retried)).not.toContain("C:/private");

    await flush();
    core.attempts.at(-1)?.result.resolve(revision(20));
    await flush();
    scheduled.shift()?.();

    expect(promoted.at(-1)?.position).toEqual(before.reservation.position);
    expect(controller.getSnapshot().batches[0].slots[0].currentItem.state).toBe(
      "PLACED",
    );
  });

  it("fully releases a failed attempt and ignores its late phase callbacks", async () => {
    const { controller, core, permits } = harness();
    controller.enqueue(request([candidate("first.png")]));
    await flush();
    const failedAttempt = core.attempts[0];

    failedAttempt.result.reject(new Error("network unavailable"));
    await flush();

    expect(failedAttempt.released).toBe(true);
    expect(failedAttempt.signal?.aborted).toBe(true);
    expect(permits.getSnapshot().runningOperationIds).toEqual([]);
    const failed = controller.getSnapshot().batches[0].slots[0];

    controller.reselect(
      controller.getSnapshot().batches[0].batchId,
      failed.slotId,
      file("second.png"),
    );
    const retried = controller.getSnapshot().batches[0].slots[0];
    const listener = vi.fn();
    controller.subscribe(listener);
    const beforeLatePhase = controller.getSnapshot();

    failedAttempt.report("receiving");

    expect(controller.getSnapshot()).toBe(beforeLatePhase);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.itemId,
    ).toBe(retried.currentItem.itemId);
    expect(listener).not.toHaveBeenCalled();
  });

  it("latches a changed target across A to B to A and never attaches the retry", async () => {
    const {
      controller,
      core,
      mediaTargets,
      targetLocks,
      attached,
      scheduled,
    } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.reject(new Error("first attempt failed"));
    await flush();

    const failed = controller.getSnapshot().batches[0].slots[0];
    mediaTargets.set("empty-media", {
      revisionId: "revision-b",
      mediaKind: "image",
    });
    controller.reselect(
      admission.batchId,
      failed.slotId,
      file("retry.png"),
    );

    const queued = controller.getSnapshot().batches[0].slots[0];
    expect(queued.currentItem.errorMessage).toContain(
      "目标已变化，成功后需决定是否作为新节点放置",
    );
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);

    mediaTargets.set("empty-media", { revisionId: null, mediaKind: null });
    await flush();
    core.attempts.at(-1)?.result.resolve(revision(30));
    await flush();

    expect(scheduled).toEqual([]);
    expect(attached).toEqual([]);
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem,
    ).toMatchObject({
      state: "SAVED_UNPLACED",
      errorMessage: expect.stringContaining(
        "目标已变化，成功后需决定是否作为新节点放置",
      ),
    });
  });

  it("reacquires an exact target lock and attaches a successful retry without relayout", async () => {
    const {
      controller,
      core,
      targetLocks,
      attached,
      scheduled,
    } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const reservation =
      controller.getSnapshot().batches[0].slots[0].reservation;
    await flush();
    core.attempts[0].result.reject(new Error("first attempt failed"));
    await flush();
    const failed = controller.getSnapshot().batches[0].slots[0];

    controller.reselect(
      admission.batchId,
      failed.slotId,
      file("retry.png"),
    );

    expect(
      controller.getSnapshot().batches[0].slots[0].reservation,
    ).toEqual(reservation);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "empty-media",
    ]);
    await flush();
    expect(core.attempts).toHaveLength(2);
    core.attempts[1].result.resolve(revision(31));
    await flush();
    scheduled.shift()?.();

    expect(attached).toEqual([
      expect.objectContaining({
        nodeId: "empty-media",
        expectedRevisionId: null,
        revisionId: "revision-31",
      }),
    ]);
    expect(
      controller.getSnapshot().batches[0].slots[0],
    ).toMatchObject({
      reservation,
      currentItem: { state: "PLACED" },
    });
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("leaves the old failure untouched when an exact retry target is busy", async () => {
    const { controller, core, targetLocks } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.reject(new Error("first attempt failed"));
    await flush();
    const before = controller.getSnapshot().batches[0].slots[0];
    const competing = targetLocks.acquire("other-operation", "empty-media");
    expect(competing).not.toBeNull();

    controller.reselect(
      admission.batchId,
      before.slotId,
      file("retry.png"),
    );
    await flush();

    const after = controller.getSnapshot().batches[0].slots[0];
    expect(after.currentItem).toEqual(before.currentItem);
    expect(after.previousAttempts).toEqual(before.previousAttempts);
    expect(after.attemptCount).toBe(before.attemptCount);
    expect(core.attempts).toHaveLength(1);
    competing?.release();
  });

  it("rolls back a retry target lease when Core throws synchronously", async () => {
    const { controller, core, targetLocks } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.reject(new Error("first attempt failed"));
    await flush();
    const before = controller.getSnapshot().batches[0].slots[0];
    core.throwNext = new Error("Core creation failed");

    controller.reselect(
      admission.batchId,
      before.slotId,
      file("retry.png"),
    );
    await flush();

    const after = controller.getSnapshot().batches[0].slots[0];
    expect(after.currentItem).toMatchObject({
      state: "FAILED",
      displayName: "retry.png",
      errorMessage: "媒体导入失败，请重试",
    });
    expect(after.currentItem.itemId).not.toBe(before.currentItem.itemId);
    expect(after.previousAttempts).toEqual([
      ...before.previousAttempts,
      before.currentItem,
    ]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(core.attempts).toHaveLength(1);
  });

  it("lets an empty target retry with a different valid media kind while preserving its fallback", async () => {
    const { controller, core, targetLocks } = harness();
    const admission = controller.enqueue(fillRequest("empty-media"));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.reject(new Error("first attempt failed"));
    await flush();
    const firstFailure = controller.getSnapshot().batches[0].slots[0];

    controller.reselect(
      admission.batchId,
      firstFailure.slotId,
      file("retry-video.webm", 4, "video/webm"),
    );

    const retry = controller.getSnapshot().batches[0].slots[0];
    expect(retry.slotId).toBe(firstFailure.slotId);
    expect(retry.reservation).toEqual(firstFailure.reservation);
    expect(retry.currentItem.itemId).not.toBe(
      firstFailure.currentItem.itemId,
    );
    expect(retry.currentItem.clientImportId).not.toBe(
      firstFailure.currentItem.clientImportId,
    );
    expect(retry.currentItem).toMatchObject({
      state: "QUEUED",
      mediaKind: "video",
      errorMessage: null,
    });
    expect(retry.previousAttempts).toEqual([firstFailure.currentItem]);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([
      "empty-media",
    ]);

    await flush();
    expect(core.attempts).toHaveLength(2);
    expect(core.inputs[1].declaration).toMatchObject({
      requestedMediaKind: "video",
      replaceRevisionId: null,
    });
  });

  it("still rejects a cross-kind retry for an existing revision replacement", async () => {
    const { controller, core, targetLocks } = harness();
    const choice = controller.enqueue(
      choiceRequest("filled-media", "revision-current"),
    );
    expect(choice.kind).toBe("choice_required");
    if (choice.kind !== "choice_required") return;
    const admission = controller.resolveTargetChoice(
      choice.choiceId,
      "replace",
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.reject(new Error("first attempt failed"));
    await flush();
    const firstFailure = controller.getSnapshot().batches[0].slots[0];

    controller.reselect(
      admission.batchId,
      firstFailure.slotId,
      file("wrong-kind.webm", 4, "video/webm"),
    );

    const wrongKind = controller.getSnapshot().batches[0].slots[0];
    expect(wrongKind.currentItem).toMatchObject({
      state: "FAILED",
      mediaKind: "video",
      errorMessage: expect.any(String),
    });
    expect(wrongKind.previousAttempts).toEqual([firstFailure.currentItem]);
    expect(core.attempts).toHaveLength(1);
    expect(targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
  });

  it("allows an ordinary create slot to retry from an image file to a valid video file", async () => {
    const { controller, core, promoted, scheduled } = harness();
    const admission = controller.enqueue(
      request([candidate("first-image.png")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].result.reject(new Error("image import failed"));
    await flush();
    const failed = controller.getSnapshot().batches[0].slots[0];

    controller.reselect(
      admission.batchId,
      failed.slotId,
      file("replacement-video.webm", 4, "video/webm"),
    );

    const queued = controller.getSnapshot().batches[0].slots[0];
    expect(queued.reservation).toEqual(failed.reservation);
    expect(queued.currentItem).toMatchObject({
      state: "QUEUED",
      mediaKind: "video",
      errorMessage: null,
    });
    expect(queued.previousAttempts).toEqual([failed.currentItem]);

    await flush();
    expect(core.attempts).toHaveLength(2);
    expect(core.inputs.at(-1)?.declaration.requestedMediaKind).toBe(
      "video",
    );
    core.attempts[1].result.resolve(revision(50, "video"));
    await flush();
    scheduled.shift()?.();

    expect(promoted.at(-1)).toMatchObject({
      position: failed.reservation.position,
      mediaKind: "video",
      revisionId: "revision-50",
    });
    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("PLACED");
  });

  it("re-gates failed-slot retries before ids, Core, or public File retention", () => {
    const scenarios: Array<
      (context: ReturnType<typeof harness>) => void
    > = [
      ({ graph }) => {
        graph.projectId = "22222222-2222-4222-8222-222222222222";
      },
      ({ graph }) => {
        graph.loadEpoch = 8;
      },
      ({ graph }) => {
        graph.conflict = true;
        graph.status = "conflict";
      },
    ];

    for (const mutate of scenarios) {
      const context = harness();
      const admission = context.controller.enqueue(
        request([
          candidate(
            "unsupported.bin",
            4,
            "application/octet-stream",
          ),
        ]),
      );
      expect(admission.kind).toBe("accepted");
      if (admission.kind !== "accepted") continue;
      const slot = context.controller.getSnapshot().batches[0].slots[0];
      const before = context.controller.getSnapshot();
      const idsBefore = context.idFactory.mock.calls.length;
      mutate(context);

      context.controller.reselect(
        admission.batchId,
        slot.slotId,
        file("C:/private/retry.png"),
      );

      expect(context.controller.getSnapshot()).toBe(before);
      expect(context.idFactory).toHaveBeenCalledTimes(idsBefore);
      expect(context.core.inputs).toEqual([]);
      expect(JSON.stringify(context.controller.getSnapshot())).not.toContain(
        "C:/private",
      );
    }
  });

  it("revalidates the 10 GiB item limit on every failed-slot retry", () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(
      request([candidate("unsupported.bin", 4, "application/octet-stream")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const before = controller.getSnapshot().batches[0].slots[0];

    controller.reselect(
      admission.batchId,
      before.slotId,
      file("oversized.png", 10_737_418_241),
    );

    const after = controller.getSnapshot().batches[0].slots[0];
    expect(after.currentItem).toMatchObject({
      state: "FAILED",
      errorMessage: expect.stringContaining("10 GiB"),
    });
    expect(after.previousAttempts).toEqual([before.currentItem]);
    expect(after.attemptCount).toBe(2);
    expect(core.inputs).toEqual([]);
  });

  it("treats a second reselect after the slot has queued as a complete no-op", () => {
    const { controller, idFactory } = harness();
    const admission = controller.enqueue(
      request([candidate("unsupported.bin", 4, "application/octet-stream")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const failed = controller.getSnapshot().batches[0].slots[0];

    controller.reselect(
      admission.batchId,
      failed.slotId,
      file("first-retry.png"),
    );
    const firstRetry = controller.getSnapshot();
    const idsAfterFirstRetry = idFactory.mock.calls.length;

    controller.reselect(
      admission.batchId,
      failed.slotId,
      file("C:/private/second-retry.png"),
    );

    expect(controller.getSnapshot()).toBe(firstRetry);
    expect(idFactory).toHaveBeenCalledTimes(idsAfterFirstRetry);
    expect(JSON.stringify(controller.getSnapshot())).not.toContain(
      "second-retry",
    );
  });

  it("ignores only local preflight failures synchronously without a delete request", () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(
      request([candidate("unsupported.bin", 4, "application/octet-stream")]),
    );
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    const failed = controller.getSnapshot().batches[0].slots[0];
    const listener = vi.fn();
    controller.subscribe(listener);

    controller.ignoreSlot(admission.batchId, failed.slotId);

    expect(
      controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("IGNORED");
    expect(listener).toHaveBeenCalledOnce();
    expect(core.discardCalls).toEqual([]);
    controller.ignoreSlot(admission.batchId, failed.slotId);
    expect(listener).toHaveBeenCalledOnce();

    controller.enqueue(request([candidate("reuses-anchor.png")]));
    expect(
      controller.getSnapshot().batches[0].slots[0].reservation.position,
    ).toEqual(failed.reservation.position);
  });

  it("aborts an in-flight discard and ignores its late completion after dispose", async () => {
    const { controller, core } = harness();
    const admission = controller.enqueue(request([candidate("dispose.png")]));
    expect(admission.kind).toBe("accepted");
    if (admission.kind !== "accepted") return;
    await flush();
    core.attempts[0].importId = IMPORT_ID;
    core.attempts[0].result.reject(new Error("failed"));
    await flush();
    const slot = controller.getSnapshot().batches[0].slots[0];
    const deletion = core.queueDiscard();
    controller.ignoreSlot(admission.batchId, slot.slotId);
    const signal = core.discardCalls[0].signal;

    controller.dispose();

    expect(signal.aborted).toBe(true);
    expect(controller.getSnapshot().batches).toEqual([]);
    deletion.resolve({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });
    await flush();
    expect(controller.getSnapshot().batches).toEqual([]);
  });

  it("closes terminal failed batches as ignore but refuses running or saved batches", async () => {
    const running = harness();
    const runningAdmission = running.controller.enqueue(
      request([candidate("running.png")]),
    );
    expect(runningAdmission.kind).toBe("accepted");
    if (runningAdmission.kind !== "accepted") return;
    running.controller.closeBatch(runningAdmission.batchId);
    expect(running.controller.getSnapshot().batches).toHaveLength(1);

    await flush();
    running.graph.loadEpoch = 8;
    running.core.attempts[0].result.resolve(revision(40));
    await flush();
    expect(
      running.controller.getSnapshot().batches[0].slots[0].currentItem.state,
    ).toBe("SAVED_UNPLACED");
    running.controller.closeBatch(runningAdmission.batchId);
    expect(running.controller.getSnapshot().batches).toHaveLength(1);

    const failed = harness();
    const failedAdmission = failed.controller.enqueue(
      request([candidate("unsupported.bin", 4, "application/octet-stream")]),
    );
    expect(failedAdmission.kind).toBe("accepted");
    if (failedAdmission.kind !== "accepted") return;
    failed.controller.closeBatch(failedAdmission.batchId);
    expect(failed.controller.getSnapshot().batches).toEqual([]);
    failed.controller.enqueue(request([candidate("after-close.png")]));
    expect(
      failed.controller.getSnapshot().batches[0].slots[0].reservation.position,
    ).toEqual({ x: 100, y: 200 });
  });
});
