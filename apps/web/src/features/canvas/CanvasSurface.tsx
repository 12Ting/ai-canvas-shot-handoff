import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  type Viewport,
} from "@xyflow/react";
import {
  useCallback,
  useContext,
  useMemo,
  useRef,
  useSyncExternalStore,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useCanvasStore } from "./canvasStore";
import type {
  CanvasNode,
  ShotAssociationTarget,
  ShotRevisionRef,
} from "./canvasTypes";
import { BatchResultCard } from "./mediaIngress/components/BatchResultCard";
import { CanvasMediaIngressBoundary } from "./mediaIngress/components/CanvasMediaIngressBoundary";
import { IngressOverlayLayer } from "./mediaIngress/components/IngressOverlayLayer";
import { MultiMediaPicker } from "./mediaIngress/components/MultiMediaPicker";
import { MediaIngressContext } from "./mediaIngress/mediaIngressContext";
import { isCanvasConnectionValid, nodeTypes } from "./nodeTypes";
import { isCompatibleShotSlot } from "./shotMediaTransfer";

export type CanvasSurfaceProps = {
  onMoveEnd(viewport: Viewport): void;
  onShotSelectionChange(shotId: string | null): void;
  associationTarget: ShotAssociationTarget | null;
  onAssociateMedia(candidate: ShotAssociationCandidate): void;
};

export type ShotAssociationCandidate = Readonly<{
  nodeId: string;
  target: ShotAssociationTarget;
  revision: ShotRevisionRef;
  targetProtectedHint: boolean;
}>;

const EMPTY_PROTECTED_TARGETS = Object.freeze({
  protectedNodeIds: Object.freeze([] as string[]),
});
const INTERACTIVE_NODE_TARGETS =
  "button,input,select,textarea,a,video,audio,[contenteditable]:not([contenteditable='false'])";

function isInteractiveNodeTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(INTERACTIVE_NODE_TARGETS) !== null;
}

function Surface({
  onMoveEnd,
  onShotSelectionChange,
  associationTarget,
  onAssociateMedia,
}: CanvasSurfaceProps) {
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const onNodesChange = useCanvasStore((state) => state.onNodesChange);
  const onEdgesChange = useCanvasStore((state) => state.onEdgesChange);
  const onConnect = useCanvasStore((state) => state.onConnect);
  const viewport = useCanvasStore((state) => state.viewport);
  const setViewport = useCanvasStore((state) => state.setViewport);
  const projectId = useCanvasStore((state) => state.projectId);
  const loadEpoch = useCanvasStore((state) => state.loadEpoch);
  const locked = useCanvasStore((state) => state.status === "conflict");
  const ingress = useContext(MediaIngressContext);
  const targetLocks =
    ingress?.projectId === projectId && ingress.loadEpoch === loadEpoch
      ? ingress.targetLocks
      : null;
  const protectedSnapshot = useSyncExternalStore(
    (listener) => targetLocks?.subscribe(listener) ?? (() => undefined),
    () => targetLocks?.getSnapshot() ?? EMPTY_PROTECTED_TARGETS,
    () => targetLocks?.getSnapshot() ?? EMPTY_PROTECTED_TARGETS,
  );
  const protectedNodeIds = useMemo(
    () => new Set(protectedSnapshot.protectedNodeIds),
    [protectedSnapshot],
  );
  const reportedShotSelection = useRef<string | null>(null);
  const reportShotSelection = useCallback(
    (shotId: string | null) => {
      if (reportedShotSelection.current === shotId) return;
      reportedShotSelection.current = shotId;
      onShotSelectionChange(shotId);
    },
    [onShotSelectionChange],
  );
  const renderedNodes = useMemo(
    () => {
      if (protectedNodeIds.size === 0 && associationTarget === null) return nodes;
      return nodes.map((node) => {
        const targetProtected = protectedNodeIds.has(node.id);
        const associationClass =
          associationTarget !== null && node.data.kind === "media_input"
            ? node.data.artifactRevisionId !== null &&
              node.data.mediaKind !== null &&
              !targetProtected &&
              isCompatibleShotSlot(associationTarget.slot, node.data.mediaKind)
              ? "canvas-node--association-candidate"
              : "canvas-node--association-incompatible"
            : null;
        if (!targetProtected && associationClass === null) return node;
        return {
          ...node,
          ...(targetProtected ? { deletable: false } : {}),
          ...(associationClass === null
            ? {}
            : {
                className: [node.className, associationClass]
                  .filter(Boolean)
                  .join(" "),
              }),
        };
      });
    },
    [associationTarget, nodes, protectedNodeIds],
  );
  const guardNodeChanges = useCallback(
    (changes: import("@xyflow/react").NodeChange<CanvasNode>[]) => {
      const applicableChanges = changes.filter(
        (change) =>
          !(
            (change.type === "remove" || change.type === "replace") &&
            protectedNodeIds.has(change.id)
          ),
      );
      if (applicableChanges.length > 0)
        onNodesChange(applicableChanges, protectedNodeIds);
    },
    [onNodesChange, protectedNodeIds],
  );
  const onNodeClick = useCallback(
    (event: Pick<ReactMouseEvent, "target"> | null, node: CanvasNode) => {
      if (
        associationTarget !== null &&
        !isInteractiveNodeTarget(event?.target ?? null) &&
        !locked &&
        !protectedNodeIds.has(node.id) &&
        node.data.kind === "media_input" &&
        node.data.artifactRevisionId !== null &&
        node.data.mediaKind !== null &&
        isCompatibleShotSlot(associationTarget.slot, node.data.mediaKind)
      ) {
        onAssociateMedia({
          nodeId: node.id,
          target: {
            shotId: associationTarget.shotId,
            slot: associationTarget.slot,
          },
          revision: {
            revisionId: node.data.artifactRevisionId,
            mediaKind: node.data.mediaKind,
          },
          targetProtectedHint: protectedNodeIds.has(node.id),
        });
      }
    },
    [associationTarget, locked, onAssociateMedia, protectedNodeIds],
  );
  const onSelectionChange = useCallback(
    ({ nodes: selectedNodes, edges: selectedEdges }: { nodes: CanvasNode[]; edges: unknown[] }) => {
      if (associationTarget !== null) return;
      if (selectedNodes.length !== 1 || selectedEdges.length > 0) {
        reportShotSelection(null);
        return;
      }
      const [selectedNode] = selectedNodes;
      reportShotSelection(
        selectedNode?.data.kind === "shot" ? selectedNode.data.shotId : null,
      );
    },
    [associationTarget, reportShotSelection],
  );

  return (
    <main className="canvas-surface" data-testid="canvas-surface">
      <CanvasMediaIngressBoundary>
        <ReactFlow
          deleteKeyCode={null}
          edges={edges}
          edgesReconnectable={!locked}
          elementsSelectable={associationTarget === null}
          isValidConnection={(connection) =>
            !locked && isCanvasConnectionValid(connection, nodes)
          }
          nodeTypes={nodeTypes}
          nodes={renderedNodes}
          nodesConnectable={!locked}
          nodesDraggable={!locked && associationTarget === null}
          onConnect={locked ? undefined : onConnect}
          onEdgesChange={locked ? undefined : onEdgesChange}
          onMoveEnd={(_event, nextViewport) => onMoveEnd(nextViewport)}
          onNodeClick={onNodeClick}
          onNodesChange={locked ? undefined : guardNodeChanges}
          onPaneClick={() => {
            if (associationTarget === null) reportShotSelection(null);
          }}
          onSelectionChange={onSelectionChange}
          onViewportChange={setViewport}
          viewport={viewport}
        >
          <Background color="#30362c" gap={24} />
          <Panel position="top-left">
            <MultiMediaPicker />
          </Panel>
          <MiniMap pannable position="top-right" zoomable />
          <Controls />
          <IngressOverlayLayer />
          <Panel position="bottom-right">
            <BatchResultCard />
          </Panel>
        </ReactFlow>
      </CanvasMediaIngressBoundary>
    </main>
  );
}

export function CanvasSurface(props: CanvasSurfaceProps) {
  return (
    <ReactFlowProvider>
      <Surface {...props} />
    </ReactFlowProvider>
  );
}
