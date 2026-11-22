import type {
  PromptBlock,
  PromptBlockKind,
  PromptDocument,
} from "../canvas/canvasTypes";
import { isCanvasUuid } from "../canvas/canvasTypes";

export const PROMPT_BLOCK_KINDS = [
  "SUBJECT",
  "SCENE_ACTION",
  "COMPOSITION_CAMERA",
  "LIGHT_COLOR_MATERIAL_STYLE",
  "NEGATIVE_CONSTRAINTS",
  "MOTION",
  "PROVIDER_EXTENSION",
  "FREEFORM",
] as const satisfies readonly PromptBlockKind[];

const promptBlockKindSet = new Set<string>(PROMPT_BLOCK_KINDS);

export type PromptDocumentValidation =
  | Readonly<{ ok: true }>
  | Readonly<{ ok: false; message: string }>;

export function rebuildPromptDocument(blocks: readonly PromptBlock[]): PromptDocument {
  let cursor = 0;
  const clonedBlocks = blocks.map((block) => ({ ...block }));
  const spans = clonedBlocks.map((block, index) => {
    const start = cursor;
    cursor += block.text.length;
    const span = { block_id: block.id, start_utf16: start, end_utf16: cursor };
    if (index < clonedBlocks.length - 1) cursor += 1;
    return span;
  });
  return {
    schema_version: 1,
    blocks: clonedBlocks,
    rendered_text: clonedBlocks.map((block) => block.text).join("\n"),
    spans,
  };
}

export function documentFromText(
  text: string,
  blockId: string = crypto.randomUUID(),
): PromptDocument {
  return rebuildPromptDocument([
    { id: blockId, kind: "FREEFORM", text, locked: false },
  ]);
}

export function validatePromptDocument(document: unknown): PromptDocumentValidation {
  if (!document || typeof document !== "object") {
    return { ok: false, message: "提示词文档格式无效。" };
  }
  const candidate = document as Partial<PromptDocument>;
  if (
    candidate.schema_version !== 1 ||
    !Array.isArray(candidate.blocks) ||
    !Array.isArray(candidate.spans) ||
    typeof candidate.rendered_text !== "string" ||
    candidate.blocks.length > 5_000 ||
    candidate.spans.length !== candidate.blocks.length
  ) {
    return { ok: false, message: "提示词文档结构不完整。" };
  }
  const blockIds = new Set<string>();
  for (let index = 0; index < candidate.blocks.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(candidate.blocks, index)) {
      return { ok: false, message: "提示词块列表不能包含空位。" };
    }
    const block = candidate.blocks[index];
    if (
      !block ||
      !isCanvasUuid(block.id) ||
      blockIds.has(block.id) ||
      !promptBlockKindSet.has(block.kind) ||
      typeof block.text !== "string" ||
      block.text.length > 100_000 ||
      typeof block.locked !== "boolean"
    ) {
      return { ok: false, message: "提示词块包含无效字段或重复 ID。" };
    }
    blockIds.add(block.id);
  }
  const rebuilt = rebuildPromptDocument(candidate.blocks);
  if (
    rebuilt.rendered_text !== candidate.rendered_text ||
    rebuilt.spans.some((span, index) => {
      if (!Object.prototype.hasOwnProperty.call(candidate.spans!, index)) return true;
      const actual = candidate.spans![index];
      return (
        !actual ||
        actual.block_id !== span.block_id ||
        actual.start_utf16 !== span.start_utf16 ||
        actual.end_utf16 !== span.end_utf16
      );
    })
  ) {
    return { ok: false, message: "渲染文本或 UTF-16 区间与提示词块不一致。" };
  }
  return { ok: true };
}

export function clonePromptDocument(document: PromptDocument): PromptDocument {
  return {
    schema_version: 1,
    blocks: document.blocks.map((block) => ({ ...block })),
    rendered_text: document.rendered_text,
    spans: document.spans.map((span) => ({ ...span })),
  };
}
