import {
  ApiRequestError,
  ArtifactDeclarationValidationError,
  ArtifactImportConflictError,
  ArtifactImportFailedError,
  SessionExpiredError,
  type ArtifactRevision,
} from "../../../api/client";
import { useCanvasStore } from "../canvasStore";
import type { MediaKind, SaveStatus } from "../canvasTypes";
import type {
  CancelReconciliation,
  CreateMediaImportAttemptInput,
  MediaImportAttempt,
  MediaImportCore,
} from "./mediaImportCore";
import type { MediaImportPermit, MediaImportPermitBroker } from "./mediaImportPermitBroker";
import type { MediaTargetLease, MediaTargetLocks } from "./mediaTargetLocks";
import { layoutReservations, type MediaIngressObstacle } from "./mediaIngressLayout";
import { preflightBatch, validateCandidate } from "./mediaIngressInput";
import type {
  IngressBatchCounts,
  IngressCandidate,
  IngressItemSnapshot,
  IngressItemState,
  IngressSlotSnapshot,
  MediaIngressAdmission,
  MediaIngressController,
  MediaIngressRequest,
  MediaIngressSnapshot,
  PendingTargetChoiceSnapshot,
  PromoteMediaReservationInput,
  TargetBinding,
} from "./mediaIngressTypes";

export type MediaIngressCanvasBinding = Readonly<{
  projectId: string | null;
  loadEpoch: number;
  conflict: boolean;
  status?: SaveStatus;
  editSequence?: number;
  savedSequence?: number;
  baseRevisionId?: string | null;
}>;
export type MediaIngressTarget = Readonly<{
  revisionId: string | null;
  mediaKind: MediaKind | null;
}>;
export type AttachMediaIngressTargetInput = Readonly<{
  projectId: string;
  loadEpoch: number;
  nodeId: string;
  expectedRevisionId: string | null;
  revisionId: string;
  mediaKind: MediaKind;
}>;
export interface MediaIngressCanvasAdapter {
  getBinding(): MediaIngressCanvasBinding;
  getMediaTarget?(nodeId: string): MediaIngressTarget | null;
  getObstacles(): readonly MediaIngressObstacle[];
  attach?(input: AttachMediaIngressTargetInput): boolean;
  promote(input: PromoteMediaReservationInput): boolean;
}
export type CreateMediaIngressControllerInput = Readonly<{
  projectId: string;
  loadEpoch: number;
  core: MediaImportCore;
  permits: MediaImportPermitBroker;
  targetLocks: MediaTargetLocks;
  canvas: MediaIngressCanvasAdapter;
  idFactory?: () => string;
  schedulePlacement?: (callback: () => void) => void;
  confirmForceRefresh?: () => boolean;
  abandonAllNow?: () => void;
}>;

type RuntimeItem = {
  itemId: string;
  clientImportId: string;
  state: IngressItemState;
  displayName: string;
  mediaKind: MediaKind | null;
  errorMessage: string | null;
  file: File | null;
  declaration: CreateMediaImportAttemptInput["declaration"] | null;
  attempt: MediaImportAttempt | null;
  recoveryAttempt: MediaImportAttempt | null;
  controller: AbortController | null;
  observerController: AbortController | null;
  discardController: AbortController | null;
  permit: MediaImportPermit | null;
  targetLease: MediaTargetLease | null;
  importId: string | null;
  completedRevision: ArtifactRevision | null;
  discardErrorMessage: string | null;
  discardOriginSnapshot: IngressItemSnapshot | null;
  discardIntent: "ignore" | "close_batch" | "reselect" | null;
  pendingReselection: File | null;
  discardGeneration: number;
  cancelGeneration: number;
  observerGeneration: number;
  queueHoldOwned: boolean;
  placementStarted: boolean;
  isLargeUpload: boolean;
  largeUploadProgress: {
    receivedByteSize: number;
    totalByteSize: number;
    startedAtMilliseconds: number;
  } | null;
};
type RuntimeSlot = {
  slotId: string;
  reservation: {
    nodeId: string;
    position: { x: number; y: number };
    width: 312;
    height: 360;
  };
  item: RuntimeItem;
  previousAttempts: IngressItemSnapshot[];
  targetBinding: TargetBinding | null;
  targetBindingStale: boolean;
  expectedMediaKind: MediaKind | null;
};
type RuntimeBatch = {
  batchId: string;
  source: MediaIngressRequest["source"];
  createdOrder: number;
  collapsed: boolean;
  closeRequested: boolean;
  slots: RuntimeSlot[];
};
type RuntimeTargetChoice = {
  choiceId: string;
  projectId: string;
  loadEpoch: number;
  source: MediaIngressRequest["source"];
  displayName: string;
  requestedMediaKind: MediaKind | null;
  currentMediaKind: MediaKind;
  replacementBlockedReason: string | null;
  targetBinding: Extract<
    TargetBinding,
    Readonly<{ expectedRevisionId: string }>
  >;
  anchor: { x: number; y: number };
  file: File | null;
  targetLease: MediaTargetLease | null;
};

const EMPTY_SNAPSHOT: MediaIngressSnapshot = Object.freeze({
  batches: Object.freeze([]),
  pendingTargetChoices: Object.freeze([]),
  protectedTargetNodeIds: Object.freeze([]),
  guardState: Object.freeze({ pendingOperationCount: 0, hasSavedUnplaced: false }),
});
const PROCESSING = new Set<IngressItemState>([
  "INITIALIZING",
  "RECEIVING",
  "VALIDATING",
  "CANCELLING",
  "DISCARDING",
  "PLACING",
]);
const TERMINAL = new Set<IngressItemState>([
  "PLACED",
  "FAILED",
  "CANCELLED",
  "SAVED_UNPLACED",
  "IGNORED",
]);
const RELEASED_RESERVATION = new Set<IngressItemState>([
  "PLACED",
  "CANCELLED",
  "IGNORED",
]);
const PROJECT_CHANGED_MESSAGE = "当前项目已发生变化";
const CONFLICT_MESSAGE = "请先处理画布版本冲突";
const TARGET_CHANGED_MESSAGE = "目标媒体节点已发生变化";
const TARGET_BUSY_MESSAGE = "该媒体节点正在处理另一项导入";
const REPLACEMENT_KIND_MESSAGE = "仅可替换为相同媒体类型";
const REPLACEMENT_SAVE_MESSAGE = "请先保存画布后再替换";
const REPLACEMENT_BASE_MESSAGE = "当前画布没有可用于替换的已保存版本";
const TARGET_STALE_RESELECT_MESSAGE =
  "目标已变化，成功后需决定是否作为新节点放置";
const CANCELLATION_UNKNOWN_MESSAGE =
  "暂时无法确认服务端导入结果，请重试确认或确认后强制刷新";
const DISCARD_UNKNOWN_MESSAGE =
  "删除结果未确认，素材和占位仍保留，请重试";
const DISCARD_PENDING_MESSAGE =
  "本地素材清理待完成，素材和占位仍保留，请重试";
const DISCARD_REFERENCED_MESSAGE =
  "本地素材仍被项目历史或其他结果引用，当前无法删除；请先处理相关引用后重试";
const DISCARD_NETWORK_MESSAGE =
  "无法连接本地服务，素材和占位仍保留；请检查本地服务后重试";
const DISCARD_NOT_FOUND_MESSAGE =
  "未找到当前项目中的精确导入结果，未执行删除；请确认项目状态后重试";
const DISCARD_INVALID_RESPONSE_MESSAGE =
  "本地服务返回了无效或不可用的删除响应，素材和占位仍保留；请确认工作台版本后重试";
const DISCARD_SERVER_MESSAGE =
  "本地服务暂时无法完成删除，素材和占位仍保留；请稍后重试";
const DISCARD_CONFLICT_MESSAGE =
  "导入结果状态已变化，当前未完成删除；请保留当前页面并重试";
const SAFE_IMPORT_FAILURE_MESSAGE = "媒体导入失败，请重试";
const DECLARATION_FAILURE_MESSAGE =
  "所选文件的名称或媒体类型声明无效，请重新选择文件。";
const SESSION_EXPIRED_MESSAGE =
  "本地会话已失效，请从 AI Canvas 启动器重新打开。";

function message(error: unknown): string {
  if (error instanceof ArtifactDeclarationValidationError) {
    return DECLARATION_FAILURE_MESSAGE;
  }
  if (error instanceof ArtifactImportFailedError) {
    switch (error.failureCode) {
      case "upload_interrupted":
        return "文件传输中断，可以使用同一文件重试。";
      case "probe_timeout":
        return "素材检查超时，可以重试。";
      case "probe_unavailable":
        return "本地素材检查工具暂不可用，可以重试。";
      case "size_mismatch":
        return "文件大小与选择时不一致，请重新选择文件。";
      case "corrupt_media":
        return "文件已损坏或不完整，请重新选择可用素材。";
      case "unsupported_media":
        return "当前文件格式或编码不受支持，请重新选择。";
      case "incompatible_declaration":
        return "文件内容与导入声明不一致，请重新选择文件。";
    }
  }
  if (error instanceof ArtifactImportConflictError) {
    switch (error.reason) {
      case "declaration_mismatch":
        return "这次导入标识已用于另一份文件，请重新选择。";
      case "content_replay_mismatch":
        return "已完成的导入会话与所选文件不一致，请重新选择文件。";
      case "upload_in_progress":
        return "同一文件仍在处理中，请稍候。";
      case "invalid_replacement":
        return "原素材已不再是可替换版本，请重新加载项目后再替换。";
      case "invalid_state":
        return "这次导入已结束，请重新选择文件。";
    }
  }
  if (error instanceof SessionExpiredError) return SESSION_EXPIRED_MESSAGE;
  return SAFE_IMPORT_FAILURE_MESSAGE;
}

function discardFailureMessage(error: unknown): string {
  if (
    error instanceof ArtifactImportConflictError &&
    error.reason === "result_referenced"
  ) {
    return DISCARD_REFERENCED_MESSAGE;
  }
  if (error instanceof TypeError) return DISCARD_NETWORK_MESSAGE;
  if (error instanceof ApiRequestError) {
    if (error.status === 404) return DISCARD_NOT_FOUND_MESSAGE;
    if (error.status === 409) return DISCARD_CONFLICT_MESSAGE;
    if (error.status === 502) return DISCARD_INVALID_RESPONSE_MESSAGE;
    if (error.status >= 500) return DISCARD_SERVER_MESSAGE;
  }
  return DISCARD_UNKNOWN_MESSAGE;
}

function readDiscardResult(value: unknown): {
  httpStatus: number;
  outcome: string;
  contentCleanup: string;
} | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const result = value as Record<string, unknown>;
  if (
    typeof result.httpStatus !== "number" ||
    typeof result.outcome !== "string" ||
    typeof result.contentCleanup !== "string"
  ) {
    return null;
  }
  return {
    httpStatus: result.httpStatus,
    outcome: result.outcome,
    contentCleanup: result.contentCleanup,
  };
}

function discardResultStatus(value: unknown): "confirmed" | "pending" | "invalid" {
  const result = readDiscardResult(value);
  if (
    !result ||
    (result.outcome !== "discarded" &&
      result.outcome !== "already_discarded")
  ) {
    return "invalid";
  }
  if (result.httpStatus === 202 && result.contentCleanup === "pending") {
    return "pending";
  }
  if (
    result.httpStatus === 200 &&
    (result.contentCleanup === "removed" ||
      result.contentCleanup === "not_present" ||
      result.contentCleanup === "retained_shared" ||
      result.contentCleanup === "not_applicable")
  ) {
    return "confirmed";
  }
  return "invalid";
}

function isItemCancellable(item: RuntimeItem): boolean {
  return (
    item.state === "QUEUED" ||
    item.state === "INITIALIZING" ||
    item.state === "RECEIVING" ||
    item.state === "VALIDATING" ||
    (item.state === "PLACING" && !item.placementStarted)
  );
}

function itemSnapshot(item: RuntimeItem): IngressItemSnapshot {
  const cancellationUnknown =
    item.state === "CANCELLING" &&
    item.errorMessage !== null &&
    item.observerController === null;
  return Object.freeze({
    itemId: item.itemId,
    clientImportId: item.clientImportId,
    state: item.state,
    displayName: item.displayName,
    mediaKind: item.mediaKind,
    errorMessage: item.errorMessage,
    discardErrorMessage: item.discardErrorMessage,
    canCancel: isItemCancellable(item),
    canRetryCancellation: cancellationUnknown,
    canRetryDiscard:
      item.state === "DISCARDING" &&
      item.discardController === null &&
      item.discardErrorMessage !== null,
    canForceRefresh: cancellationUnknown,
    resumeScopeMessage: item.isLargeUpload
      ? "大文件仅可在当前页面会话内断点续传；刷新或关闭页面后请重新上传。"
      : null,
    largeUploadProgress: item.largeUploadProgress
      ? Object.freeze({
          receivedByteSize: item.largeUploadProgress.receivedByteSize,
          totalByteSize: item.largeUploadProgress.totalByteSize,
          estimatedSecondsRemaining: estimateSecondsRemaining(
            item.largeUploadProgress,
          ),
        })
      : null,
  });
}

function estimateSecondsRemaining(progress: {
  receivedByteSize: number;
  totalByteSize: number;
  startedAtMilliseconds: number;
}): number | null {
  if (progress.receivedByteSize >= progress.totalByteSize) return 0;
  if (progress.receivedByteSize < 1) return null;
  const elapsedMilliseconds = Date.now() - progress.startedAtMilliseconds;
  if (elapsedMilliseconds < 250) return null;
  const bytesPerSecond = (progress.receivedByteSize * 1_000) / elapsedMilliseconds;
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return null;
  return Math.max(
    1,
    Math.ceil((progress.totalByteSize - progress.receivedByteSize) / bytesPerSecond),
  );
}

function counts(slots: readonly RuntimeSlot[]): IngressBatchCounts {
  const states = slots.map((slot) => slot.item.state);
  const count = (state: IngressItemState) => states.filter((value) => value === state).length;
  return Object.freeze({
    total: states.length,
    queued: count("QUEUED"),
    processing: states.filter((state) => PROCESSING.has(state)).length,
    placed: count("PLACED"),
    failed: count("FAILED"),
    cancelled: count("CANCELLED"),
    savedUnplaced: count("SAVED_UNPLACED"),
    ignored: count("IGNORED"),
    terminal: states.filter((state) => TERMINAL.has(state)).length,
  });
}

class Controller implements MediaIngressController {
  private readonly projectId: string;
  private readonly loadEpoch: number;
  private readonly core: MediaImportCore;
  private readonly permits: MediaImportPermitBroker;
  private readonly targetLocks: MediaTargetLocks;
  private readonly canvas: MediaIngressCanvasAdapter;
  private readonly idFactory: () => string;
  private readonly schedulePlacement: (callback: () => void) => void;
  private readonly confirmForceRefresh: () => boolean;
  private readonly abandonAllNow: () => void;
  private readonly batches: RuntimeBatch[] = [];
  private readonly pendingTargetChoices: RuntimeTargetChoice[] = [];
  private readonly listeners = new Set<() => void>();
  private snapshot: MediaIngressSnapshot = EMPTY_SNAPSHOT;
  private unsubscribeTargetLocks: (() => void) | null = null;
  private createdOrder = 0;
  private disposed = false;
  private abandoned = false;
  private suppressTargetLockPublish = 0;

  constructor(input: CreateMediaIngressControllerInput) {
    this.projectId = input.projectId;
    this.loadEpoch = input.loadEpoch;
    this.core = input.core;
    this.permits = input.permits;
    this.targetLocks = input.targetLocks;
    this.canvas = input.canvas;
    this.idFactory = input.idFactory ?? (() => crypto.randomUUID());
    this.schedulePlacement = input.schedulePlacement ?? ((callback) => queueMicrotask(callback));
    this.confirmForceRefresh = input.confirmForceRefresh ?? (() => false);
    this.abandonAllNow = input.abandonAllNow ?? (() => undefined);
  }

  mount(): void {
    if (this.disposed || this.unsubscribeTargetLocks) return;
    this.unsubscribeTargetLocks = this.targetLocks.subscribe(() =>
      this.syncProtectedTargets(),
    );
    this.syncProtectedTargets();
  }

  unmount(): void {
    this.unsubscribeTargetLocks?.();
    this.unsubscribeTargetLocks = null;
  }

  private syncProtectedTargets(): void {
    if (this.suppressTargetLockPublish > 0) return;
    const current = this.snapshot.protectedTargetNodeIds;
    const next = this.targetLocks.getSnapshot().protectedNodeIds;
    if (
      current.length === next.length &&
      current.every((nodeId, index) => nodeId === next[index])
    ) {
      return;
    }
    this.publish();
  }

  private mutateTargetLocks<T>(callback: () => T): T {
    this.suppressTargetLockPublish += 1;
    try {
      return callback();
    } finally {
      this.suppressTargetLockPublish -= 1;
    }
  }

  private admissionRejection(
    projectId: string,
    loadEpoch: number,
  ): Extract<MediaIngressAdmission, Readonly<{ kind: "rejected" }>> | null {
    const binding = this.canvas.getBinding();
    if (
      this.disposed ||
      this.abandoned ||
      projectId !== this.projectId ||
      loadEpoch !== this.loadEpoch ||
      binding.projectId !== this.projectId ||
      binding.loadEpoch !== this.loadEpoch
    ) {
      return { kind: "rejected", message: PROJECT_CHANGED_MESSAGE };
    }
    if (binding.conflict) {
      return { kind: "rejected", message: CONFLICT_MESSAGE };
    }
    return null;
  }

  private recoveryPlacementRejected(
    projectId: string,
    loadEpoch: number,
  ): boolean {
    const binding = this.canvas.getBinding();
    return (
      this.disposed ||
      this.abandoned ||
      projectId !== this.projectId ||
      loadEpoch !== this.loadEpoch ||
      binding.projectId !== this.projectId ||
      binding.loadEpoch !== this.loadEpoch
    );
  }

  private getMediaTarget(nodeId: string): MediaIngressTarget | null {
    if (this.canvas.getMediaTarget) {
      return this.canvas.getMediaTarget(nodeId);
    }
    const state = useCanvasStore.getState();
    if (
      state.projectId !== this.projectId ||
      state.loadEpoch !== this.loadEpoch
    ) {
      return null;
    }
    const node = state.nodes.find((candidate) => candidate.id === nodeId);
    if (
      !node ||
      node.type !== "media_input" ||
      node.data.kind !== "media_input"
    ) {
      return null;
    }
    return {
      revisionId: node.data.artifactRevisionId,
      mediaKind: node.data.mediaKind,
    };
  }

  private attachTarget(input: AttachMediaIngressTargetInput): boolean {
    if (this.canvas.attach) return this.canvas.attach(input);
    return useCanvasStore.getState().attachMediaRevision(input);
  }

  private replacementGate(): Readonly<{
    status: SaveStatus;
    editSequence: number;
    savedSequence: number;
    baseRevisionId: string | null;
  }> {
    const binding = this.canvas.getBinding();
    const state = useCanvasStore.getState();
    const storeMatches =
      state.projectId === this.projectId &&
      state.loadEpoch === this.loadEpoch;
    return {
      status:
        binding.status ??
        (storeMatches
          ? state.status
          : binding.conflict
            ? "conflict"
            : "clean"),
      editSequence:
        binding.editSequence ?? (storeMatches ? state.editSequence : 0),
      savedSequence:
        binding.savedSequence ?? (storeMatches ? state.savedSequence : 0),
      baseRevisionId:
        binding.baseRevisionId !== undefined
          ? binding.baseRevisionId
          : storeMatches
            ? state.baseRevisionId
            : null,
    };
  }

  private replacementBlockedReason(
    choice: RuntimeTargetChoice,
  ): string | null {
    if (
      choice.requestedMediaKind === null ||
      choice.requestedMediaKind !== choice.currentMediaKind
    ) {
      return REPLACEMENT_KIND_MESSAGE;
    }
    const gate = this.replacementGate();
    if (
      gate.status !== "clean" ||
      gate.editSequence !== gate.savedSequence
    ) {
      return REPLACEMENT_SAVE_MESSAGE;
    }
    if (gate.baseRevisionId === null) return REPLACEMENT_BASE_MESSAGE;
    return null;
  }

  private targetMatches(binding: TargetBinding): MediaIngressTarget | null {
    const target = this.getMediaTarget(binding.targetNodeId);
    return target?.revisionId === binding.expectedRevisionId ? target : null;
  }

  private slotTargetMatches(slot: RuntimeSlot): boolean {
    const binding = slot.targetBinding;
    if (!binding) return true;
    const target = this.getMediaTarget(binding.targetNodeId);
    if (!target || target.revisionId !== binding.expectedRevisionId) {
      return false;
    }
    if (binding.expectedRevisionId === null) {
      return target.mediaKind === null;
    }
    return (
      slot.expectedMediaKind === null ||
      target.mediaKind === slot.expectedMediaKind
    );
  }

  enqueue(request: MediaIngressRequest): MediaIngressAdmission {
    if (request.candidates.length === 0) return { kind: "empty" };
    const gate = this.admissionRejection(
      request.projectId,
      request.loadEpoch,
    );
    if (gate) return gate;
    const preflight = preflightBatch(request.candidates);
    if (preflight.kind !== "accepted") return preflight;

    if (request.targetMode !== "create") {
      const candidate =
        request.candidates.length === 1 ? request.candidates[0] : null;
      if (!candidate || candidate.kind !== "file") {
        return {
          kind: "rejected",
          message: "目标媒体节点一次只能接收一个文件",
        };
      }
      const target = this.targetMatches(request.targetBinding);
      if (!target) {
        return { kind: "rejected", message: TARGET_CHANGED_MESSAGE };
      }

      if (request.targetMode === "fill_existing") {
        const itemId = this.idFactory();
        return this.mutateTargetLocks(() => {
          const targetLease = this.targetLocks.acquire(
            itemId,
            request.targetBinding.targetNodeId,
          );
          if (!targetLease) {
            return { kind: "rejected", message: TARGET_BUSY_MESSAGE };
          }
          return this.createBatch(
            request,
            request.targetBinding,
            targetLease,
            null,
            itemId,
          );
        });
      }

      if (target.mediaKind === null) {
        return { kind: "rejected", message: TARGET_CHANGED_MESSAGE };
      }
      const validation = validateCandidate(candidate);
      const choiceId = this.idFactory();
      return this.mutateTargetLocks(() => {
        const targetLease = this.targetLocks.acquire(
          choiceId,
          request.targetBinding.targetNodeId,
        );
        if (!targetLease) {
          return { kind: "rejected", message: TARGET_BUSY_MESSAGE };
        }
        const pendingChoice: RuntimeTargetChoice = {
          choiceId,
          projectId: request.projectId,
          loadEpoch: request.loadEpoch,
          source: request.source,
          displayName: validation.displayName,
          requestedMediaKind: validation.mediaKind,
          currentMediaKind: target.mediaKind,
          replacementBlockedReason: null,
          targetBinding: request.targetBinding,
          anchor: { ...request.anchor },
          file: candidate.file,
          targetLease,
        };
        pendingChoice.replacementBlockedReason =
          this.replacementBlockedReason(pendingChoice);
        this.pendingTargetChoices.push(pendingChoice);
        this.publish();
        return { kind: "choice_required", choiceId };
      });
    }

    return this.createBatch(request, null, null, null);
  }

  private createBatch(
    request: MediaIngressRequest,
    targetBinding: TargetBinding | null,
    targetLease: MediaTargetLease | null,
    replaceRevisionId: string | null,
    preallocatedItemId?: string,
  ): MediaIngressAdmission {
    const batchId = this.idFactory();
    const positions = layoutReservations(
      request.candidates.length,
      request.anchor,
      [
        ...this.canvas.getObstacles(),
        ...this.liveReservationObstacles(),
      ],
    );
    const batch: RuntimeBatch = {
      batchId,
      source: request.source,
      createdOrder: ++this.createdOrder,
      collapsed: false,
      closeRequested: false,
      slots: request.candidates.map((candidate, index) =>
        this.createSlot(
          candidate,
          positions[index],
          targetBinding,
          index === 0 ? targetLease : null,
          replaceRevisionId,
          index === 0 ? preallocatedItemId : undefined,
        ),
      ),
    };
    this.batches.unshift(batch);
    this.publish();
    for (const slot of batch.slots) {
      if (slot.item.file && slot.item.declaration) this.begin(slot);
    }
    return { kind: "accepted", batchId };
  }

  private liveReservationObstacles(
    excludedSlot: RuntimeSlot | null = null,
  ): MediaIngressObstacle[] {
    return this.batches.flatMap((batch) =>
      batch.slots
        .filter(
          (slot) =>
            slot !== excludedSlot &&
            (slot.targetBinding
              ? slot.item.state !== "PLACED" &&
                slot.item.state !== "CANCELLED" &&
                slot.item.state !== "IGNORED"
              : !RELEASED_RESERVATION.has(slot.item.state)),
        )
        .map((slot) => ({
          x: slot.reservation.position.x,
          y: slot.reservation.position.y,
          width: slot.reservation.width,
          height: slot.reservation.height,
        })),
    );
  }

  private createSlot(
    candidate: IngressCandidate,
    position: { x: number; y: number },
    targetBinding: TargetBinding | null,
    targetLease: MediaTargetLease | null,
    replaceRevisionId: string | null,
    preallocatedItemId?: string,
  ): RuntimeSlot {
    const slotId = this.idFactory();
    let nodeId = this.idFactory();
    if (targetBinding && nodeId === targetBinding.targetNodeId) {
      nodeId = this.idFactory();
    }
    const itemId = preallocatedItemId ?? this.idFactory();
    const clientImportId = this.idFactory();
    const validation = validateCandidate(candidate);
    const item: RuntimeItem = {
      itemId,
      clientImportId,
      state: validation.errorMessage ? "FAILED" : "QUEUED",
      displayName: validation.displayName,
      mediaKind: validation.mediaKind,
      errorMessage: validation.errorMessage,
      file: candidate.kind === "file" && !validation.errorMessage ? candidate.file : null,
      declaration: null,
      attempt: null,
      recoveryAttempt: null,
      controller: null,
      observerController: null,
      discardController: null,
      permit: null,
      targetLease,
      importId: null,
      completedRevision: null,
      discardErrorMessage: null,
      discardOriginSnapshot: null,
      discardIntent: null,
      pendingReselection: null,
      discardGeneration: 0,
      cancelGeneration: 0,
      observerGeneration: 0,
      queueHoldOwned: false,
      placementStarted: false,
      isLargeUpload:
        candidate.kind === "file" &&
        !validation.errorMessage &&
        candidate.file.size > 64 * 1024 * 1024,
      largeUploadProgress: null,
    };
    if (item.file && item.mediaKind) {
      item.declaration = Object.freeze({
        clientImportId,
        filename: validation.displayName,
        expectedByteSize: item.file.size,
        requestedMediaKind: item.mediaKind,
        declaredContentType: item.file.type.trim() || null,
        replaceRevisionId,
      });
    }
    const slot: RuntimeSlot = {
      slotId,
      reservation: Object.freeze({
        nodeId,
        position: Object.freeze({ ...position }),
        width: 312,
        height: 360,
      }),
      item,
      previousAttempts: [],
      targetBinding,
      targetBindingStale: false,
      expectedMediaKind:
        targetBinding && targetBinding.expectedRevisionId !== null
          ? validation.mediaKind
          : null,
    };
    if ((!item.file || !item.declaration) && item.targetLease)
      this.releaseTarget(item);
    return slot;
  }

  private begin(slot: RuntimeSlot): void {
    const item = slot.item;
    if (
      item.state !== "QUEUED" ||
      !item.file ||
      !item.declaration ||
      item.controller
    )
      return;
    const queueController = new AbortController();
    item.controller = queueController;
    void this.permits
      .acquire(item.itemId, queueController.signal)
      .then((permit) => {
        if (
          this.disposed ||
          item.controller !== queueController ||
          item.state !== "QUEUED"
        ) {
          permit.release();
          return;
        }
        item.permit = permit;
        const file = item.file;
        const declaration = item.declaration;
        if (!file || !declaration) {
          this.finishFailure(slot, item, new Error("媒体导入尝试不可用"));
          return;
        }
        let attempt: MediaImportAttempt;
        try {
          attempt = this.core.createAttempt({
            operationId: item.itemId,
            projectId: this.projectId,
            file,
            declaration,
          });
          item.attempt = attempt;
        } catch (error) {
          this.finishFailure(slot, item, error);
          return;
        }
        void attempt
          .execute({
            signal: queueController.signal,
            onPhase: (phase) => {
              if (item.controller !== queueController) return;
              const nextState = phase.toUpperCase() as IngressItemState;
              if (item.state === nextState) return;
              item.state = nextState;
              this.publish();
            },
            onProgress: (progress) => {
              if (item.controller !== queueController || !item.isLargeUpload) return;
              if (
                !Number.isSafeInteger(progress.receivedByteSize) ||
                !Number.isSafeInteger(progress.totalByteSize) ||
                progress.receivedByteSize < 0 ||
                progress.totalByteSize < 1 ||
                progress.receivedByteSize > progress.totalByteSize
              ) {
                return;
              }
              item.largeUploadProgress = {
                receivedByteSize: progress.receivedByteSize,
                totalByteSize: progress.totalByteSize,
                startedAtMilliseconds:
                  item.largeUploadProgress?.startedAtMilliseconds ?? Date.now(),
              };
              this.publish();
            },
          })
          .then((result) => this.completed(slot, result, queueController))
          .catch((error) => {
            if (item.controller === queueController) {
              this.finishFailure(slot, item, error);
            }
          });
      })
      .catch((error) => {
        if (!this.disposed && item.controller === queueController) {
          this.finishFailure(slot, item, error);
        }
      });
  }

  private completed(
    slot: RuntimeSlot,
    revision: ArtifactRevision,
    controller: AbortController,
  ): void {
    const item = slot.item;
    if (item.controller !== controller || this.disposed) return;
    item.completedRevision = revision;
    this.releaseNetwork(item, true);
    const binding = this.canvas.getBinding();
    if (
      binding.projectId !== this.projectId ||
      binding.loadEpoch !== this.loadEpoch
    ) {
      item.state = "SAVED_UNPLACED";
      this.releaseTarget(item);
      this.publish();
      return;
    }
    if (
      slot.targetBinding &&
      !slot.targetBindingStale &&
      !this.slotTargetMatches(slot)
    ) {
      this.markTargetBindingStale(slot, item);
    }
    if (slot.targetBindingStale) {
      item.state = "SAVED_UNPLACED";
      this.markTargetBindingStale(slot, item);
      this.releaseTarget(item);
      this.publish();
      return;
    }
    item.state = "PLACING";
    this.publish();
    const generation = item.cancelGeneration;
    this.schedulePlacement(() => {
      if (this.disposed || item.state !== "PLACING") return;
      const latest = this.canvas.getBinding();
      if (
        item.cancelGeneration !== generation ||
        latest.projectId !== this.projectId ||
        latest.loadEpoch !== this.loadEpoch
      ) {
        item.state = "SAVED_UNPLACED";
      } else if (slot.targetBinding) {
        item.placementStarted = true;
        if (
          slot.targetBindingStale ||
          !this.slotTargetMatches(slot)
        ) {
          this.markTargetBindingStale(slot, item);
          item.state = "SAVED_UNPLACED";
        } else {
          const attached = this.attachTarget({
            projectId: this.projectId,
            loadEpoch: this.loadEpoch,
            nodeId: slot.targetBinding.targetNodeId,
            expectedRevisionId: slot.targetBinding.expectedRevisionId,
            revisionId: revision.revision_id,
            mediaKind: revision.media_kind as MediaKind,
          });
          if (!attached) this.markTargetBindingStale(slot, item);
          item.state = attached ? "PLACED" : "SAVED_UNPLACED";
        }
      } else {
        item.placementStarted = true;
        const promoted = this.canvas.promote({
          projectId: this.projectId,
          loadEpoch: this.loadEpoch,
          nodeId: slot.reservation.nodeId,
          position: slot.reservation.position,
          revisionId: revision.revision_id,
          mediaKind: revision.media_kind as MediaKind,
        });
        item.state = promoted ? "PLACED" : "SAVED_UNPLACED";
      }
      this.releaseTarget(item);
      this.publish();
    });
  }

  private markTargetBindingStale(
    slot: RuntimeSlot,
    item: RuntimeItem,
  ): void {
    slot.targetBindingStale = true;
    if (item.errorMessage?.includes(TARGET_STALE_RESELECT_MESSAGE)) return;
    item.errorMessage = item.errorMessage
      ? `${item.errorMessage}\n${TARGET_STALE_RESELECT_MESSAGE}`
      : TARGET_STALE_RESELECT_MESSAGE;
  }

  private finishFailure(
    slot: RuntimeSlot,
    item: RuntimeItem,
    error: unknown,
  ): void {
    item.state = "FAILED";
    item.errorMessage = message(error);
    if (slot.targetBindingStale) {
      this.markTargetBindingStale(slot, item);
    }
    this.releaseAll(item, true);
    this.publish();
  }

  private releaseNetwork(
    item: RuntimeItem,
    releasePermit: boolean,
    preserveRecoveryAttempt = false,
  ): void {
    const controller = item.controller;
    item.controller = null;
    controller?.abort();
    const attempt = item.attempt;
    item.importId ??= attempt?.getImportId() ?? null;
    item.recoveryAttempt =
      preserveRecoveryAttempt &&
      item.importId === null &&
      attempt?.wasCreateDispatched()
        ? attempt
        : null;
    item.attempt = null;
    attempt?.releaseFile();
    item.file = null;
    item.declaration = null;
    if (releasePermit) {
      item.permit?.release();
      item.permit = null;
    }
  }

  private releaseObserver(item: RuntimeItem): void {
    const observerController = item.observerController;
    item.observerController = null;
    observerController?.abort();
  }

  private releaseTarget(item: RuntimeItem): void {
    const targetLease = item.targetLease;
    item.targetLease = null;
    if (targetLease) {
      this.mutateTargetLocks(() => targetLease.release());
    }
  }

  private releaseQueueHold(item: RuntimeItem): void {
    if (!item.queueHoldOwned) return;
    item.queueHoldOwned = false;
    this.permits.releaseQueueHold(item.itemId);
  }

  private releaseDiscard(item: RuntimeItem): void {
    const discardController = item.discardController;
    item.discardController = null;
    item.discardGeneration += 1;
    discardController?.abort();
    item.pendingReselection = null;
    item.discardIntent = null;
    item.discardOriginSnapshot = null;
    item.discardErrorMessage = null;
  }

  private releaseAll(
    item: RuntimeItem,
    preserveRecoveryAttempt = false,
  ): void {
    this.releaseDiscard(item);
    this.releaseObserver(item);
    this.releaseNetwork(item, true, preserveRecoveryAttempt);
    this.releaseTarget(item);
    this.releaseQueueHold(item);
  }

  private startDiscard(
    batch: RuntimeBatch,
    slot: RuntimeSlot,
    intent: "ignore" | "close_batch" | "reselect",
    pendingReselection: File | null = null,
  ): void {
    const item = slot.item;
    if (item.state !== "DISCARDING") {
      if (item.state !== "FAILED" && item.state !== "SAVED_UNPLACED") return;
      item.discardOriginSnapshot = itemSnapshot(item);
      item.discardIntent = intent;
      item.pendingReselection = pendingReselection;
      item.state = "DISCARDING";
      item.discardErrorMessage = null;
    } else if (
      item.discardController ||
      item.discardErrorMessage === null ||
      item.discardIntent !== intent
    ) {
      return;
    }

    const importId = item.importId;
    const recoveryAttempt = item.recoveryAttempt;
    if (!importId && !recoveryAttempt) {
      item.discardErrorMessage = DISCARD_UNKNOWN_MESSAGE;
      this.publish();
      return;
    }
    const discardController = new AbortController();
    const generation = ++item.discardGeneration;
    item.discardController = discardController;
    item.discardErrorMessage = null;
    this.publish();
    let operation: ReturnType<MediaImportCore["discardImport"]>;
    try {
      operation = (async () => {
        const resolvedImportId =
          importId ??
          (await recoveryAttempt!.recoverImportId({
            signal: discardController.signal,
          }));
        if (
          this.disposed ||
          slot.item !== item ||
          item.state !== "DISCARDING" ||
          item.discardController !== discardController ||
          item.discardGeneration !== generation
        ) {
          throw new DOMException("Aborted", "AbortError");
        }
        item.importId = resolvedImportId;
        item.recoveryAttempt = null;
        return this.core.discardImport({
          projectId: this.projectId,
          importId: resolvedImportId,
          clientImportId: item.clientImportId,
          expectedRevisionId: item.completedRevision?.revision_id ?? null,
          ...(item.isLargeUpload ? { isLargeUpload: true } : {}),
          signal: discardController.signal,
        });
      })();
    } catch (error) {
      item.discardController = null;
      item.discardErrorMessage = discardFailureMessage(error);
      this.publish();
      return;
    }
    void operation
      .then((result) => {
        if (
          this.disposed ||
          slot.item !== item ||
          item.state !== "DISCARDING" ||
          item.discardController !== discardController ||
          item.discardGeneration !== generation
        ) {
          return;
        }
        item.discardController = null;
        const resultStatus = discardResultStatus(result);
        if (resultStatus !== "confirmed") {
          item.discardErrorMessage =
            resultStatus === "pending"
              ? DISCARD_PENDING_MESSAGE
              : DISCARD_INVALID_RESPONSE_MESSAGE;
          this.publish();
          return;
        }
        const discardIntent = item.discardIntent;
        const reselectedFile = item.pendingReselection;
        const originSnapshot = item.discardOriginSnapshot;
        this.releaseAll(item);
        item.importId = null;
        item.completedRevision = null;
        item.state = "IGNORED";
        item.errorMessage = null;
        if (discardIntent === "reselect" && reselectedFile) {
          this.replaceFailedItem(
            slot,
            reselectedFile,
            originSnapshot,
            true,
          );
          return;
        }
        this.publish();
        if (discardIntent === "close_batch") {
          this.finishRequestedClose(batch);
        }
      })
      .catch((error: unknown) => {
        if (
          this.disposed ||
          slot.item !== item ||
          item.state !== "DISCARDING" ||
          item.discardController !== discardController ||
          item.discardGeneration !== generation
        ) {
          return;
        }
        item.discardController = null;
        item.discardErrorMessage = discardFailureMessage(error);
        this.publish();
      });
  }

  private publish(): void {
    const batchSnapshots = this.batches.map((batch) => {
      const batchCounts = counts(batch.slots);
      const slots: IngressSlotSnapshot[] = batch.slots.map((slot) =>
        Object.freeze({
          slotId: slot.slotId,
          reservation: slot.reservation,
          currentItem: itemSnapshot(slot.item),
        previousAttempts: Object.freeze([...slot.previousAttempts]),
          attemptCount: slot.previousAttempts.length + 1,
          targetNodeId: slot.targetBinding?.targetNodeId ?? null,
          canReselect: slot.item.state === "FAILED",
          canIgnore:
            slot.item.state === "FAILED" ||
            slot.item.state === "SAVED_UNPLACED",
          canPlaceSavedUnplaced: slot.item.state === "SAVED_UNPLACED",
        }),
      );
      return Object.freeze({
        batchId: batch.batchId,
        source: batch.source,
        createdOrder: batch.createdOrder,
        collapsed: batch.collapsed,
        counts: batchCounts,
        slots: Object.freeze(slots),
        canStop: batch.slots.some((slot) =>
          isItemCancellable(slot.item),
        ),
        canClose:
          batchCounts.queued + batchCounts.processing === 0 &&
          batchCounts.savedUnplaced === 0,
      });
    });
    const items = this.batches.flatMap((batch) => batch.slots.map((slot) => slot.item));
    const pendingTargetChoices: PendingTargetChoiceSnapshot[] =
      this.pendingTargetChoices.map((choice) => {
        const replacementBlockedReason =
          choice.replacementBlockedReason;
        return Object.freeze({
          choiceId: choice.choiceId,
          displayName: choice.displayName,
          targetNodeId: choice.targetBinding.targetNodeId,
          requestedMediaKind: choice.requestedMediaKind,
          currentMediaKind: choice.currentMediaKind,
          canReplace: replacementBlockedReason === null,
          replacementBlockedReason,
        });
      });
    this.snapshot = Object.freeze({
      batches: Object.freeze(batchSnapshots),
      pendingTargetChoices: Object.freeze(pendingTargetChoices),
      protectedTargetNodeIds: this.targetLocks.getSnapshot().protectedNodeIds,
      guardState: Object.freeze({
        pendingOperationCount: items.filter(
          (item) => item.state === "QUEUED" || PROCESSING.has(item.state),
        ).length + this.pendingTargetChoices.length,
        hasSavedUnplaced: items.some((item) => item.state === "SAVED_UNPLACED"),
      }),
    });
    for (const listener of this.listeners) listener();
  }

  getSnapshot = (): MediaIngressSnapshot => this.snapshot;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  toggleBatchCollapsed(batchId: string): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    if (!batch) return;
    batch.collapsed = !batch.collapsed;
    this.publish();
  }
  closeBatch(batchId: string): void {
    const index = this.batches.findIndex((batch) => batch.batchId === batchId);
    if (index < 0) return;
    const batch = this.batches[index];
    if (batch.slots.some((slot) => !TERMINAL.has(slot.item.state))) return;
    if (batch.slots.some((slot) => slot.item.state === "SAVED_UNPLACED")) return;
    batch.closeRequested = true;
    for (const slot of batch.slots) {
      if (slot.item.state !== "FAILED") continue;
      if (slot.item.importId || slot.item.recoveryAttempt) {
        this.startDiscard(batch, slot, "close_batch");
      } else {
        this.releaseAll(slot.item);
        slot.item.state = "IGNORED";
      }
    }
    this.finishRequestedClose(batch);
  }
  private finishRequestedClose(batch: RuntimeBatch): void {
    if (!batch.closeRequested) return;
    const index = this.batches.indexOf(batch);
    if (index < 0) return;
    if (
      batch.slots.some(
        (slot) =>
          !TERMINAL.has(slot.item.state) ||
          slot.item.state === "SAVED_UNPLACED" ||
          slot.item.state === "FAILED",
      )
    ) {
      return;
    }
    for (const slot of batch.slots) this.releaseAll(slot.item);
    this.batches.splice(index, 1);
    this.publish();
  }
  private releaseTargetChoice(choice: RuntimeTargetChoice): void {
    choice.file = null;
    const targetLease = choice.targetLease;
    choice.targetLease = null;
    if (targetLease) {
      this.mutateTargetLocks(() => targetLease.release());
    }
  }
  resolveTargetChoice(
    choiceId: string,
    selected: "create" | "replace" | "cancel",
  ): MediaIngressAdmission {
    const index = this.pendingTargetChoices.findIndex(
      (choice) => choice.choiceId === choiceId,
    );
    if (index < 0) return { kind: "empty" };
    const [choice] = this.pendingTargetChoices.splice(index, 1);

    if (selected === "cancel") {
      this.releaseTargetChoice(choice);
      this.publish();
      return { kind: "empty" };
    }

    const gate = this.admissionRejection(
      choice.projectId,
      choice.loadEpoch,
    );
    if (gate) {
      this.releaseTargetChoice(choice);
      this.publish();
      return gate;
    }
    if (!this.targetMatches(choice.targetBinding)) {
      this.releaseTargetChoice(choice);
      this.publish();
      return { kind: "rejected", message: TARGET_CHANGED_MESSAGE };
    }
    if (selected === "replace") {
      const blockedReason = this.replacementBlockedReason(choice);
      if (blockedReason) {
        this.releaseTargetChoice(choice);
        this.publish();
        return { kind: "rejected", message: blockedReason };
      }
    }

    const file = choice.file;
    if (!file) {
      this.releaseTargetChoice(choice);
      this.publish();
      return { kind: "rejected", message: "待处理文件已释放" };
    }
    choice.file = null;
    const candidate: IngressCandidate = {
      kind: "file",
      file,
      displayName: choice.displayName,
    };

    if (selected === "create") {
      this.releaseTargetChoice(choice);
      return this.createBatch(
        {
          projectId: choice.projectId,
          loadEpoch: choice.loadEpoch,
          source: choice.source,
          candidates: [candidate],
          anchor: choice.anchor,
          targetMode: "create",
          targetBinding: null,
        },
        null,
        null,
        null,
        choice.choiceId,
      );
    }

    const targetLease = choice.targetLease;
    choice.targetLease = null;
    if (!targetLease) {
      this.publish();
      return { kind: "rejected", message: TARGET_CHANGED_MESSAGE };
    }
    return this.createBatch(
      {
        projectId: choice.projectId,
        loadEpoch: choice.loadEpoch,
        source: choice.source,
        candidates: [candidate],
        anchor: choice.anchor,
        targetMode: "choose_create_or_replace",
        targetBinding: choice.targetBinding,
      },
      choice.targetBinding,
      targetLease,
      choice.targetBinding.expectedRevisionId,
      choice.choiceId,
    );
  }
  cancelItem(batchId: string, itemId: string): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    const slot = batch?.slots.find((value) => value.item.itemId === itemId);
    if (!slot) return;
    const item = slot.item;
    if (!isItemCancellable(item)) return;
    if (item.state === "PLACING") {
      item.cancelGeneration += 1;
      item.state = "SAVED_UNPLACED";
      this.releaseTarget(item);
      this.publish();
      return;
    }

    item.cancelGeneration += 1;
    const attempt = item.attempt;
    item.controller?.abort();
    const largeCreateMayNeedRecovery =
      item.isLargeUpload && attempt?.wasCreateDispatched() === true;
    if (
      item.state === "QUEUED" ||
      !attempt ||
      (attempt.getImportId() === null &&
        !attempt.wasContentPutDispatched() &&
        !largeCreateMayNeedRecovery)
    ) {
      item.state = "CANCELLED";
      item.errorMessage = null;
      this.releaseAll(item);
      this.publish();
      return;
    }

    item.state = "CANCELLING";
    item.errorMessage = null;
    item.controller = null;
    if (!item.queueHoldOwned) {
      item.queueHoldOwned = true;
      this.permits.holdQueue(item.itemId);
    }
    this.publish();
    this.observeCancellation(slot, item);
  }

  stopBatch(batchId: string): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    if (!batch) return;
    for (const slot of batch.slots) {
      this.cancelItem(batchId, slot.item.itemId);
    }
  }

  private observeCancellation(slot: RuntimeSlot, item: RuntimeItem): void {
    const attempt = item.attempt;
    if (
      this.disposed ||
      item.state !== "CANCELLING" ||
      !attempt ||
      item.observerController
    )
      return;
    const observerController = new AbortController();
    const observerGeneration = ++item.observerGeneration;
    item.observerController = observerController;
    item.errorMessage = null;
    this.publish();
    void attempt
      .reconcileCancellation({
        signal: observerController.signal,
        maxPolls: 40,
        deadlineMs: 10_000,
      })
      .then((result) =>
        this.finishCancellationObservation(
          slot,
          item,
          observerController,
          observerGeneration,
          result,
        ),
      )
      .catch(() =>
        this.finishCancellationObservation(
          slot,
          item,
          observerController,
          observerGeneration,
          {
            kind: "unconfirmed",
            reason: "observer_failed",
            errorMessage: CANCELLATION_UNKNOWN_MESSAGE,
          },
        ),
      );
  }

  private finishCancellationObservation(
    slot: RuntimeSlot,
    item: RuntimeItem,
    observerController: AbortController,
    observerGeneration: number,
    result: CancelReconciliation,
  ): void {
    if (
      this.disposed ||
      item.state !== "CANCELLING" ||
      item.observerController !== observerController ||
      item.observerGeneration !== observerGeneration
    )
      return;
    item.observerController = null;
    observerController.abort();
    if (result.kind === "pending" || result.kind === "unconfirmed") {
      item.errorMessage = CANCELLATION_UNKNOWN_MESSAGE;
      this.publish();
      return;
    }

    if (result.kind === "completed") {
      item.completedRevision = result.revision;
      item.state = "SAVED_UNPLACED";
      if (slot.targetBinding) slot.targetBindingStale = true;
    } else {
      item.completedRevision = null;
      item.state = "CANCELLED";
    }
    item.errorMessage = null;
    this.releaseNetwork(item, true);
    this.releaseTarget(item);
    this.publish();
    this.releaseQueueHold(item);
  }
  reselect(batchId: string, slotId: string, file: File): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    const slot = batch?.slots.find((value) => value.slotId === slotId);
    if (!batch || !slot || slot.item.state !== "FAILED") return;
    if (this.admissionRejection(this.projectId, this.loadEpoch)) return;
    if (slot.item.importId || slot.item.recoveryAttempt) {
      this.startDiscard(batch, slot, "reselect", file);
      return;
    }
    this.replaceFailedItem(slot, file, itemSnapshot(slot.item));
  }

  private replaceFailedItem(
    slot: RuntimeSlot,
    file: File,
    previousAttempt: IngressItemSnapshot | null,
    oldResultAlreadyDiscarded = false,
  ): void {
    const candidate: IngressCandidate = {
      kind: "file",
      file,
      displayName: file.name,
    };
    const validation = validateCandidate(candidate);
    let validationError = validation.errorMessage;
    if (
      validationError === null &&
      slot.expectedMediaKind !== null &&
      validation.mediaKind !== slot.expectedMediaKind
    ) {
      validationError = REPLACEMENT_KIND_MESSAGE;
    }

    const itemId = this.idFactory();
    let targetLease: MediaTargetLease | null = null;
    if (slot.targetBinding && !slot.targetBindingStale) {
      if (!this.slotTargetMatches(slot)) {
        slot.targetBindingStale = true;
      } else if (validationError === null) {
        targetLease = this.mutateTargetLocks(() =>
          this.targetLocks.acquire(
            itemId,
            slot.targetBinding!.targetNodeId,
          ),
        );
        if (!targetLease) {
          if (!oldResultAlreadyDiscarded) return;
          validationError = TARGET_BUSY_MESSAGE;
        } else if (!this.slotTargetMatches(slot)) {
          this.mutateTargetLocks(() => targetLease?.release());
          targetLease = null;
          slot.targetBindingStale = true;
        }
      }
    }

    const clientImportId = this.idFactory();
    const nextItem: RuntimeItem = {
      itemId,
      clientImportId,
      state: validationError ? "FAILED" : "QUEUED",
      displayName: validation.displayName,
      mediaKind: validation.mediaKind,
      errorMessage: validationError,
      file: validationError ? null : file,
      declaration: null,
      attempt: null,
      recoveryAttempt: null,
      controller: null,
      observerController: null,
      discardController: null,
      permit: null,
      targetLease,
      importId: null,
      completedRevision: null,
      discardErrorMessage: null,
      discardOriginSnapshot: null,
      discardIntent: null,
      pendingReselection: null,
      discardGeneration: 0,
      cancelGeneration: 0,
      observerGeneration: 0,
      queueHoldOwned: false,
      placementStarted: false,
      isLargeUpload: !validationError && file.size > 64 * 1024 * 1024,
      largeUploadProgress: null,
    };
    if (slot.targetBindingStale) {
      this.markTargetBindingStale(slot, nextItem);
    }

    if (nextItem.file && nextItem.mediaKind) {
      nextItem.declaration = Object.freeze({
        clientImportId,
        filename: validation.displayName,
        expectedByteSize: nextItem.file.size,
        requestedMediaKind: nextItem.mediaKind,
        declaredContentType: nextItem.file.type.trim() || null,
        replaceRevisionId: slot.targetBindingStale
          ? null
          : slot.targetBinding?.expectedRevisionId ?? null,
      });
    }

    this.releaseAll(slot.item);
    if (previousAttempt) slot.previousAttempts.push(previousAttempt);
    slot.item = nextItem;
    this.publish();
    if (nextItem.file && nextItem.declaration) this.begin(slot);
  }
  retryCancellation(batchId: string, itemId: string): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    const slot = batch?.slots.find((value) => value.item.itemId === itemId);
    if (
      !slot ||
      slot.item.state !== "CANCELLING" ||
      slot.item.errorMessage === null ||
      slot.item.observerController
    )
      return;
    this.observeCancellation(slot, slot.item);
  }
  requestForceRefresh(): void {
    if (this.disposed || this.abandoned) return;
    const hasUnknownCancellation = this.batches.some((batch) =>
      batch.slots.some(
        (slot) =>
          slot.item.state === "CANCELLING" &&
          slot.item.errorMessage !== null &&
          slot.item.observerController === null,
      ),
    );
    if (!hasUnknownCancellation || !this.confirmForceRefresh()) return;
    this.abandoned = true;
    this.abandonAllNow();
  }
  placeSavedUnplaced(batchId: string, slotId: string): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    const slot = batch?.slots.find((value) => value.slotId === slotId);
    const item = slot?.item;
    if (
      !slot ||
      !item ||
      item.state !== "SAVED_UNPLACED" ||
      !item.completedRevision ||
      this.recoveryPlacementRejected(this.projectId, this.loadEpoch)
    )
      return;

    const revision = item.completedRevision;
    item.state = "PLACING";
    item.placementStarted = true;
    this.publish();
    const promoted = this.canvas.promote({
      projectId: this.projectId,
      loadEpoch: this.loadEpoch,
      nodeId: slot.reservation.nodeId,
      position: slot.reservation.position,
      revisionId: revision.revision_id,
      mediaKind: revision.media_kind as MediaKind,
    });
    if (promoted) {
      slot.targetBinding = null;
      slot.targetBindingStale = true;
      item.state = "PLACED";
      item.completedRevision = null;
      item.errorMessage = null;
    } else {
      item.state = "SAVED_UNPLACED";
      item.placementStarted = false;
    }
    this.publish();
  }
  ignoreSlot(batchId: string, slotId: string): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    const slot = batch?.slots.find((value) => value.slotId === slotId);
    if (
      !slot ||
      (slot.item.state !== "FAILED" &&
        slot.item.state !== "SAVED_UNPLACED")
    )
      return;
    if (
      slot.item.state === "FAILED" &&
      !slot.item.importId &&
      !slot.item.recoveryAttempt
    ) {
      this.releaseAll(slot.item);
      slot.item.completedRevision = null;
      slot.item.state = "IGNORED";
      this.publish();
      return;
    }
    this.startDiscard(batch, slot, "ignore");
  }
  retryDiscard(batchId: string, slotId: string): void {
    const batch = this.batches.find((value) => value.batchId === batchId);
    const slot = batch?.slots.find((value) => value.slotId === slotId);
    const intent = slot?.item.discardIntent;
    if (!batch || !slot || !intent) return;
    this.startDiscard(
      batch,
      slot,
      intent,
      slot.item.pendingReselection,
    );
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.unmount();
    for (const choice of this.pendingTargetChoices) {
      this.releaseTargetChoice(choice);
    }
    this.pendingTargetChoices.splice(0);
    for (const batch of this.batches) {
      for (const slot of batch.slots) {
        this.releaseAll(slot.item);
      }
    }
    this.batches.splice(0);
    this.publish();
    this.listeners.clear();
  }
}

export function createMediaIngressController(
  input: CreateMediaIngressControllerInput,
): MediaIngressController {
  return new Controller(input);
}
