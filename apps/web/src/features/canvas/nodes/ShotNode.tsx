import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { useCanvasStore } from "../canvasStore";
import type { ShotNodeData } from "../canvasTypes";
import { shotOrderLabel, stableShotCode } from "../components/shotIdentity";
import { ShotThumbnail } from "../components/ShotThumbnail";

type ReadonlyShotNode = Node<ShotNodeData, "shot">;

export function ShotNode({ id, data }: NodeProps<ReadonlyShotNode>) {
  const shot = useCanvasStore((state) =>
    state.shots.find((candidate) => candidate.id === data.shotId),
  );
  const selected = useCanvasStore((state) => state.selectedShotId === data.shotId);
  const shotCount = useCanvasStore((state) => state.shots.length);
  const locked = useCanvasStore((state) => state.status === "conflict");
  const moveShot = useCanvasStore((state) => state.moveShot);

  if (!shot) {
    return (
      <article
        aria-label="缺失的分镜"
        className="semantic-node semantic-node--shot"
        data-node-id={id}
        data-node-kind="shot"
        role="alert"
      >
        找不到这个分镜的数据。
      </article>
    );
  }
  const orderLabel = shotOrderLabel(shot.order);
  const stableCode = stableShotCode(shot.id);

  return (
    <article
      aria-label={`分镜 ${shot.order + 1}`}
      className={`semantic-node semantic-node--shot${
        selected ? " semantic-node--shot-selected" : ""
      }`}
      data-node-id={id}
      data-node-kind="shot"
    >
      <Handle
        aria-label="主提示词输入"
        data-testid={`target-prompt-document-${id}`}
        id="prompt_document"
        position={Position.Left}
        type="target"
      />
      <header>
        <div className="shot-node__identity">
          <strong>{orderLabel}</strong>
          <span className="shot-stable-id">镜头 ID {stableCode}</span>
        </div>
        <div>
          <button
            aria-label={`上移分镜 ${shot.order + 1}`}
            className="nodrag"
            disabled={locked || shot.order === 0}
            onClick={(event) => {
              event.stopPropagation();
              moveShot(shot.id, -1);
            }}
            type="button"
          >
            上移
          </button>
          <button
            aria-label={`下移分镜 ${shot.order + 1}`}
            className="nodrag"
            disabled={locked || shot.order >= shotCount - 1}
            onClick={(event) => {
              event.stopPropagation();
              moveShot(shot.id, 1);
            }}
            type="button"
          >
            下移
          </button>
        </div>
      </header>
      <ShotThumbnail
        compact={false}
        revisionId={shot.imageRevisionId}
        shotLabel={orderLabel}
      />
    </article>
  );
}
