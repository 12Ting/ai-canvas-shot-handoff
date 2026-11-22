import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCanvasStore } from "../../canvasStore";
import { MediaIngressContext, type MediaIngressRuntimePort } from "../mediaIngressContext";
import type { MediaIngressController } from "../mediaIngressTypes";
import { MediaTargetLocks } from "../mediaTargetLocks";
import { MultiMediaPicker } from "./MultiMediaPicker";

const screenToFlowPosition = vi.fn(() => ({ x: 321, y: 654 }));
vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return { ...actual, useReactFlow: () => ({ screenToFlowPosition }) };
});

const enqueue = vi.fn();
function runtime(): MediaIngressRuntimePort {
  return {
    projectId: "project-1",
    loadEpoch: useCanvasStore.getState().loadEpoch,
    nodeImport: {} as never,
    targetLocks: new MediaTargetLocks(),
    controller: { enqueue } as unknown as MediaIngressController,
  };
}
function subject() {
  return render(
    <>
      <div className="react-flow" data-testid="other-flow-root" />
      <div className="react-flow" data-testid="current-flow-root">
        <MediaIngressContext.Provider value={runtime()}>
          <MultiMediaPicker />
        </MediaIngressContext.Provider>
      </div>
    </>,
  );
}

describe("MultiMediaPicker", () => {
  beforeEach(() => {
    enqueue.mockReset();
    enqueue.mockReturnValue({ kind: "accepted", batchId: "batch-1" });
    screenToFlowPosition.mockClear();
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [],
      edges: [],
      shots: [],
      revisionId: "revision-1",
    });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1000 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("uses the approved formats and multiple selection without creating a batch on cancel", () => {
    subject();
    const input = screen.getByLabelText("选择多个本地媒体") as HTMLInputElement;
    expect(input.multiple).toBe(true);
    expect(input.accept).toBe(
      "image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime,video/x-matroska,.mov,.mkv",
    );
    fireEvent.change(input, { target: { files: [] } });
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("copies files, clears the DOM input synchronously, and enqueues at the current flow center", () => {
    subject();
    const flowRoot = screen.getByTestId("current-flow-root");
    vi.spyOn(flowRoot, "getBoundingClientRect").mockReturnValue({
      x: 100,
      y: 200,
      left: 100,
      top: 200,
      right: 900,
      bottom: 700,
      width: 800,
      height: 500,
      toJSON: () => undefined,
    });
    const input = screen.getByLabelText("选择多个本地媒体") as HTMLInputElement;
    const selected = new File(["raw"], "same.png", { type: "image/png" });
    enqueue.mockImplementation(() => {
      expect(input.value).toBe("");
      return { kind: "accepted", batchId: "batch-1" };
    });

    fireEvent.change(input, { target: { files: [selected] } });
    fireEvent.change(input, { target: { files: [selected] } });

    expect(screenToFlowPosition).toHaveBeenCalledWith({ x: 500, y: 450 });
    expect(enqueue).toHaveBeenCalledTimes(2);
    expect(enqueue.mock.calls[0][0]).toMatchObject({
      projectId: "project-1",
      source: "multi_picker",
      anchor: { x: 321, y: 654 },
      candidates: [{ kind: "file", displayName: "same.png", file: selected }],
      targetMode: "create",
      targetBinding: null,
    });
  });

  it("clears rejected input and blocks conflict before handing any File to the controller", () => {
    subject();
    const input = screen.getByLabelText("选择多个本地媒体") as HTMLInputElement;
    act(() =>
      useCanvasStore.getState().markConflict(
        useCanvasStore.getState().loadEpoch,
        "project-1",
        "server-revision",
      ),
    );

    fireEvent.change(input, {
      target: { files: [new File(["raw"], "private.png", { type: "image/png" })] },
    });

    expect(input.value).toBe("");
    expect(enqueue).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("请先处理画布版本冲突");
  });
});
