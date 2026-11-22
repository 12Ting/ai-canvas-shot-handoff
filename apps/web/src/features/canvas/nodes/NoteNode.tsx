import type { NodeProps } from "@xyflow/react";
import { useCanvasStore } from "../canvasStore";
import type { CanvasNode } from "../canvasTypes";

export function NoteNode({ id, data }: NodeProps<CanvasNode>) {
  const updateText = useCanvasStore((state) => state.updateNodeText);
  const copyNode = useCanvasStore((state) => state.copyNode);
  const deleteNode = useCanvasStore((state) => state.deleteNode);
  const locked = useCanvasStore((state) => state.status === "conflict");

  return (
    <article
      aria-label="便签节点"
      className="semantic-node semantic-node--note"
      data-node-id={id}
      data-node-kind="note"
    >
      <header>
        <strong>便签</strong>
        <button
          aria-label="复制便签节点"
          className="nodrag"
          disabled={locked}
          onClick={() => copyNode(id)}
          type="button"
        >
          复制
        </button>
        <button
          aria-label="删除便签节点"
          className="nodrag"
          disabled={locked}
          onClick={() => deleteNode(id)}
          type="button"
        >
          删除
        </button>
      </header>
      <textarea
        aria-label="便签内容"
        className="nodrag nopan nowheel"
        disabled={locked}
        maxLength={100_000}
        onChange={(event) => updateText(id, event.target.value)}
        value={data.text}
      />
    </article>
  );
}
