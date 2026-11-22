import {
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  ApiRequestError,
  ArtifactDeclarationValidationError,
  ArtifactImportConflictError,
  ArtifactImportFailedError,
  inferRequestedMediaKind,
  SessionExpiredError,
  type ArtifactImportDeclaration,
  type LargeUploadDeclaration,
} from "../../api/client";
import {
  createMediaImportCore,
  type MediaImportCore,
  type MediaImportAttempt,
  type MediaImportNetworkPhase,
} from "./mediaIngress/mediaImportCore";
import {
  MediaImportPermitBroker,
  type MediaImportPermit,
} from "./mediaIngress/mediaImportPermitBroker";
import {
  MediaTargetLocks,
  type MediaTargetLease,
} from "./mediaIngress/mediaTargetLocks";
import {
  MediaIngressContext,
  type MediaIngressRuntimePort,
  type NodeMediaImportPhase,
  type NodeMediaImportRecoveryKind,
  type NodeMediaImportSnapshot,
} from "./mediaIngress/mediaIngressContext";
import { useCanvasStore } from "./canvasStore";

export type MediaImportRuntimeDependencies = Readonly<{
  core: MediaImportCore;
  permits: MediaImportPermitBroker;
  targetLocks: MediaTargetLocks;
}>;

export function createMediaImportRuntimeDependencies(): MediaImportRuntimeDependencies {
  return Object.freeze({
    core: createMediaImportCore(),
    permits: new MediaImportPermitBroker(),
    targetLocks: new MediaTargetLocks(),
  });
}

export type MediaImportPhase = NodeMediaImportPhase;
export type MediaImportRecoveryKind = NodeMediaImportRecoveryKind;
type MediaImportSnapshot = NodeMediaImportSnapshot;
export type MediaImportControls = MediaImportSnapshot & {
  replacementBlockedReason: string | null;
  selectionDisabled: boolean;
  selectFile(file: File): void;
  retry(): void;
  requestReload(): void;
};

type ImportOperation = {
  token: symbol;
  nodeId: string;
  operationId: string;
  projectId: string;
  loadEpoch: number;
  expectedRevisionId: string | null;
  declaration: Readonly<ArtifactImportDeclaration | LargeUploadDeclaration>;
  attempt: MediaImportAttempt;
  running: boolean;
  controller: AbortController | null;
  queueController: AbortController | null;
  permit: MediaImportPermit | null;
  targetLease: MediaTargetLease | null;
};
const IDLE_SNAPSHOT: MediaImportSnapshot = Object.freeze({
  phase: "idle",
  active: false,
  errorMessage: null,
  recoveryKind: null,
});
const GENERIC_IMPORT_FAILURE_MESSAGE = "素材导入失败，请重试。";
const SESSION_EXPIRED_MESSAGE =
  "本地会话已失效，请从 AI Canvas 启动器重新打开。";
function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}
function operationGate(projectId: string, nodeId: string): string | null {
  const state = useCanvasStore.getState();
  if (state.projectId !== projectId) return "当前项目已发生变化。";
  const node = state.nodes.find((candidate) => candidate.id === nodeId);
  if (!node || node.type !== "media_input" || node.data.kind !== "media_input")
    return "媒体节点已不存在。";
  if (state.status === "conflict") return "请先处理画布版本冲突。";
  if (
    node.data.artifactRevisionId !== null &&
    (state.status !== "clean" ||
      state.editSequence !== state.savedSequence ||
      state.baseRevisionId === null)
  )
    return "等待画布保存后再替换素材。";
  return null;
}
function errorMessage(error: unknown): string {
  if (error instanceof ArtifactDeclarationValidationError)
    return "所选文件的名称或媒体类型声明无效，请重新选择文件。";
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
  return GENERIC_IMPORT_FAILURE_MESSAGE;
}
function recovery(error: unknown): MediaImportRecoveryKind {
  if (error instanceof ArtifactDeclarationValidationError) return "reselect";
  if (
    error instanceof ArtifactImportConflictError &&
    error.reason === "invalid_replacement"
  )
    return "reload_project";
  return error instanceof TypeError ||
    (error instanceof ApiRequestError && error.retryable)
    ? "retry"
    : "reselect";
}

export class MediaImportRuntime {
  readonly projectId: string;
  private readonly core: MediaImportCore;
  private readonly permits: MediaImportPermitBroker;
  private readonly targetLocks: MediaTargetLocks;
  private readonly disposeDependencies: boolean;
  private readonly snapshots = new Map<string, MediaImportSnapshot>();
  private readonly listeners = new Map<string, Set<() => void>>();
  private readonly operations = new Map<string, ImportOperation>();
  private readonly activeTokens = new Set<symbol>();
  private activeCallback: (count: number) => void = () => undefined;
  private reloadCallback: () => void = () => undefined;
  private disposeTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  private abandoned = false;
  constructor(
    projectId: string,
    dependencies: MediaImportRuntimeDependencies,
    disposeDependencies: boolean,
  ) {
    this.projectId = projectId;
    this.core = dependencies.core;
    this.permits = dependencies.permits;
    this.targetLocks = dependencies.targetLocks;
    this.disposeDependencies = disposeDependencies;
  }
  setActiveCallback(callback: (count: number) => void): void {
    this.activeCallback = callback;
  }
  setReloadCallback(callback: () => void): void {
    this.reloadCallback = callback;
  }
  mount(): void {
    if (this.disposeTimer) clearTimeout(this.disposeTimer);
    this.disposeTimer = null;
    if (this.abandoned) {
      this.activeCallback(0);
      return;
    }
    this.disposed = false;
    this.activeCallback(this.activeTokens.size);
  }
  scheduleDispose(): void {
    if (this.disposeTimer) return;
    this.disposeTimer = setTimeout(() => {
      this.disposeTimer = null;
      this.disposeNow();
    }, 0);
  }
  disposeNow(): void {
    this.disposeInternal(false);
  }
  getPendingOperationCount(): number {
    return this.operations.size;
  }
  abandonAllNow(): number {
    return this.disposeInternal(true);
  }
  private disposeInternal(abandon: boolean): number {
    if (this.disposeTimer) clearTimeout(this.disposeTimer);
    this.disposeTimer = null;
    if (abandon) this.abandoned = true;
    if (this.disposed) return this.operations.size;
    this.disposed = true;
    for (const operation of this.operations.values())
      this.release(operation, true);
    this.operations.clear();
    this.snapshots.clear();
    this.activeTokens.clear();
    if (this.disposeDependencies) this.permits.dispose();
    this.activeCallback(0);
    return this.operations.size;
  }
  getSnapshot = (nodeId: string): MediaImportSnapshot =>
    this.snapshots.get(nodeId) ?? IDLE_SNAPSHOT;
  subscribe = (nodeId: string, listener: () => void): (() => void) => {
    let nodeListeners = this.listeners.get(nodeId);
    if (!nodeListeners) {
      nodeListeners = new Set();
      this.listeners.set(nodeId, nodeListeners);
    }
    nodeListeners.add(listener);
    return () => {
      nodeListeners?.delete(listener);
      if (nodeListeners?.size === 0) {
        this.listeners.delete(nodeId);
        const operation = this.operations.get(nodeId);
        if (operation) {
          this.release(operation, true);
          this.operations.delete(nodeId);
        }
        this.snapshots.delete(nodeId);
      }
    };
  };
  private notify(nodeId: string): void {
    for (const listener of this.listeners.get(nodeId) ?? []) listener();
  }
  private isCurrent(operation: ImportOperation): boolean {
    return (
      !this.disposed &&
      this.operations.get(operation.nodeId)?.token === operation.token
    );
  }
  private snapshot(
    operation: ImportOperation,
    phase: MediaImportPhase,
    active: boolean,
    errorMessage: string | null = null,
    recoveryKind: MediaImportRecoveryKind = null,
  ): void {
    if (!this.isCurrent(operation)) return;
    const current = this.snapshots.get(operation.nodeId);
    if (
      current?.phase === phase &&
      current.active === active &&
      current.errorMessage === errorMessage &&
      current.recoveryKind === recoveryKind
    )
      return;
    this.snapshots.set(
      operation.nodeId,
      Object.freeze({ phase, active, errorMessage, recoveryKind }),
    );
    this.notify(operation.nodeId);
  }
  private markActive(operation: ImportOperation, active: boolean): void {
    const before = this.activeTokens.size;
    if (active) this.activeTokens.add(operation.token);
    else this.activeTokens.delete(operation.token);
    if (before !== this.activeTokens.size)
      this.activeCallback(this.activeTokens.size);
  }
  private release(operation: ImportOperation, releaseFile: boolean): void {
    operation.controller?.abort();
    operation.queueController?.abort();
    operation.permit?.release();
    operation.targetLease?.release();
    operation.controller = null;
    operation.queueController = null;
    operation.permit = null;
    operation.targetLease = null;
    this.markActive(operation, false);
    if (releaseFile) operation.attempt.releaseFile();
  }
  private validTarget(operation: ImportOperation): boolean {
    const canvas = useCanvasStore.getState();
    const node = canvas.nodes.find(
      (candidate) => candidate.id === operation.nodeId,
    );
    return (
      canvas.projectId === operation.projectId &&
      canvas.loadEpoch === operation.loadEpoch &&
      !!node &&
      node.type === "media_input" &&
      node.data.kind === "media_input" &&
      node.data.artifactRevisionId === operation.expectedRevisionId
    );
  }
  private finishObsolete(operation: ImportOperation): void {
    this.release(operation, true);
    this.snapshot(
      operation,
      "final_error",
      false,
      "画布或媒体节点已变化，请重新选择文件。",
      "reselect",
    );
    this.operations.delete(operation.nodeId);
  }
  private finishRetryBlocked(
    operation: ImportOperation,
    blockedReason: string,
  ): void {
    this.release(operation, false);
    this.snapshot(
      operation,
      "retryable_error",
      false,
      blockedReason,
      "retry",
    );
  }
  selectFile(nodeId: string, file: File): void {
    if (this.disposed || operationGate(this.projectId, nodeId)) return;
    const current = this.operations.get(nodeId);
    if (current?.running || current?.queueController) return;
    if (current) this.release(current, true);
    const kind = inferRequestedMediaKind(file);
    const canvas = useCanvasStore.getState();
    const node = canvas.nodes.find((candidate) => candidate.id === nodeId);
    const reject = (message: string) => {
      this.operations.delete(nodeId);
      this.snapshots.set(
        nodeId,
        Object.freeze({
          phase: "final_error",
          active: false,
          errorMessage: message,
          recoveryKind: "reselect",
        }),
      );
      this.notify(nodeId);
    };
    if (!kind || !node || node.data.kind !== "media_input") {
      reject("请选择支持的 PNG、JPEG、WebP、MP4、WebM、MOV 或 MKV 文件。");
      return;
    }
    if (node.data.artifactRevisionId !== null && node.data.mediaKind !== kind) {
      reject(
        "替换素材必须选择相同类型的文件；如需切换图片与视频，请新建媒体节点。",
      );
      return;
    }
    if (file.size <= 0 || file.size > 10 * 1024 * 1024 * 1024) {
      reject("文件为空或超过 10 GiB，请重新选择。");
      return;
    }
    const declaredContentType = file.type.trim().toLowerCase() || null;
    if (
      Array.from(file.name).length === 0 ||
      Array.from(file.name).length > 512 ||
      (declaredContentType !== null &&
        Array.from(declaredContentType).length > 255)
    ) {
      reject("文件名或媒体类型声明过长，请重新选择文件。");
      return;
    }
    const declaration = Object.freeze({
      clientImportId: crypto.randomUUID(),
      filename: file.name,
      expectedByteSize: file.size,
      requestedMediaKind: kind,
      declaredContentType,
      replaceRevisionId: node.data.artifactRevisionId,
    });
    const operationId = declaration.clientImportId;
    const operation: ImportOperation = {
      token: Symbol("media-import"),
      nodeId,
      operationId,
      projectId: this.projectId,
      loadEpoch: canvas.loadEpoch,
      expectedRevisionId: node.data.artifactRevisionId,
      declaration,
      attempt: this.core.createAttempt({
        operationId,
        projectId: this.projectId,
        declaration,
        file,
      }),
      running: false,
      controller: null,
      queueController: null,
      permit: null,
      targetLease: this.targetLocks.acquire(operationId, nodeId),
    };
    if (!operation.targetLease) return;
    this.operations.set(nodeId, operation);
    this.begin(operation);
  }
  retry(nodeId: string): void {
    const operation = this.operations.get(nodeId);
    if (
      !operation ||
      operation.running ||
      operation.queueController ||
      this.getSnapshot(nodeId).phase !== "retryable_error"
    )
      return;
    const lease = this.targetLocks.acquire(operation.operationId, nodeId);
    if (!lease) {
      this.snapshot(
        operation,
        "retryable_error",
        false,
        "目标正被其他导入操作使用，请稍后重试。",
        "retry",
      );
      return;
    }
    operation.targetLease = lease;
    if (!this.validTarget(operation)) {
      this.finishObsolete(operation);
      return;
    }
    const blockedReason = operationGate(operation.projectId, nodeId);
    if (blockedReason) {
      this.finishRetryBlocked(operation, blockedReason);
      return;
    }
    this.begin(operation);
  }
  requestReload(): void {
    this.reloadCallback();
  }
  private begin(operation: ImportOperation): void {
    if (!this.isCurrent(operation)) return;
    const queueController = new AbortController();
    operation.queueController = queueController;
    this.snapshot(operation, "queued", true);
    const waiting = this.permits.acquire(
      operation.operationId,
      queueController.signal,
    );
    void waiting
      .then((permit) => {
        if (!this.isCurrent(operation) || queueController.signal.aborted) {
          permit.release();
          return;
        }
        operation.permit = permit;
        operation.queueController = null;
        if (!this.validTarget(operation)) {
          this.finishObsolete(operation);
          return;
        }
        const blockedReason = operationGate(
          operation.projectId,
          operation.nodeId,
        );
        if (blockedReason) {
          this.finishRetryBlocked(operation, blockedReason);
          return;
        }
        void this.execute(operation);
      })
      .catch((error) => {
        if (this.isCurrent(operation) && !isAbortError(error))
          this.finishError(operation, error);
      });
  }
  private async execute(operation: ImportOperation): Promise<void> {
    if (!this.isCurrent(operation) || operation.running) return;
    operation.running = true;
    operation.controller = new AbortController();
    this.markActive(operation, true);
    try {
      const revision = await operation.attempt.execute({
        signal: operation.controller.signal,
        onPhase: (phase: MediaImportNetworkPhase) =>
          this.snapshot(operation, phase, true),
      });
      if (!this.isCurrent(operation)) return;
      const attached = useCanvasStore
        .getState()
        .attachMediaRevision({
          projectId: operation.projectId,
          loadEpoch: operation.loadEpoch,
          nodeId: operation.nodeId,
          expectedRevisionId: operation.expectedRevisionId,
          revisionId: revision.revision_id,
          mediaKind: revision.media_kind,
        });
      if (!attached) {
        this.finishError(
          operation,
          new Error("媒体节点已变化，请重新选择文件。"),
        );
        return;
      }
      this.snapshot(operation, "completed", false);
      this.operations.delete(operation.nodeId);
      this.release(operation, true);
    } catch (error) {
      if (this.isCurrent(operation) && !isAbortError(error))
        this.finishError(operation, error);
    } finally {
      operation.running = false;
      operation.controller = null;
      this.markActive(operation, false);
      if (
        this.isCurrent(operation) &&
        this.getSnapshot(operation.nodeId).phase !== "retryable_error"
      )
        this.release(operation, true);
    }
  }
  private finishError(operation: ImportOperation, error: unknown): void {
    const kind = recovery(error);
    this.snapshot(
      operation,
      kind === "retry" ? "retryable_error" : "final_error",
      false,
      errorMessage(error),
      kind,
    );
    this.release(operation, kind !== "retry");
    if (kind !== "retry") this.operations.delete(operation.nodeId);
  }
}

export function createMediaImportRuntime(
  projectId: string,
  dependencies: MediaImportRuntimeDependencies,
): MediaImportRuntime {
  return new MediaImportRuntime(projectId, dependencies, false);
}

export type MediaImportProviderProps = {
  projectId: string;
  dependencies?: MediaImportRuntimeDependencies;
  onActiveCountChange?(count: number): void;
  onRequestReload?(): void;
  children: ReactNode;
};
export function MediaImportProvider({
  projectId,
  dependencies,
  onActiveCountChange = () => undefined,
  onRequestReload = () => undefined,
  children,
}: MediaImportProviderProps) {
  const resolvedDependencies = useMemo(
    () => dependencies ?? createMediaImportRuntimeDependencies(),
    [dependencies],
  );
  const runtime = useMemo(
    () =>
      new MediaImportRuntime(
        projectId,
        resolvedDependencies,
        dependencies === undefined,
      ),
    [dependencies, projectId, resolvedDependencies],
  );
  runtime.setActiveCallback(onActiveCountChange);
  runtime.setReloadCallback(onRequestReload);
  useEffect(() => {
    runtime.mount();
    return () => runtime.scheduleDispose();
  }, [runtime]);
  const contextValue = useMemo<MediaIngressRuntimePort>(
    () => ({
      projectId,
      loadEpoch: useCanvasStore.getState().loadEpoch,
      nodeImport: runtime,
      controller: null,
      // This wrapper is retained only for isolated legacy node tests.
      // Production composition is owned by MediaIngressProvider.
      targetLocks: resolvedDependencies.targetLocks,
    }),
    [projectId, resolvedDependencies.targetLocks, runtime],
  );
  return createElement(
    MediaIngressContext.Provider,
    { value: contextValue },
    children,
  );
}
export function useMediaImport(nodeId: string): MediaImportControls {
  const ingress = useContext(MediaIngressContext);
  const runtime = ingress?.nodeImport;
  if (!runtime) throw new Error("useMediaImport requires MediaIngressProvider");
  const subscribe = useCallback(
    (listener: () => void) => runtime.subscribe(nodeId, listener),
    [nodeId, runtime],
  );
  const getSnapshot = useCallback(
    () => runtime.getSnapshot(nodeId),
    [nodeId, runtime],
  );
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const replacementBlockedReason = useCanvasStore((_state) =>
    operationGate(runtime.projectId, nodeId),
  );
  return {
    ...snapshot,
    replacementBlockedReason,
    selectionDisabled: snapshot.active || replacementBlockedReason !== null,
    selectFile: (file) => runtime.selectFile(nodeId, file),
    retry: () => runtime.retry(nodeId),
    requestReload: () => runtime.requestReload(),
  };
}
