import { useEffect, useId, useState, type DragEvent } from "react";
import { useCanvasStore } from "../canvasStore";
import { FloatingPanelResetButton } from "./FloatingPanelControls";
import { FloatingPanelResizeHandles } from "./FloatingPanelResizeHandles";
import { SHOT_TRACK_COLLAPSED_HEIGHT } from "./floatingPanelLayout";
import { shotOrderLabel, stableShotCode } from "./shotIdentity";
import {
  dropPlacementFromMidpoint,
  reorderIdsAtPlacement,
  type DropPlacement,
} from "./shotReorder";
import { ShotThumbnail } from "./ShotThumbnail";
import type { FloatingPanelController } from "./useFloatingPanel";

const SHOT_TRANSFER_TYPE = "application/x-ai-canvas-shot-id";
const SHOT_FALLBACK_WIDTH = 280;
const SHOT_FALLBACK_HEIGHT = 380;
const SHOT_CREATE_GAP = 40;
const MEDIA_FALLBACK_WIDTH = 312;
const MEDIA_FALLBACK_HEIGHT = 360;
const TEXT_FALLBACK_WIDTH = 248;
const TEXT_FALLBACK_HEIGHT = 180;

function viewportShotAnchor(viewport: { x: number; y: number; zoom: number }) {
  return {
    x: (240 - viewport.x) / viewport.zoom,
    y: (180 - viewport.y) / viewport.zoom,
  };
}

function nodeBounds(node: ReturnType<typeof useCanvasStore.getState>["nodes"][number]) {
  const measuredWidth = node.measured?.width;
  const measuredHeight = node.measured?.height;
  const fallback =
    node.data.kind === "media_input"
      ? { width: MEDIA_FALLBACK_WIDTH, height: MEDIA_FALLBACK_HEIGHT }
      : node.data.kind === "shot"
        ? { width: SHOT_FALLBACK_WIDTH, height: SHOT_FALLBACK_HEIGHT }
        : { width: TEXT_FALLBACK_WIDTH, height: TEXT_FALLBACK_HEIGHT };
  return {
    x: node.position.x,
    y: node.position.y,
    width:
      typeof measuredWidth === "number" && Number.isFinite(measuredWidth) && measuredWidth > 0
        ? measuredWidth
        : fallback.width,
    height:
      typeof measuredHeight === "number" && Number.isFinite(measuredHeight) && measuredHeight > 0
        ? measuredHeight
        : fallback.height,
  };
}

function overlapsNodeBounds(
  position: { x: number; y: number },
  obstacle: ReturnType<typeof nodeBounds>,
): boolean {
  return (
    position.x < obstacle.x + obstacle.width &&
    position.x + SHOT_FALLBACK_WIDTH > obstacle.x &&
    position.y < obstacle.y + obstacle.height &&
    position.y + SHOT_FALLBACK_HEIGHT > obstacle.y
  );
}

function searchOffsets(radius: number): Array<readonly [number, number]> {
  if (radius === 0) return [[0, 0]];
  const offsets: Array<readonly [number, number]> = [];
  for (let x = -radius; x <= radius; x += 1) {
    offsets.push([x, -radius], [x, radius]);
  }
  for (let y = -radius + 1; y < radius; y += 1) {
    offsets.push([-radius, y], [radius, y]);
  }
  const directionRank = ([x, y]: readonly [number, number]) => {
    if (x > 0 && Math.abs(x) >= Math.abs(y)) return 0;
    if (y > 0 && Math.abs(y) > Math.abs(x)) return 1;
    if (x < 0 && Math.abs(x) >= Math.abs(y)) return 2;
    return 3;
  };
  return offsets.sort((left, right) => {
    const leftDistance = left[0] * left[0] + left[1] * left[1];
    const rightDistance = right[0] * right[0] + right[1] * right[1];
    if (leftDistance !== rightDistance) return leftDistance - rightDistance;
    const direction = directionRank(left) - directionRank(right);
    if (direction !== 0) return direction;
    if (left[1] !== right[1]) return left[1] - right[1];
    return left[0] - right[0];
  });
}

function vacantShotPosition(
  anchor: { x: number; y: number },
  nodes: ReturnType<typeof useCanvasStore.getState>["nodes"],
) {
  const obstacles = nodes.map(nodeBounds);
  for (let radius = 0; ; radius += 1) {
    for (const [x, y] of searchOffsets(radius)) {
      const position = {
        x: anchor.x + x * (SHOT_FALLBACK_WIDTH + SHOT_CREATE_GAP),
        y: anchor.y + y * (SHOT_FALLBACK_HEIGHT + SHOT_CREATE_GAP),
      };
      if (obstacles.every((obstacle) => !overlapsNodeBounds(position, obstacle))) {
        return position;
      }
    }
  }
}

type ShotTrackShot = ReturnType<typeof useCanvasStore.getState>["shots"][number];

type ActiveShotDrag = Readonly<{
  loadEpoch: number;
  projectId: string | null;
  shotId: string;
}>;

type ShotDropTarget = Readonly<{
  placement: DropPlacement;
  shotId: string;
}>;

function hasTransferType(dataTransfer: DataTransfer, transferType: string): boolean {
  return Array.from(dataTransfer.types).includes(transferType);
}

function ShotTrackItem({
  index,
  locked,
  onDelete,
  onDragEnd,
  onDragLeave,
  onDragOver,
  onDragStart,
  onDrop,
  onMove,
  onSelect,
  selected,
  shot,
  total,
  dragging,
  dropPlacement,
}: {
  index: number;
  locked: boolean;
  onDelete(): void;
  onDragEnd(): void;
  onDragLeave(event: DragEvent<HTMLLIElement>): void;
  onDragOver(event: DragEvent<HTMLLIElement>): void;
  onDragStart(event: DragEvent<HTMLLIElement>): void;
  onDrop(event: DragEvent<HTMLLIElement>): void;
  onMove(direction: -1 | 1): void;
  onSelect(): void;
  selected: boolean;
  shot: ShotTrackShot;
  total: number;
  dragging: boolean;
  dropPlacement: DropPlacement | null;
}) {
  const orderLabel = shotOrderLabel(shot.order);
  const accessibilityId = useId();
  const orderId = `${accessibilityId}-order`;
  const stableId = `${accessibilityId}-stable`;
  const thumbnailId = `${accessibilityId}-thumbnail`;
  return (
    <li
      aria-current={selected ? "true" : undefined}
      className={[
        selected ? "is-selected" : "",
        dragging ? "is-dragging" : "",
        dropPlacement === "before" ? "is-drop-before" : "",
        dropPlacement === "after" ? "is-drop-after" : "",
      ].filter(Boolean).join(" ")}
      draggable={!locked}
      onDragEnd={onDragEnd}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDragStart={onDragStart}
      onDrop={onDrop}
    >
      <button
        aria-describedby={`${stableId} ${thumbnailId}`}
        aria-labelledby={orderId}
        className="shot-track__select"
        onClick={onSelect}
        type="button"
      >
        <ShotThumbnail
          compact
          id={thumbnailId}
          revisionId={shot.imageRevisionId}
          shotLabel={orderLabel}
        />
        <span className="shot-track__labels">
          <strong id={orderId}>{orderLabel}</strong>
          <span className="shot-stable-id" id={stableId}>
            镜头 ID {stableShotCode(shot.id)}
          </span>
        </span>
      </button>
      <div className="shot-track__actions">
        <button
          aria-label={`上移分镜 ${index + 1}`}
          disabled={locked || index === 0}
          onClick={() => onMove(-1)}
          type="button"
        >
          上移
        </button>
        <button
          aria-label={`下移分镜 ${index + 1}`}
          disabled={locked || index === total - 1}
          onClick={() => onMove(1)}
          type="button"
        >
          下移
        </button>
        <button
          aria-label={`删除分镜 ${index + 1}`}
          disabled={locked}
          onClick={onDelete}
          type="button"
        >
          删除
        </button>
      </div>
    </li>
  );
}

export function ShotTrack({
  floatingPanel,
  onRequestDelete,
  selectedShotId,
}: {
  floatingPanel: FloatingPanelController;
  onRequestDelete(shotId: string): void;
  selectedShotId: string | null;
}) {
  const shots = useCanvasStore((state) => state.shots);
  const nodes = useCanvasStore((state) => state.nodes);
  const viewport = useCanvasStore((state) => state.viewport);
  const locked = useCanvasStore((state) => state.status === "conflict");
  const projectId = useCanvasStore((state) => state.projectId);
  const loadEpoch = useCanvasStore((state) => state.loadEpoch);
  const selectShot = useCanvasStore((state) => state.selectShot);
  const moveShot = useCanvasStore((state) => state.moveShot);
  const createShot = useCanvasStore((state) => state.createShot);
  const [activeDrag, setActiveDrag] = useState<ActiveShotDrag | null>(null);
  const [dropTarget, setDropTarget] = useState<ShotDropTarget | null>(null);
  const [reorderStatus, setReorderStatus] = useState("");
  const ordered = [...shots].sort((left, right) => left.order - right.order);
  const trackState =
    floatingPanel.rect === null ||
    floatingPanel.rect.height <= SHOT_TRACK_COLLAPSED_HEIGHT
      ? "collapsed"
      : "expanded";
  const collapsed = floatingPanel.active && trackState === "collapsed";

  const clearDragFeedback = () => {
    setActiveDrag(null);
    setDropTarget(null);
  };

  useEffect(() => {
    if (!activeDrag) return;
    const current = useCanvasStore.getState();
    if (
      current.status === "conflict" ||
      current.projectId !== activeDrag.projectId ||
      current.loadEpoch !== activeDrag.loadEpoch ||
      !current.shots.some((shot) => shot.id === activeDrag.shotId) ||
      (dropTarget !== null && !current.shots.some((shot) => shot.id === dropTarget.shotId))
    ) {
      clearDragFeedback();
    }
  }, [activeDrag, dropTarget, loadEpoch, locked, projectId, shots]);

  const currentActiveShot = () => {
    const current = useCanvasStore.getState();
    if (
      !activeDrag ||
      current.status === "conflict" ||
      current.projectId !== activeDrag.projectId ||
      current.loadEpoch !== activeDrag.loadEpoch ||
      !current.shots.some((shot) => shot.id === activeDrag.shotId)
    ) {
      return null;
    }
    return { current, shotId: activeDrag.shotId };
  };
  const currentActiveShotForDragOver = (event: DragEvent<HTMLLIElement>) => {
    const active = currentActiveShot();
    return active && hasTransferType(event.dataTransfer, SHOT_TRANSFER_TYPE) ? active : null;
  };
  const currentActiveShotForDrop = (event: DragEvent<HTMLLIElement>) => {
    const active = currentActiveShot();
    return active && event.dataTransfer.getData(SHOT_TRANSFER_TYPE) === active.shotId
      ? active
      : null;
  };

  const create = () => {
    if (locked) return;
    const selectedNode = nodes.find(
      (node) => node.data.kind === "shot" && node.data.shotId === selectedShotId,
    );
    const preferredPosition = selectedNode
      ? { x: selectedNode.position.x + 320, y: selectedNode.position.y + 80 }
      : viewportShotAnchor(viewport);
    const position = vacantShotPosition(preferredPosition, nodes);
    const created = createShot({ afterShotId: selectedShotId, position });
    if (created) selectShot(created.shotId);
  };
  const beginDrag = (event: DragEvent<HTMLLIElement>, shotId: string) => {
    const current = useCanvasStore.getState();
    if (current.status === "conflict" || !current.shots.some((shot) => shot.id === shotId)) return;
    event.dataTransfer.setData(SHOT_TRANSFER_TYPE, shotId);
    event.dataTransfer.setData("text/plain", shotId);
    event.dataTransfer.effectAllowed = "move";
    setReorderStatus("");
    setDropTarget(null);
    setActiveDrag({
      shotId,
      projectId: current.projectId,
      loadEpoch: current.loadEpoch,
    });
  };
  const dragOverTarget = (event: DragEvent<HTMLLIElement>, targetId: string) => {
    const active = currentActiveShotForDragOver(event);
    if (!active || active.shotId === targetId || !active.current.shots.some((shot) => shot.id === targetId)) {
      if (!active) clearDragFeedback();
      else setDropTarget(null);
      return;
    }
    const placement = dropPlacementFromMidpoint(
      "horizontal",
      event,
      event.currentTarget.getBoundingClientRect(),
    );
    if (!placement) {
      setDropTarget(null);
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropTarget({ shotId: targetId, placement });
  };
  const dragLeaveTarget = (event: DragEvent<HTMLLIElement>, targetId: string) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
    if (dropTarget?.shotId === targetId) setDropTarget(null);
  };
  const dropOn = (event: DragEvent<HTMLLIElement>, targetId: string) => {
    const active = currentActiveShotForDrop(event);
    if (!active || active.shotId === targetId || !active.current.shots.some((shot) => shot.id === targetId)) {
      clearDragFeedback();
      return;
    }
    const placement = dropPlacementFromMidpoint(
      "horizontal",
      event,
      event.currentTarget.getBoundingClientRect(),
    );
    if (!placement) {
      clearDragFeedback();
      return;
    }
    event.preventDefault();
    const current = useCanvasStore.getState();
    if (
      current.status === "conflict" ||
      current.projectId !== activeDrag?.projectId ||
      current.loadEpoch !== activeDrag?.loadEpoch
    ) {
      clearDragFeedback();
      return;
    }
    const proposedOrder = reorderIdsAtPlacement(
      current.shots
        .slice()
        .sort((left, right) => left.order - right.order)
        .map((shot) => shot.id),
      active.shotId,
      targetId,
      placement,
    );
    if (!proposedOrder || !current.reorderShots(proposedOrder)) {
      clearDragFeedback();
      return;
    }
    const position = useCanvasStore
      .getState()
      .shots
      .slice()
      .sort((left, right) => left.order - right.order)
      .findIndex((shot) => shot.id === active.shotId);
    if (position >= 0) {
      setReorderStatus(`镜头 ID ${stableShotCode(active.shotId)} 现在位于第 ${position + 1} 位`);
    }
    clearDragFeedback();
  };

  return (
    <nav
      aria-label="分镜轨道"
      className={`shot-track${floatingPanel.active ? " floating-panel--active" : ""}`}
      data-track-state={trackState}
      data-testid="shot-track"
      ref={floatingPanel.panelRef}
      style={floatingPanel.style}
    >
      {!collapsed ? (
        <>
          <div
            {...floatingPanel.directMoveHandleProps}
            className="shot-track__header floating-panel__drag-region"
            data-floating-panel-drag-region="true"
            title="拖拽空白区域移动"
          >
            <h2>分镜轨道</h2>
            <div className="shot-track__header-actions">
              <button disabled={locked} onClick={create} type="button">新建分镜</button>
              <FloatingPanelResetButton controller={floatingPanel} label="分镜轨道" />
            </div>
          </div>
          <ol>
            {ordered.map((shot, index) => (
              <ShotTrackItem
                dragging={activeDrag?.shotId === shot.id}
                dropPlacement={dropTarget?.shotId === shot.id ? dropTarget.placement : null}
                index={index}
                key={shot.id}
                locked={locked}
                onDelete={() => onRequestDelete(shot.id)}
                onDragEnd={clearDragFeedback}
                onDragLeave={(event) => dragLeaveTarget(event, shot.id)}
                onDragOver={(event) => dragOverTarget(event, shot.id)}
                onDragStart={(event) => beginDrag(event, shot.id)}
                onDrop={(event) => dropOn(event, shot.id)}
                onMove={(direction) => moveShot(shot.id, direction)}
                onSelect={() => selectShot(shot.id)}
                selected={shot.id === selectedShotId}
                shot={shot}
                total={ordered.length}
              />
            ))}
          </ol>
          <p aria-atomic="true" aria-live="polite" className="shot-track__reorder-status" role="status">
            {dropTarget
              ? `将放到${shotOrderLabel(ordered.find((shot) => shot.id === dropTarget.shotId)?.order ?? 0)}${dropTarget.placement === "before" ? "之前" : "之后"}`
              : reorderStatus}
          </p>
        </>
      ) : null}
      <FloatingPanelResizeHandles controller={floatingPanel} kind="track" />
    </nav>
  );
}
