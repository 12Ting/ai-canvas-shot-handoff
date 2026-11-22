import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCanvasStore } from "../../canvasStore";
import {
  MediaIngressContext,
  type MediaIngressRuntimePort,
} from "../mediaIngressContext";
import type {
  MediaIngressController,
  MediaIngressRequest,
  MediaIngressSnapshot,
} from "../mediaIngressTypes";
import { MediaTargetLocks } from "../mediaTargetLocks";
import { CanvasMediaIngressBoundary } from "./CanvasMediaIngressBoundary";

const screenToFlowPosition = vi.hoisted(() =>
  vi.fn(({ x, y }: { x: number; y: number }) => ({ x: x - 10, y: y - 20 })),
);
const getNodes = vi.hoisted(() => vi.fn());
const getEdges = vi.hoisted(() => vi.fn());

vi.mock("@xyflow/react", async () => {
  const actual =
    await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    useReactFlow: () => ({ getEdges, getNodes, screenToFlowPosition }),
  };
});

const EMPTY_SNAPSHOT: MediaIngressSnapshot = {
  batches: [],
  pendingTargetChoices: [],
  protectedTargetNodeIds: [],
  guardState: { pendingOperationCount: 0, hasSavedUnplaced: false },
};

type TransferItemInput = Readonly<{
  kind: DataTransferItem["kind"];
  type: string;
  file?: File | null;
  entry?: Readonly<{ isDirectory: boolean; name: string }>;
  getAsFile?: () => File | null;
}>;

function transfer(
  inputs: readonly TransferItemInput[],
  files: readonly File[] = [],
  types?: readonly string[],
) {
  const getData = vi.fn(() => "https://example.invalid/never-read");
  const items = inputs.map((input) => ({
    kind: input.kind,
    type: input.type,
    getAsFile: input.getAsFile ?? (() => input.file ?? null),
    webkitGetAsEntry:
      input.entry === undefined ? undefined : () => input.entry,
  })) as unknown as DataTransferItem[];
  return {
    dataTransfer: {
      items: items as unknown as DataTransferItemList,
      files: files as unknown as FileList,
      types:
        types ??
        (inputs.some((input) => input.kind === "file") || files.length > 0
          ? ["Files"]
          : inputs.map((input) => input.type)),
      getData,
    } as unknown as DataTransfer,
    getData,
  };
}

function nativeDragEvent(
  type: "dragenter" | "dragover" | "dragleave" | "dragend" | "drop",
  dataTransfer: DataTransfer,
  options: { clientX?: number; clientY?: number; relatedTarget?: EventTarget | null } = {},
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    dataTransfer: { value: dataTransfer },
    clientX: { value: options.clientX ?? 0 },
    clientY: { value: options.clientY ?? 0 },
    relatedTarget: { value: options.relatedTarget ?? null },
  });
  return event;
}

function nativePasteEvent(
  dataTransfer: DataTransfer,
  composedPath?: () => EventTarget[],
) {
  const event = new Event("paste", {
    bubbles: true,
    cancelable: true,
    composed: true,
  });
  Object.defineProperty(event, "clipboardData", { value: dataTransfer });
  if (composedPath) {
    Object.defineProperty(event, "composedPath", { value: composedPath });
  }
  return event;
}

function runtime(enqueue = vi.fn()): MediaIngressRuntimePort {
  return {
    projectId: "project-1",
    loadEpoch: useCanvasStore.getState().loadEpoch,
    nodeImport: {} as never,
    controller: {
      enqueue,
      getSnapshot: () => EMPTY_SNAPSHOT,
      subscribe: () => () => undefined,
    } as unknown as MediaIngressController,
    targetLocks: new MediaTargetLocks(),
  };
}

function rejectingChoiceRuntime(
  rejectionMessages: Readonly<Record<string, string>>,
) {
  let snapshot = EMPTY_SNAPSHOT;
  let choiceSequence = 0;
  const listeners = new Set<() => void>();
  const publish = () => {
    for (const listener of listeners) listener();
  };
  const enqueue = vi.fn((request: MediaIngressRequest) => {
    choiceSequence += 1;
    const choiceId = `choice-${choiceSequence}`;
    const targetNodeId =
      request.targetBinding?.targetNodeId ?? `target-${choiceSequence}`;
    snapshot = {
      batches: [],
      pendingTargetChoices: [
        ...snapshot.pendingTargetChoices,
        {
          choiceId,
          displayName:
            request.candidates[0]?.displayName ??
            `replacement-${choiceSequence}.png`,
          targetNodeId,
          requestedMediaKind: "image",
          currentMediaKind: "image",
          canReplace: true,
          replacementBlockedReason: null,
        },
      ],
      protectedTargetNodeIds: [
        ...snapshot.protectedTargetNodeIds,
        targetNodeId,
      ],
      guardState: {
        pendingOperationCount:
          snapshot.guardState.pendingOperationCount + 1,
        hasSavedUnplaced: false,
      },
    };
    publish();
    return { kind: "choice_required" as const, choiceId };
  });
  const resolveTargetChoice = vi.fn(
    (
      choiceId: string,
      selected: "create" | "replace" | "cancel",
    ) => {
      const pending = snapshot.pendingTargetChoices.find(
        (choice) => choice.choiceId === choiceId,
      );
      if (!pending) {
        return { kind: "empty" as const };
      }
      const remaining = snapshot.pendingTargetChoices.filter(
        (choice) => choice.choiceId !== choiceId,
      );
      snapshot = {
        batches: [],
        pendingTargetChoices: remaining,
        protectedTargetNodeIds: remaining.map(
          (choice) => choice.targetNodeId,
        ),
        guardState: {
          pendingOperationCount: remaining.length,
          hasSavedUnplaced: false,
        },
      };
      publish();
      if (selected === "cancel") return { kind: "empty" as const };
      const message = rejectionMessages[choiceId];
      return message
        ? { kind: "rejected" as const, message }
        : { kind: "accepted" as const, batchId: `batch-${choiceId}` };
    },
  );
  const controller = {
    enqueue,
    resolveTargetChoice,
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  } as unknown as MediaIngressController;
  return {
    value: {
      projectId: "project-1",
      loadEpoch: useCanvasStore.getState().loadEpoch,
      nodeImport: {} as never,
      controller,
      targetLocks: new MediaTargetLocks(),
    } satisfies MediaIngressRuntimePort,
    controller,
    enqueue,
    resolveTargetChoice,
  };
}

function renderBoundary(
  value: MediaIngressRuntimePort | null,
  child: ReactNode = <div data-testid="drop-target"><span>child</span></div>,
) {
  return render(
    <MediaIngressContext.Provider value={value}>
      <CanvasMediaIngressBoundary>{child}</CanvasMediaIngressBoundary>
    </MediaIngressContext.Provider>,
  );
}

describe("CanvasMediaIngressBoundary", () => {
  beforeEach(() => {
    screenToFlowPosition.mockClear();
    getNodes.mockReset();
    getEdges.mockReset();
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId: "project-1",
      revisionId: "revision-1",
      nodes: [],
      edges: [],
      shots: [],
    });
    getNodes.mockImplementation(() => useCanvasStore.getState().nodes);
    getEdges.mockImplementation(() => useCanvasStore.getState().edges);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows feedback only for local file intent and clears it after leaving", () => {
    renderBoundary(runtime());
    const boundary = screen.getByTestId("canvas-media-ingress-boundary");
    const image = new File(["png"], "shot.png", { type: "image/png" });
    const getAsFile = vi.fn(() => image);
    const local = transfer(
      [{ kind: "file", type: "image/png", getAsFile }],
      [],
      ["Files"],
    );

    const enter = nativeDragEvent("dragenter", local.dataTransfer);
    fireEvent(boundary, enter);

    expect(enter.defaultPrevented).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("释放以导入");
    expect(getAsFile).not.toHaveBeenCalled();

    const leave = nativeDragEvent("dragleave", local.dataTransfer);
    fireEvent(boundary, leave);
    expect(screen.queryByRole("status")).toBeNull();

    fireEvent(boundary, nativeDragEvent("dragenter", local.dataTransfer));
    expect(screen.getByRole("status")).toBeTruthy();
    const end = nativeDragEvent("dragend", local.dataTransfer);
    fireEvent(boundary, end);
    expect(end.defaultPrevented).toBe(false);
    expect(screen.queryByRole("status")).toBeNull();

    const url = transfer(
      [{ kind: "string", type: "text/uri-list" }],
      [],
      ["text/uri-list"],
    );
    const urlEnter = nativeDragEvent("dragenter", url.dataTransfer);
    fireEvent(boundary, urlEnter);
    expect(urlEnter.defaultPrevented).toBe(true);
    expect(screen.queryByRole("status")).toBeNull();
    expect(url.getData).not.toHaveBeenCalled();
  });

  it.each(["text/uri-list", "text/html", "text/plain"])(
    "blocks %s navigation without reading or importing it",
    (type) => {
      const enqueue = vi.fn();
      renderBoundary(runtime(enqueue));
      const boundary = screen.getByTestId("canvas-media-ingress-boundary");
      const external = transfer(
        [{ kind: "string", type }],
        [],
        [type],
      );
      const fetchSpy = vi.fn();
      vi.stubGlobal("fetch", fetchSpy);

      const over = nativeDragEvent("dragover", external.dataTransfer);
      fireEvent(boundary, over);
      const drop = nativeDragEvent("drop", external.dataTransfer, {
        clientX: 120,
        clientY: 90,
      });
      fireEvent(boundary, drop);

      expect(over.defaultPrevented).toBe(true);
      expect(drop.defaultPrevented).toBe(true);
      expect(external.getData).not.toHaveBeenCalled();
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(enqueue).not.toHaveBeenCalled();
      expect(screen.queryByRole("status")).toBeNull();
    },
  );

  it("leaves internal or unknown drags entirely to React Flow", () => {
    const enqueue = vi.fn();
    renderBoundary(runtime(enqueue));
    const boundary = screen.getByTestId("canvas-media-ingress-boundary");
    const internal = transfer([], [], ["application/x-reactflow"]);

    const enter = nativeDragEvent("dragenter", internal.dataTransfer);
    const over = nativeDragEvent("dragover", internal.dataTransfer);
    const drop = nativeDragEvent("drop", internal.dataTransfer);
    fireEvent(boundary, enter);
    fireEvent(boundary, over);
    fireEvent(boundary, drop);

    expect(enter.defaultPrevented).toBe(false);
    expect(over.defaultPrevented).toBe(false);
    expect(drop.defaultPrevented).toBe(false);
    expect(enqueue).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("enqueues one authoritative item at the converted drop coordinate", () => {
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(runtime(enqueue));
    const boundary = screen.getByTestId("canvas-media-ingress-boundary");
    const image = new File(["png"], "shot.png", { type: "image/png" });
    const filesOnly = new File(["other"], "other.png", { type: "image/png" });
    const input = transfer(
      [{ kind: "file", type: "image/png", file: image }],
      [image, filesOnly],
      ["Files"],
    );
    const drop = nativeDragEvent("drop", input.dataTransfer, {
      clientX: 210,
      clientY: 320,
    });

    fireEvent(boundary, drop);

    expect(drop.defaultPrevented).toBe(true);
    expect(screenToFlowPosition).toHaveBeenCalledWith({ x: 210, y: 320 });
    expect(enqueue).toHaveBeenCalledTimes(1);
    const request = enqueue.mock.calls[0]?.[0] as MediaIngressRequest;
    expect(request).toMatchObject({
      projectId: "project-1",
      source: "drop",
      anchor: { x: 200, y: 300 },
      targetMode: "create",
      targetBinding: null,
    });
    expect(request.candidates).toEqual([
      { kind: "file", file: image, displayName: "shot.png" },
    ]);
  });

  it("dispatches one file on an actual empty media node as a null-CAS fill at the node-near anchor", () => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "empty-media",
          type: "media_input",
          position: { x: 100, y: 60 },
          measured: { width: 220, height: 180 },
          data: {
            kind: "media_input",
            artifactRevisionId: null,
            mediaKind: null,
          },
        },
      ],
    });
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(
      runtime(enqueue),
      <div className="react-flow__node" data-id="empty-media">
        <button data-testid="empty-media-target" type="button">
          empty media
        </button>
      </div>,
    );
    const image = new File(["png"], "fill.png", { type: "image/png" });
    const input = transfer([
      { kind: "file", type: "image/png", file: image },
    ]);

    fireEvent(
      screen.getByTestId("empty-media-target"),
      nativeDragEvent("drop", input.dataTransfer, {
        clientX: 900,
        clientY: 700,
      }),
    );

    expect(screenToFlowPosition).not.toHaveBeenCalled();
    expect(enqueue).toHaveBeenCalledOnce();
    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      anchor: { x: 360, y: 60 },
      targetMode: "fill_existing",
      targetBinding: {
        targetNodeId: "empty-media",
        expectedRevisionId: null,
      },
    });
  });

  it("dispatches one file on a filled media node to a guarded choice using the media fallback width", () => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "filled-media",
          type: "media_input",
          position: { x: 20, y: 30 },
          data: {
            kind: "media_input",
            artifactRevisionId: "revision-current",
            mediaKind: "image",
          },
        },
      ],
    });
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "choice_required" as const,
      choiceId: "choice-1",
    }));
    renderBoundary(
      runtime(enqueue),
      <div className="react-flow__node" data-id="filled-media">
        <span data-testid="filled-media-target">filled media</span>
      </div>,
    );
    const image = new File(["png"], "replace.png", { type: "image/png" });
    const input = transfer([
      { kind: "file", type: "image/png", file: image },
    ]);

    fireEvent(
      screen.getByTestId("filled-media-target"),
      nativeDragEvent("drop", input.dataTransfer),
    );

    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      anchor: { x: 372, y: 30 },
      targetMode: "choose_create_or_replace",
      targetBinding: {
        targetNodeId: "filled-media",
        expectedRevisionId: "revision-current",
      },
    });
  });

  it.each([
    {
      selected: "create" as const,
      buttonName: "新建节点",
      message: "请先处理画布版本冲突",
    },
    {
      selected: "replace" as const,
      buttonName: "替换当前",
      message: "目标媒体节点已变化，请重新导入",
    },
  ])(
    "surfaces a rejected $selected choice after the controller atomically removes it",
    ({ selected, buttonName, message }) => {
      useCanvasStore.setState({
        nodes: [
          {
            id: "filled-media",
            type: "media_input",
            position: { x: 20, y: 30 },
            data: {
              kind: "media_input",
              artifactRevisionId: "revision-current",
              mediaKind: "image",
            },
          },
        ],
      });
      const choiceRuntime = rejectingChoiceRuntime({
        "choice-1": message,
      });
      renderBoundary(
        choiceRuntime.value,
        <div className="react-flow__node" data-id="filled-media">
          <button data-testid="filled-media-choice-target" type="button">
            filled media
          </button>
        </div>,
      );
      const image = new File(["png"], "replacement.png", {
        type: "image/png",
      });
      const input = transfer([
        { kind: "file", type: "image/png", file: image },
      ]);

      fireEvent(
        screen.getByTestId("filled-media-choice-target"),
        nativeDragEvent("drop", input.dataTransfer),
      );
      expect(screen.getByRole("dialog", { name: "选择导入方式" })).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: buttonName }));

      expect(choiceRuntime.resolveTargetChoice).toHaveBeenCalledWith(
        "choice-1",
        selected,
      );
      expect(
        choiceRuntime.controller.getSnapshot().pendingTargetChoices,
      ).toEqual([]);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByRole("alert").textContent).toBe(message);
    },
  );

  it("keeps a rejected FIFO choice error above the next dialog and preserves per-choice focus", async () => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "filled-media-1",
          type: "media_input",
          position: { x: 20, y: 30 },
          data: {
            kind: "media_input",
            artifactRevisionId: "revision-first",
            mediaKind: "image",
          },
        },
        {
          id: "filled-media-2",
          type: "media_input",
          position: { x: 420, y: 30 },
          data: {
            kind: "media_input",
            artifactRevisionId: "revision-second",
            mediaKind: "image",
          },
        },
      ],
    });
    const rejectionMessage = "请先处理画布版本冲突";
    const choiceRuntime = rejectingChoiceRuntime({
      "choice-1": rejectionMessage,
    });
    renderBoundary(
      choiceRuntime.value,
      <>
        <div className="react-flow__node" data-id="filled-media-1">
          <button data-testid="first-choice-trigger" type="button">
            first media
          </button>
        </div>
        <div className="react-flow__node" data-id="filled-media-2">
          <button data-testid="second-choice-trigger" type="button">
            second media
          </button>
        </div>
      </>,
    );
    const firstInput = transfer([
      {
        kind: "file",
        type: "image/png",
        file: new File(["first"], "first.png", { type: "image/png" }),
      },
    ]);
    const secondInput = transfer([
      {
        kind: "file",
        type: "image/png",
        file: new File(["second"], "second.png", { type: "image/png" }),
      },
    ]);

    fireEvent(
      screen.getByTestId("first-choice-trigger"),
      nativeDragEvent("drop", firstInput.dataTransfer),
    );
    fireEvent(
      screen.getByTestId("second-choice-trigger"),
      nativeDragEvent("drop", secondInput.dataTransfer),
    );
    expect(screen.getByRole("dialog").textContent).toContain("first.png");

    fireEvent.click(screen.getByRole("button", { name: "新建节点" }));

    const alert = screen.getByRole("alert");
    const nextDialog = screen.getByRole("dialog");
    const nextCreate = screen.getByRole("button", { name: "新建节点" });
    const dialogLayer = nextDialog.parentElement;
    expect(alert.textContent).toBe(rejectionMessage);
    expect(nextDialog.textContent).toContain("second.png");
    expect(nextCreate).toBe(document.activeElement);
    expect(choiceRuntime.resolveTargetChoice).toHaveBeenNthCalledWith(
      1,
      "choice-1",
      "create",
    );
    expect(alert.classList.contains("canvas-media-drop-feedback--error")).toBe(
      true,
    );
    expect(dialogLayer).toHaveProperty(
      "className",
      "media-target-choice-layer",
    );
    expect(Number(alert.style.zIndex)).toBeGreaterThan(40);
    expect(alert.style.pointerEvents).toBe("none");

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await act(async () => undefined);

    expect(choiceRuntime.resolveTargetChoice).toHaveBeenNthCalledWith(
      2,
      "choice-2",
      "cancel",
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(
      screen.getByTestId("second-choice-trigger"),
    );
    expect(document.activeElement).not.toBe(
      screen.getByTestId("first-choice-trigger"),
    );
  });

  it.each([
    { label: "empty", revisionId: null, mediaKind: null },
    {
      label: "filled",
      revisionId: "revision-current",
      mediaKind: "image",
    },
  ] as const)(
    "creates every item near a $label media target for multi-drop and never fills or replaces the first",
    ({ revisionId, mediaKind }) => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "media-multi",
          type: "media_input",
          position: { x: 40, y: 90 },
          measured: { width: Number.NaN, height: 180 },
          data: {
            kind: "media_input",
            artifactRevisionId: revisionId,
            mediaKind,
          },
        },
      ],
    });
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(
      runtime(enqueue),
      <div className="react-flow__node" data-id="media-multi">
        <span data-testid="media-multi-target">media</span>
      </div>,
    );
    const first = new File(["a"], "first.png", { type: "image/png" });
    const second = new File(["b"], "second.webm", { type: "video/webm" });
    const input = transfer([
      { kind: "file", type: "image/png", file: first },
      { kind: "file", type: "video/webm", file: second },
    ]);

    fireEvent(
      screen.getByTestId("media-multi-target"),
      nativeDragEvent("drop", input.dataTransfer),
    );

    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      anchor: { x: 392, y: 90 },
      targetMode: "create",
      targetBinding: null,
    });
    },
  );

  it("does not grant target semantics to a non-media Store node with React Flow node markup", () => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "note-1",
          type: "note",
          position: { x: 10, y: 20 },
          data: { kind: "note", text: "not a media target" },
        },
      ],
    });
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(
      runtime(enqueue),
      <div className="react-flow__node" data-id="note-1">
        <span data-testid="note-drop-target">note</span>
      </div>,
    );
    const image = new File(["png"], "new.png", { type: "image/png" });
    const input = transfer([
      { kind: "file", type: "image/png", file: image },
    ]);

    fireEvent(
      screen.getByTestId("note-drop-target"),
      nativeDragEvent("drop", input.dataTransfer, {
        clientX: 210,
        clientY: 320,
      }),
    );

    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      anchor: { x: 200, y: 300 },
      targetMode: "create",
      targetBinding: null,
    });
  });

  it("uses FileList order only when no file-kind item exists and keeps a media-node multi-drop in create mode", () => {
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(
      runtime(enqueue),
      <div className="react-flow__node" data-id="media-1">
        <div data-testid="media-drop-target">media</div>
      </div>,
    );
    const first = new File(["a"], "first.png", { type: "image/png" });
    const second = new File(["b"], "second.webm", { type: "video/webm" });
    const input = transfer(
      [{ kind: "string", type: "text/plain" }],
      [first, second],
      ["Files", "text/plain"],
    );

    fireEvent(
      screen.getByTestId("media-drop-target"),
      nativeDragEvent("drop", input.dataTransfer),
    );

    const request = enqueue.mock.calls[0]?.[0] as MediaIngressRequest;
    expect(request.candidates.map((candidate) => candidate.displayName)).toEqual([
      "first.png",
      "second.webm",
    ]);
    expect(request.targetMode).toBe("create");
    expect(request.anchor).toEqual({ x: -10, y: -20 });
  });

  it("rejects a conflict before reading any File while still blocking navigation", () => {
    const enqueue = vi.fn();
    const getAsFile = vi.fn(() => new File(["png"], "shot.png"));
    renderBoundary(runtime(enqueue));
    act(() => {
      useCanvasStore
        .getState()
        .markConflict(
          useCanvasStore.getState().loadEpoch,
          "project-1",
          "revision-2",
        );
    });
    const input = transfer(
      [{ kind: "file", type: "image/png", getAsFile }],
      [],
      ["Files"],
    );
    const drop = nativeDragEvent("drop", input.dataTransfer);

    fireEvent(screen.getByTestId("canvas-media-ingress-boundary"), drop);

    expect(drop.defaultPrevented).toBe(true);
    expect(getAsFile).not.toHaveBeenCalled();
    expect(enqueue).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(
      "请先处理画布版本冲突",
    );
  });

  it.each([
    {
      label: "input",
      testId: "editable-input",
      element: <input data-testid="editable-input" />,
    },
    {
      label: "textarea",
      testId: "editable-textarea",
      element: <textarea data-testid="editable-textarea" />,
    },
    {
      label: "contenteditable",
      testId: "editable-content",
      element: (
        <div
          contentEditable
          data-testid="editable-content"
          suppressContentEditableWarning
        >
          editable
        </div>
      ),
    },
    {
      label: "code editor",
      testId: "editable-code",
      element: (
        <div className="monaco-editor" data-testid="editable-code">
          editor
        </div>
      ),
    },
    {
      label: "prompt editor",
      testId: "editable-prompt",
      element: <textarea data-testid="editable-prompt" />,
    },
    {
      label: "note edit state",
      testId: "editable-note",
      element: <textarea data-testid="editable-note" />,
    },
  ])(
    "keeps native paste in an $label without extracting clipboard candidates",
    ({ element, testId }) => {
      const enqueue = vi.fn();
      const getAsFile = vi.fn(
        () => new File(["image"], "clipboard.png", { type: "image/png" }),
      );
      const input = transfer([
        { kind: "file", type: "image/png", getAsFile },
      ]);
      renderBoundary(runtime(enqueue), element);
      const target = screen.getByTestId(testId);
      const composedPath = vi.fn(() => [
        target,
        screen.getByTestId("canvas-media-ingress-boundary"),
        document,
        window,
      ]);
      const paste = nativePasteEvent(input.dataTransfer, composedPath);

      fireEvent(target, paste);

      expect(composedPath).toHaveBeenCalled();
      expect(paste.defaultPrevented).toBe(false);
      expect(getAsFile).not.toHaveBeenCalled();
      expect(input.getData).not.toHaveBeenCalled();
      expect(enqueue).not.toHaveBeenCalled();
    },
  );

  it("does not inherit an outer contenteditable root through an explicit false region", () => {
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    const getAsFile = vi.fn(
      () => new File(["image"], "clipboard.png", { type: "image/png" }),
    );
    const clipboard = transfer([
      { kind: "file", type: "image/png", getAsFile },
    ]);
    renderBoundary(
      runtime(enqueue),
      <div contentEditable suppressContentEditableWarning>
        <div
          contentEditable={false}
          data-testid="non-editable-region"
          suppressContentEditableWarning
        >
          <span data-testid="non-editable-child">canvas surface</span>
        </div>
      </div>,
    );
    const child = screen.getByTestId("non-editable-child");
    const falseRegion = screen.getByTestId("non-editable-region");
    const outer = falseRegion.parentElement!;
    const composedPath = vi.fn(() => [
      child,
      falseRegion,
      outer,
      screen.getByTestId("canvas-media-ingress-boundary"),
      document,
      window,
    ]);
    const paste = nativePasteEvent(clipboard.dataTransfer, composedPath);

    fireEvent(child, paste);

    expect(paste.defaultPrevented).toBe(true);
    expect(getAsFile).toHaveBeenCalledOnce();
    expect(enqueue).toHaveBeenCalledOnce();
  });

  it.each(["text/plain", "text/uri-list", "text/html"])(
    "leaves a clipboard containing only %s native and never parses or imports it",
    (type) => {
      const enqueue = vi.fn();
      const clipboard = transfer(
        [{ kind: "string", type }],
        [],
        [type],
      );
      const fetchSpy = vi.fn();
      vi.stubGlobal("fetch", fetchSpy);
      renderBoundary(runtime(enqueue));
      const paste = nativePasteEvent(clipboard.dataTransfer);

      fireEvent(screen.getByTestId("canvas-media-ingress-boundary"), paste);

      expect(paste.defaultPrevented).toBe(false);
      expect(clipboard.getData).not.toHaveBeenCalled();
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(enqueue).not.toHaveBeenCalled();
    },
  );

  it("imports only the real image from mixed image and text clipboard representations", () => {
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    const image = new File(["image"], "clipboard.png", {
      type: "image/png",
    });
    const filesOnly = new File(["duplicate"], "duplicate.png", {
      type: "image/png",
    });
    const clipboard = transfer(
      [
        { kind: "string", type: "text/plain" },
        { kind: "string", type: "text/html" },
        { kind: "file", type: "image/png", file: image },
      ],
      [image, filesOnly],
      ["Files", "text/plain", "text/html"],
    );
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderBoundary(runtime(enqueue));
    const paste = nativePasteEvent(clipboard.dataTransfer);

    fireEvent(screen.getByTestId("canvas-media-ingress-boundary"), paste);

    expect(paste.defaultPrevented).toBe(true);
    expect(clipboard.getData).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(enqueue).toHaveBeenCalledOnce();
    const request = enqueue.mock.calls[0]?.[0] as MediaIngressRequest;
    expect(request.source).toBe("paste");
    expect(request.candidates).toEqual([
      { kind: "file", file: image, displayName: "clipboard.png" },
    ]);
  });

  it("leaves unavailable or non-image file-kind clipboard items native and ignores FileList fallback", () => {
    const enqueue = vi.fn();
    const fallback = new File(["fallback"], "fallback.png", {
      type: "image/png",
    });
    const clipboard = transfer(
      [
        { kind: "file", type: "image/png", file: null },
        {
          kind: "file",
          type: "text/plain",
          file: new File(["text"], "notes.txt", { type: "text/plain" }),
        },
      ],
      [fallback],
      ["Files"],
    );
    renderBoundary(runtime(enqueue));
    const paste = nativePasteEvent(clipboard.dataTransfer);

    fireEvent(screen.getByTestId("canvas-media-ingress-boundary"), paste);

    expect(paste.defaultPrevented).toBe(false);
    expect(enqueue).not.toHaveBeenCalled();
  });

  it("takes over a real image paste in conflict without retaining it or creating a batch", () => {
    const enqueue = vi.fn();
    const getAsFile = vi.fn(
      () => new File(["image"], "private.png", { type: "image/png" }),
    );
    const clipboard = transfer([
      { kind: "file", type: "image/png", getAsFile },
    ]);
    renderBoundary(runtime(enqueue));
    act(() => {
      useCanvasStore
        .getState()
        .markConflict(
          useCanvasStore.getState().loadEpoch,
          "project-1",
          "revision-2",
        );
    });
    const paste = nativePasteEvent(clipboard.dataTransfer);

    fireEvent(screen.getByTestId("canvas-media-ingress-boundary"), paste);

    expect(paste.defaultPrevented).toBe(true);
    expect(enqueue).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(
      "请先处理画布版本冲突",
    );
  });

  it("registers one project paste listener and removes that exact listener on unmount", () => {
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");

    const rendered = renderBoundary(runtime());

    const pasteAdds = add.mock.calls.filter(([type]) => type === "paste");
    expect(pasteAdds).toHaveLength(1);
    const listener = pasteAdds[0]?.[1];

    rendered.unmount();

    expect(
      remove.mock.calls.filter(
        ([type, removedListener]) =>
          type === "paste" && removedListener === listener,
      ),
    ).toHaveLength(1);
    add.mockRestore();
    remove.mockRestore();
  });

  it("fills the one freshly selected empty media node with a null binding", () => {
    const emptyMedia = {
      id: "empty-selected",
      type: "media_input",
      position: { x: 100, y: 60 },
      measured: { width: 220, height: 180 },
      data: {
        kind: "media_input",
        artifactRevisionId: null,
        mediaKind: null,
      },
    } as const;
    useCanvasStore.setState({ nodes: [emptyMedia] });
    getNodes.mockReturnValue([]);
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(runtime(enqueue));
    getNodes.mockReturnValue([{ ...emptyMedia, selected: true }]);
    const image = new File(["image"], "fill.png", { type: "image/png" });
    const clipboard = transfer([
      { kind: "file", type: "image/png", file: image },
    ]);

    fireEvent(
      screen.getByTestId("canvas-media-ingress-boundary"),
      nativePasteEvent(clipboard.dataTransfer),
    );

    expect(getNodes).toHaveBeenCalled();
    expect(screenToFlowPosition).not.toHaveBeenCalled();
    expect(enqueue).toHaveBeenCalledOnce();
    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      source: "paste",
      anchor: { x: 360, y: 60 },
      targetMode: "fill_existing",
      targetBinding: {
        targetNodeId: "empty-selected",
        expectedRevisionId: null,
      },
    });
  });

  it("uses the exact revision of the one freshly selected filled media node for a choice", () => {
    const filledMedia = {
      id: "filled-selected",
      type: "media_input",
      position: { x: 20, y: 30 },
      measured: { width: Number.NaN, height: 180 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-current",
        mediaKind: "image",
      },
    } as const;
    useCanvasStore.setState({ nodes: [filledMedia] });
    getNodes.mockReturnValue([{ ...filledMedia, selected: true }]);
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "choice_required" as const,
      choiceId: "choice-1",
    }));
    renderBoundary(runtime(enqueue));
    const clipboard = transfer([
      {
        kind: "file",
        type: "image/png",
        file: new File(["image"], "replace.png", { type: "image/png" }),
      },
    ]);

    fireEvent(
      screen.getByTestId("canvas-media-ingress-boundary"),
      nativePasteEvent(clipboard.dataTransfer),
    );

    expect(enqueue).toHaveBeenCalledOnce();
    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      source: "paste",
      anchor: { x: 372, y: 30 },
      targetMode: "choose_create_or_replace",
      targetBinding: {
        targetNodeId: "filled-selected",
        expectedRevisionId: "revision-current",
      },
    });
  });

  it("creates one image at the converted boundary viewport center when two media nodes are selected", () => {
    const first = {
      id: "first-selected",
      type: "media_input",
      position: { x: 20, y: 30 },
      data: {
        kind: "media_input",
        artifactRevisionId: null,
        mediaKind: null,
      },
    } as const;
    const second = {
      id: "second-selected",
      type: "media_input",
      position: { x: 420, y: 30 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-second",
        mediaKind: "image",
      },
    } as const;
    useCanvasStore.setState({ nodes: [first, second] });
    getNodes.mockReturnValue([
      { ...first, selected: true },
      { ...second, selected: true },
    ]);
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(runtime(enqueue));
    const boundary = screen.getByTestId("canvas-media-ingress-boundary");
    vi.spyOn(boundary, "getBoundingClientRect").mockReturnValue({
      bottom: 600,
      height: 400,
      left: 100,
      right: 700,
      top: 200,
      width: 600,
      x: 100,
      y: 200,
      toJSON: () => undefined,
    });
    const clipboard = transfer([
      {
        kind: "file",
        type: "image/png",
        file: new File(["image"], "new.png", { type: "image/png" }),
      },
    ]);

    fireEvent(boundary, nativePasteEvent(clipboard.dataTransfer));

    expect(screenToFlowPosition).toHaveBeenCalledWith({ x: 400, y: 400 });
    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      anchor: { x: 390, y: 380 },
      targetMode: "create",
      targetBinding: null,
    });
  });

  it("creates every pasted image in clipboard order near one selected media node", () => {
    const selectedMedia = {
      id: "selected-media",
      type: "media_input",
      position: { x: 40, y: 90 },
      measured: { width: 240, height: 180 },
      data: {
        kind: "media_input",
        artifactRevisionId: null,
        mediaKind: null,
      },
    } as const;
    useCanvasStore.setState({ nodes: [selectedMedia] });
    getNodes.mockReturnValue([{ ...selectedMedia, selected: true }]);
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(runtime(enqueue));
    const first = new File(["first"], "first.png", { type: "image/png" });
    const second = new File(["second"], "second.webp", {
      type: "image/webp",
    });
    const clipboard = transfer([
      { kind: "file", type: "image/png", file: first },
      { kind: "file", type: "image/webp", file: second },
    ]);

    fireEvent(
      screen.getByTestId("canvas-media-ingress-boundary"),
      nativePasteEvent(clipboard.dataTransfer),
    );

    const request = enqueue.mock.calls[0]?.[0] as MediaIngressRequest;
    expect(request).toMatchObject({
      source: "paste",
      anchor: { x: 320, y: 90 },
      targetMode: "create",
      targetBinding: null,
    });
    expect(request.candidates.map((value) => value.displayName)).toEqual([
      "first.png",
      "second.webp",
    ]);
  });

  it.each([
    { label: "selected note", noteSelected: true, edgeSelected: false },
    { label: "selected edge", noteSelected: false, edgeSelected: true },
  ])(
    "uses viewport center when a selected media node is accompanied by a $label",
    ({ edgeSelected, noteSelected }) => {
    const selectedMedia = {
      id: "selected-media",
      type: "media_input",
      position: { x: 40, y: 90 },
      data: {
        kind: "media_input",
        artifactRevisionId: null,
        mediaKind: null,
      },
    } as const;
    const selectedNote = {
      id: "selected-note",
      type: "note",
      position: { x: 400, y: 90 },
      data: { kind: "note", text: "note" },
    } as const;
    useCanvasStore.setState({
      nodes: [selectedMedia, selectedNote],
      edges: [
        {
          id: "selected-edge",
          source: "selected-media",
          target: "selected-note",
          selected: edgeSelected,
        },
      ],
    });
    getNodes.mockReturnValue([
      { ...selectedMedia, selected: true },
      { ...selectedNote, selected: noteSelected },
    ]);
    getEdges.mockReturnValue(useCanvasStore.getState().edges);
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(runtime(enqueue));
    const boundary = screen.getByTestId("canvas-media-ingress-boundary");
    vi.spyOn(boundary, "getBoundingClientRect").mockReturnValue({
      bottom: 300,
      height: 200,
      left: 100,
      right: 500,
      top: 100,
      width: 400,
      x: 100,
      y: 100,
      toJSON: () => undefined,
    });
    const clipboard = transfer([
      {
        kind: "file",
        type: "image/png",
        file: new File(["image"], "new.png", { type: "image/png" }),
      },
    ]);

    fireEvent(boundary, nativePasteEvent(clipboard.dataTransfer));

    expect(screenToFlowPosition).toHaveBeenCalledWith({ x: 300, y: 200 });
    expect(enqueue.mock.calls[0]?.[0]).toMatchObject({
      anchor: { x: 290, y: 180 },
      targetMode: "create",
      targetBinding: null,
    });
    },
  );

  it("renders a malicious clipboard filename as inert text in the target choice", () => {
    const filledMedia = {
      id: "filled-media",
      type: "media_input",
      position: { x: 20, y: 30 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-current",
        mediaKind: "image",
      },
    } as const;
    useCanvasStore.setState({ nodes: [filledMedia] });
    getNodes.mockReturnValue([{ ...filledMedia, selected: true }]);
    const choiceRuntime = rejectingChoiceRuntime({});
    renderBoundary(choiceRuntime.value);
    const malicious = `<img src=x onerror=alert("clipboard")>.png`;
    const clipboard = transfer([
      {
        kind: "file",
        type: "image/png",
        file: new File(["image"], malicious, { type: "image/png" }),
      },
    ]);

    fireEvent(
      screen.getByTestId("canvas-media-ingress-boundary"),
      nativePasteEvent(clipboard.dataTransfer),
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain(malicious);
    expect(dialog.querySelector("img")).toBeNull();
  });

  it("keeps a folder and unavailable file as failed candidates without recursion", () => {
    const enqueue = vi.fn((_request: MediaIngressRequest) => ({
      kind: "accepted" as const,
      batchId: "batch-1",
    }));
    renderBoundary(runtime(enqueue));
    const folderRead = vi.fn();
    const folder = {
      isDirectory: true,
      name: "frames",
      createReader: folderRead,
    };
    const input = transfer(
      [
        { kind: "file", type: "", entry: folder, file: null },
        { kind: "file", type: "image/png", getAsFile: () => null },
      ],
      [],
      ["Files"],
    );

    fireEvent(
      screen.getByTestId("canvas-media-ingress-boundary"),
      nativeDragEvent("drop", input.dataTransfer),
    );

    const request = enqueue.mock.calls[0]?.[0] as MediaIngressRequest;
    expect(request.candidates).toHaveLength(2);
    expect(request.candidates[0]).toMatchObject({
      kind: "entry_error",
      code: "folder_not_supported",
      displayName: "frames",
    });
    expect(request.candidates[1]).toMatchObject({
      kind: "entry_error",
      code: "file_unavailable",
    });
    expect(folderRead).not.toHaveBeenCalled();
  });

  it("does not register a drop boundary without a loaded matching controller", () => {
    useCanvasStore.getState().reset();
    renderBoundary(null);
    const boundary = screen.getByTestId("canvas-media-ingress-boundary");
    const input = transfer(
      [{ kind: "file", type: "image/png", file: null }],
      [],
      ["Files"],
    );
    const drop = nativeDragEvent("drop", input.dataTransfer);

    fireEvent(boundary, drop);

    expect(drop.defaultPrevented).toBe(false);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
