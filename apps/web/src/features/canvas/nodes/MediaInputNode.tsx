import {
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ChangeEvent,
} from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { useCanvasStore } from "../canvasStore";
import type { MediaInputNodeData } from "../canvasTypes";
import { MediaIngressContext } from "../mediaIngress/mediaIngressContext";
import type {
  IngressItemState,
  MediaIngressSnapshot,
} from "../mediaIngress/mediaIngressTypes";
import { useArtifactPreview } from "../useArtifactPreview";
import { useMediaImport } from "../useMediaImport";
import { isCompatibleShotSlot, writeShotMediaTransfer } from "../shotMediaTransfer";

type MediaNode = Node<MediaInputNodeData, "media_input">;

const ACCEPTED_MEDIA =
  "image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime,video/x-matroska";
const EMPTY_TARGET_LOCKS = Object.freeze({
  protectedNodeIds: Object.freeze([] as string[]),
});
const EMPTY_INGRESS_SNAPSHOT: MediaIngressSnapshot = Object.freeze({
  batches: Object.freeze([]),
  pendingTargetChoices: Object.freeze([]),
  protectedTargetNodeIds: Object.freeze([]),
  guardState: Object.freeze({
    pendingOperationCount: 0,
    hasSavedUnplaced: false,
  }),
});

function formatBytes(byteSize: number): string {
  if (byteSize < 1_024) return `${byteSize} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = byteSize / 1_024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1_024; index += 1) {
    value /= 1_024;
    unit = units[index];
  }
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value)} ${unit}`;
}

function importPhaseLabel(phase: ReturnType<typeof useMediaImport>["phase"]): string | null {
  switch (phase) {
    case "queued":
      return "排队中";
    case "initializing":
      return "正在创建本地导入会话…";
    case "receiving":
      return "正在传输素材…";
    case "validating":
      return "正在检查素材…";
    case "completed":
      return "导入完成，等待画布自动保存。";
    default:
      return null;
  }
}

function targetPhaseLabel(state: IngressItemState): string | null {
  switch (state) {
    case "QUEUED":
      return "排队中";
    case "INITIALIZING":
    case "RECEIVING":
    case "VALIDATING":
      return "正在处理导入素材…";
    case "CANCELLING":
      return "正在确认取消结果…";
    case "PLACING":
      return "正在更新当前节点…";
    default:
      return null;
  }
}

export function MediaInputNode({ id, data }: NodeProps<MediaNode>) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [codecError, setCodecError] = useState(false);
  const copyNode = useCanvasStore((state) => state.copyNode);
  const deleteNode = useCanvasStore((state) => state.deleteNode);
  const projectId = useCanvasStore((state) => state.projectId);
  const loadEpoch = useCanvasStore((state) => state.loadEpoch);
  const locked = useCanvasStore((state) => state.status === "conflict");
  const associationTarget = useCanvasStore((state) => state.associationTarget);
  const ingress = useContext(MediaIngressContext);
  const matchingIngress =
    ingress &&
    ingress.projectId === projectId &&
    ingress.loadEpoch === loadEpoch
      ? ingress
      : null;
  const targetLocks = matchingIngress?.targetLocks;
  const targetLockSnapshot = useSyncExternalStore(
    (listener) => targetLocks?.subscribe(listener) ?? (() => undefined),
    () => targetLocks?.getSnapshot() ?? EMPTY_TARGET_LOCKS,
    () => targetLocks?.getSnapshot() ?? EMPTY_TARGET_LOCKS,
  );
  const ingressController = matchingIngress?.controller;
  const ingressSnapshot = useSyncExternalStore(
    (listener) =>
      ingressController?.subscribe(listener) ?? (() => undefined),
    () => ingressController?.getSnapshot() ?? EMPTY_INGRESS_SNAPSHOT,
    () => ingressController?.getSnapshot() ?? EMPTY_INGRESS_SNAPSHOT,
  );
  const mediaImport = useMediaImport(id);
  const preview = useArtifactPreview(data.artifactRevisionId);

  useEffect(() => setCodecError(false), [data.artifactRevisionId, preview.objectUrl]);

  const attached = data.artifactRevisionId !== null;
  const ready = preview.phase === "ready";
  const previewMismatch =
    ready &&
    (preview.revision.revision_id !== data.artifactRevisionId ||
      preview.revision.media_kind !== data.mediaKind);
  const summary = ready && !previewMismatch ? preview.revision : null;
  const openFileChooser = () => fileInput.current?.click();
  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const [file] = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    if (file) mediaImport.selectFile(file);
  };
  const phaseLabel = importPhaseLabel(mediaImport.phase);
  const targetProtected =
    targetLockSnapshot.protectedNodeIds.includes(id);
  const targetSlot = ingressSnapshot.batches
    .flatMap((batch) => batch.slots)
    .find((slot) => slot.targetNodeId === id);
  const pendingTargetChoice = ingressSnapshot.pendingTargetChoices.find(
    (choice) => choice.targetNodeId === id,
  );
  const controllerPhaseLabel = pendingTargetChoice
    ? "等待选择导入方式"
    : targetSlot
      ? targetPhaseLabel(targetSlot.currentItem.state)
      : null;
  const selectionDisabled =
    mediaImport.selectionDisabled || targetProtected;
  const selectionTitle = targetProtected
    ? "该媒体节点正在处理另一项导入"
    : mediaImport.replacementBlockedReason ?? undefined;
  const associationClass =
    associationTarget === null
      ? null
      : attached &&
          data.mediaKind !== null &&
          !targetProtected &&
          isCompatibleShotSlot(associationTarget.slot, data.mediaKind)
        ? "semantic-node--media-association-candidate"
        : "semantic-node--media-association-incompatible";
  const assetStrip = summary ? (
    <div
      className="media-node__asset-strip nodrag"
      draggable={!targetProtected}
      onDragStart={(event) => {
        if (targetProtected || !data.artifactRevisionId || !data.mediaKind) return;
        writeShotMediaTransfer(event.nativeEvent, {
          revisionId: data.artifactRevisionId,
          mediaKind: data.mediaKind,
          nodeId: id,
        });
      }}
    >
      <div className="media-node__preview nodrag nowheel">
        {summary.media_kind === "image" ? (
          <img alt={summary.display_filename} src={preview.objectUrl} />
        ) : (
          <video
            aria-label={`视频预览：${summary.display_filename}`}
            controls
            muted
            onError={() => setCodecError(true)}
            playsInline
            preload="metadata"
            src={preview.objectUrl}
          />
        )}
      </div>
      <dl className="media-node__metadata">
        <div className="media-node__filename">
          <dt>文件</dt>
          <dd>
            <bdi dir="auto">{summary.display_filename}</bdi>
          </dd>
        </div>
        <div>
          <dt>类型</dt>
          <dd>{summary.media_kind === "image" ? "图片" : "视频"}</dd>
        </div>
        <div>
          <dt>大小</dt>
          <dd>{formatBytes(summary.byte_size)}</dd>
        </div>
        <div>
          <dt>画面</dt>
          <dd>
            {summary.metadata.width} × {summary.metadata.height}
          </dd>
        </div>
        {summary.metadata.kind === "video" ? (
          <div>
            <dt>时长</dt>
            <dd>{summary.metadata.duration_seconds} 秒</dd>
          </div>
        ) : null}
      </dl>
    </div>
  ) : null;

  return (
    <article
      aria-label="媒体节点"
      className={`semantic-node semantic-node--media${
        associationClass ? ` ${associationClass}` : ""
      }`}
      data-node-id={id}
      data-node-kind="media_input"
    >
      <header>
        <strong>本地媒体</strong>
        <button
          aria-label="复制媒体节点"
          className="nodrag"
          disabled={locked}
          onClick={() => copyNode(id)}
          type="button"
        >
          复制
        </button>
        <button
          aria-label="删除媒体节点"
          className="nodrag"
          disabled={locked || mediaImport.active || targetProtected}
          onClick={() => deleteNode(id)}
          type="button"
        >
          删除
        </button>
      </header>

      <input
        accept={ACCEPTED_MEDIA}
        aria-label="选择要导入的本地媒体文件"
        className="media-node__file-input nodrag"
        disabled={selectionDisabled}
        onChange={onFileChange}
        ref={fileInput}
        tabIndex={-1}
        type="file"
      />

      {assetStrip ? assetStrip : attached && preview.phase === "loading" ? (
        <div aria-live="polite" className="media-node__placeholder" role="status">
          正在安全加载预览…
        </div>
      ) : !attached ? (
        <div className="media-node__placeholder">尚未选择素材</div>
      ) : null}

      {preview.phase === "error" ? (
        <div className="media-node__feedback media-node__feedback--error" role="alert">
          <span>{preview.errorMessage}</span>
          {preview.retryable ? (
            <button className="nodrag" onClick={preview.retry} type="button">
              重试预览
            </button>
          ) : null}
        </div>
      ) : null}
      {previewMismatch ? (
        <p className="media-node__feedback media-node__feedback--error" role="alert">
          预览摘要与当前节点不一致，已停止展示。
        </p>
      ) : null}
      {codecError ? (
        <p className="media-node__feedback media-node__feedback--error" role="alert">
          素材已保存，但浏览器无法预览当前编码。
        </p>
      ) : null}
      {mediaImport.errorMessage ? (
        <div className="media-node__feedback media-node__feedback--error" role="alert">
          <span>{mediaImport.errorMessage}</span>
          {mediaImport.recoveryKind === "retry" ? (
            <button
              className="nodrag"
              disabled={selectionDisabled}
              onClick={mediaImport.retry}
              title={selectionTitle}
              type="button"
            >
              重试导入
            </button>
          ) : null}
          {mediaImport.recoveryKind === "reselect" ? (
            <button
              className="nodrag"
              disabled={selectionDisabled}
              onClick={openFileChooser}
              title={selectionTitle}
              type="button"
            >
              重新选择文件
            </button>
          ) : null}
          {mediaImport.recoveryKind === "reload_project" ? (
            <button
              className="nodrag"
              disabled={selectionDisabled}
              onClick={mediaImport.requestReload}
              title={selectionTitle}
              type="button"
            >
              重新加载项目
            </button>
          ) : null}
        </div>
      ) : null}
      {phaseLabel ? (
        <p aria-live="polite" className="media-node__feedback" role="status">
          {phaseLabel}
        </p>
      ) : null}
      {controllerPhaseLabel ? (
        <p aria-live="polite" className="media-node__feedback" role="status">
          {controllerPhaseLabel}
        </p>
      ) : null}

      <button
        aria-label={attached ? "替换媒体" : "选择本地图片或视频"}
        className="media-node__select nodrag"
        disabled={selectionDisabled}
        onClick={openFileChooser}
        title={selectionTitle}
        type="button"
      >
        {attached ? "替换媒体" : "选择本地图片或视频"}
      </button>
    </article>
  );
}
