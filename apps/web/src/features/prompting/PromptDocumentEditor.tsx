import { useState } from "react";
import { createPortal } from "react-dom";
import { useCanvasStore } from "../canvas/canvasStore";
import type { PromptBlockKind, PromptDocument } from "../canvas/canvasTypes";
import {
  clonePromptDocument,
  PROMPT_BLOCK_KINDS,
  rebuildPromptDocument,
  validatePromptDocument,
} from "./promptDocument";

const BLOCK_LABELS: Record<PromptBlockKind, string> = {
  SUBJECT: "主体",
  SCENE_ACTION: "场景与动作",
  COMPOSITION_CAMERA: "构图与镜头",
  LIGHT_COLOR_MATERIAL_STYLE: "光线、色彩、材质与风格",
  NEGATIVE_CONSTRAINTS: "负向约束",
  MOTION: "运动",
  PROVIDER_EXTENSION: "提供方扩展",
  FREEFORM: "自由文本",
};

type PromptDocumentEditorProps = {
  nodeId: string;
  onClose(): void;
};

export function PromptDocumentEditor({ nodeId, onClose }: PromptDocumentEditorProps) {
  const node = useCanvasStore((state) => state.nodes.find((candidate) => candidate.id === nodeId));
  const conflict = useCanvasStore((state) => state.status === "conflict");
  const addPromptBlock = useCanvasStore((state) => state.addPromptBlock);
  const updatePromptBlock = useCanvasStore((state) => state.updatePromptBlock);
  const movePromptBlock = useCanvasStore((state) => state.movePromptBlock);
  const setPromptBlockLocked = useCanvasStore((state) => state.setPromptBlockLocked);
  const removePromptBlock = useCanvasStore((state) => state.removePromptBlock);
  const initialDocument =
    node?.data.kind === "prompt"
      ? clonePromptDocument(node.data.document)
      : ({ schema_version: 1, blocks: [], rendered_text: "", spans: [] } as PromptDocument);
  const [draft, setDraft] = useState<PromptDocument>(initialDocument);
  const [error, setError] = useState<string | null>(null);

  function updateDraftText(blockId: string, text: string): void {
    const blocks = draft.blocks.map((block) =>
      block.id === blockId ? { ...block, text } : block,
    );
    const next = rebuildPromptDocument(blocks);
    setDraft(next);
    const validation = validatePromptDocument(next);
    if (validation.ok === false) {
      setError(
        text.length > 100_000
          ? "每个提示词块最多包含 100000 个 UTF-16 字符；草稿已保留。"
          : validation.message,
      );
      return;
    }
    updatePromptBlock(nodeId, blockId, { text });
    setError(null);
  }

  function updateDraftKind(blockId: string, kind: PromptBlockKind): void {
    if (!updatePromptBlock(nodeId, blockId, { kind })) return;
    setDraft(
      rebuildPromptDocument(
        draft.blocks.map((block) => (block.id === blockId ? { ...block, kind } : block)),
      ),
    );
    setError(null);
  }

  function toggleLock(blockId: string, locked: boolean): void {
    if (!setPromptBlockLocked(nodeId, blockId, locked)) return;
    setDraft(
      rebuildPromptDocument(
        draft.blocks.map((block) => (block.id === blockId ? { ...block, locked } : block)),
      ),
    );
    setError(null);
  }

  function moveBlock(blockId: string, direction: -1 | 1): void {
    if (!movePromptBlock(nodeId, blockId, direction)) return;
    const blocks = draft.blocks.map((block) => ({ ...block }));
    const index = blocks.findIndex((block) => block.id === blockId);
    const destination = index + direction;
    [blocks[index], blocks[destination]] = [blocks[destination], blocks[index]];
    setDraft(rebuildPromptDocument(blocks));
    setError(null);
  }

  function addBlock(): void {
    const block = {
      id: crypto.randomUUID(),
      kind: "FREEFORM" as const,
      text: "",
      locked: false,
    };
    if (!addPromptBlock(nodeId, block)) return;
    setDraft(rebuildPromptDocument([...draft.blocks, block]));
    setError(null);
  }

  function removeBlock(blockId: string): void {
    if (!removePromptBlock(nodeId, blockId)) return;
    setDraft(
      rebuildPromptDocument(draft.blocks.filter((block) => block.id !== blockId)),
    );
    setError(null);
  }

  function closeEditor(): void {
    const validation = validatePromptDocument(draft);
    if (validation.ok === false) {
      setError(
        draft.blocks.some((block) => block.text.length > 100_000)
          ? "每个提示词块最多包含 100000 个 UTF-16 字符；草稿已保留。"
          : validation.message,
      );
      return;
    }
    onClose();
  }

  const titleId = `prompt-document-editor-title-${nodeId}`;
  return createPortal(
    <div className="prompt-document-editor__backdrop">
    <section
      aria-labelledby={titleId}
      aria-modal="true"
      className="prompt-document-editor nodrag nopan nowheel"
      role="dialog"
    >
      <header>
        <div>
          <span className="prompt-document-editor__eyebrow">PROMPT DOCUMENT · V1</span>
          <h2 id={titleId}>结构化提示词编辑器</h2>
        </div>
        <button aria-label="关闭提示词编辑器" onClick={closeEditor} type="button">
          关闭
        </button>
      </header>

      {conflict ? (
        <p className="prompt-document-editor__notice">
          当前图版本存在冲突。你可以查看草稿，但需先处理冲突才能继续编辑。
        </p>
      ) : null}

      <div className="prompt-document-editor__workspace">
        <ol aria-label="提示词块" className="prompt-document-editor__blocks">
          {draft.blocks.map((block, index) => (
            <li className={block.locked ? "is-locked" : undefined} key={block.id}>
              <div className="prompt-document-editor__block-header">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <label>
                  <span className="sr-only">提示词块 {index + 1} 类型</span>
                  <select
                    aria-label={`提示词块 ${index + 1} 类型`}
                    disabled={conflict || block.locked}
                    onChange={(event) =>
                      updateDraftKind(block.id, event.target.value as PromptBlockKind)
                    }
                    value={block.kind}
                  >
                    {PROMPT_BLOCK_KINDS.map((kind) => (
                      <option key={kind} value={kind}>
                        {BLOCK_LABELS[kind]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="prompt-document-editor__lock">
                  <input
                    aria-label={`锁定提示词块 ${index + 1}`}
                    checked={block.locked}
                    disabled={conflict}
                    onChange={(event) => toggleLock(block.id, event.target.checked)}
                    type="checkbox"
                  />
                  锁定
                </label>
              </div>
              <textarea
                aria-label={`提示词块 ${index + 1} 内容`}
                disabled={conflict || block.locked}
                onChange={(event) => updateDraftText(block.id, event.target.value)}
                value={block.text}
              />
              <div className="prompt-document-editor__block-actions">
                <button
                  aria-label={`上移提示词块 ${index + 1}`}
                  disabled={conflict || index === 0}
                  onClick={() => moveBlock(block.id, -1)}
                  type="button"
                >
                  上移
                </button>
                <button
                  aria-label={`下移提示词块 ${index + 1}`}
                  disabled={conflict || index === draft.blocks.length - 1}
                  onClick={() => moveBlock(block.id, 1)}
                  type="button"
                >
                  下移
                </button>
                <button
                  aria-label={`删除提示词块 ${index + 1}`}
                  disabled={conflict || block.locked}
                  onClick={() => removeBlock(block.id)}
                  type="button"
                >
                  删除
                </button>
              </div>
            </li>
          ))}
        </ol>

        <aside className="prompt-document-editor__preview">
          <div>
            <span>DETERMINISTIC RENDER</span>
            <strong>{draft.rendered_text.length} UTF-16</strong>
          </div>
          <pre aria-label="渲染预览">{draft.rendered_text}</pre>
        </aside>
      </div>

      <footer>
        <button disabled={conflict} onClick={addBlock} type="button">
          新增提示词块
        </button>
        <p aria-live="polite" role="status">
          {error ?? ""}
        </p>
      </footer>
    </section>
    </div>,
    document.body,
  );
}
