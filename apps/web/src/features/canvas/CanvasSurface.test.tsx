import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { NodeChange } from "@xyflow/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCanvasStore } from "./canvasStore";
import type { CanvasNode } from "./canvasTypes";
import { CanvasSurface } from "./CanvasSurface";
import { documentFromText } from "../prompting/promptDocument";
import { MediaIngressContext, type MediaIngressRuntimePort } from "./mediaIngress/mediaIngressContext";
import type { MediaIngressController, MediaIngressSnapshot } from "./mediaIngress/mediaIngressTypes";
import { MediaTargetLocks } from "./mediaIngress/mediaTargetLocks";

const flow = vi.hoisted(() => ({ props: {} as Record<string, unknown> }));

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Background: () => <div data-testid="background" />,
    Controls: () => <div data-testid="controls" />,
    MiniMap: ({ position }: { position?: string }) => <div data-position={position} data-testid="minimap" />,
    Panel: ({ children, position }: { children: React.ReactNode; position: string }) => <div data-position={position} data-testid={`panel-${position}`}>{children}</div>,
    ReactFlow: ({ children, ...props }: { children: React.ReactNode }) => {
      flow.props = props;
      return <div data-testid="react-flow">{children}</div>;
    },
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    ViewportPortal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    useReactFlow: () => ({ screenToFlowPosition: (position: object) => position }),
  };
});

describe("CanvasSurface", () => {
  beforeEach(() => {
    flow.props = {};
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [
        {
          id: "source",
          type: "text_source",
          position: { x: 0, y: 0 },
          data: { kind: "text_source", text: "" },
        },
        {
          id: "prompt",
          type: "prompt",
          position: { x: 100, y: 0 },
          data: {
            kind: "prompt",
            document: documentFromText("", "11111111-1111-4111-8111-111111111111"),
          },
        },
      ],
      edges: [],
      shots: [],
      revisionId: "revision-1",
      viewport: { x: 3, y: 4, zoom: 1.25 },
    });
  });
  afterEach(cleanup);

  it("wires a controlled flow surface, viewport persistence, and exact connection validation", () => {
    const onMoveEnd = vi.fn();
    render(<CanvasSurface associationTarget={null} onAssociateMedia={vi.fn()} onMoveEnd={onMoveEnd} onShotSelectionChange={vi.fn()} />);

    expect(screen.getByTestId("canvas-surface")).toBeTruthy();
    expect(screen.getByTestId("canvas-media-ingress-boundary")).toBeTruthy();
    expect(screen.getByTestId("background")).toBeTruthy();
    expect(screen.getByTestId("minimap")).toBeTruthy();
    expect(screen.getByTestId("minimap").getAttribute("data-position")).toBe("top-right");
    expect(screen.getByTestId("panel-top-left")).toBeTruthy();
    expect(screen.getByTestId("panel-bottom-right")).toBeTruthy();
    expect(screen.getByTestId("controls")).toBeTruthy();
    expect(flow.props.nodes).toBe(useCanvasStore.getState().nodes);
    expect(flow.props.edges).toBe(useCanvasStore.getState().edges);
    expect(flow.props.viewport).toEqual({ x: 3, y: 4, zoom: 1.25 });
    expect(Object.keys(flow.props.nodeTypes as object).sort()).toEqual([
      "media_input",
      "note",
      "prompt",
      "shot",
      "text_source",
    ]);
    expect(flow.props.deleteKeyCode).toBeNull();
    expect(flow.props.elementsSelectable).toBe(true);
    expect(flow.props.nodesDraggable).toBe(true);
    expect(flow.props.onNodesChange).toBeTypeOf("function");
    expect(flow.props.onEdgesChange).toBeTypeOf("function");
    expect(flow.props.onConnect).toBeTypeOf("function");

    const isValidConnection = flow.props.isValidConnection as (connection: object) => boolean;
    expect(
      isValidConnection({
        source: "source",
        sourceHandle: "text",
        target: "prompt",
        targetHandle: "text",
      }),
    ).toBe(true);
    expect(
      isValidConnection({
        source: "prompt",
        sourceHandle: "text",
        target: "source",
        targetHandle: "text",
      }),
    ).toBe(false);

    act(() => {
      (flow.props.onViewportChange as (viewport: object) => void)({ x: 8, y: 9, zoom: 2 });
    });
    expect(useCanvasStore.getState().viewport).toEqual({ x: 8, y: 9, zoom: 2 });
    (flow.props.onMoveEnd as (event: unknown, viewport: object) => void)(null, {
      x: 8,
      y: 9,
      zoom: 2,
    });
    expect(onMoveEnd).toHaveBeenCalledWith({ x: 8, y: 9, zoom: 2 });
  });

  it("locks graph mutations while a revision conflict awaits a choice", () => {
    render(<CanvasSurface associationTarget={null} onAssociateMedia={vi.fn()} onMoveEnd={vi.fn()} onShotSelectionChange={vi.fn()} />);

    act(() => {
      useCanvasStore
        .getState()
        .markConflict(useCanvasStore.getState().loadEpoch, "project-1", "revision-2");
    });

    expect(flow.props.nodesDraggable).toBe(false);
    expect(flow.props.nodesConnectable).toBe(false);
    expect(flow.props.deleteKeyCode).toBeNull();
    expect(flow.props.onNodesChange).toBeUndefined();
    expect(flow.props.onEdgesChange).toBeUndefined();
    expect(flow.props.onConnect).toBeUndefined();
    const isValidConnection = flow.props.isValidConnection as (connection: object) => boolean;
    expect(
      isValidConnection({
        source: "source",
        sourceHandle: "text",
        target: "prompt",
        targetHandle: "text",
      }),
    ).toBe(false);
  });

  it("reports only transient Shot selection and clears it for the pane, ordinary nodes, and multi-selection", () => {
    const shot = {
      id: "shot-node",
      type: "shot",
      position: { x: 200, y: 0 },
      data: { kind: "shot", shotId: "shot-1" },
      deletable: false,
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [...useCanvasStore.getState().nodes, shot],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const onShotSelectionChange = vi.fn();
    const initialEditSequence = useCanvasStore.getState().editSequence;
    const initialNodes = useCanvasStore.getState().nodes;

    render(
      <CanvasSurface
        associationTarget={null}
        onAssociateMedia={vi.fn()}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );

    act(() => {
      (
        flow.props.onSelectionChange as (selection: { nodes: CanvasNode[]; edges: [] }) => void
      )({ nodes: [shot], edges: [] });
    });
    expect(onShotSelectionChange).toHaveBeenLastCalledWith("shot-1");
    expect(useCanvasStore.getState().editSequence).toBe(initialEditSequence);
    expect(useCanvasStore.getState().nodes).toBe(initialNodes);

    act(() => {
      (flow.props.onPaneClick as (event: unknown) => void)(null);
      (
        flow.props.onSelectionChange as (selection: { nodes: CanvasNode[]; edges: [] }) => void
      )({ nodes: [initialNodes[0]!], edges: [] });
      (
        flow.props.onSelectionChange as (selection: { nodes: CanvasNode[]; edges: [] }) => void
      )({ nodes: [shot, initialNodes[0]!], edges: [] });
    });
    expect(onShotSelectionChange).toHaveBeenLastCalledWith(null);
    expect(useCanvasStore.getState().editSequence).toBe(initialEditSequence);
  });

  it("waits for the selection result before reporting a Shot after a Ctrl/Cmd multi-select click", () => {
    const shot = {
      id: "shot-node",
      type: "shot",
      position: { x: 200, y: 0 },
      data: { kind: "shot", shotId: "shot-1" },
      deletable: false,
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [...useCanvasStore.getState().nodes, shot],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const onShotSelectionChange = vi.fn();
    render(
      <CanvasSurface
        associationTarget={null}
        onAssociateMedia={vi.fn()}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );
    const source = useCanvasStore.getState().nodes[0]!;
    const onNodeClick = flow.props.onNodeClick as (event: unknown, node: CanvasNode) => void;
    const onSelectionChange = flow.props.onSelectionChange as (selection: {
      nodes: CanvasNode[];
      edges: [];
    }) => void;

    act(() => {
      onSelectionChange({ nodes: [source], edges: [] });
      onNodeClick(null, shot);
      onSelectionChange({ nodes: [source, shot], edges: [] });
    });
    expect(onShotSelectionChange.mock.calls.flat()).not.toContain("shot-1");

    act(() => {
      onSelectionChange({ nodes: [shot], edges: [] });
    });
    expect(onShotSelectionChange).toHaveBeenLastCalledWith("shot-1");
  });

  it("associates only compatible, attached, unprotected media without creating an edge", () => {
    const shot = {
      id: "shot-node",
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: "shot-1" },
      deletable: false,
    } as CanvasNode;
    const image = {
      id: "image-media",
      type: "media_input",
      position: { x: 160, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "33333333-3333-4333-8333-333333333333",
        mediaKind: "image",
      },
    } as CanvasNode;
    const video = {
      id: "video-media",
      type: "media_input",
      position: { x: 320, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-video",
        mediaKind: "video",
      },
    } as CanvasNode;
    const empty = {
      id: "empty-media",
      type: "media_input",
      position: { x: 480, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [shot, image, video, empty],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const onAssociateMedia = vi.fn();
    const onShotSelectionChange = vi.fn();

    render(
      <CanvasSurface
        associationTarget={{ shotId: "shot-1", slot: "image" }}
        onAssociateMedia={onAssociateMedia}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );

    expect(flow.props.elementsSelectable).toBe(false);
    expect(flow.props.nodesDraggable).toBe(false);
    const renderedNodes = flow.props.nodes as CanvasNode[];
    expect(renderedNodes.find((node) => node.id === "image-media")?.className).toContain(
      "canvas-node--association-candidate",
    );
    expect(renderedNodes.find((node) => node.id === "video-media")?.className).toContain(
      "canvas-node--association-incompatible",
    );
    expect(renderedNodes.find((node) => node.id === "empty-media")?.className).toContain(
      "canvas-node--association-incompatible",
    );

    act(() => {
      const onNodeClick = flow.props.onNodeClick as (event: unknown, node: CanvasNode) => void;
      onNodeClick(null, image);
      onNodeClick(null, video);
      onNodeClick(null, empty);
      onNodeClick(null, shot);
    });

    expect(onAssociateMedia).toHaveBeenCalledTimes(1);
    expect(onAssociateMedia).toHaveBeenCalledWith({
      nodeId: "image-media",
      target: { shotId: "shot-1", slot: "image" },
      revision: {
        revisionId: "33333333-3333-4333-8333-333333333333",
        mediaKind: "image",
      },
      targetProtectedHint: false,
    });
    expect(onShotSelectionChange).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().edges).toEqual([]);
  });

  it("freezes native media selection so a delayed selection result cannot replace the active Shot", () => {
    const shot = {
      id: "shot-node",
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: "shot-1" },
      deletable: false,
    } as CanvasNode;
    const image = {
      id: "image-media",
      type: "media_input",
      position: { x: 160, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "33333333-3333-4333-8333-333333333333",
        mediaKind: "image",
      },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [shot, image],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const onShotSelectionChange = vi.fn((shotId: string | null) =>
      useCanvasStore.getState().selectShot(shotId),
    );
    const onAssociateMedia = vi.fn(() => {
      const current = useCanvasStore.getState();
      current.setShotSlot("shot-1", "image", {
        revisionId: "33333333-3333-4333-8333-333333333333",
        mediaKind: "image",
      });
      current.cancelShotAssociation();
    });
    const view = render(
      <CanvasSurface
        associationTarget={null}
        onAssociateMedia={onAssociateMedia}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );
    act(() => {
      (
        flow.props.onSelectionChange as (selection: {
          nodes: CanvasNode[];
          edges: [];
        }) => void
      )({ nodes: [shot], edges: [] });
      useCanvasStore.getState().beginShotAssociation("shot-1", "image");
    });
    expect(useCanvasStore.getState().selectedShotId).toBe("shot-1");
    onShotSelectionChange.mockClear();
    view.rerender(
      <CanvasSurface
        associationTarget={{ shotId: "shot-1", slot: "image" }}
        onAssociateMedia={onAssociateMedia}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );
    const associationFlow = { ...flow.props };

    act(() => {
      (
        associationFlow.onNodeClick as (
          event: unknown,
          node: CanvasNode,
        ) => void
      )(null, image);
    });
    view.rerender(
      <CanvasSurface
        associationTarget={null}
        onAssociateMedia={onAssociateMedia}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );
    act(() => {
      if (associationFlow.elementsSelectable !== false) {
        (
          flow.props.onSelectionChange as (selection: {
            nodes: CanvasNode[];
            edges: [];
          }) => void
        )({ nodes: [image], edges: [] });
      }
    });

    expect(onAssociateMedia).toHaveBeenCalledOnce();
    expect(useCanvasStore.getState().shots[0]?.imageRevisionId).toBe(
      "33333333-3333-4333-8333-333333333333",
    );
    expect(useCanvasStore.getState().associationTarget).toBeNull();
    expect(useCanvasStore.getState().selectedShotId).toBe("shot-1");
    expect(onShotSelectionChange).not.toHaveBeenCalled();
  });

  it("does not associate a target-locked compatible media node", () => {
    const shot = {
      id: "shot-node",
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: "shot-1" },
      deletable: false,
    } as CanvasNode;
    const protectedImage = {
      id: "protected-image",
      type: "media_input",
      position: { x: 160, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-protected-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    const availableImage = {
      id: "available-image",
      type: "media_input",
      position: { x: 320, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-available-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [shot, protectedImage, availableImage],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const targetLocks = new MediaTargetLocks();
    const lease = targetLocks.acquire("operation-1", "protected-image");
    const runtime: MediaIngressRuntimePort = {
      projectId: "project-1",
      loadEpoch: useCanvasStore.getState().loadEpoch,
      nodeImport: {} as never,
      controller: null,
      targetLocks,
    };
    const onAssociateMedia = vi.fn();

    render(
      <MediaIngressContext.Provider value={runtime}>
        <CanvasSurface
          associationTarget={{ shotId: "shot-1", slot: "image" }}
          onAssociateMedia={onAssociateMedia}
          onMoveEnd={vi.fn()}
          onShotSelectionChange={vi.fn()}
        />
      </MediaIngressContext.Provider>,
    );

    act(() => {
      const onNodeClick = flow.props.onNodeClick as (event: unknown, node: CanvasNode) => void;
      onNodeClick(null, protectedImage);
      onNodeClick(null, availableImage);
    });

    expect(onAssociateMedia).toHaveBeenCalledTimes(1);
    expect(onAssociateMedia).toHaveBeenCalledWith({
      nodeId: "available-image",
      target: { shotId: "shot-1", slot: "image" },
      revision: {
        revisionId: "revision-available-image",
        mediaKind: "image",
      },
      targetProtectedHint: false,
    });
    expect(useCanvasStore.getState().edges).toEqual([]);
    lease?.release();
  });

  it("ignores nested interactive media controls while association mode is active", () => {
    const media = {
      id: "image-media",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [media],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const onAssociateMedia = vi.fn();
    render(
      <CanvasSurface
        associationTarget={{ shotId: "shot-1", slot: "image" }}
        onAssociateMedia={onAssociateMedia}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={vi.fn()}
      />,
    );
    const onNodeClick = flow.props.onNodeClick as (event: unknown, node: CanvasNode) => void;
    render(
      <div
        data-testid="associated-media-body"
        onClick={(event) => onNodeClick(event, media)}
      >
        <button aria-label="复制媒体节点" type="button">复制</button>
        <button aria-label="删除媒体节点" type="button">删除</button>
        <button aria-label="选择本地图片或视频" type="button">选择</button>
        <button aria-label="重试导入" type="button">重试</button>
        <input aria-label="媒体文件" />
        <select aria-label="媒体类型"><option>图片</option></select>
        <textarea aria-label="媒体说明" />
        <a href="#media">媒体链接</a>
        <video aria-label="视频预览" controls />
        <audio aria-label="音频预览" controls />
        <div contentEditable data-testid="editable-media-label" />
      </div>,
    );

    fireEvent.click(screen.getByRole("button", { name: "复制媒体节点" }));
    fireEvent.click(screen.getByRole("button", { name: "删除媒体节点" }));
    fireEvent.click(screen.getByRole("button", { name: "选择本地图片或视频" }));
    fireEvent.click(screen.getByRole("button", { name: "重试导入" }));
    fireEvent.click(screen.getByLabelText("媒体文件"));
    fireEvent.click(screen.getByLabelText("媒体类型"));
    fireEvent.click(screen.getByLabelText("媒体说明"));
    fireEvent.click(screen.getByRole("link", { name: "媒体链接" }));
    fireEvent.click(screen.getByLabelText("视频预览"));
    fireEvent.click(screen.getByLabelText("音频预览"));
    fireEvent.click(screen.getByTestId("editable-media-label"));
    expect(onAssociateMedia).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("associated-media-body"));
    expect(onAssociateMedia).toHaveBeenCalledOnce();
    expect(onAssociateMedia).toHaveBeenCalledWith({
      nodeId: "image-media",
      target: { shotId: "shot-1", slot: "image" },
      revision: { revisionId: "revision-image", mediaKind: "image" },
      targetProtectedHint: false,
    });
  });

  it("keeps the active association Shot selected across pane and cross-Shot interactions", () => {
    const firstShot = {
      id: "first-shot-node",
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: "shot-1" },
      deletable: false,
    } as CanvasNode;
    const otherShot = {
      id: "other-shot-node",
      type: "shot",
      position: { x: 200, y: 0 },
      data: { kind: "shot", shotId: "shot-2" },
      deletable: false,
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [firstShot, otherShot],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
        {
          id: "shot-2",
          order: 1,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const onShotSelectionChange = vi.fn((shotId: string | null) =>
      useCanvasStore.getState().selectShot(shotId),
    );
    const view = render(
      <CanvasSurface
        associationTarget={null}
        onAssociateMedia={vi.fn()}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );
    act(() => {
      (
        flow.props.onSelectionChange as (selection: { nodes: CanvasNode[]; edges: [] }) => void
      )({ nodes: [firstShot], edges: [] });
    });
    onShotSelectionChange.mockClear();
    useCanvasStore.getState().beginShotAssociation("shot-1", "image");
    const associationTarget = useCanvasStore.getState().associationTarget;
    view.rerender(
      <CanvasSurface
        associationTarget={associationTarget}
        onAssociateMedia={vi.fn()}
        onMoveEnd={vi.fn()}
        onShotSelectionChange={onShotSelectionChange}
      />,
    );

    act(() => {
      (flow.props.onPaneClick as (event: unknown) => void)(null);
      (
        flow.props.onSelectionChange as (selection: { nodes: CanvasNode[]; edges: [] }) => void
      )({ nodes: [otherShot], edges: [] });
      (flow.props.onNodeClick as (event: unknown, node: CanvasNode) => void)(null, otherShot);
    });

    expect(onShotSelectionChange).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().selectedShotId).toBe("shot-1");
    expect(useCanvasStore.getState().associationTarget).toEqual(associationTarget);
  });

  it("protects only target-locked nodes while unrelated graph edits continue", () => {
    const targetLocks = new MediaTargetLocks();
    const lease = targetLocks.acquire("operation-1", "source");
    const empty: MediaIngressSnapshot = {
      batches: [], pendingTargetChoices: [], protectedTargetNodeIds: [],
      guardState: { pendingOperationCount: 0, hasSavedUnplaced: false },
    };
    const runtime: MediaIngressRuntimePort = {
      projectId: "project-1",
      loadEpoch: useCanvasStore.getState().loadEpoch,
      nodeImport: {} as never,
      controller: { getSnapshot: () => empty, subscribe: () => () => undefined } as unknown as MediaIngressController,
      targetLocks,
    };
    render(
      <MediaIngressContext.Provider value={runtime}>
        <CanvasSurface associationTarget={null} onAssociateMedia={vi.fn()} onMoveEnd={vi.fn()} onShotSelectionChange={vi.fn()} />
      </MediaIngressContext.Provider>,
    );
    const source = useCanvasStore.getState().nodes[0];
    const prompt = useCanvasStore.getState().nodes[1];
    expect(source).toBeDefined();
    expect(prompt).toBeDefined();
    expect(flow.props.deleteKeyCode).toBeNull();
    expect(flow.props.nodesDraggable).toBe(true);
    expect((flow.props.nodes as CanvasNode[]).find((node) => node.id === "source")?.deletable).toBe(false);
    expect((flow.props.nodes as CanvasNode[]).find((node) => node.id === "prompt")?.deletable).not.toBe(false);

    act(() => {
      (
        flow.props.onNodesChange as (
          changes: NodeChange<CanvasNode>[],
        ) => void
      )([
        { id: "source", type: "remove" },
        {
          id: "source",
          item: {
            ...source!,
            data: { kind: "text_source", text: "不应替换" },
          },
          type: "replace",
        },
        {
          id: "prompt",
          type: "position",
          position: { x: 180, y: 25 },
          dragging: false,
        },
      ]);
    });

    expect(useCanvasStore.getState().nodes).toHaveLength(2);
    expect(useCanvasStore.getState().nodes.some((node) => node.id === "source")).toBe(true);
    expect(useCanvasStore.getState().nodes.find((node) => node.id === "prompt")).toMatchObject({
      position: { x: 180, y: 25 },
      data: {
        kind: "prompt",
        document: { rendered_text: "" },
      },
    });

    act(() => {
      (
        flow.props.onNodesChange as (
          changes: NodeChange<CanvasNode>[],
        ) => void
      )([{ id: "prompt", type: "remove" }]);
    });
    expect(useCanvasStore.getState().nodes.some((node) => node.id === "source")).toBe(true);
    expect(useCanvasStore.getState().nodes.some((node) => node.id === "prompt")).toBe(false);
    lease?.release();
  });

  it("ignores target locks from a stale project load epoch", () => {
    const targetLocks = new MediaTargetLocks();
    const lease = targetLocks.acquire("stale-operation", "source");
    const empty: MediaIngressSnapshot = {
      batches: [],
      pendingTargetChoices: [],
      protectedTargetNodeIds: ["source"],
      guardState: { pendingOperationCount: 1, hasSavedUnplaced: false },
    };
    const runtime: MediaIngressRuntimePort = {
      projectId: "project-1",
      loadEpoch: useCanvasStore.getState().loadEpoch - 1,
      nodeImport: {} as never,
      controller: {
        getSnapshot: () => empty,
        subscribe: () => () => undefined,
      } as unknown as MediaIngressController,
      targetLocks,
    };
    render(
      <MediaIngressContext.Provider value={runtime}>
        <CanvasSurface associationTarget={null} onAssociateMedia={vi.fn()} onMoveEnd={vi.fn()} onShotSelectionChange={vi.fn()} />
      </MediaIngressContext.Provider>,
    );

    expect(
      (flow.props.nodes as CanvasNode[]).find(
        (node) => node.id === "source",
      )?.deletable,
    ).not.toBe(false);
    act(() => {
      (
        flow.props.onNodesChange as (
          changes: NodeChange<CanvasNode>[],
        ) => void
      )([{ id: "source", type: "remove" }]);
    });
    expect(
      useCanvasStore.getState().nodes.some((node) => node.id === "source"),
    ).toBe(false);
    lease?.release();
  });

  it("preserves a protected node and its edge during mixed deletion while removing unrelated selected structure", () => {
    const protectedMedia = {
      id: "A",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    const connected = {
      id: "B",
      type: "prompt",
      position: { x: 100, y: 0 },
      data: {
        kind: "prompt",
        document: documentFromText("", "33333333-3333-4333-8333-333333333333"),
      },
    } as CanvasNode;
    const unrelatedSelected = {
      id: "C",
      type: "note",
      position: { x: 0, y: 100 },
      data: { kind: "note", text: "" },
    } as CanvasNode;
    const unrelatedConnected = {
      id: "D",
      type: "text_source",
      position: { x: 100, y: 100 },
      data: { kind: "text_source", text: "" },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      revisionId: "revision-1",
      nodes: [
        protectedMedia,
        connected,
        unrelatedSelected,
        unrelatedConnected,
      ],
      edges: [
        { id: "A-B", source: "A", target: "B" },
        { id: "C-D", source: "C", target: "D" },
      ],
      shots: [],
    });
    const targetLocks = new MediaTargetLocks();
    const lease = targetLocks.acquire("operation-1", "A");
    const empty: MediaIngressSnapshot = {
      batches: [],
      pendingTargetChoices: [],
      protectedTargetNodeIds: [],
      guardState: { pendingOperationCount: 0, hasSavedUnplaced: false },
    };
    const runtime: MediaIngressRuntimePort = {
      projectId: "project-1",
      loadEpoch: useCanvasStore.getState().loadEpoch,
      nodeImport: {} as never,
      controller: {
        getSnapshot: () => empty,
        subscribe: () => () => undefined,
      } as unknown as MediaIngressController,
      targetLocks,
    };
    render(
      <MediaIngressContext.Provider value={runtime}>
        <CanvasSurface associationTarget={null} onAssociateMedia={vi.fn()} onMoveEnd={vi.fn()} onShotSelectionChange={vi.fn()} />
      </MediaIngressContext.Provider>,
    );

    act(() => {
      (
        flow.props.onNodesChange as (
          changes: NodeChange<CanvasNode>[],
        ) => void
      )([
        { id: "A", type: "remove" },
        { id: "C", type: "remove" },
      ]);
    });

    expect(useCanvasStore.getState().nodes.map((node) => node.id)).toEqual([
      "A",
      "B",
      "D",
    ]);
    expect(useCanvasStore.getState().edges.map((edge) => edge.id)).toEqual([
      "A-B",
    ]);

    act(() => {
      (
        flow.props.onNodesChange as (
          changes: NodeChange<CanvasNode>[],
        ) => void
      )([{ id: "B", type: "remove" }]);
    });
    expect(useCanvasStore.getState().nodes.map((node) => node.id)).toEqual([
      "A",
      "D",
    ]);
    expect(useCanvasStore.getState().edges).toEqual([]);

    act(() => {
      useCanvasStore.getState().onNodesChange(
        [
          { id: "A", type: "remove" },
          {
            id: "A",
            type: "replace",
            item: {
              ...protectedMedia,
              data: {
                kind: "media_input",
                artifactRevisionId: "should-not-replace",
                mediaKind: "image",
              },
            },
          },
        ],
        new Set(["A"]),
      );
    });
    expect(useCanvasStore.getState().nodes.find((node) => node.id === "A")).toEqual(
      protectedMedia,
    );
    lease?.release();
  });
});
