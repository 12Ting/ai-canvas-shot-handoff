import type { XYPosition } from "@xyflow/react";
import type { MediaKind } from "../canvasTypes";

export type IngressSource = "drop" | "paste" | "multi_picker";
export type IngressTargetMode =
  | "create"
  | "fill_existing"
  | "choose_create_or_replace";
export type IngressEntryErrorCode = "folder_not_supported" | "file_unavailable";
export type IngressCandidate =
  | Readonly<{ kind: "file"; file: File; displayName: string }>
  | Readonly<{
      kind: "entry_error";
      displayName: string;
      code: IngressEntryErrorCode;
      message: string;
    }>;
export type EmptyTargetBinding = Readonly<{
  targetNodeId: string;
  expectedRevisionId: null;
}>;
export type FilledTargetBinding = Readonly<{
  targetNodeId: string;
  expectedRevisionId: string;
}>;
export type TargetBinding = EmptyTargetBinding | FilledTargetBinding;
export type MediaReservation = Readonly<{
  nodeId: string;
  position: XYPosition;
  width: 312;
  height: 360;
}>;
type MediaIngressRequestBase = Readonly<{
  projectId: string;
  loadEpoch: number;
  source: IngressSource;
  candidates: readonly IngressCandidate[];
  anchor: XYPosition;
}>;
export type MediaIngressRequest = MediaIngressRequestBase &
  (
    | Readonly<{ targetMode: "create"; targetBinding: null }>
    | Readonly<{
        targetMode: "fill_existing";
        targetBinding: EmptyTargetBinding;
      }>
    | Readonly<{
        targetMode: "choose_create_or_replace";
        targetBinding: FilledTargetBinding;
      }>
  );
export type IngressItemState =
  | "QUEUED"
  | "INITIALIZING"
  | "RECEIVING"
  | "VALIDATING"
  | "CANCELLING"
  | "DISCARDING"
  | "PLACING"
  | "PLACED"
  | "FAILED"
  | "CANCELLED"
  | "SAVED_UNPLACED"
  | "IGNORED";
export type MediaIngressAdmission =
  | Readonly<{ kind: "empty" }>
  | Readonly<{ kind: "rejected"; message: string }>
  | Readonly<{ kind: "choice_required"; choiceId: string }>
  | Readonly<{ kind: "accepted"; batchId: string }>;
export type MediaIngressGuardState = Readonly<{
  pendingOperationCount: number;
  hasSavedUnplaced: boolean;
}>;
export type PromoteMediaReservationInput = Readonly<{
  projectId: string;
  loadEpoch: number;
  nodeId: string;
  position: XYPosition;
  revisionId: string;
  mediaKind: MediaKind;
}>;
export type IngressItemSnapshot = Readonly<{
  itemId: string;
  clientImportId: string;
  state: IngressItemState;
  displayName: string;
  mediaKind: MediaKind | null;
  errorMessage: string | null;
  discardErrorMessage: string | null;
  canCancel: boolean;
  canRetryCancellation: boolean;
  canRetryDiscard: boolean;
  canForceRefresh: boolean;
  /** Large-upload sessions are resumable only for the current page lifetime. */
  resumeScopeMessage?: string | null;
  /** Server-confirmed progress for the active B4 upload, never an optimistic byte count. */
  largeUploadProgress?: Readonly<{
    receivedByteSize: number;
    totalByteSize: number;
    estimatedSecondsRemaining: number | null;
  }> | null;
}>;
export type IngressSlotSnapshot = Readonly<{
  slotId: string;
  reservation: MediaReservation;
  currentItem: IngressItemSnapshot;
  previousAttempts: readonly IngressItemSnapshot[];
  attemptCount: number;
  targetNodeId: string | null;
  canReselect: boolean;
  canIgnore: boolean;
  canPlaceSavedUnplaced: boolean;
}>;
export type IngressBatchCounts = Readonly<{
  total: number;
  queued: number;
  processing: number;
  placed: number;
  failed: number;
  cancelled: number;
  savedUnplaced: number;
  ignored: number;
  terminal: number;
}>;
export type IngressBatchSnapshot = Readonly<{
  batchId: string;
  source: IngressSource;
  createdOrder: number;
  collapsed: boolean;
  counts: IngressBatchCounts;
  slots: readonly IngressSlotSnapshot[];
  canStop: boolean;
  canClose: boolean;
}>;
export type PendingTargetChoiceSnapshot = Readonly<{
  choiceId: string;
  displayName: string;
  targetNodeId: string;
  requestedMediaKind: MediaKind | null;
  currentMediaKind: MediaKind;
  canReplace: boolean;
  replacementBlockedReason: string | null;
}>;
export type MediaIngressSnapshot = Readonly<{
  batches: readonly IngressBatchSnapshot[];
  pendingTargetChoices: readonly PendingTargetChoiceSnapshot[];
  protectedTargetNodeIds: readonly string[];
  guardState: MediaIngressGuardState;
}>;

export interface MediaIngressController {
  mount(): void;
  unmount(): void;
  enqueue(request: MediaIngressRequest): MediaIngressAdmission;
  resolveTargetChoice(
    choiceId: string,
    choice: "create" | "replace" | "cancel",
  ): MediaIngressAdmission;
  cancelItem(batchId: string, itemId: string): void;
  stopBatch(batchId: string): void;
  reselect(batchId: string, slotId: string, file: File): void;
  retryCancellation(batchId: string, itemId: string): void;
  requestForceRefresh(): void;
  placeSavedUnplaced(batchId: string, slotId: string): void;
  ignoreSlot(batchId: string, slotId: string): void;
  retryDiscard(batchId: string, slotId: string): void;
  toggleBatchCollapsed(batchId: string): void;
  closeBatch(batchId: string): void;
  getSnapshot(): MediaIngressSnapshot;
  subscribe(listener: () => void): () => void;
  dispose(): void;
}
