import { useContext, useEffect, useState, type DragEvent } from "react";
import { useCanvasStore } from "../canvasStore";
import type { ShotAssociationTarget } from "../canvasTypes";
import { MediaIngressContext } from "../mediaIngress/mediaIngressContext";
import { isCompatibleShotSlot, readShotMediaTransfer } from "../shotMediaTransfer";
import { useArtifactPreview } from "../useArtifactPreview";
import { ArtifactProvenance } from "./ArtifactProvenance";
import type { ShotHistoryTarget } from "./ArtifactHistoryDrawer";
import { FloatingPanelResetButton } from "./FloatingPanelControls";
import { FloatingPanelResizeHandles } from "./FloatingPanelResizeHandles";
import { shotOrderLabel, stableShotCode } from "./shotIdentity";
import {
  dropPlacementFromMidpoint,
  reorderIdsAtPlacement,
  type DropPlacement,
} from "./shotReorder";
import type { FloatingPanelController } from "./useFloatingPanel";

const REFERENCE_REORDER_TRANSFER_TYPE = "application/x-ai-canvas-shot-reference";

type ActiveReferenceDrag = Readonly<{
  displayFilename: string;
  loadEpoch: number;
  projectId: string | null;
  revisionId: string;
  shotId: string;
}>;

type ReferenceDropTarget = Readonly<{
  displayFilename: string;
  placement: DropPlacement;
  revisionId: string;
}>;

function hasTransferType(dataTransfer: DataTransfer, transferType: string): boolean {
  return Array.from(dataTransfer.types).includes(transferType);
}

function AssetSummary({ revisionId }: { revisionId: string | null }) {
  const preview = useArtifactPreview(revisionId);
  if (!revisionId) return <p className="shot-inspector__empty">尚未关联素材</p>;
  if (preview.phase === "loading") return <p className="shot-inspector__loading">正在安全加载预览…</p>;
  if (preview.phase !== "ready") return <p className="shot-inspector__error">预览不可用</p>;
  const { revision } = preview;
  return (
    <div
      className="shot-inspector__asset"
      data-artifact-display-filename={revision.display_filename}
    >
      {revision.media_kind === "image" ? (
        <img alt={revision.display_filename} src={preview.objectUrl ?? undefined} />
      ) : (
        <video aria-label={`${revision.display_filename} 预览`} controls muted src={preview.objectUrl ?? undefined} />
      )}
      <dl>
        <div><dt>文件</dt><dd><bdi>{revision.display_filename}</bdi></dd></div>
        <div><dt>类型</dt><dd>{revision.media_type}</dd></div>
        <div><dt>画面</dt><dd>{revision.metadata.width} × {revision.metadata.height}</dd></div>
        <div><dt>来源</dt><dd>{revision.source_kind}</dd></div>
        <div><dt>版本</dt><dd>版本 {revision.sequence}</dd></div>
      </dl>
      <ArtifactProvenance revision={revision} />
    </div>
  );
}

export function ShotInspector({
  associationTarget = null,
  floatingPanel,
  onOpenHistory,
  selectedShotId,
}: {
  associationTarget?: ShotAssociationTarget | null;
  floatingPanel: FloatingPanelController;
  onOpenHistory(target: ShotHistoryTarget): void;
  selectedShotId: string | null;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [activeReferenceDrag, setActiveReferenceDrag] = useState<ActiveReferenceDrag | null>(null);
  const [referenceDropTarget, setReferenceDropTarget] = useState<ReferenceDropTarget | null>(null);
  const [referenceReorderStatus, setReferenceReorderStatus] = useState("");
  const ingress = useContext(MediaIngressContext);
  const shot = useCanvasStore((state) =>
    selectedShotId === null ? null : state.shots.find((candidate) => candidate.id === selectedShotId) ?? null,
  );
  const locked = useCanvasStore((state) => state.status === "conflict");
  const projectId = useCanvasStore((state) => state.projectId);
  const loadEpoch = useCanvasStore((state) => state.loadEpoch);
  const beginShotAssociation = useCanvasStore((state) => state.beginShotAssociation);
  const cancelShotAssociation = useCanvasStore((state) => state.cancelShotAssociation);
  const setShotSlot = useCanvasStore((state) => state.setShotSlot);
  const removeShotReference = useCanvasStore((state) => state.removeShotReference);
  const floatingClass = floatingPanel.active ? " floating-panel--active" : "";

  const clearReferenceDrag = () => {
    setActiveReferenceDrag(null);
    setReferenceDropTarget(null);
  };

  useEffect(() => {
    if (!activeReferenceDrag) return;
    const current = useCanvasStore.getState();
    const currentShot = current.shots.find((candidate) => candidate.id === activeReferenceDrag.shotId);
    if (
      current.status === "conflict" ||
      current.projectId !== activeReferenceDrag.projectId ||
      current.loadEpoch !== activeReferenceDrag.loadEpoch ||
      shot?.id !== activeReferenceDrag.shotId ||
      !currentShot?.referenceRevisionIds.includes(activeReferenceDrag.revisionId) ||
      (referenceDropTarget !== null &&
        !currentShot?.referenceRevisionIds.includes(referenceDropTarget.revisionId))
    ) {
      clearReferenceDrag();
    }
  }, [
    activeReferenceDrag,
    loadEpoch,
    locked,
    projectId,
    referenceDropTarget,
    shot?.id,
    shot?.referenceRevisionIds,
  ]);

  if (!shot) {
    return (
      <aside
        aria-label="分镜检查器"
        className={`shot-inspector shot-inspector--empty${floatingClass}`}
        data-testid="shot-inspector"
        ref={floatingPanel.panelRef}
        style={floatingPanel.style}
      >
        <header
          {...floatingPanel.directMoveHandleProps}
          className="shot-inspector__header floating-panel__drag-region"
          data-floating-panel-drag-region="true"
          title="拖拽空白区域移动"
        >
          <div><p>未选择分镜</p><h2>素材检查器</h2></div>
          <FloatingPanelResetButton controller={floatingPanel} label="分镜素材检查器" />
        </header>
        <div className="shot-inspector__content">
          <p>选择一个分镜以检查素材。</p>
        </div>
        <FloatingPanelResizeHandles controller={floatingPanel} kind="inspector" />
      </aside>
    );
  }
  const orderLabel = shotOrderLabel(shot.order);
  const stableCode = stableShotCode(shot.id);

  const handleDrop = (slot: "image" | "video" | "reference") => (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    const revision = readShotMediaTransfer(event.nativeEvent);
    if (!revision || !isCompatibleShotSlot(slot, revision.mediaKind)) return;
    const current = useCanvasStore.getState();
    const source = current.nodes.find((candidate) => candidate.id === revision.nodeId);
    if (
      current.status === "conflict" ||
      ingress === null ||
      ingress.projectId !== current.projectId ||
      ingress.loadEpoch !== current.loadEpoch ||
      ingress.targetLocks.getSnapshot().protectedNodeIds.includes(revision.nodeId) ||
      !source ||
      source.type !== "media_input" ||
      source.data.kind !== "media_input" ||
      source.data.artifactRevisionId !== revision.revisionId ||
      source.data.mediaKind !== revision.mediaKind
    ) {
      return;
    }
    const changed =
      slot === "reference"
        ? current.addShotReference(shot.id, revision)
        : current.setShotSlot(shot.id, slot, revision);
    const target = current.associationTarget;
    if (changed && target?.shotId === shot.id && target.slot === slot) {
      current.cancelShotAssociation();
    }
  };
  const openHistory = (slot: "image" | "video" | "reference", revisionId: string | null) => {
    if (revisionId) onOpenHistory({ shotId: shot.id, slot, revisionId });
  };
  const moveReference = (revisionId: string, direction: -1 | 1) => {
    const index = shot.referenceRevisionIds.indexOf(revisionId);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= shot.referenceRevisionIds.length) return;
    const ordered = [...shot.referenceRevisionIds];
    [ordered[index], ordered[nextIndex]] = [ordered[nextIndex]!, ordered[index]!];
    useCanvasStore.getState().reorderShotReferences(shot.id, ordered);
  };
  const currentActiveReference = () => {
    const current = useCanvasStore.getState();
    const currentShot = activeReferenceDrag
      ? current.shots.find((candidate) => candidate.id === activeReferenceDrag.shotId)
      : null;
    if (
      !activeReferenceDrag ||
      current.status === "conflict" ||
      current.projectId !== activeReferenceDrag.projectId ||
      current.loadEpoch !== activeReferenceDrag.loadEpoch ||
      !currentShot?.referenceRevisionIds.includes(activeReferenceDrag.revisionId)
    ) {
      return null;
    }
    return { current, currentShot, revisionId: activeReferenceDrag.revisionId };
  };
  const currentActiveReferenceForDragOver = (event: DragEvent<HTMLLIElement>) => {
    const active = currentActiveReference();
    return active && hasTransferType(event.dataTransfer, REFERENCE_REORDER_TRANSFER_TYPE)
      ? active
      : null;
  };
  const currentActiveReferenceForDrop = (event: DragEvent<HTMLLIElement>) => {
    const active = currentActiveReference();
    return active &&
      event.dataTransfer.getData(REFERENCE_REORDER_TRANSFER_TYPE) === active.revisionId
      ? active
      : null;
  };
  const beginReferenceDrag = (event: DragEvent<HTMLLIElement>, revisionId: string) => {
    const current = useCanvasStore.getState();
    const currentShot = current.shots.find((candidate) => candidate.id === shot.id);
    const displayFilename = event.currentTarget
      .querySelector<HTMLElement>("[data-artifact-display-filename]")
      ?.dataset.artifactDisplayFilename;
    if (
      current.status === "conflict" ||
      !currentShot?.referenceRevisionIds.includes(revisionId) ||
      !displayFilename
    ) {
      clearReferenceDrag();
      return;
    }
    event.dataTransfer.setData(REFERENCE_REORDER_TRANSFER_TYPE, revisionId);
    event.dataTransfer.setData("text/plain", revisionId);
    event.dataTransfer.effectAllowed = "move";
    setReferenceReorderStatus("");
    setReferenceDropTarget(null);
    setActiveReferenceDrag({
      displayFilename,
      loadEpoch: current.loadEpoch,
      projectId: current.projectId,
      revisionId,
      shotId: currentShot.id,
    });
  };
  const dragOverReference = (event: DragEvent<HTMLLIElement>, targetRevisionId: string) => {
    const active = currentActiveReferenceForDragOver(event);
    if (!active) {
      clearReferenceDrag();
      return;
    }
    if (
      active.revisionId === targetRevisionId ||
      !active.currentShot.referenceRevisionIds.includes(targetRevisionId)
    ) {
      setReferenceDropTarget(null);
      return;
    }
    const placement = dropPlacementFromMidpoint(
      "vertical",
      event,
      event.currentTarget.getBoundingClientRect(),
    );
    if (!placement) {
      setReferenceDropTarget(null);
      return;
    }
    const displayFilename = event.currentTarget
      .querySelector<HTMLElement>("[data-artifact-display-filename]")
      ?.dataset.artifactDisplayFilename;
    if (!displayFilename) {
      setReferenceDropTarget(null);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "move";
    setReferenceDropTarget({ revisionId: targetRevisionId, placement, displayFilename });
  };
  const dragLeaveReference = (event: DragEvent<HTMLLIElement>, targetRevisionId: string) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
    if (referenceDropTarget?.revisionId === targetRevisionId) setReferenceDropTarget(null);
  };
  const reorderDraggedReference = (targetRevisionId: string) => (event: DragEvent<HTMLLIElement>) => {
    const active = currentActiveReferenceForDrop(event);
    if (!active) {
      clearReferenceDrag();
      return;
    }
    if (
      active.revisionId === targetRevisionId ||
      !active.currentShot.referenceRevisionIds.includes(targetRevisionId)
    ) {
      clearReferenceDrag();
      return;
    }
    const placement = dropPlacementFromMidpoint(
      "vertical",
      event,
      event.currentTarget.getBoundingClientRect(),
    );
    if (!placement) {
      clearReferenceDrag();
      return;
    }
    const current = useCanvasStore.getState();
    if (
      current.status === "conflict" ||
      current.projectId !== activeReferenceDrag?.projectId ||
      current.loadEpoch !== activeReferenceDrag?.loadEpoch
    ) {
      clearReferenceDrag();
      return;
    }
    const currentShot = current.shots.find((candidate) => candidate.id === active.currentShot.id);
    const ordered = currentShot
      ? reorderIdsAtPlacement(
          currentShot.referenceRevisionIds,
          active.revisionId,
          targetRevisionId,
          placement,
        )
      : null;
    if (!currentShot || !ordered || !current.reorderShotReferences(currentShot.id, ordered)) {
      clearReferenceDrag();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const position = useCanvasStore
      .getState()
      .shots
      .find((candidate) => candidate.id === currentShot.id)
      ?.referenceRevisionIds.indexOf(active.revisionId) ?? -1;
    if (position >= 0) {
      setReferenceReorderStatus(
        `参考文件 ${activeReferenceDrag.displayFilename} 现在位于第 ${position + 1} 位`,
      );
    }
    clearReferenceDrag();
  };

  const slot = (
    slotName: "image" | "video",
    label: string,
    revisionId: string | null,
  ) => (
    <section
      aria-label={`${label}槽位`}
      className="shot-inspector__slot"
      key={slotName}
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop(slotName)}
      role="region"
    >
      <header><h3>{label}</h3><span>{slotName === "image" ? "仅图片" : "仅视频"}</span></header>
      <AssetSummary revisionId={revisionId} />
      <div className="shot-inspector__actions">
        <button disabled={locked} onClick={() => beginShotAssociation(shot.id, slotName)} type="button">关联{label}</button>
        <button disabled={locked || !revisionId} onClick={() => setShotSlot(shot.id, slotName, null)} type="button">清除{label}</button>
        <button
          data-shot-history-revision-id={revisionId ?? undefined}
          data-shot-history-shot-id={revisionId ? shot.id : undefined}
          data-shot-history-slot={revisionId ? slotName : undefined}
          data-shot-history-trigger={revisionId ? "true" : undefined}
          disabled={!revisionId}
          onClick={() => openHistory(slotName, revisionId)}
          type="button"
        >
          查看历史
        </button>
      </div>
    </section>
  );

  return (
    <aside
      aria-label="分镜检查器"
      className={`shot-inspector${collapsed ? " shot-inspector--collapsed" : ""}${floatingClass}`}
      data-testid="shot-inspector"
      ref={floatingPanel.panelRef}
      style={floatingPanel.style}
    >
      <header
        {...floatingPanel.directMoveHandleProps}
        className="shot-inspector__header floating-panel__drag-region"
        data-floating-panel-drag-region="true"
        title="拖拽空白区域移动"
      >
        <div className="shot-inspector__identity">
          <p>{orderLabel}</p>
          <span className="shot-stable-id">镜头 ID {stableCode}</span>
          <h2>素材检查器</h2>
        </div>
        <FloatingPanelResetButton controller={floatingPanel} label="分镜素材检查器" />
        <button
          aria-label={collapsed ? "展开检查器" : "收起检查器"}
          aria-pressed={collapsed}
          onClick={() => setCollapsed((current) => !current)}
          type="button"
        >
          {collapsed ? "展开" : "收起"}
        </button>
        {associationTarget?.shotId === shot.id ? (
          <button disabled={locked} onClick={cancelShotAssociation} type="button">取消关联</button>
        ) : null}
      </header>
      <div className="shot-inspector__content" hidden={collapsed}>
        {slot("image", "图片", shot.imageRevisionId)}
        {slot("video", "视频", shot.videoRevisionId)}
        <section
        aria-label="参考槽位"
        className="shot-inspector__slot"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop("reference")}
        role="region"
        >
        <header><h3>参考</h3><span>仅图片，可排序</span></header>
        <div className="shot-inspector__actions">
          <button disabled={locked} onClick={() => beginShotAssociation(shot.id, "reference")} type="button">关联参考</button>
        </div>
        {shot.referenceRevisionIds.length === 0 ? <p className="shot-inspector__empty">尚未关联参考素材</p> : null}
        <ol className="shot-inspector__references">
          {shot.referenceRevisionIds.map((revisionId, index) => (
            <li
              className={[
                activeReferenceDrag?.revisionId === revisionId ? "is-dragging" : "",
                referenceDropTarget?.revisionId === revisionId && referenceDropTarget.placement === "before"
                  ? "is-drop-before"
                  : "",
                referenceDropTarget?.revisionId === revisionId && referenceDropTarget.placement === "after"
                  ? "is-drop-after"
                  : "",
              ].filter(Boolean).join(" ")}
              draggable={!locked}
              key={revisionId}
              onDragEnd={clearReferenceDrag}
              onDragLeave={(event) => dragLeaveReference(event, revisionId)}
              onDragOver={(event) => dragOverReference(event, revisionId)}
              onDragStart={(event) => beginReferenceDrag(event, revisionId)}
              onDrop={reorderDraggedReference(revisionId)}
            >
              <AssetSummary revisionId={revisionId} />
              <div className="shot-inspector__actions">
                <button disabled={locked} onClick={() => removeShotReference(shot.id, revisionId)} type="button">移除参考 {index + 1}</button>
                <button disabled={locked || index === 0} onClick={() => moveReference(revisionId, -1)} type="button">上移参考 {index + 1}</button>
                <button disabled={locked || index === shot.referenceRevisionIds.length - 1} onClick={() => moveReference(revisionId, 1)} type="button">下移参考 {index + 1}</button>
                <button
                  data-shot-history-revision-id={revisionId}
                  data-shot-history-shot-id={shot.id}
                  data-shot-history-slot="reference"
                  data-shot-history-trigger="true"
                  onClick={() => openHistory("reference", revisionId)}
                  type="button"
                >
                  查看参考历史 {index + 1}
                </button>
              </div>
            </li>
          ))}
        </ol>
        <p aria-atomic="true" aria-live="polite" className="shot-inspector__reorder-status" role="status">
          {referenceDropTarget
            ? `将放到${referenceDropTarget.displayFilename}${referenceDropTarget.placement === "before" ? "之前" : "之后"}`
            : referenceReorderStatus}
        </p>
        </section>
      </div>
      <FloatingPanelResizeHandles controller={floatingPanel} kind="inspector" />
    </aside>
  );
}
