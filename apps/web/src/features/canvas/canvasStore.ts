import {
  addEdge as connectEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type Viewport,
  type XYPosition,
} from "@xyflow/react";
import { create } from "zustand";
import type { CanvasBranchContext } from "../branching/branchTypes";
import {
  clonePromptDocument,
  documentFromText,
  PROMPT_BLOCK_KINDS,
  rebuildPromptDocument,
  validatePromptDocument,
} from "../prompting/promptDocument";
import { toGraphSnapshot } from "./graphMappers";
import { isCanvasUuid } from "./canvasTypes";
import type {
  AddableNodeKind,
  CanvasEdge,
  CanvasNode,
  CanvasResourceBinding,
  CanvasShot,
  GraphSnapshot,
  SaveStatus,
  MediaKind,
  PromptBlock,
  PromptBlockKind,
  ShotAssociationTarget,
  ShotRevisionRef,
} from "./canvasTypes";

export type SaveCapture = {
  editSequence: number;
  loadEpoch: number;
  projectId: string;
  baseRevisionId: string;
  viewedBranchId: string | null;
  viewedBranchHeadRevisionId: string | null;
  viewedBranchHeadVersion: number | null;
  selectionVersion: number | null;
  snapshot: GraphSnapshot;
};

export type CanvasLoad = {
  projectId: string;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  shots: CanvasShot[];
  resourceBindings?: CanvasResourceBinding[];
  revisionId: string;
  viewport?: Viewport;
  branchContext?: CanvasBranchContext;
};

export type AttachMediaRevisionInput = {
  projectId: string;
  loadEpoch: number;
  nodeId: string;
  expectedRevisionId: string | null;
  revisionId: string;
  mediaKind: MediaKind;
};

export type PromoteMediaReservationInput = {
  projectId: string;
  loadEpoch: number;
  nodeId: string;
  position: XYPosition;
  revisionId: string;
  mediaKind: MediaKind;
};

export type CanvasState = {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  shots: CanvasShot[];
  resourceBindings: CanvasResourceBinding[];
  selectedShotId: string | null;
  associationTarget: ShotAssociationTarget | null;
  projectId: string | null;
  baseRevisionId: string | null;
  branches: CanvasBranchContext["branches"];
  selectedBranchId: string | null;
  selectionVersion: number | null;
  viewedBranchId: string | null;
  viewedBranchHeadRevisionId: string | null;
  viewedBranchHeadVersion: number | null;
  isViewingUnpromotedBranch: boolean;
  editSequence: number;
  savedSequence: number;
  loadEpoch: number;
  status: SaveStatus;
  conflictRevisionId: string | null;
  errorMessage: string | null;
  viewport: Viewport;
  reset(): void;
  load(input: CanvasLoad): void;
  refreshApplied(input: CanvasLoad): void;
  syncBranchContext(context: CanvasBranchContext): void;
  selectShot(shotId: string | null): void;
  beginShotAssociation(shotId: string, slot: "image" | "video" | "reference"): void;
  cancelShotAssociation(): void;
  createShot(input: {
    afterShotId: string | null;
    position: XYPosition;
  }): { shotId: string; nodeId: string } | null;
  deleteShot(shotId: string): string | null;
  reorderShots(orderedShotIds: readonly string[]): boolean;
  moveShot(shotId: string, direction: -1 | 1): boolean;
  setShotSlot(
    shotId: string,
    slot: "image" | "video",
    revision: ShotRevisionRef | null,
  ): boolean;
  setShotSlotIfCurrent(
    shotId: string,
    slot: "image" | "video",
    expectedRevisionId: string | null,
    revision: ShotRevisionRef | null,
  ): boolean;
  addShotReference(shotId: string, revision: ShotRevisionRef, index?: number): boolean;
  replaceShotReference(
    shotId: string,
    currentRevisionId: string,
    revision: ShotRevisionRef,
  ): boolean;
  removeShotReference(shotId: string, revisionId: string): boolean;
  reorderShotReferences(shotId: string, orderedRevisionIds: readonly string[]): boolean;
  addNode(kind: AddableNodeKind, position: XYPosition): void;
  copyNode(nodeId: string): void;
  deleteNode(nodeId: string): void;
  attachMediaRevision(input: AttachMediaRevisionInput): boolean;
  promoteMediaReservation(input: PromoteMediaReservationInput): boolean;
  updateNodeText(nodeId: string, text: string): void;
  addPromptBlock(nodeId: string, block: PromptBlock, index?: number): boolean;
  updatePromptBlock(
    nodeId: string,
    blockId: string,
    patch: Partial<Pick<PromptBlock, "kind" | "text">>,
  ): boolean;
  movePromptBlock(nodeId: string, blockId: string, direction: -1 | 1): boolean;
  setPromptBlockLocked(nodeId: string, blockId: string, locked: boolean): boolean;
  removePromptBlock(nodeId: string, blockId: string): boolean;
  setResourceBinding(binding: CanvasResourceBinding): boolean;
  removeResourceBinding(bindingId: string): boolean;
  onNodesChange(
    changes: NodeChange<CanvasNode>[],
    protectedNodeIds?: ReadonlySet<string>,
  ): void;
  onEdgesChange(changes: EdgeChange<CanvasEdge>[]): void;
  onConnect(connection: Connection): void;
  setViewport(viewport: Viewport): void;
  beginSave(): SaveCapture;
  completeSave(
    capturedSequence: number,
    capturedEpoch: number,
    capturedProjectId: string,
    revisionId: string,
    capturedViewedBranchId?: string | null,
    capturedViewedBranchHeadVersion?: number | null,
    capturedSelectionVersion?: number | null,
  ): void;
  failSave(
    capturedEpoch: number,
    capturedProjectId: string,
    message: string,
    capturedViewedBranchId?: string | null,
    capturedViewedBranchHeadVersion?: number | null,
    capturedSelectionVersion?: number | null,
  ): void;
  markConflict(
    capturedEpoch: number,
    capturedProjectId: string,
    currentRevisionId: string,
    capturedViewedBranchId?: string | null,
    capturedViewedBranchHeadVersion?: number | null,
    capturedSelectionVersion?: number | null,
  ): void;
};

const defaultViewport: Viewport = { x: 0, y: 0, zoom: 1 };

const initialState = {
  nodes: [] as CanvasNode[],
  edges: [] as CanvasEdge[],
  shots: [] as CanvasShot[],
  resourceBindings: [] as CanvasResourceBinding[],
  selectedShotId: null as string | null,
  associationTarget: null as ShotAssociationTarget | null,
  projectId: null as string | null,
  baseRevisionId: null as string | null,
  branches: [] as CanvasBranchContext["branches"],
  selectedBranchId: null as string | null,
  selectionVersion: null as number | null,
  viewedBranchId: null as string | null,
  viewedBranchHeadRevisionId: null as string | null,
  viewedBranchHeadVersion: null as number | null,
  isViewingUnpromotedBranch: false,
  editSequence: 0,
  savedSequence: 0,
  loadEpoch: 0,
  status: "clean" as SaveStatus,
  conflictRevisionId: null as string | null,
  errorMessage: null as string | null,
  viewport: defaultViewport,
};

function dirty(sequence: number) {
  return {
    editSequence: sequence + 1,
    status: "dirty" as const,
    conflictRevisionId: null,
    errorMessage: null,
  };
}

function cloneShot(shot: CanvasShot): CanvasShot {
  return {
    id: shot.id,
    order: shot.order,
    imageRevisionId: shot.imageRevisionId,
    videoRevisionId: shot.videoRevisionId,
    referenceRevisionIds: [...shot.referenceRevisionIds],
  };
}

function cloneResourceBinding(binding: CanvasResourceBinding): CanvasResourceBinding {
  return { ...binding };
}

const promptBlockKinds = new Set<string>(PROMPT_BLOCK_KINDS);

function isPromptBlock(block: PromptBlock): boolean {
  return (
    isCanvasUuid(block.id) &&
    promptBlockKinds.has(block.kind) &&
    typeof block.text === "string" &&
    block.text.length <= 100_000 &&
    typeof block.locked === "boolean"
  );
}

function isResourceBindingValid(
  binding: CanvasResourceBinding,
  nodes: readonly CanvasNode[],
  shots: readonly CanvasShot[],
): boolean {
  return (
    isCanvasUuid(binding.id) &&
    isCanvasUuid(binding.resourceRevisionId) &&
    (binding.resourceKind === "STYLE" ||
      binding.resourceKind === "CHARACTER" ||
      binding.resourceKind === "PROMPT" ||
      binding.resourceKind === "MEDIA") &&
    /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(binding.bindingKey) &&
    ((binding.scopeKind === "PROJECT" && binding.scopeId === null) ||
      (binding.scopeKind === "SHOT" &&
        isCanvasUuid(binding.scopeId) &&
        shots.some((shot) => shot.id === binding.scopeId)) ||
      (binding.scopeKind === "NODE" &&
        isCanvasUuid(binding.scopeId) &&
        nodes.some((node) => node.id === binding.scopeId)))
  );
}

function shotsInOrder(shots: readonly CanvasShot[]): CanvasShot[] {
  return [...shots].sort((left, right) => left.order - right.order);
}

function normaliseShotOrders(shots: readonly CanvasShot[]): CanvasShot[] {
  return shots.map((shot, order) => ({ ...cloneShot(shot), order }));
}

function shotNodeFor(shotId: string, nodeId: string, position: XYPosition): CanvasNode {
  return {
    id: nodeId,
    type: "shot",
    deletable: false,
    position: { x: position.x, y: position.y },
    data: { kind: "shot", shotId },
  };
}

function isShotSlotCompatible(
  slot: ShotAssociationTarget["slot"],
  mediaKind: MediaKind,
): boolean {
  return (
    (slot === "image" && mediaKind === "image") ||
    (slot === "video" && mediaKind === "video") ||
    (slot === "reference" && mediaKind === "image")
  );
}

function replaceShot(
  shots: readonly CanvasShot[],
  shotId: string,
  replacement: CanvasShot,
): CanvasShot[] | null {
  const index = shots.findIndex((shot) => shot.id === shotId);
  if (index === -1) return null;
  const nextShots = [...shots];
  nextShots[index] = replacement;
  return nextShots;
}

function nextSelectedShotIdAfterDelete(
  shots: readonly CanvasShot[],
  deletedShotId: string,
): string | null {
  const orderedShots = shotsInOrder(shots);
  const index = orderedShots.findIndex((shot) => shot.id === deletedShotId);
  if (index === -1) return null;
  return orderedShots[index + 1]?.id ?? orderedShots[index - 1]?.id ?? null;
}

function isFinitePosition(position: XYPosition): boolean {
  return Number.isFinite(position.x) && Number.isFinite(position.y);
}

function isShotRevisionRef(value: unknown): value is ShotRevisionRef {
  if (!value || typeof value !== "object") return false;
  const revision = value as { revisionId?: unknown; mediaKind?: unknown };
  return (
    isCanvasUuid(revision.revisionId) &&
    (revision.mediaKind === "image" || revision.mediaKind === "video")
  );
}

function isExactOrdering(
  orderedIds: readonly string[],
  currentIds: readonly string[],
): boolean {
  if (!Array.isArray(orderedIds) || orderedIds.length !== currentIds.length) return false;
  const requestedIds = new Set(orderedIds);
  return (
    requestedIds.size === orderedIds.length &&
    currentIds.every((id) => requestedIds.has(id)) &&
    orderedIds.every((id) => typeof id === "string")
  );
}

function newNode(kind: AddableNodeKind, position: XYPosition): CanvasNode {
  const base = {
    id: crypto.randomUUID(),
    position: { x: position.x, y: position.y },
  };
  switch (kind) {
    case "text_source":
    case "note":
      return { ...base, type: kind, data: { kind, text: "" } } as CanvasNode;
    case "prompt":
      return {
        ...base,
        type: "prompt",
        data: { kind: "prompt", document: documentFromText("") },
      };
    case "media_input":
      return {
        ...base,
        type: "media_input",
        data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
      };
  }
}

function copiedNode(source: CanvasNode): CanvasNode | null {
  const kind = source.data.kind;
  if (source.type !== kind) {
    throw new Error(`Node ${source.id} type does not match its data kind`);
  }
  const base = {
    id: crypto.randomUUID(),
    position: { x: source.position.x + 32, y: source.position.y + 32 },
  };
  switch (kind) {
    case "text_source":
    case "note":
      if (typeof source.data.text !== "string") {
        throw new Error(`Invalid text for node ${source.id}`);
      }
      return { ...base, type: kind, data: { kind, text: source.data.text } } as CanvasNode;
    case "prompt": {
      const validation = validatePromptDocument(source.data.document);
      if (!validation.ok) throw new Error(`Invalid prompt document for node ${source.id}`);
      return {
        ...base,
        type: "prompt",
        data: { kind: "prompt", document: clonePromptDocument(source.data.document) },
      };
    }
    case "media_input":
      if (
        (source.data.artifactRevisionId === null) !== (source.data.mediaKind === null) ||
        (source.data.artifactRevisionId !== null &&
          typeof source.data.artifactRevisionId !== "string") ||
        (source.data.mediaKind !== null &&
          source.data.mediaKind !== "image" &&
          source.data.mediaKind !== "video")
      ) {
        throw new Error(`Invalid media data for node ${source.id}`);
      }
      return {
        ...base,
        type: "media_input",
        data: {
          kind: "media_input",
          artifactRevisionId: source.data.artifactRevisionId,
          mediaKind: source.data.mediaKind,
        },
      };
    case "shot":
      return null;
  }
}

function matchesCapture(
  state: CanvasState,
  capturedEpoch: number,
  capturedProjectId: string,
  capturedViewedBranchId?: string | null,
  capturedViewedBranchHeadVersion?: number | null,
  capturedSelectionVersion?: number | null,
): boolean {
  return (
    state.loadEpoch === capturedEpoch &&
    state.projectId === capturedProjectId &&
    (capturedViewedBranchId === undefined ||
      state.viewedBranchId === capturedViewedBranchId) &&
    (capturedViewedBranchHeadVersion === undefined ||
      state.viewedBranchHeadVersion === capturedViewedBranchHeadVersion) &&
    (capturedSelectionVersion === undefined ||
      state.selectionVersion === capturedSelectionVersion)
  );
}

export const useCanvasStore = create<CanvasState>((set, get) => ({
  ...initialState,
  reset: () =>
    set((state) => ({
      ...initialState,
      nodes: [],
      edges: [],
      shots: [],
      resourceBindings: [],
      viewport: { ...defaultViewport },
      loadEpoch: state.loadEpoch + 1,
    })),
  load: ({
    projectId,
    nodes,
    edges,
    shots,
    resourceBindings = [],
    revisionId,
    viewport = defaultViewport,
    branchContext,
  }) =>
    set((state) => ({
      ...initialState,
      nodes,
      edges,
      shots: shots.map(cloneShot),
      resourceBindings: resourceBindings.map(cloneResourceBinding),
      projectId,
      baseRevisionId: revisionId,
      ...(branchContext ?? {}),
      viewport: { ...viewport },
      loadEpoch: state.loadEpoch + 1,
    })),
  refreshApplied: ({
    projectId,
    nodes,
    edges,
    shots,
    resourceBindings = [],
    revisionId,
    viewport = defaultViewport,
    branchContext,
  }) =>
    set((state) => {
      if (state.projectId !== projectId) return state;
      const shotIds = new Set(shots.map((shot) => shot.id));
      return {
        nodes,
        edges,
        shots: shots.map(cloneShot),
        resourceBindings: resourceBindings.map(cloneResourceBinding),
        baseRevisionId: revisionId,
        ...(branchContext ?? {}),
        viewport: { ...viewport },
        selectedShotId:
          state.selectedShotId !== null && shotIds.has(state.selectedShotId)
            ? state.selectedShotId
            : null,
        associationTarget:
          state.associationTarget !== null && shotIds.has(state.associationTarget.shotId)
            ? state.associationTarget
            : null,
      };
    }),
  syncBranchContext: (context) => set({ ...context }),
  selectShot: (shotId) =>
    set((state) => {
      const selectedShotId =
        shotId !== null && state.shots.some((shot) => shot.id === shotId) ? shotId : null;
      return state.selectedShotId === selectedShotId ? state : { selectedShotId };
    }),
  beginShotAssociation: (shotId, slot) =>
    set((state) => {
      if (
        !state.shots.some((shot) => shot.id === shotId) ||
        (slot !== "image" && slot !== "video" && slot !== "reference")
      ) {
        return state.associationTarget === null ? state : { associationTarget: null };
      }
      const associationTarget: ShotAssociationTarget = { shotId, slot };
      return state.associationTarget?.shotId === shotId && state.associationTarget.slot === slot
        ? state
        : { associationTarget };
    }),
  cancelShotAssociation: () =>
    set((state) => (state.associationTarget === null ? state : { associationTarget: null })),
  createShot: ({ afterShotId, position }) => {
    let created: { shotId: string; nodeId: string } | null = null;
    set((state) => {
      if (
        state.status === "conflict" ||
        !isFinitePosition(position) ||
        (afterShotId !== null &&
          (typeof afterShotId !== "string" ||
            !state.shots.some((shot) => shot.id === afterShotId)))
      ) {
        return state;
      }
      const shotId = crypto.randomUUID();
      const nodeId = crypto.randomUUID();
      const orderedShots = shotsInOrder(state.shots);
      const insertionIndex =
        afterShotId === null
          ? orderedShots.length
          : orderedShots.findIndex((shot) => shot.id === afterShotId) + 1;
      const shots = normaliseShotOrders([
        ...orderedShots.slice(0, insertionIndex),
        {
          id: shotId,
          order: insertionIndex,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
        ...orderedShots.slice(insertionIndex),
      ]);
      created = { shotId, nodeId };
      return {
        nodes: [...state.nodes, shotNodeFor(shotId, nodeId, position)],
        shots,
        ...dirty(state.editSequence),
      };
    });
    return created;
  },
  deleteShot: (shotId) => {
    let nextSelectedShotId: string | null = null;
    set((state) => {
      if (state.status === "conflict" || !state.shots.some((shot) => shot.id === shotId)) {
        return state;
      }
      nextSelectedShotId = nextSelectedShotIdAfterDelete(state.shots, shotId);
      const removedNodeIds = new Set(
        state.nodes
          .filter((node) => node.data.kind === "shot" && node.data.shotId === shotId)
          .map((node) => node.id),
      );
      return {
        nodes: state.nodes.filter((node) => !removedNodeIds.has(node.id)),
        edges: state.edges.filter(
          (edge) => !removedNodeIds.has(edge.source) && !removedNodeIds.has(edge.target),
        ),
        shots: normaliseShotOrders(
          shotsInOrder(state.shots).filter((shot) => shot.id !== shotId),
        ),
        resourceBindings: state.resourceBindings.filter(
          (binding) =>
            !(
              (binding.scopeKind === "SHOT" && binding.scopeId === shotId) ||
              (binding.scopeKind === "NODE" &&
                binding.scopeId !== null &&
                removedNodeIds.has(binding.scopeId))
            ),
        ),
        selectedShotId:
          state.selectedShotId === shotId ? nextSelectedShotId : state.selectedShotId,
        associationTarget:
          state.associationTarget?.shotId === shotId ? null : state.associationTarget,
        ...dirty(state.editSequence),
      };
    });
    return nextSelectedShotId;
  },
  reorderShots: (orderedShotIds) => {
    let reordered = false;
    set((state) => {
      if (state.status === "conflict") return state;
      const currentShots = shotsInOrder(state.shots);
      const currentIds = currentShots.map((shot) => shot.id);
      if (
        !isExactOrdering(orderedShotIds, currentIds) ||
        orderedShotIds.every((shotId, index) => shotId === currentIds[index])
      ) {
        return state;
      }
      const shotsById = new Map(currentShots.map((shot) => [shot.id, shot]));
      reordered = true;
      return {
        shots: normaliseShotOrders(orderedShotIds.map((shotId) => shotsById.get(shotId)!)),
        ...dirty(state.editSequence),
      };
    });
    return reordered;
  },
  moveShot: (shotId, direction) => {
    let moved = false;
    set((state) => {
      if (state.status === "conflict" || (direction !== -1 && direction !== 1)) return state;
      const currentShots = shotsInOrder(state.shots);
      const index = currentShots.findIndex((shot) => shot.id === shotId);
      const destination = index + direction;
      if (index === -1 || destination < 0 || destination >= currentShots.length) return state;
      const reorderedShots = [...currentShots];
      [reorderedShots[index], reorderedShots[destination]] = [
        reorderedShots[destination],
        reorderedShots[index],
      ];
      moved = true;
      return { shots: normaliseShotOrders(reorderedShots), ...dirty(state.editSequence) };
    });
    return moved;
  },
  setShotSlot: (shotId, slot, revision) => {
    let updated = false;
    set((state) => {
      if (
        state.status === "conflict" ||
        (slot !== "image" && slot !== "video") ||
        (revision !== null &&
          (!isShotRevisionRef(revision) || !isShotSlotCompatible(slot, revision.mediaKind)))
      ) {
        return state;
      }
      const shot = state.shots.find((candidate) => candidate.id === shotId);
      if (!shot) return state;
      const field = slot === "image" ? "imageRevisionId" : "videoRevisionId";
      const revisionId = revision?.revisionId ?? null;
      if (shot[field] === revisionId) return state;
      const shots = replaceShot(state.shots, shotId, { ...cloneShot(shot), [field]: revisionId });
      if (!shots) return state;
      updated = true;
      return { shots, ...dirty(state.editSequence) };
    });
    return updated;
  },
  setShotSlotIfCurrent: (shotId, slot, expectedRevisionId, revision) => {
    let updated = false;
    set((state) => {
      if (
        state.status === "conflict" ||
        (slot !== "image" && slot !== "video") ||
        (revision !== null &&
          (!isShotRevisionRef(revision) || !isShotSlotCompatible(slot, revision.mediaKind)))
      ) {
        return state;
      }
      const shot = state.shots.find((candidate) => candidate.id === shotId);
      if (!shot) return state;
      const field = slot === "image" ? "imageRevisionId" : "videoRevisionId";
      if (shot[field] !== expectedRevisionId) return state;
      const revisionId = revision?.revisionId ?? null;
      if (shot[field] === revisionId) return state;
      const shots = replaceShot(state.shots, shotId, { ...cloneShot(shot), [field]: revisionId });
      if (!shots) return state;
      updated = true;
      return { shots, ...dirty(state.editSequence) };
    });
    return updated;
  },
  addShotReference: (shotId, revision, index) => {
    let added = false;
    set((state) => {
      if (
        state.status === "conflict" ||
        !isShotRevisionRef(revision) ||
        !isShotSlotCompatible("reference", revision.mediaKind)
      ) {
        return state;
      }
      const shot = state.shots.find((candidate) => candidate.id === shotId);
      if (!shot || shot.referenceRevisionIds.length >= 64 || shot.referenceRevisionIds.includes(revision.revisionId)) {
        return state;
      }
      const insertionIndex = index ?? shot.referenceRevisionIds.length;
      if (
        !Number.isSafeInteger(insertionIndex) ||
        insertionIndex < 0 ||
        insertionIndex > shot.referenceRevisionIds.length
      ) {
        return state;
      }
      const shots = replaceShot(state.shots, shotId, {
        ...cloneShot(shot),
        referenceRevisionIds: [
          ...shot.referenceRevisionIds.slice(0, insertionIndex),
          revision.revisionId,
          ...shot.referenceRevisionIds.slice(insertionIndex),
        ],
      });
      if (!shots) return state;
      added = true;
      return { shots, ...dirty(state.editSequence) };
    });
    return added;
  },
  replaceShotReference: (shotId, currentRevisionId, revision) => {
    let replaced = false;
    set((state) => {
      if (
        state.status === "conflict" ||
        !isShotRevisionRef(revision) ||
        !isShotSlotCompatible("reference", revision.mediaKind)
      ) {
        return state;
      }
      const shot = state.shots.find((candidate) => candidate.id === shotId);
      const index = shot?.referenceRevisionIds.indexOf(currentRevisionId) ?? -1;
      if (
        !shot ||
        index === -1 ||
        revision.revisionId === currentRevisionId ||
        shot.referenceRevisionIds.includes(revision.revisionId)
      ) {
        return state;
      }
      const referenceRevisionIds = [...shot.referenceRevisionIds];
      referenceRevisionIds[index] = revision.revisionId;
      const shots = replaceShot(state.shots, shotId, {
        ...cloneShot(shot),
        referenceRevisionIds,
      });
      if (!shots) return state;
      replaced = true;
      return { shots, ...dirty(state.editSequence) };
    });
    return replaced;
  },
  removeShotReference: (shotId, revisionId) => {
    let removed = false;
    set((state) => {
      if (state.status === "conflict") return state;
      const shot = state.shots.find((candidate) => candidate.id === shotId);
      if (!shot || !shot.referenceRevisionIds.includes(revisionId)) return state;
      const shots = replaceShot(state.shots, shotId, {
        ...cloneShot(shot),
        referenceRevisionIds: shot.referenceRevisionIds.filter(
          (candidate) => candidate !== revisionId,
        ),
      });
      if (!shots) return state;
      removed = true;
      return { shots, ...dirty(state.editSequence) };
    });
    return removed;
  },
  reorderShotReferences: (shotId, orderedRevisionIds) => {
    let reordered = false;
    set((state) => {
      if (state.status === "conflict") return state;
      const shot = state.shots.find((candidate) => candidate.id === shotId);
      if (
        !shot ||
        !isExactOrdering(orderedRevisionIds, shot.referenceRevisionIds) ||
        orderedRevisionIds.every(
          (revisionId, index) => revisionId === shot.referenceRevisionIds[index],
        )
      ) {
        return state;
      }
      const shots = replaceShot(state.shots, shotId, {
        ...cloneShot(shot),
        referenceRevisionIds: [...orderedRevisionIds],
      });
      if (!shots) return state;
      reordered = true;
      return { shots, ...dirty(state.editSequence) };
    });
    return reordered;
  },
  addNode: (kind, position) =>
    set((state) => ({
      nodes: [...state.nodes, newNode(kind, position)],
      ...dirty(state.editSequence),
    })),
  copyNode: (nodeId) =>
    set((state) => {
      const source = state.nodes.find((node) => node.id === nodeId);
      if (!source) return state;
      const copy = copiedNode(source);
      if (!copy) return state;
      return { nodes: [...state.nodes, copy], ...dirty(state.editSequence) };
    }),
  deleteNode: (nodeId) =>
    set((state) => {
      const node = state.nodes.find((candidate) => candidate.id === nodeId);
      if (!node || node.data.kind === "shot") return state;
      return {
        nodes: state.nodes.filter((candidate) => candidate.id !== nodeId),
        edges: state.edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
        resourceBindings: state.resourceBindings.filter(
          (binding) => !(binding.scopeKind === "NODE" && binding.scopeId === nodeId),
        ),
        ...dirty(state.editSequence),
      };
    }),
  attachMediaRevision: (input) => {
    let attached = false;
    set((state) => {
      if (state.projectId !== input.projectId || state.loadEpoch !== input.loadEpoch) {
        return state;
      }
      const index = state.nodes.findIndex((node) => node.id === input.nodeId);
      const node = state.nodes[index];
      if (
        !node ||
        node.type !== "media_input" ||
        node.data.kind !== "media_input" ||
        typeof input.revisionId !== "string" ||
        input.revisionId.length === 0 ||
        (input.mediaKind !== "image" && input.mediaKind !== "video")
      ) {
        return state;
      }
      if (node.data.artifactRevisionId !== input.expectedRevisionId) return state;
      if (
        node.data.artifactRevisionId === input.revisionId &&
        node.data.mediaKind === input.mediaKind
      ) {
        attached = true;
        return state;
      }

      attached = true;
      const nodes = [...state.nodes];
      nodes[index] = {
        ...node,
        data: {
          kind: "media_input",
          artifactRevisionId: input.revisionId,
          mediaKind: input.mediaKind,
        },
      };
      if (state.status === "conflict") {
        return {
          nodes,
          editSequence: state.editSequence + 1,
          status: "conflict",
          conflictRevisionId: state.conflictRevisionId,
          errorMessage: state.errorMessage,
        };
      }
      return { nodes, ...dirty(state.editSequence) };
    });
    return attached;
  },
  promoteMediaReservation: (input) => {
    let promoted = false;
    set((state) => {
      if (
        state.projectId !== input.projectId ||
        state.loadEpoch !== input.loadEpoch ||
        state.nodes.some((node) => node.id === input.nodeId) ||
        typeof input.revisionId !== "string" ||
        input.revisionId.length === 0 ||
        (input.mediaKind !== "image" && input.mediaKind !== "video")
      ) {
        return state;
      }
      promoted = true;
      const nodes: CanvasNode[] = [
        ...state.nodes,
        {
          id: input.nodeId,
          type: "media_input",
          position: { x: input.position.x, y: input.position.y },
          data: {
            kind: "media_input",
            artifactRevisionId: input.revisionId,
            mediaKind: input.mediaKind,
          },
        },
      ];
      if (state.status === "conflict") {
        return {
          nodes,
          editSequence: state.editSequence + 1,
          status: "conflict",
          conflictRevisionId: state.conflictRevisionId,
          errorMessage: state.errorMessage,
        };
      }
      return { nodes, ...dirty(state.editSequence) };
    });
    return promoted;
  },
  updateNodeText: (nodeId, text) =>
    set((state) => {
      const current = state.nodes.find((node) => node.id === nodeId);
      if (
        !current ||
        (current.data.kind !== "text_source" && current.data.kind !== "prompt" && current.data.kind !== "note") ||
        typeof text !== "string" ||
        text.length > 100_000 ||
        (current.data.kind === "prompt"
          ? current.data.document.rendered_text === text
          : current.data.text === text)
      ) {
        return state;
      }
      return {
        nodes: state.nodes.map((node) => {
          if (node.id !== nodeId) return node;
          return node.data.kind === "prompt"
            ? {
                ...node,
                data: { kind: "prompt", document: documentFromText(text, node.id) },
              }
            : { ...node, data: { ...node.data, text } };
        }) as CanvasNode[],
        ...dirty(state.editSequence),
      };
    }),
  addPromptBlock: (nodeId, block, index) => {
    let added = false;
    set((state) => {
      if (state.status === "conflict" || !isPromptBlock(block)) return state;
      const nodeIndex = state.nodes.findIndex((node) => node.id === nodeId);
      const node = state.nodes[nodeIndex];
      if (
        !node ||
        node.data.kind !== "prompt" ||
        node.data.document.blocks.length >= 5_000 ||
        node.data.document.blocks.some((candidate) => candidate.id === block.id)
      ) return state;
      const insertionIndex = index ?? node.data.document.blocks.length;
      if (
        !Number.isSafeInteger(insertionIndex) ||
        insertionIndex < 0 ||
        insertionIndex > node.data.document.blocks.length
      ) return state;
      const blocks = [...node.data.document.blocks];
      blocks.splice(insertionIndex, 0, { ...block });
      const document = rebuildPromptDocument(blocks);
      if (!validatePromptDocument(document).ok) return state;
      const nodes = [...state.nodes];
      nodes[nodeIndex] = { ...node, data: { kind: "prompt", document } };
      added = true;
      return { nodes, ...dirty(state.editSequence) };
    });
    return added;
  },
  updatePromptBlock: (nodeId, blockId, patch) => {
    let updated = false;
    set((state) => {
      if (state.status === "conflict") return state;
      const nodeIndex = state.nodes.findIndex((node) => node.id === nodeId);
      const node = state.nodes[nodeIndex];
      if (!node || node.data.kind !== "prompt") return state;
      const blockIndex = node.data.document.blocks.findIndex((block) => block.id === blockId);
      const block = node.data.document.blocks[blockIndex];
      if (!block || block.locked) return state;
      const nextKind = patch.kind ?? block.kind;
      const nextText = patch.text ?? block.text;
      if (
        !promptBlockKinds.has(nextKind) ||
        typeof nextText !== "string" ||
        nextText.length > 100_000 ||
        (nextKind === block.kind && nextText === block.text)
      ) return state;
      const blocks = node.data.document.blocks.map((candidate, index) =>
        index === blockIndex
          ? { ...candidate, kind: nextKind as PromptBlockKind, text: nextText }
          : candidate,
      );
      const nodes = [...state.nodes];
      nodes[nodeIndex] = {
        ...node,
        data: { kind: "prompt", document: rebuildPromptDocument(blocks) },
      };
      updated = true;
      return { nodes, ...dirty(state.editSequence) };
    });
    return updated;
  },
  movePromptBlock: (nodeId, blockId, direction) => {
    let moved = false;
    set((state) => {
      if (state.status === "conflict" || (direction !== -1 && direction !== 1)) return state;
      const nodeIndex = state.nodes.findIndex((node) => node.id === nodeId);
      const node = state.nodes[nodeIndex];
      if (!node || node.data.kind !== "prompt") return state;
      const blockIndex = node.data.document.blocks.findIndex((block) => block.id === blockId);
      const destination = blockIndex + direction;
      if (blockIndex < 0 || destination < 0 || destination >= node.data.document.blocks.length) {
        return state;
      }
      const blocks = node.data.document.blocks.map((block) => ({ ...block }));
      [blocks[blockIndex], blocks[destination]] = [blocks[destination], blocks[blockIndex]];
      const nodes = [...state.nodes];
      nodes[nodeIndex] = {
        ...node,
        data: { kind: "prompt", document: rebuildPromptDocument(blocks) },
      };
      moved = true;
      return { nodes, ...dirty(state.editSequence) };
    });
    return moved;
  },
  setPromptBlockLocked: (nodeId, blockId, locked) => {
    let updated = false;
    set((state) => {
      if (state.status === "conflict" || typeof locked !== "boolean") return state;
      const nodeIndex = state.nodes.findIndex((node) => node.id === nodeId);
      const node = state.nodes[nodeIndex];
      if (!node || node.data.kind !== "prompt") return state;
      const blockIndex = node.data.document.blocks.findIndex((block) => block.id === blockId);
      const block = node.data.document.blocks[blockIndex];
      if (!block || block.locked === locked) return state;
      const blocks = node.data.document.blocks.map((candidate, index) =>
        index === blockIndex ? { ...candidate, locked } : candidate,
      );
      const nodes = [...state.nodes];
      nodes[nodeIndex] = {
        ...node,
        data: { kind: "prompt", document: rebuildPromptDocument(blocks) },
      };
      updated = true;
      return { nodes, ...dirty(state.editSequence) };
    });
    return updated;
  },
  removePromptBlock: (nodeId, blockId) => {
    let removed = false;
    set((state) => {
      if (state.status === "conflict") return state;
      const nodeIndex = state.nodes.findIndex((node) => node.id === nodeId);
      const node = state.nodes[nodeIndex];
      if (!node || node.data.kind !== "prompt") return state;
      const block = node.data.document.blocks.find((candidate) => candidate.id === blockId);
      if (!block || block.locked) return state;
      const nodes = [...state.nodes];
      nodes[nodeIndex] = {
        ...node,
        data: {
          kind: "prompt",
          document: rebuildPromptDocument(
            node.data.document.blocks.filter((candidate) => candidate.id !== blockId),
          ),
        },
      };
      removed = true;
      return { nodes, ...dirty(state.editSequence) };
    });
    return removed;
  },
  setResourceBinding: (binding) => {
    let updated = false;
    set((state) => {
      if (state.status === "conflict" || !isResourceBindingValid(binding, state.nodes, state.shots)) {
        return state;
      }
      const duplicateKey = state.resourceBindings.some(
        (candidate) =>
          candidate.id !== binding.id &&
          candidate.scopeKind === binding.scopeKind &&
          candidate.scopeId === binding.scopeId &&
          candidate.bindingKey === binding.bindingKey,
      );
      if (duplicateKey) return state;
      const index = state.resourceBindings.findIndex((candidate) => candidate.id === binding.id);
      if (
        index >= 0 &&
        Object.entries(binding).every(
          ([key, value]) => state.resourceBindings[index][key as keyof CanvasResourceBinding] === value,
        )
      ) return state;
      const resourceBindings = state.resourceBindings.map(cloneResourceBinding);
      if (index === -1) resourceBindings.push(cloneResourceBinding(binding));
      else resourceBindings[index] = cloneResourceBinding(binding);
      updated = true;
      return { resourceBindings, ...dirty(state.editSequence) };
    });
    return updated;
  },
  removeResourceBinding: (bindingId) => {
    let removed = false;
    set((state) => {
      if (
        state.status === "conflict" ||
        !state.resourceBindings.some((binding) => binding.id === bindingId)
      ) return state;
      removed = true;
      return {
        resourceBindings: state.resourceBindings.filter((binding) => binding.id !== bindingId),
        ...dirty(state.editSequence),
      };
    });
    return removed;
  },
  onNodesChange: (changes, protectedNodeIds = new Set()) =>
    set((state) => {
      const protectedShotIds = new Set(
        state.nodes
          .filter((node) => node.data.kind === "shot")
          .map((node) => node.id),
      );
      const applicableChanges = changes.filter(
        (change) =>
          !(
            ((change.type === "add" || change.type === "replace") &&
              (change.item.type === "shot" || change.item.data.kind === "shot")) ||
            ((change.type === "remove" || change.type === "replace") &&
              (protectedShotIds.has(change.id) || protectedNodeIds.has(change.id)))
          ),
      );
      if (applicableChanges.length === 0) return state;
      const nodes = applyNodeChanges(applicableChanges, state.nodes);
      const removedIds = new Set(
        applicableChanges
          .filter((change) => change.type === "remove")
          .map((change) => change.id),
      );
      const edges =
        removedIds.size === 0
          ? state.edges
          : state.edges.filter(
              (edge) => !removedIds.has(edge.source) && !removedIds.has(edge.target),
            );
      const resourceBindings =
        removedIds.size === 0
          ? state.resourceBindings
          : state.resourceBindings.filter(
              (binding) =>
                !(binding.scopeKind === "NODE" && binding.scopeId !== null && removedIds.has(binding.scopeId)),
            );
      const commitsPosition = applicableChanges.some(
        (change) => change.type === "position" && change.dragging === false,
      );
      const changesStructure = applicableChanges.some(
        (change) =>
          change.type === "add" || change.type === "replace" || change.type === "remove",
      );
      return commitsPosition || changesStructure
        ? { nodes, edges, resourceBindings, ...dirty(state.editSequence) }
        : { nodes, edges, resourceBindings };
    }),
  onEdgesChange: (changes) =>
    set((state) => {
      const edges = applyEdgeChanges(changes, state.edges);
      const changesStructure = changes.some(
        (change) =>
          change.type === "add" || change.type === "replace" || change.type === "remove",
      );
      return changesStructure ? { edges, ...dirty(state.editSequence) } : { edges };
    }),
  onConnect: (connection) =>
    set((state) => ({
      edges: connectEdge({ ...connection, id: crypto.randomUUID() }, state.edges),
      ...dirty(state.editSequence),
    })),
  setViewport: (viewport) => set({ viewport: { ...viewport } }),
  beginSave: () => {
    const state = get();
    if (!state.projectId) throw new Error("Canvas has no active project");
    if (!state.baseRevisionId) throw new Error("Canvas has no base revision");
    const capture: SaveCapture = {
      editSequence: state.editSequence,
      loadEpoch: state.loadEpoch,
      projectId: state.projectId,
      baseRevisionId: state.baseRevisionId,
      viewedBranchId: state.viewedBranchId,
      viewedBranchHeadRevisionId: state.viewedBranchHeadRevisionId,
      viewedBranchHeadVersion: state.viewedBranchHeadVersion,
      selectionVersion: state.selectionVersion,
      snapshot: toGraphSnapshot(
        state.nodes,
        state.edges,
        state.shots,
        state.resourceBindings,
      ),
    };
    set({ status: "saving", errorMessage: null });
    return capture;
  },
  completeSave: (
    capturedSequence,
    capturedEpoch,
    capturedProjectId,
    revisionId,
    capturedViewedBranchId,
    capturedViewedBranchHeadVersion,
    capturedSelectionVersion,
  ) =>
    set((state) =>
      !matchesCapture(
        state,
        capturedEpoch,
        capturedProjectId,
        capturedViewedBranchId,
        capturedViewedBranchHeadVersion,
        capturedSelectionVersion,
      )
        ? state
        : {
            baseRevisionId: revisionId,
            savedSequence: Math.max(state.savedSequence, capturedSequence),
            status: state.editSequence === capturedSequence ? "clean" : "dirty",
            conflictRevisionId: null,
            errorMessage: null,
          },
    ),
  failSave: (
    capturedEpoch,
    capturedProjectId,
    message,
    capturedViewedBranchId,
    capturedViewedBranchHeadVersion,
    capturedSelectionVersion,
  ) =>
    set((state) =>
      !matchesCapture(
        state,
        capturedEpoch,
        capturedProjectId,
        capturedViewedBranchId,
        capturedViewedBranchHeadVersion,
        capturedSelectionVersion,
      )
        ? state
        : {
            status: state.status === "dirty" ? "dirty" : "error",
            conflictRevisionId: null,
            errorMessage: message,
          },
    ),
  markConflict: (
    capturedEpoch,
    capturedProjectId,
    currentRevisionId,
    capturedViewedBranchId,
    capturedViewedBranchHeadVersion,
    capturedSelectionVersion,
  ) =>
    set((state) =>
      !matchesCapture(
        state,
        capturedEpoch,
        capturedProjectId,
        capturedViewedBranchId,
        capturedViewedBranchHeadVersion,
        capturedSelectionVersion,
      )
        ? state
        : {
            status: "conflict",
            conflictRevisionId: currentRevisionId,
            errorMessage: null,
          },
    ),
}));
