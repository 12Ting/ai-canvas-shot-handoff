import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useRef, useState } from "react";
import { PromptDocumentEditor } from "../../prompting/PromptDocumentEditor";
import { useCanvasStore } from "../canvasStore";
import type { CanvasNode } from "../canvasTypes";

export function PromptNode({ id, data }: NodeProps<CanvasNode>) {
  const copyNode = useCanvasStore((state) => state.copyNode);
  const deleteNode = useCanvasStore((state) => state.deleteNode);
  const locked = useCanvasStore((state) => state.status === "conflict");
  const [editorOpen, setEditorOpen] = useState(false);
  const editButtonRef = useRef<HTMLButtonElement>(null);

  if (data.kind !== "prompt") return null;

  function closeEditor(): void {
    setEditorOpen(false);
    queueMicrotask(() => editButtonRef.current?.focus());
  }

  return (
    <article
      aria-label="提示词节点"
      className="semantic-node semantic-node--prompt"
      data-node-id={id}
      data-node-kind="prompt"
    >
      <Handle
        data-testid={`target-text-${id}`}
        id="text"
        position={Position.Left}
        type="target"
      />
      <Handle
        aria-label="主提示词输出"
        data-testid={`source-prompt-document-${id}`}
        id="prompt_document"
        position={Position.Right}
        type="source"
      />
      <header>
        <strong>提示词</strong>
        <button
          aria-label="编辑结构化提示词"
          className="nodrag"
          onClick={() => setEditorOpen(true)}
          ref={editButtonRef}
          type="button"
        >
          编辑
        </button>
        <button
          aria-label="复制提示词节点"
          className="nodrag"
          disabled={locked}
          onClick={() => copyNode(id)}
          type="button"
        >
          复制
        </button>
        <button
          aria-label="删除提示词节点"
          className="nodrag"
          disabled={locked}
          onClick={() => deleteNode(id)}
          type="button"
        >
          删除
        </button>
      </header>
      <p className="prompt-node__excerpt">
        {data.document.rendered_text.trim() || "空提示词 · 打开编辑器添加结构化内容"}
      </p>
      {editorOpen ? <PromptDocumentEditor nodeId={id} onClose={closeEditor} /> : null}
    </article>
  );
}
