import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useCanvasStore } from "../canvasStore";
import type { CanvasNode } from "../canvasTypes";

export function TextSourceNode({ id, data }: NodeProps<CanvasNode>) {
  const updateText = useCanvasStore((state) => state.updateNodeText);
  const copyNode = useCanvasStore((state) => state.copyNode);
  const deleteNode = useCanvasStore((state) => state.deleteNode);
  const locked = useCanvasStore((state) => state.status === "conflict");

  return (
    <article
      aria-label="文案节点"
      className="semantic-node semantic-node--text-source"
      data-node-id={id}
      data-node-kind="text_source"
    >
      <header>
        <strong>文案</strong>
        <button
          aria-label="复制文案节点"
          className="nodrag"
          disabled={locked}
          onClick={() => copyNode(id)}
          type="button"
        >
          复制
        </button>
        <button
          aria-label="删除文案节点"
          className="nodrag"
          disabled={locked}
          onClick={() => deleteNode(id)}
          type="button"
        >
          删除
        </button>
      </header>
      <textarea
        aria-label="文案内容"
        className="nodrag nopan nowheel"
        disabled={locked}
        maxLength={100_000}
        onChange={(event) => updateText(id, event.target.value)}
        value={data.text}
      />
      <Handle
        data-testid={`source-text-${id}`}
        id="text"
        position={Position.Right}
        type="source"
      />
    </article>
  );
}
