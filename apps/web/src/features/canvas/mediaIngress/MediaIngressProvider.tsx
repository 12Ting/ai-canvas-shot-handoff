import { useEffect, useMemo, type ReactNode } from "react";
import { useCanvasStore } from "../canvasStore";
import {
  createMediaImportRuntime,
  createMediaImportRuntimeDependencies,
  type MediaImportRuntimeDependencies,
} from "../useMediaImport";
import { MediaIngressContext, type MediaIngressRuntimePort } from "./mediaIngressContext";
import { createMediaIngressController } from "./mediaIngressController";
import type { MediaIngressObstacle } from "./mediaIngressLayout";
import type { MediaIngressGuardState } from "./mediaIngressTypes";

const NOOP_RELOAD = () => undefined;
const NOOP_PENDING_COUNT_CHANGE = () => undefined;
const NOOP_GUARD_STATE_CHANGE = () => undefined;
const REFUSE_FORCE_REFRESH = () => false;

function sameGuardState(
  left: MediaIngressGuardState | null,
  right: MediaIngressGuardState,
): boolean {
  return (
    left !== null &&
    left.pendingOperationCount === right.pendingOperationCount &&
    left.hasSavedUnplaced === right.hasSavedUnplaced
  );
}

export type MediaIngressProviderProps = Readonly<{
  projectId: string;
  loadEpoch: number;
  generation: string;
  dependencies?: MediaImportRuntimeDependencies;
  onPendingCountChange?(generation: string, count: number): void;
  onGuardStateChange?(
    generation: string,
    state: MediaIngressGuardState,
  ): void;
  onRequestReload?(): void;
  confirmForceRefresh?(): boolean;
  onForceRefreshApproved?(): void;
  children: ReactNode;
}>;

function nodeObstacle(node: ReturnType<typeof useCanvasStore.getState>["nodes"][number]): MediaIngressObstacle {
  const measuredWidth = node.measured?.width;
  const measuredHeight = node.measured?.height;
  const fallback =
    node.data.kind === "media_input"
      ? { width: 312, height: 360 }
      : node.data.kind === "shot"
        ? { width: 280, height: 180 }
        : { width: 248, height: 180 };
  return {
    x: node.position.x,
    y: node.position.y,
    width:
      typeof measuredWidth === "number" &&
      Number.isFinite(measuredWidth) &&
      measuredWidth > 0
        ? measuredWidth
        : fallback.width,
    height:
      typeof measuredHeight === "number" &&
      Number.isFinite(measuredHeight) &&
      measuredHeight > 0
        ? measuredHeight
        : fallback.height,
  };
}

export function MediaIngressProvider({
  projectId,
  loadEpoch,
  generation,
  dependencies,
  onPendingCountChange = NOOP_PENDING_COUNT_CHANGE,
  onGuardStateChange = NOOP_GUARD_STATE_CHANGE,
  onRequestReload = NOOP_RELOAD,
  confirmForceRefresh = REFUSE_FORCE_REFRESH,
  onForceRefreshApproved = NOOP_RELOAD,
  children,
}: MediaIngressProviderProps) {
  const bundle = useMemo(() => {
    const shared = dependencies ?? createMediaImportRuntimeDependencies();
    const nodeImport = createMediaImportRuntime(projectId, shared);
    const lifecycle = {
      abandoned: false,
      confirmForceRefresh: REFUSE_FORCE_REFRESH,
      reportAbandoned: NOOP_RELOAD,
      forceRefreshApproved: NOOP_RELOAD,
      abandonAllNow: NOOP_RELOAD,
    };
    const controller = createMediaIngressController({
      projectId,
      loadEpoch,
      core: shared.core,
      permits: shared.permits,
      targetLocks: shared.targetLocks,
      schedulePlacement: (callback) => queueMicrotask(callback),
      confirmForceRefresh: () => lifecycle.confirmForceRefresh(),
      abandonAllNow: () => lifecycle.abandonAllNow(),
      canvas: {
        getBinding: () => {
          const state = useCanvasStore.getState();
          return {
            projectId: state.projectId,
            loadEpoch: state.loadEpoch,
            conflict: state.status === "conflict",
          };
        },
        getObstacles: () => useCanvasStore.getState().nodes.map(nodeObstacle),
        promote: (input) => useCanvasStore.getState().promoteMediaReservation(input),
      },
    });
    lifecycle.abandonAllNow = () => {
      if (lifecycle.abandoned) return;
      lifecycle.abandoned = true;
      shared.permits.dispose();
      controller.dispose();
      nodeImport.abandonAllNow();
      const broker = shared.permits.getSnapshot();
      const aggregateIsZero =
        broker.runningOperationIds.length === 0 &&
        broker.queuedOperationIds.length === 0 &&
        controller.getSnapshot().guardState.pendingOperationCount === 0 &&
        nodeImport.getPendingOperationCount() === 0;
      if (!aggregateIsZero) return;
      lifecycle.reportAbandoned();
      lifecycle.forceRefreshApproved();
    };
    const value: MediaIngressRuntimePort = Object.freeze({
      projectId,
      loadEpoch,
      nodeImport,
      controller,
      targetLocks: shared.targetLocks,
    });
    return {
      shared,
      ownsDependencies: dependencies === undefined,
      nodeImport,
      controller,
      lifecycle,
      value,
      lastGuardState: null as MediaIngressGuardState | null,
      reportGuardState: NOOP_RELOAD as (force?: boolean) => void,
      disposeTimer: null as ReturnType<typeof setTimeout> | null,
    };
  }, [dependencies, loadEpoch, projectId]);

  useEffect(() => {
    bundle.lifecycle.confirmForceRefresh = confirmForceRefresh;
    bundle.lifecycle.reportAbandoned = () => bundle.reportGuardState(true);
    bundle.lifecycle.forceRefreshApproved = onForceRefreshApproved;
  }, [
    bundle,
    confirmForceRefresh,
    onForceRefreshApproved,
  ]);

  useEffect(() => {
    bundle.nodeImport.setReloadCallback(onRequestReload);
    return () => bundle.nodeImport.setReloadCallback(NOOP_RELOAD);
  }, [bundle, onRequestReload]);

  useEffect(() => {
    if (bundle.disposeTimer) clearTimeout(bundle.disposeTimer);
    bundle.disposeTimer = null;
    bundle.controller.mount();
    bundle.nodeImport.mount();
    const report = (force = false) => {
      if (bundle.lifecycle.abandoned && !force) return;
      const broker = bundle.shared.permits.getSnapshot();
      const operationIds = new Set([
        ...broker.runningOperationIds,
        ...broker.queuedOperationIds,
      ]);
      const controllerSnapshot = bundle.controller.getSnapshot();
      for (const choice of controllerSnapshot.pendingTargetChoices) {
        operationIds.add(choice.choiceId);
      }
      for (const batch of controllerSnapshot.batches) {
        for (const slot of batch.slots) {
          if (
            slot.currentItem.state === "QUEUED" ||
            slot.currentItem.state === "INITIALIZING" ||
            slot.currentItem.state === "RECEIVING" ||
            slot.currentItem.state === "VALIDATING" ||
            slot.currentItem.state === "CANCELLING" ||
            slot.currentItem.state === "DISCARDING" ||
            slot.currentItem.state === "PLACING"
          ) {
            operationIds.add(slot.currentItem.itemId);
          }
        }
      }
      const next: MediaIngressGuardState = Object.freeze({
        pendingOperationCount: operationIds.size,
        hasSavedUnplaced: controllerSnapshot.guardState.hasSavedUnplaced,
      });
      if (sameGuardState(bundle.lastGuardState, next)) return;
      bundle.lastGuardState = next;
      onPendingCountChange(generation, next.pendingOperationCount);
      onGuardStateChange(generation, next);
    };
    bundle.reportGuardState = report;
    bundle.nodeImport.setActiveCallback(() => report());
    const unsubscribe = bundle.controller.subscribe(report);
    const unsubscribeBroker = bundle.shared.permits.subscribe(report);
    report();
    return () => {
      unsubscribe();
      unsubscribeBroker();
      bundle.controller.unmount();
      bundle.disposeTimer = setTimeout(() => {
        bundle.disposeTimer = null;
        bundle.controller.dispose();
        bundle.nodeImport.disposeNow();
        if (bundle.ownsDependencies) bundle.shared.permits.dispose();
      }, 0);
    };
  }, [bundle, generation, onGuardStateChange, onPendingCountChange]);

  return (
    <MediaIngressContext.Provider value={bundle.value}>
      {children}
    </MediaIngressContext.Provider>
  );
}
