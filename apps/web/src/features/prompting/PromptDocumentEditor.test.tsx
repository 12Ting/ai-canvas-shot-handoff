import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCanvasStore } from "../canvas/canvasStore";
import type { CanvasNode } from "../canvas/canvasTypes";
import { documentFromText } from "./promptDocument";
import { PromptDocumentEditor } from "./PromptDocumentEditor";

const NODE_ID = "11111111-1111-4111-8111-111111111111";

function loadPrompt(text = "主体🙂"): void {
  const node: CanvasNode = {
    id: NODE_ID,
    type: "prompt",
    position: { x: 0, y: 0 },
    data: { kind: "prompt", document: documentFromText(text, NODE_ID) },
  };
  useCanvasStore.getState().load({
    projectId: "project-1",
    nodes: [node],
    edges: [],
    shots: [],
    revisionId: "revision-1",
  });
}

describe("PromptDocumentEditor", () => {
  afterEach(cleanup);
  beforeEach(() => {
    useCanvasStore.getState().reset();
    loadPrompt();
  });

  it("edits labeled blocks and shows deterministic rendered preview", () => {
    const close = vi.fn();
    render(<PromptDocumentEditor nodeId={NODE_ID} onClose={close} />);

    expect(screen.getByRole("dialog", { name: "结构化提示词编辑器" })).toBeTruthy();
    const text = screen.getByRole("textbox", { name: "提示词块 1 内容" });
    fireEvent.change(text, { target: { value: "新主体🙂" } });

    expect(screen.getByLabelText("渲染预览").textContent).toBe("新主体🙂");
    const node = useCanvasStore.getState().nodes[0];
    expect(node.data.kind === "prompt" && node.data.document.rendered_text).toBe("新主体🙂");
    fireEvent.click(screen.getByRole("checkbox", { name: "锁定提示词块 1" }));
    expect(screen.getByRole("textbox", { name: "提示词块 1 内容" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("keeps an invalid draft open and recoverable until corrected", () => {
    const close = vi.fn();
    render(<PromptDocumentEditor nodeId={NODE_ID} onClose={close} />);
    const text = screen.getByRole("textbox", { name: "提示词块 1 内容" });
    const invalidDraft = "x".repeat(100_001);

    fireEvent.change(text, { target: { value: invalidDraft } });
    fireEvent.click(screen.getByRole("button", { name: "关闭提示词编辑器" }));

    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toContain("100000");
    expect(screen.getByRole("textbox", { name: "提示词块 1 内容" })).toHaveProperty(
      "value",
      invalidDraft,
    );

    fireEvent.change(text, { target: { value: "已修复" } });
    fireEvent.click(screen.getByRole("button", { name: "关闭提示词编辑器" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("disables every mutation when graph conflict mode is active", () => {
    useCanvasStore.getState().markConflict(
      useCanvasStore.getState().loadEpoch,
      "project-1",
      "revision-2",
    );
    render(<PromptDocumentEditor nodeId={NODE_ID} onClose={vi.fn()} />);

    expect(screen.getByRole("textbox", { name: "提示词块 1 内容" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: "新增提示词块" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("checkbox", { name: "锁定提示词块 1" })).toHaveProperty(
      "disabled",
      true,
    );
  });
});
