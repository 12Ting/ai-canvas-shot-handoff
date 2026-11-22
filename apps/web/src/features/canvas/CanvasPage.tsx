import type { Viewport } from "@xyflow/react";
import { useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  applyGraphChangeSet,
  approveGraphChangeSet,
  createProject,
  getBranchGraph,
  getGraph,
  getProject,
  listBranches,
  listProjects,
  proposeGraphChangeSet,
  saveViewport,
  type GraphChangeSetResponse,
  type BranchWorkspaceResponse,
  type ProjectResponse,
} from "../../api/client";
import {
  CanvasSurface,
  type ShotAssociationCandidate,
} from "./CanvasSurface";
import { useCanvasStore } from "./canvasStore";
import type { CanvasRuntimeIdentity } from "./canvasTypes";
import { ArtifactHistoryDrawer, type ShotHistoryTarget } from "./components/ArtifactHistoryDrawer";
import { ControlConfigPanel } from "./components/ControlConfigPanel";
import { GraphChangeReview } from "./components/GraphChangeReview";
import { NodePalette } from "./components/NodePalette";
import { NoEgressDryRunPanel } from "./components/NoEgressDryRunPanel";
import { OperationTracePanel } from "./components/OperationTracePanel";
import { RevisionConflictBanner } from "./components/RevisionConflictBanner";
import { SaveStatus } from "./components/SaveStatus";
import { ShotDeleteDialog } from "./components/ShotDeleteDialog";
import { ShotInspector } from "./components/ShotInspector";
import { ShotTrack } from "./components/ShotTrack";
import { useFloatingPanel } from "./components/useFloatingPanel";
import { fromGraphSnapshot, toGraphSnapshot } from "./graphMappers";
import { isCompatibleShotSlot } from "./shotMediaTransfer";
import { MediaIngressProvider } from "./mediaIngress/MediaIngressProvider";
import { MediaIngressContext } from "./mediaIngress/mediaIngressContext";
import { MediaLifecyclePanel } from "./mediaLifecycle/MediaLifecyclePanel";
import { ResourceCatalogPanel } from "../resources/ResourceCatalogPanel";
import { bindDirectory, createDirectoryProject, DirectorySelectionCancelled, studioUrl } from "../handoff/client";
import "../handoff/studio.css";
import { BranchPanel } from "../branching/BranchPanel";
import { AgentProposalPanel } from "../agent/AgentProposalPanel";
import { PromptBatchPanel } from "../promptBatch/PromptBatchPanel";
import { ModelProfileSettingsPanel } from "../modelProfiles/ModelProfileSettingsPanel";
import { deriveBranchViewState } from "../branching/branchViewState";
import "./components/floatingPanels.css";
import "./components/shotAssociationHistory.css";
import "./components/shotVisualIdentity.css";

const DEFAULT_PROJECT_NAME = "未命名创作";
const PROJECT_NAME_LIMIT = 200;
const CONFLICT_COPY_SUFFIX = "（冲突副本）";

type BoundShotHistoryTarget = ShotHistoryTarget &
  CanvasRuntimeIdentity &
  Readonly<{ anchorRevisionId: string }>;
type BoundShotDeleteTarget = CanvasRuntimeIdentity & Readonly<{ shotId: string }>;
type PendingConflictCopyReview = Readonly<{
  sourceProjectId: string;
  capturedEpoch: number;
  capturedSequence: number;
  copyProjectId: string;
  proposal: GraphChangeSetResponse;
}>;

function queryProject(): string | null {
  return new URLSearchParams(location.search).get("project");
}

function replaceProjectQuery(projectId: string | null): void {
  const url = new URL(location.href);
  if (projectId) url.searchParams.set("project", projectId);
  else {
    url.searchParams.delete("project");
    url.searchParams.delete("view");
  }
  history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

function userMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误，请重试。";
}

function conflictCopyName(projectName: string): string {
  const base = projectName.trim() || DEFAULT_PROJECT_NAME;
  return `${base.slice(0, PROJECT_NAME_LIMIT - CONFLICT_COPY_SUFFIX.length)}${CONFLICT_COPY_SUFFIX}`;
}

function CanvasShotWorkspace({
  associationTarget,
  deletingShotTarget,
  historyTarget,
  onCloseHistory,
  onCloseShotDelete,
  onMoveEnd,
  onOpenHistory,
  onRequestDelete,
  onShotSelectionChange,
  selectedShotId,
}: {
  associationTarget: import("./canvasTypes").ShotAssociationTarget | null;
  deletingShotTarget: BoundShotDeleteTarget | null;
  historyTarget: BoundShotHistoryTarget | null;
  onCloseHistory(): void;
  onCloseShotDelete(): void;
  onMoveEnd(viewport: Viewport): void;
  onOpenHistory(target: ShotHistoryTarget): void;
  onRequestDelete(shotId: string): void;
  onShotSelectionChange(shotId: string | null): void;
  selectedShotId: string | null;
}) {
  const mediaIngress = useContext(MediaIngressContext);
  const currentProjectId = useCanvasStore((state) => state.projectId);
  const currentLoadEpoch = useCanvasStore((state) => state.loadEpoch);
  const selectedShotIsCurrent = useCanvasStore((state) =>
    selectedShotId !== null && state.shots.some((shot) => shot.id === selectedShotId),
  );
  const workspaceRef = useRef<HTMLDivElement>(null);
  const deleteTriggerRef = useRef<HTMLElement | null>(null);
  const inspectorFloatingPanel = useFloatingPanel({
    panelId: "shot-inspector",
    projectId: currentProjectId,
  });
  const trackFloatingPanel = useFloatingPanel({
    panelId: "shot-track",
    projectId: currentProjectId,
  });
  const historyTargetIsCurrent =
    historyTarget !== null &&
    historyTarget.shotId === selectedShotId &&
    historyTarget.projectId === currentProjectId &&
    historyTarget.loadEpoch === currentLoadEpoch;
  const deleteTargetIsCurrent =
    deletingShotTarget !== null &&
    deletingShotTarget.projectId === currentProjectId &&
    deletingShotTarget.loadEpoch === currentLoadEpoch;
  const associateMediaWithShot = useCallback(
    (candidate: ShotAssociationCandidate) => {
      const current = useCanvasStore.getState();
      const target = current.associationTarget;
      const nodeId = candidate.nodeId;
      if (
        current.status === "conflict" ||
        !target ||
        target.shotId !== candidate.target.shotId ||
        target.slot !== candidate.target.slot ||
        currentProjectId === null ||
        current.projectId !== currentProjectId ||
        current.loadEpoch !== currentLoadEpoch ||
        mediaIngress === null ||
        mediaIngress.projectId !== currentProjectId ||
        mediaIngress.loadEpoch !== currentLoadEpoch ||
        mediaIngress.targetLocks.getSnapshot().protectedNodeIds.includes(nodeId)
      ) {
        return;
      }
      const node = current.nodes.find((candidate) => candidate.id === nodeId);
      if (
        !node ||
        node.data.kind !== "media_input" ||
        node.data.artifactRevisionId === null ||
        node.data.mediaKind === null ||
        node.data.artifactRevisionId !== candidate.revision.revisionId ||
        node.data.mediaKind !== candidate.revision.mediaKind ||
        !isCompatibleShotSlot(target.slot, node.data.mediaKind)
      ) {
        return;
      }
      const revision = {
        revisionId: candidate.revision.revisionId,
        mediaKind: candidate.revision.mediaKind,
      };
      const changed =
        target.slot === "reference"
          ? current.addShotReference(target.shotId, revision)
          : current.setShotSlot(target.shotId, target.slot, revision);
      if (changed) current.cancelShotAssociation();
    },
    [currentLoadEpoch, currentProjectId, mediaIngress],
  );

  return (
    <div className="canvas-workspace" ref={workspaceRef} tabIndex={-1}>
      <CanvasSurface
        associationTarget={associationTarget}
        onAssociateMedia={associateMediaWithShot}
        onMoveEnd={onMoveEnd}
        onShotSelectionChange={onShotSelectionChange}
      />
      {historyTargetIsCurrent ? (
        <ArtifactHistoryDrawer
          floatingPanel={inspectorFloatingPanel}
          identity={historyTarget}
          onClose={onCloseHistory}
          target={historyTarget}
        />
      ) : selectedShotIsCurrent ? (
        <ShotInspector
          associationTarget={associationTarget}
          floatingPanel={inspectorFloatingPanel}
          onOpenHistory={onOpenHistory}
          selectedShotId={selectedShotId}
        />
      ) : null}
      <ShotTrack
        floatingPanel={trackFloatingPanel}
        onRequestDelete={(shotId) => {
          deleteTriggerRef.current =
            document.activeElement instanceof HTMLElement ? document.activeElement : null;
          onRequestDelete(shotId);
        }}
        selectedShotId={selectedShotId}
      />
      <ShotDeleteDialog
        fallbackFocusRef={workspaceRef}
        getTriggerElement={() => deleteTriggerRef.current}
        identity={deleteTargetIsCurrent ? deletingShotTarget : null}
        onClose={onCloseShotDelete}
        shotId={deleteTargetIsCurrent ? deletingShotTarget.shotId : null}
      />
    </div>
  );
}

export function CanvasPage() {
  const [projectId, setProjectId] = useState<string | null>(queryProject);
  const [loadedProjectId, setLoadedProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("");
  const [handoffEnabled, setHandoffEnabled] = useState(false);
  const [newName, setNewName] = useState(DEFAULT_PROJECT_NAME);
  const [recent, setRecent] = useState<ProjectResponse[]>([]);
  const [branchWorkspace, setBranchWorkspace] = useState<BranchWorkspaceResponse | null>(null);
  const [viewedBranchId, setViewedBranchId] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(projectId));
  const [recentLoading, setRecentLoading] = useState(!projectId);
  const [creating, setCreating] = useState(false);
  const [returnRequested, setReturnRequested] = useState(false);
  const [activeImportCount, setActiveImportCount] = useState(0);
  const [hasSavedUnplaced, setHasSavedUnplaced] = useState(false);
  const [pageError, setPageError] = useState("");
  const [viewportError, setViewportError] = useState("");
  const [c3Open, setC3Open] = useState(false);
  const [c4Open, setC4Open] = useState(false);
  const [conflictCopyReview, setConflictCopyReview] =
    useState<PendingConflictCopyReview | null>(null);
  const [agentGraphProposal, setAgentGraphProposal] = useState<GraphChangeSetResponse | null>(null);
  const [historyTarget, setHistoryTarget] = useState<BoundShotHistoryTarget | null>(null);
  const [deletingShotTarget, setDeletingShotTarget] = useState<BoundShotDeleteTarget | null>(null);
  const loadRequestEpoch = useRef(0);
  const branchLoadRequestEpoch = useRef(0);
  const recentRequestEpoch = useRef(0);
  const projectIdRef = useRef(projectId);
  const activeImportCountRef = useRef(0);
  const hasSavedUnplacedRef = useRef(false);
  const activeImportGenerationRef = useRef(0);
  const activeImportRuntimeRef = useRef<string | null>(null);
  const forceRefreshApprovedRef = useRef(false);
  const mountedRef = useRef(true);
  const conflictActionInFlight = useRef(false);
  const conflictCopyTriggerRef = useRef<HTMLElement | null>(null);
  const viewportQueues = useRef(new Map<string, Promise<void>>());
  const status = useCanvasStore((state) => state.status);
  const loadEpoch = useCanvasStore((state) => state.loadEpoch);
  const load = useCanvasStore((state) => state.load);
  const selectedShotId = useCanvasStore((state) => state.selectedShotId);
  const shots = useCanvasStore((state) => state.shots);
  const associationTarget = useCanvasStore((state) => state.associationTarget);
  const selectShot = useCanvasStore((state) => state.selectShot);
  projectIdRef.current = projectId;
  const mediaImportRuntimeGeneration =
    projectId === null ? null : `${projectId}:${loadEpoch}`;
  activeImportRuntimeRef.current = mediaImportRuntimeGeneration;

  useEffect(() => {
    setHistoryTarget((target) =>
      target !== null && target.projectId === projectId && target.loadEpoch === loadEpoch
        ? target
        : null,
    );
    setDeletingShotTarget((target) =>
      target !== null && target.projectId === projectId && target.loadEpoch === loadEpoch
        ? target
        : null,
    );
  }, [loadEpoch, projectId]);

  const handleIngressGuardState = useCallback((generation: string | null, state: {
    pendingOperationCount: number;
    hasSavedUnplaced: boolean;
  }) => {
    if (activeImportRuntimeRef.current !== generation) return;
    if (
      activeImportCountRef.current !== state.pendingOperationCount ||
      hasSavedUnplacedRef.current !== state.hasSavedUnplaced
    ) {
      activeImportGenerationRef.current += 1;
    }
    activeImportCountRef.current = state.pendingOperationCount;
    hasSavedUnplacedRef.current = state.hasSavedUnplaced;
    setActiveImportCount(state.pendingOperationCount);
    setHasSavedUnplaced(state.hasSavedUnplaced);
  }, []);

  const focusBatchCenter = useCallback(() => {
    setPageError("请先在媒体导入批次中心放入画布或删除本地素材后再继续。");
    queueMicrotask(() => {
      const center = document.querySelector<HTMLElement>(".media-ingress-batches");
      if (!center) return;
      center.tabIndex = -1;
      center.focus({ preventScroll: true });
      center.scrollIntoView?.({ block: "nearest" });
    });
  }, []);

  const loadCurrent = useCallback(
    async (id: string, preserveCurrent = false) => {
      const requestEpoch = ++loadRequestEpoch.current;
      branchLoadRequestEpoch.current += 1;
      const preservedCanvas = preserveCurrent
        ? (() => {
            const state = useCanvasStore.getState();
            return {
              projectId: state.projectId,
              loadEpoch: state.loadEpoch,
              editSequence: state.editSequence,
              savedSequence: state.savedSequence,
              status: state.status,
              baseRevisionId: state.baseRevisionId,
              activeImportGeneration: activeImportGenerationRef.current,
            };
          })()
        : null;
      if (!preserveCurrent) {
        setLoading(true);
        setLoadedProjectId(null);
        setViewportError("");
      }
      setPageError("");
      try {
        const [project, graph, branches] = await Promise.all([
          getProject(id),
          getGraph(id),
          listBranches(id),
        ]);
        if (
          !mountedRef.current ||
          requestEpoch !== loadRequestEpoch.current ||
          projectIdRef.current !== id
        ) {
          return;
        }
        if (preservedCanvas) {
          if (
            activeImportCountRef.current > 0 ||
            hasSavedUnplacedRef.current ||
            activeImportGenerationRef.current !==
              preservedCanvas.activeImportGeneration
          ) {
            setPageError("重新加载期间有媒体导入开始，未重新加载服务端版本。");
            return;
          }
          const current = useCanvasStore.getState();
          if (
            current.projectId !== preservedCanvas.projectId ||
            current.loadEpoch !== preservedCanvas.loadEpoch ||
            current.editSequence !== preservedCanvas.editSequence ||
            current.savedSequence !== preservedCanvas.savedSequence ||
            current.status !== preservedCanvas.status ||
            current.baseRevisionId !== preservedCanvas.baseRevisionId
          ) {
            setPageError("重新加载期间画布已变化，未重新加载服务端版本。");
            return;
          }
        }
        if (project.handoff_enabled && !new URLSearchParams(location.search).has("view")) {
          location.replace(studioUrl(id, "shots"));
          return;
        }
        setHandoffEnabled(Boolean(project.handoff_enabled));
        const mapped = fromGraphSnapshot(graph.snapshot);
        setProjectName(project.name);
        setBranchWorkspace(branches);
        setViewedBranchId(branches.selection.branch_id);
        load({
          projectId: id,
          nodes: mapped.nodes,
          edges: mapped.edges,
          shots: mapped.shots,
          resourceBindings: mapped.resourceBindings,
          revisionId: graph.revision_id,
          viewport: graph.viewport,
          branchContext: deriveBranchViewState(
            branches,
            branches.selection.branch_id,
          ),
        });
        setLoadedProjectId(id);
      } catch (error: unknown) {
        if (
          mountedRef.current &&
          requestEpoch === loadRequestEpoch.current &&
          projectIdRef.current === id
        ) {
          setPageError(userMessage(error));
        }
      } finally {
        if (
          !preserveCurrent &&
          mountedRef.current &&
          requestEpoch === loadRequestEpoch.current &&
          projectIdRef.current === id
        ) {
          setLoading(false);
        }
      }
    },
    [load],
  );

  const refreshBranchWorkspace = useCallback(async () => {
    if (!projectId) return;
    const workspace = await listBranches(projectId);
    setBranchWorkspace(workspace);
    const currentViewedBranchId = useCanvasStore.getState().viewedBranchId;
    if (currentViewedBranchId) {
      useCanvasStore
        .getState()
        .syncBranchContext(
          deriveBranchViewState(workspace, currentViewedBranchId),
        );
    }
  }, [projectId]);

  const openBranch = useCallback(
    async (
      branchId: string,
      options: Readonly<{
        force?: boolean;
        confirmDiscard?: boolean;
        refreshAppliedBranch?: boolean;
      }> = {},
    ) => {
      if (!projectId || (!options.force && branchId === viewedBranchId)) return;
      const current = useCanvasStore.getState();
      if (
        options.confirmDiscard !== false &&
        current.status === "dirty" &&
        !window.confirm("切换分支会放弃当前未审阅草稿，是否继续？")
      ) {
        return;
      }
      const requestEpoch = ++branchLoadRequestEpoch.current;
      const captured = {
        projectId: current.projectId,
        loadEpoch: current.loadEpoch,
        editSequence: current.editSequence,
      };
      try {
        const [branchGraph, workspace] = await Promise.all([
          getBranchGraph(projectId, branchId),
          listBranches(projectId),
        ]);
        const latest = useCanvasStore.getState();
        if (
          !mountedRef.current ||
          requestEpoch !== branchLoadRequestEpoch.current ||
          projectIdRef.current !== projectId ||
          latest.projectId !== captured.projectId ||
          latest.loadEpoch !== captured.loadEpoch ||
          latest.editSequence !== captured.editSequence
        ) {
          return;
        }
        const mapped = fromGraphSnapshot(branchGraph.revision.snapshot);
        const nextCanvas = {
          projectId,
          nodes: mapped.nodes,
          edges: mapped.edges,
          shots: mapped.shots,
          resourceBindings: mapped.resourceBindings,
          revisionId: branchGraph.revision.revision_id,
          viewport: branchGraph.revision.viewport,
          branchContext: deriveBranchViewState(workspace, branchId),
        };
        if (
          options.refreshAppliedBranch &&
          branchId === current.viewedBranchId
        ) {
          useCanvasStore.getState().refreshApplied(nextCanvas);
        } else {
          load(nextCanvas);
        }
        setViewedBranchId(branchId);
        if (
          mountedRef.current &&
          requestEpoch === branchLoadRequestEpoch.current &&
          projectIdRef.current === projectId
        ) {
          setBranchWorkspace(workspace);
        }
      } catch (error: unknown) {
        if (
          mountedRef.current &&
          requestEpoch === branchLoadRequestEpoch.current &&
          projectIdRef.current === projectId
        ) {
          setPageError(userMessage(error));
        }
      }
    },
    [load, projectId, viewedBranchId],
  );

  const handleBranchApplied = useCallback(
    async (changeSet: GraphChangeSetResponse) => {
      if (!projectId) return;
      const branchId = changeSet.created_branch_id ?? changeSet.applied_branch_id;
      if (branchId) {
        await openBranch(branchId, {
          force: true,
          confirmDiscard: false,
          refreshAppliedBranch: changeSet.created_branch_id === null,
        });
      }
    },
    [openBranch, projectId],
  );

  const refreshRecent = useCallback(async () => {
    const requestEpoch = ++recentRequestEpoch.current;
    setRecentLoading(true);
    setPageError("");
    try {
      const projects = await listProjects();
      if (
        mountedRef.current &&
        requestEpoch === recentRequestEpoch.current &&
        projectIdRef.current === null
      ) {
        setRecent(projects);
      }
    } catch (error: unknown) {
      if (
        mountedRef.current &&
        requestEpoch === recentRequestEpoch.current &&
        projectIdRef.current === null
      ) {
        setPageError(userMessage(error));
      }
    } finally {
      if (
        mountedRef.current &&
        requestEpoch === recentRequestEpoch.current &&
        projectIdRef.current === null
      ) {
        setRecentLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (projectId) {
      void loadCurrent(projectId);
    } else {
      loadRequestEpoch.current += 1;
      setLoading(false);
      setLoadedProjectId(null);
      setViewportError("");
      void refreshRecent();
    }
  }, [projectId, loadCurrent, refreshRecent]);

  useEffect(() => {
    activeImportCountRef.current = 0;
    hasSavedUnplacedRef.current = false;
    setActiveImportCount(0);
    setHasSavedUnplaced(false);
  }, [mediaImportRuntimeGeneration]);

  useEffect(() => {
    const handleNavigation = () => {
      const nextProjectId = queryProject();
      if (nextProjectId === projectIdRef.current) return;
      if (activeImportCountRef.current > 0 || hasSavedUnplacedRef.current) {
        replaceProjectQuery(projectIdRef.current);
        if (hasSavedUnplacedRef.current) focusBatchCenter();
        return;
      }
      useCanvasStore.getState().reset();
      setPageError("");
      setViewportError("");
      setLoadedProjectId(null);
      setReturnRequested(false);
      setProjectId(nextProjectId);
    };
    window.addEventListener("popstate", handleNavigation);
    return () => window.removeEventListener("popstate", handleNavigation);
  }, [focusBatchCenter]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadRequestEpoch.current += 1;
      recentRequestEpoch.current += 1;
    };
  }, []);

  useEffect(() => {
    if (status === "clean" && activeImportCount === 0 && !hasSavedUnplaced) return;
    const protectDraft = (event: BeforeUnloadEvent) => {
      if (forceRefreshApprovedRef.current) {
        forceRefreshApprovedRef.current = false;
        return;
      }
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protectDraft);
    return () => window.removeEventListener("beforeunload", protectDraft);
  }, [activeImportCount, hasSavedUnplaced, status]);

  useEffect(() => {
    if (hasSavedUnplaced) focusBatchCenter();
  }, [focusBatchCenter, hasSavedUnplaced]);

  const switchProject = useCallback((nextProjectId: string | null) => {
    if (activeImportCountRef.current > 0) return;
    if (hasSavedUnplacedRef.current) {
      focusBatchCenter();
      return;
    }
    replaceProjectQuery(nextProjectId);
    useCanvasStore.getState().reset();
    setPageError("");
    setViewportError("");
    setLoadedProjectId(null);
    setReturnRequested(false);
    setProjectId(nextProjectId);
  }, [focusBatchCenter]);

  const returnBlocked =
    status === "conflict" ||
    status === "error" ||
    activeImportCount > 0 ||
    hasSavedUnplaced;
  const returnToProjects = useCallback(() => {
    if (hasSavedUnplaced) {
      focusBatchCenter();
      return;
    }
    if (returnBlocked || returnRequested) return;
    if (status === "clean") {
      switchProject(null);
      return;
    }
    setReturnRequested(true);
  }, [focusBatchCenter, hasSavedUnplaced, returnBlocked, returnRequested, status, switchProject]);

  useEffect(() => {
    if (!returnRequested) return;
    if (activeImportCount > 0) return;
    if (hasSavedUnplaced) {
      setReturnRequested(false);
      focusBatchCenter();
      return;
    }
    if (status === "clean") {
      switchProject(null);
      return;
    }
    if (status === "conflict" || status === "error") {
      setReturnRequested(false);
    }
  }, [activeImportCount, focusBatchCenter, hasSavedUnplaced, returnRequested, status, switchProject]);

  const createBlank = async () => {
    const name = newName.trim();
    if (!name || creating) return;
    const creationContextProjectId = projectIdRef.current;
    setCreating(true);
    setPageError("");
    try {
      const project = await createProject(name);
      if (
        mountedRef.current &&
        projectIdRef.current === creationContextProjectId &&
        creationContextProjectId === null
      ) {
        switchProject(project.id);
      }
    } catch (error: unknown) {
      if (
        mountedRef.current &&
        projectIdRef.current === creationContextProjectId
      ) {
        setPageError(userMessage(error));
      }
    } finally {
      if (mountedRef.current) setCreating(false);
    }
  };

  const enterStudio = async () => {
    if (creating || (!projectId && !newName.trim())) return;
    setCreating(true);
    setPageError("");
    try {
      if (projectId && handoffEnabled) {
        location.assign(studioUrl(projectId, "shots"));
        return;
      }
      const workspace = projectId
        ? await bindDirectory(projectId)
        : await createDirectoryProject(newName.trim());
      location.assign(studioUrl(workspace.project_id, "shots"));
    } catch (error) {
      if (!(error instanceof DirectorySelectionCancelled)) setPageError(userMessage(error));
    } finally {
      if (mountedRef.current) setCreating(false);
    }
  };

  const reloadServer = useCallback(async () => {
    if (conflictActionInFlight.current || activeImportCountRef.current > 0) return;
    if (hasSavedUnplacedRef.current) {
      focusBatchCenter();
      return;
    }
    const targetProject = projectIdRef.current;
    if (!targetProject) return;
    conflictActionInFlight.current = true;
    try {
      await loadCurrent(targetProject, true);
    } finally {
      conflictActionInFlight.current = false;
    }
  }, [focusBatchCenter, loadCurrent]);

  const requestMediaRecoveryReload = useCallback(() => {
    const current = useCanvasStore.getState();
    if (
      !projectIdRef.current ||
      current.projectId !== projectIdRef.current ||
      current.loadEpoch !== loadEpoch ||
      current.status !== "clean" ||
      current.editSequence !== current.savedSequence ||
      current.baseRevisionId === null ||
      hasSavedUnplacedRef.current
    ) {
      if (hasSavedUnplacedRef.current) focusBatchCenter();
      else setPageError("请等待当前画布保存完成后再重新加载项目。");
      return;
    }
    void reloadServer();
  }, [focusBatchCenter, loadEpoch, reloadServer]);

  const confirmMediaForceRefresh = useCallback(
    () =>
      window.confirm(
        "强制刷新会丢失未保存的画布改动。系统只能尽力中止当前本地任务；刷新后请根据服务端状态检查结果，不能保证恢复当前内存批次。是否继续？",
      ),
    [],
  );

  const forceRefreshApproved = useCallback(() => {
    forceRefreshApprovedRef.current = true;
    window.history.go(0);
  }, []);

  const keepLocalCopy = useCallback(async () => {
    if (
      conflictActionInFlight.current ||
      conflictCopyReview !== null ||
      activeImportCountRef.current > 0
    ) return;
    if (hasSavedUnplacedRef.current) {
      focusBatchCenter();
      return;
    }
    const sourceProjectId = projectIdRef.current;
    const sourceState = useCanvasStore.getState();
    if (
      !sourceProjectId ||
      sourceState.projectId !== sourceProjectId ||
      sourceState.status !== "conflict"
    ) {
      return;
    }

    const capturedEpoch = sourceState.loadEpoch;
    const capturedSequence = sourceState.editSequence;
    const capturedSnapshot = toGraphSnapshot(
      sourceState.nodes,
      sourceState.edges,
      sourceState.shots,
      sourceState.resourceBindings,
    );
    conflictActionInFlight.current = true;
    conflictCopyTriggerRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setPageError("");
    try {
      const copy = await createProject(conflictCopyName(projectName));
      const proposal = await proposeGraphChangeSet(copy.id, {
        baseRevisionId: copy.active_revision_id,
        clientMutationId: crypto.randomUUID(),
        snapshot: capturedSnapshot,
      });
      const current = useCanvasStore.getState();
      const contextIsUnchanged =
        projectIdRef.current === sourceProjectId &&
        current.projectId === sourceProjectId &&
        current.loadEpoch === capturedEpoch &&
        current.editSequence === capturedSequence &&
        current.status === "conflict";
      if (!contextIsUnchanged) {
        if (mountedRef.current && projectIdRef.current === sourceProjectId) {
          setPageError("保存副本期间本地画布已发生变化，当前画布未被替换。");
        }
        return;
      }
      if (mountedRef.current) {
        setConflictCopyReview({
          sourceProjectId,
          capturedEpoch,
          capturedSequence,
          copyProjectId: copy.id,
          proposal,
        });
      }
    } catch (error: unknown) {
      const current = useCanvasStore.getState();
      if (
        mountedRef.current &&
        projectIdRef.current === sourceProjectId &&
        current.loadEpoch === capturedEpoch
      ) {
        setPageError(userMessage(error));
      }
    } finally {
      conflictActionInFlight.current = false;
    }
  }, [conflictCopyReview, focusBatchCenter, projectName]);

  const cancelConflictCopyReview = useCallback(async () => {
    const pending = conflictCopyReview;
    if (!pending || conflictActionInFlight.current) return;
    conflictActionInFlight.current = true;
    setConflictCopyReview(null);
    try {
      await approveGraphChangeSet(
        pending.copyProjectId,
        pending.proposal.id,
        pending.proposal.changeset_sha256,
        "REJECTED",
      );
    } catch (error: unknown) {
      if (mountedRef.current) setPageError(userMessage(error));
    } finally {
      conflictActionInFlight.current = false;
      conflictCopyTriggerRef.current?.focus();
    }
  }, [conflictCopyReview]);

  const confirmConflictCopyReview = useCallback(async () => {
    const pending = conflictCopyReview;
    if (!pending || conflictActionInFlight.current) return;
    const before = useCanvasStore.getState();
    if (
      projectIdRef.current !== pending.sourceProjectId ||
      before.projectId !== pending.sourceProjectId ||
      before.loadEpoch !== pending.capturedEpoch ||
      before.editSequence !== pending.capturedSequence ||
      before.status !== "conflict"
    ) {
      setConflictCopyReview(null);
      setPageError("审阅期间本地画布已发生变化，副本提案未应用。");
      conflictCopyTriggerRef.current?.focus();
      return;
    }
    conflictActionInFlight.current = true;
    setPageError("");
    try {
      const approval = await approveGraphChangeSet(
        pending.copyProjectId,
        pending.proposal.id,
        pending.proposal.changeset_sha256,
      );
      const applied = await applyGraphChangeSet(
        pending.copyProjectId,
        approval.id,
        approval.changeset_sha256,
      );
      if (applied.state !== "APPLIED" || applied.applied_graph_revision_id === null) {
        throw new Error("本地副本没有生成新的图谱版本。");
      }
      const current = useCanvasStore.getState();
      const contextIsUnchanged =
        projectIdRef.current === pending.sourceProjectId &&
        current.projectId === pending.sourceProjectId &&
        current.loadEpoch === pending.capturedEpoch &&
        current.editSequence === pending.capturedSequence &&
        current.status === "conflict";
      setConflictCopyReview(null);
      if (!contextIsUnchanged) {
        if (mountedRef.current && projectIdRef.current === pending.sourceProjectId) {
          setPageError("保存副本期间本地画布已发生变化，当前画布未被替换。");
        }
        return;
      }
      if (mountedRef.current) switchProject(pending.copyProjectId);
    } catch (error: unknown) {
      if (mountedRef.current) setPageError(userMessage(error));
    } finally {
      conflictActionInFlight.current = false;
    }
  }, [conflictCopyReview, switchProject]);

  const persistViewport = useCallback((viewport: Viewport) => {
    const targetProject = projectIdRef.current;
    if (!targetProject) return;
    const targetEpoch = useCanvasStore.getState().loadEpoch;
    const previous = viewportQueues.current.get(targetProject) ?? Promise.resolve();
    const task = previous
      .catch(() => undefined)
      .then(() => {
        if (
          !mountedRef.current ||
          projectIdRef.current !== targetProject ||
          useCanvasStore.getState().loadEpoch !== targetEpoch
        ) {
          return;
        }
        return saveViewport(targetProject, viewport);
      })
      .then(() => {
        if (
          mountedRef.current &&
          projectIdRef.current === targetProject &&
          useCanvasStore.getState().loadEpoch === targetEpoch
        ) {
          setViewportError("");
        }
      })
      .catch((error: unknown) => {
        if (
          mountedRef.current &&
          projectIdRef.current === targetProject &&
          useCanvasStore.getState().loadEpoch === targetEpoch
        ) {
          setViewportError(`视图位置保存失败：${userMessage(error)}`);
        }
      });
    viewportQueues.current.set(targetProject, task);
    void task.then(() => {
      if (viewportQueues.current.get(targetProject) === task) {
        viewportQueues.current.delete(targetProject);
      }
    });
  }, []);

  const openHistory = useCallback((target: ShotHistoryTarget) => {
    const current = useCanvasStore.getState();
    if (!current.projectId || !current.baseRevisionId || current.selectedShotId !== target.shotId) {
      return;
    }
    const shot = current.shots.find((candidate) => candidate.id === target.shotId);
    if (!shot) return;
    const targetStillCurrent =
      target.slot === "image"
        ? shot.imageRevisionId === target.revisionId
        : target.slot === "video"
          ? shot.videoRevisionId === target.revisionId
          : shot.referenceRevisionIds.includes(target.revisionId);
    if (!targetStillCurrent) return;
    setHistoryTarget({
      ...target,
      projectId: current.projectId,
      loadEpoch: current.loadEpoch,
      anchorRevisionId: current.baseRevisionId,
    });
  }, []);

  const requestShotDelete = useCallback((shotId: string) => {
    const current = useCanvasStore.getState();
    if (!current.projectId) return;
    setDeletingShotTarget({
      shotId,
      projectId: current.projectId,
      loadEpoch: current.loadEpoch,
    });
  }, []);

  const viewedBranch = branchWorkspace?.branches.find(
    (branch) => branch.id === viewedBranchId,
  );

  if (!projectId) {
    return (
      <main className="hf-home"><div className="hf-home-inner">
        <header><div className="hf-brand"><span className="hf-mark" aria-hidden="true"/><strong>AI Canvas</strong></div>
          <h1>镜头工作台</h1><p>按集和镜头整理生成结果，查看前后镜，打包交给剪辑。</p></header>
        <div className="hf-home-grid"><section>
          <h2>新建项目</h2>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void enterStudio();
            }}
          >
            <label htmlFor="new-project-name">项目名称</label>
            <input
              id="new-project-name"
              maxLength={PROJECT_NAME_LIMIT}
              onChange={(event) => setNewName(event.target.value)}
              value={newName}
            />
            <div><button className="hf-primary" disabled={!newName.trim() || creating} type="submit">
              创建镜头项目</button>
            <button disabled={!newName.trim() || creating} onClick={() => void createBlank()} type="button">
              创建空白项目
            </button></div>
            {creating && <span role="status">正在创建项目…</span>}
          </form>
          {pageError ? (
            <section aria-label="页面错误" className="canvas-error" role="alert">
              <p>{pageError}</p>
              <button onClick={() => void refreshRecent()} type="button">
                重试加载项目
              </button>
            </section>
          ) : null}
        </section>
        <section aria-labelledby="recent-projects-title" className="hf-recent">
          <h2 id="recent-projects-title">最近项目</h2>
          {recentLoading ? (
            <p aria-live="polite" role="status">
              正在加载最近项目…
            </p>
          ) : null}
          {!recentLoading && recent.length === 0 ? <p>尚无项目。选择工作目录后，可以添加第一集和镜头。</p> : null}
          {recent.map((project) => (
            <button
              disabled={creating}
              key={project.id}
              onClick={() => project.handoff_enabled ? location.assign(studioUrl(project.id, "shots")) : switchProject(project.id)}
              type="button"
            >
              {project.name}
            </button>
          ))}
        </section></div></div>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="boot-state">
        <div aria-live="polite" role="status">
          正在加载项目…
        </div>
      </main>
    );
  }

  if (loadedProjectId !== projectId) {
    return (
      <main className="boot-state boot-state--error">
        <section aria-label="项目加载失败" role="alert">
          <h1>项目加载失败</h1>
          <p>{pageError || "无法读取项目。"}</p>
          <div className="canvas-error__actions">
            <button onClick={() => void loadCurrent(projectId)} type="button">
              重试
            </button>
            <button onClick={() => switchProject(null)} type="button">
              返回最近项目
            </button>
          </div>
          <p>若提示会话失效，请从启动器重新打开。</p>
        </section>
      </main>
    );
  }

  return (
    <div className="canvas-page">
      <header className="canvas-toolbar">
        <div className="canvas-toolbar__identity">
          <button
            aria-busy={returnRequested || activeImportCount > 0}
            aria-label="返回项目列表"
            className="canvas-toolbar__project-nav"
            disabled={returnBlocked || returnRequested}
            onClick={returnToProjects}
            title={
              activeImportCount > 0
                ? "等待媒体导入完成后再离开项目"
                : hasSavedUnplaced
                  ? "请先在媒体导入批次中心放入画布或删除本地素材"
                : status === "conflict"
                ? "请先处理版本冲突"
                : status === "error"
                  ? "保存失败时会保留当前画布"
                  : returnRequested
                    ? "正在等待自动保存完成"
                    : "返回项目列表"
            }
            type="button"
          >
            {activeImportCount > 0
              ? "导入中，请稍候…"
              : hasSavedUnplaced
                ? "请处理待放置素材"
              : returnRequested
                ? "保存后返回…"
                : "← 项目列表"}
          </button>
          <h1>{projectName}</h1>
          <button type="button" disabled={creating || returnBlocked || status !== "clean"}
            onClick={() => void enterStudio()}>{handoffEnabled ? "镜头工作台" : "启用目录选片"}</button>
        </div>
        <NodePalette />
        {branchWorkspace && viewedBranchId ? (
          <BranchPanel
            onOpen={openBranch}
            onSelectionChanged={refreshBranchWorkspace}
            projectId={projectId}
            viewedBranchId={viewedBranchId}
            workspace={branchWorkspace}
          />
        ) : null}
        <GraphChangeReview
          branchContext={
            viewedBranch
              ? {
                  branchId: viewedBranch.id,
                  branchHeadRevisionId: viewedBranch.head.graph_revision_id,
                  branchHeadVersion: viewedBranch.head.version,
                  selectionVersion: branchWorkspace?.selection.version ?? 1,
                }
              : undefined
          }
          disabled={activeImportCount > 0 || hasSavedUnplaced}
          externalProposal={agentGraphProposal}
          onApplied={handleBranchApplied}
          onExternalConsumed={() => setAgentGraphProposal(null)}
          projectId={projectId}
        />
        <SaveStatus />
        <MediaLifecyclePanel projectId={projectId} />
        <ResourceCatalogPanel projectId={projectId} />
      </header>
      <div className="canvas-notices">
        {status === "conflict" ? (
          <RevisionConflictBanner
            disabledReason={
              activeImportCount > 0
                ? "等待媒体导入完成后再处理版本冲突"
                : hasSavedUnplaced
                  ? "请先在媒体导入批次中心放入画布或删除本地素材"
                  : conflictCopyReview !== null
                    ? "请先完成副本变更审阅"
                  : null
            }
            keepLocalCopy={keepLocalCopy}
            reloadServer={reloadServer}
          />
        ) : null}
        {conflictCopyReview ? (
          <section
            aria-labelledby="conflict-copy-review-title"
            aria-modal="true"
            className="graph-change-review__dialog"
            role="dialog"
          >
            <p className="graph-change-review__eyebrow">GRAPH CHANGE SET</p>
            <h2 id="conflict-copy-review-title">冲突副本变更审阅</h2>
            <p>确认后才会把本地冲突草稿应用到新项目；不会运行媒体、GPU、付费或外发操作。</p>
            <dl>
              <div>
                <dt>变更</dt>
                <dd>{String(conflictCopyReview.proposal.diff.op ?? "replace_snapshot")}</dd>
              </div>
              <div>
                <dt>风险</dt>
                <dd>
                  {Array.isArray(conflictCopyReview.proposal.risk_summary.classes)
                    ? conflictCopyReview.proposal.risk_summary.classes.join(" · ")
                    : "GRAPH_WRITE"}
                </dd>
              </div>
              <div>
                <dt>基线</dt>
                <dd>{conflictCopyReview.proposal.base_graph_revision_id.slice(0, 12)}</dd>
              </div>
              <div>
                <dt>审批哈希</dt>
                <dd>{conflictCopyReview.proposal.changeset_sha256.slice(0, 12)}</dd>
              </div>
            </dl>
            <div className="graph-change-review__actions">
              <button onClick={() => void cancelConflictCopyReview()} type="button">
                取消并拒绝
              </button>
              <button onClick={() => void confirmConflictCopyReview()} type="button">
                确认创建副本
              </button>
            </div>
          </section>
        ) : null}
        {pageError ? (
          <section aria-label="页面错误" className="canvas-error" role="alert">
            <p>{pageError}</p>
            <span>若提示会话失效，请从启动器重新打开。</span>
          </section>
        ) : null}
        {viewportError ? (
          <p className="viewport-error" role="status">
            {viewportError}
          </p>
        ) : null}
        <section aria-label="F1 控制面板" className="f1-control-dock">
          <ControlConfigPanel projectId={projectId} />
          <NoEgressDryRunPanel projectId={projectId} />
          <OperationTracePanel projectId={projectId} />
          <div className="agent-control-stack">
            <details
              className="f1-control-panel c3-control-shell"
              onToggle={(event) => setC3Open(event.currentTarget.open)}
            >
              <summary>C3 模型与 Agent</summary>
              {c3Open ? (
                <>
                  <ModelProfileSettingsPanel projectId={projectId} />
                  <AgentProposalPanel
                    branchContext={viewedBranch ? {
                      branchId: viewedBranch.id,
                      branchHeadRevisionId: viewedBranch.head.graph_revision_id,
                      branchHeadVersion: viewedBranch.head.version,
                      selectionVersion: branchWorkspace?.selection.version ?? 1,
                    } : null}
                    nodes={useCanvasStore.getState().nodes}
                    onGraphChangeSet={setAgentGraphProposal}
                    projectId={projectId}
                  />
                </>
              ) : null}
            </details>
            <details
              className="f1-control-panel c3-control-shell"
              onToggle={(event) => setC4Open(event.currentTarget.open)}
            >
              <summary>C4 PromptProfile 与批次</summary>
              {c4Open ? <PromptBatchPanel
                branchContext={viewedBranch ? {
                  branchId: viewedBranch.id,
                  branchHeadRevisionId: viewedBranch.head.graph_revision_id,
                  branchHeadVersion: viewedBranch.head.version,
                  selectionVersion: branchWorkspace?.selection.version ?? 1,
                } : null}
                onGraphChangeSet={setAgentGraphProposal}
                nodes={useCanvasStore.getState().nodes}
                projectId={projectId}
                shots={shots}
              /> : null}
            </details>
          </div>
        </section>
      </div>
      <MediaIngressProvider
        confirmForceRefresh={confirmMediaForceRefresh}
        generation={`${projectId}:${loadEpoch}`}
        key={`${projectId}:${loadEpoch}`}
        loadEpoch={loadEpoch}
        onGuardStateChange={handleIngressGuardState}
        onForceRefreshApproved={forceRefreshApproved}
        onRequestReload={requestMediaRecoveryReload}
        projectId={projectId}
      >
        <CanvasShotWorkspace
          associationTarget={associationTarget}
          deletingShotTarget={deletingShotTarget}
          historyTarget={historyTarget}
          onCloseHistory={() => setHistoryTarget(null)}
          onCloseShotDelete={() => setDeletingShotTarget(null)}
          onMoveEnd={persistViewport}
          onOpenHistory={openHistory}
          onRequestDelete={requestShotDelete}
          onShotSelectionChange={selectShot}
          selectedShotId={selectedShotId}
        />
      </MediaIngressProvider>
    </div>
  );
}
