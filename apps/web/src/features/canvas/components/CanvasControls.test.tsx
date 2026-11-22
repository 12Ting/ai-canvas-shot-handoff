import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useCanvasStore } from "../canvasStore";
import { NodePalette } from "./NodePalette";
import { SaveStatus } from "./SaveStatus";

describe("canvas controls", () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [],
      edges: [],
      shots: [],
      revisionId: "revision-1",
    });
  });
  afterEach(cleanup);

  it("adds the four B1 palette node kinds on a count-based grid without exposing Shot", () => {
    render(<NodePalette />);

    fireEvent.click(screen.getByRole("button", { name: "添加文案节点" }));
    fireEvent.click(screen.getByRole("button", { name: "添加提示词节点" }));
    fireEvent.click(screen.getByRole("button", { name: "添加便签" }));
    fireEvent.click(screen.getByRole("button", { name: "添加媒体节点" }));

    expect(useCanvasStore.getState().nodes.map(({ data, position }) => ({
      kind: data.kind,
      position,
    }))).toEqual([
      { kind: "text_source", position: { x: 120, y: 120 } },
      { kind: "prompt", position: { x: 440, y: 120 } },
      { kind: "note", position: { x: 760, y: 120 } },
      { kind: "media_input", position: { x: 120, y: 340 } },
    ]);
    expect(screen.queryByRole("button", { name: /分镜/ })).toBeNull();
  });

  it("locks the palette while a conflict awaits a choice", () => {
    useCanvasStore
      .getState()
      .markConflict(useCanvasStore.getState().loadEpoch, "project-1", "revision-2");
    render(<NodePalette />);

    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveProperty("disabled", true);
      expect(button.getAttribute("type")).toBe("button");
    }
  });

  it.each([
    ["clean", "已保存"],
    ["dirty", "有未保存修改"],
    ["saving", "保存中"],
    ["conflict", "保存冲突"],
    ["error", "保存失败：磁盘不可用"],
  ] as const)("announces %s as %s", (status, label) => {
    useCanvasStore.setState({
      status,
      errorMessage: status === "error" ? "磁盘不可用" : null,
    });
    render(<SaveStatus />);

    expect(screen.getByRole("status").textContent).toBe(label);
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
  });
});
