/// <reference types="node" />

import { act, cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useRef, useState, type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ArtifactRevision } from "../../../api/client";
import { useCanvasStore } from "../canvasStore";
import { documentFromText } from "../../prompting/promptDocument";
import {
  MediaIngressContext,
  type MediaIngressRuntimePort,
} from "../mediaIngress/mediaIngressContext";
import { MediaTargetLocks } from "../mediaIngress/mediaTargetLocks";
import {
  ArtifactHistoryDrawer as ArtifactHistoryDrawerPanel,
  type BoundShotHistoryTarget,
  type ShotHistoryTarget,
} from "./ArtifactHistoryDrawer";
import {
  defaultPanelRect,
  panelStorageKey,
  SHOT_TRACK_COLLAPSED_HEIGHT,
} from "./floatingPanelLayout";
import { ShotDeleteDialog } from "./ShotDeleteDialog";
import { ShotInspector as ShotInspectorPanel } from "./ShotInspector";
import { ShotTrack as ShotTrackPanel } from "./ShotTrack";
import { useFloatingPanel } from "./useFloatingPanel";

const shotWorkspaceStyles = readFileSync(
  resolve(process.cwd(), "src/app/styles.css"),
  "utf8",
);
const floatingPanelStylesPath = resolve(
  process.cwd(),
  "src/features/canvas/components/floatingPanels.css",
);
const floatingPanelStyles = existsSync(floatingPanelStylesPath)
  ? readFileSync(floatingPanelStylesPath, "utf8")
  : "";
const shotVisualIdentityStylesPath = resolve(
  process.cwd(),
  "src/features/canvas/components/shotVisualIdentity.css",
);
const shotVisualIdentityStyles = existsSync(shotVisualIdentityStylesPath)
  ? readFileSync(shotVisualIdentityStylesPath, "utf8")
  : "";
const shotAssociationHistoryStylesPath = resolve(
  process.cwd(),
  "src/features/canvas/components/shotAssociationHistory.css",
);
const shotAssociationHistoryStyles = existsSync(shotAssociationHistoryStylesPath)
  ? readFileSync(shotAssociationHistoryStylesPath, "utf8")
  : "";
const canvasPageSource = readFileSync(
  resolve(process.cwd(), "src/features/canvas/CanvasPage.tsx"),
  "utf8",
);

const ids = {
  shotOne: "11111111-1111-4111-8111-111111111111",
  shotTwo: "22222222-2222-4222-8222-222222222222",
  imageOne: "33333333-3333-4333-8333-333333333333",
  imageTwo: "44444444-4444-4444-8444-444444444444",
  videoOne: "55555555-5555-4555-8555-555555555555",
  videoTwo: "88888888-8888-4888-8888-888888888888",
  mediaNode: "99999999-9999-4999-8999-999999999999",
  secondMediaNode: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  artifact: "66666666-6666-4666-8666-666666666666",
  otherArtifact: "77777777-7777-4777-8777-777777777777",
};

const api = vi.hoisted(() => ({
  listArtifactRevisions: vi.fn(),
  listShotSlotAssociations: vi.fn(),
}));
const preview = vi.hoisted(() => ({ useArtifactPreview: vi.fn() }));

vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  ...api,
}));
vi.mock("../useArtifactPreview", () => ({ useArtifactPreview: preview.useArtifactPreview }));

function revision(
  revisionId: string,
  overrides: Partial<ArtifactRevision> = {},
): ArtifactRevision {
  return {
    revision_id: revisionId,
    artifact_id: ids.artifact,
    sequence: 1,
    media_kind: "image",
    media_type: "image/png",
    byte_size: 12,
    display_filename: "safe-storyboard.png",
    metadata: { kind: "image", width: 1280, height: 720 },
    source_kind: "import",
    source_project_id: "88888888-8888-4888-8888-888888888888",
    origin_project_id: "88888888-8888-4888-8888-888888888888",
    created_at: "2026-07-28T08:00:00Z",
    ...overrides,
  };
}

function readyPreview(revisionId: string | null) {
  const item = revisionId === ids.videoOne
    ? revision(revisionId, {
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "safe-motion.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 4,
          frames_per_second: 24,
        },
      })
    : revision(revisionId ?? ids.imageOne);
  return {
    phase: "ready" as const,
    revision: item,
    objectUrl: `blob:${item.revision_id}`,
    errorMessage: null,
    retryable: false,
    retry: vi.fn(),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, reject, resolve };
}

function boundHistoryTarget(
  slot: ShotHistoryTarget["slot"],
  revisionId: string,
  shotId = ids.shotOne,
): BoundShotHistoryTarget {
  return {
    shotId,
    slot,
    revisionId,
    anchorRevisionId: "revision-1",
  };
}

function associationEvent(
  graphRevisionId: string,
  graphSequence: number,
  eventOrdinal: number,
  action: "associated" | "cleared" | "removed",
  artifactRevision: ArtifactRevision | null,
  referenceIndex: number | null = null,
) {
  return {
    graph_revision_id: graphRevisionId,
    graph_revision_sequence: graphSequence,
    event_ordinal: eventOrdinal,
    occurred_at: `2026-07-30T${String(graphSequence).padStart(2, "0")}:00:00Z`,
    action,
    reference_index: referenceIndex,
    artifact_revision: artifactRevision,
  };
}

function associationPage(
  slot: ShotHistoryTarget["slot"],
  items: ReturnType<typeof associationEvent>[] = [],
  nextCursor: string | null = null,
) {
  return {
    project_id: "project-1",
    shot_id: ids.shotOne,
    slot,
    anchor_graph_revision_id: "revision-1",
    anchor_graph_sequence: 20,
    items,
    next_cursor: nextCursor,
  };
}

function loadCanvas() {
  useCanvasStore.getState().load({
    projectId: "project-1",
    revisionId: "revision-1",
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [
      {
        id: "shot-node-1",
        type: "shot",
        position: { x: 100, y: 200 },
        data: { kind: "shot", shotId: ids.shotOne },
      },
      {
        id: "shot-node-2",
        type: "shot",
        position: { x: 500, y: 200 },
        data: { kind: "shot", shotId: ids.shotTwo },
      },
      {
        id: ids.mediaNode,
        type: "media_input",
        position: { x: 0, y: 0 },
        data: {
          kind: "media_input",
          artifactRevisionId: ids.imageOne,
          mediaKind: "image",
        },
      },
      {
        id: ids.secondMediaNode,
        type: "media_input",
        position: { x: 360, y: 0 },
        data: {
          kind: "media_input",
          artifactRevisionId: ids.imageTwo,
          mediaKind: "image",
        },
      },
    ],
    edges: [],
    shots: [
      {
        id: ids.shotOne,
        order: 0,
        imageRevisionId: null,
        videoRevisionId: ids.videoOne,
        referenceRevisionIds: [],
      },
      {
        id: ids.shotTwo,
        order: 1,
        imageRevisionId: null,
        videoRevisionId: null,
        referenceRevisionIds: [],
      },
    ],
  });
  useCanvasStore.getState().selectShot(ids.shotOne);
}

function mediaTransfer(
  revisionId: string,
  mediaKind: "image" | "video",
  nodeId = ids.mediaNode,
) {
  return {
    getData: (type: string) =>
      type === "application/x-ai-canvas-media-revision"
        ? JSON.stringify({ revisionId, mediaKind, nodeId })
        : "",
  };
}

function inspectorIngress(targetLocks = new MediaTargetLocks()): MediaIngressRuntimePort {
  const current = useCanvasStore.getState();
  if (!current.projectId) throw new Error("Inspector test requires a loaded project");
  return {
    projectId: current.projectId,
    loadEpoch: current.loadEpoch,
    controller: null,
    targetLocks,
    nodeImport: {
      projectId: current.projectId,
      getSnapshot: () => ({
        phase: "idle",
        active: false,
        errorMessage: null,
        recoveryKind: null,
      }),
      subscribe: () => () => undefined,
      selectFile: vi.fn(),
      retry: vi.fn(),
      requestReload: vi.fn(),
    },
  };
}

function ShotInspector({
  projectId,
  ...props
}: Omit<ComponentProps<typeof ShotInspectorPanel>, "floatingPanel"> & {
  projectId?: string | null;
}) {
  const storeProjectId = useCanvasStore((state) => state.projectId);
  const floatingPanel = useFloatingPanel({
    panelId: "shot-inspector",
    projectId: projectId ?? storeProjectId,
  });
  return <ShotInspectorPanel {...props} floatingPanel={floatingPanel} />;
}

function ShotTrack({
  projectId,
  ...props
}: Omit<ComponentProps<typeof ShotTrackPanel>, "floatingPanel"> & {
  projectId?: string | null;
}) {
  const storeProjectId = useCanvasStore((state) => state.projectId);
  const floatingPanel = useFloatingPanel({
    panelId: "shot-track",
    projectId: projectId ?? storeProjectId,
  });
  return <ShotTrackPanel {...props} floatingPanel={floatingPanel} />;
}

function ArtifactHistoryDrawer(
  props: Omit<ComponentProps<typeof ArtifactHistoryDrawerPanel>, "floatingPanel">,
) {
  const projectId = useCanvasStore((state) => state.projectId);
  const floatingPanel = useFloatingPanel({
    panelId: "shot-inspector",
    projectId,
  });
  return (
    <ArtifactHistoryDrawerPanel
      {...props}
      floatingPanel={floatingPanel}
    />
  );
}

function renderInspector(targetLocks = new MediaTargetLocks()) {
  render(
    <MediaIngressContext.Provider value={inspectorIngress(targetLocks)}>
      <ShotInspector selectedShotId={ids.shotOne} onOpenHistory={vi.fn()} />
    </MediaIngressContext.Provider>,
  );
  return targetLocks;
}

function mockWorkspaceBounds(initialWidth = 1200, initialHeight = 900) {
  let width = initialWidth;
  let height = initialHeight;
  const original = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function () {
    if (!this.classList.contains("canvas-workspace")) return original.call(this);
    return {
      bottom: height,
      height,
      left: 0,
      right: width,
      top: 0,
      width,
      x: 0,
      y: 0,
      toJSON: () => undefined,
    };
  });
  return {
    resize(nextWidth: number, nextHeight: number) {
      width = nextWidth;
      height = nextHeight;
    },
  };
}

function installResizeObserver() {
  let callback: ResizeObserverCallback | null = null;
  const observe = vi.fn();
  const disconnect = vi.fn();
  class TestResizeObserver {
    constructor(nextCallback: ResizeObserverCallback) {
      callback = nextCallback;
    }
    observe = observe;
    disconnect = disconnect;
    unobserve = vi.fn();
  }
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  return {
    disconnect,
    observe,
    notify() {
      if (callback === null) throw new Error("ResizeObserver was not created");
      callback([], {} as ResizeObserver);
    },
  };
}

function installAlwaysVisibleIntersectionObserver() {
  class AlwaysVisibleIntersectionObserver {
    private readonly callback: IntersectionObserverCallback;

    constructor(callback: IntersectionObserverCallback) {
      this.callback = callback;
    }

    observe(element: Element) {
      const rect = element.getBoundingClientRect();
      queueMicrotask(() => {
        this.callback(
          [
            {
              boundingClientRect: rect,
              intersectionRatio: 1,
              intersectionRect: rect,
              isIntersecting: true,
              rootBounds: null,
              target: element,
              time: 0,
            },
          ],
          this as unknown as IntersectionObserver,
        );
      });
    }

    disconnect() {}
    takeRecords() {
      return [];
    }
    unobserve() {}
  }

  vi.stubGlobal("IntersectionObserver", AlwaysVisibleIntersectionObserver);
}

function renderFloatingInspector(
  selectedShotId: string | null = ids.shotOne,
  generation = "epoch-1",
) {
  return render(
    <div className="canvas-workspace" data-testid="floating-workspace">
      <ShotInspector
        key={generation}
        onOpenHistory={vi.fn()}
        projectId="project-1"
        selectedShotId={selectedShotId}
      />
    </div>,
  );
}

function renderFloatingTrack() {
  return render(
    <div className="canvas-workspace" data-testid="floating-workspace">
      <ShotTrack
        onRequestDelete={vi.fn()}
        projectId="project-1"
        selectedShotId={ids.shotOne}
      />
    </div>,
  );
}

function panelDragRegion(panel: HTMLElement): HTMLElement {
  const region = panel.querySelector<HTMLElement>("[data-floating-panel-drag-region]");
  if (region === null) throw new Error("Floating panel title drag region is unavailable");
  return region;
}

function panelResizeEdge(
  panel: HTMLElement,
  edge: string,
): HTMLElement {
  const handle = panel.querySelector<HTMLElement>(
    `[data-floating-panel-resize-edge="${edge}"]`,
  );
  if (handle === null) throw new Error(`Floating panel ${edge} resize edge is unavailable`);
  return handle;
}

function commitPanelMove(
  panel: HTMLElement,
  pointerId: number,
  start: { x: number; y: number },
  end: { x: number; y: number },
) {
  const region = panelDragRegion(panel);
  fireEvent.pointerDown(region, { clientX: start.x, clientY: start.y, pointerId });
  fireEvent.pointerMove(region, { clientX: end.x, clientY: end.y, pointerId });
  fireEvent.pointerUp(region, { clientX: end.x, clientY: end.y, pointerId });
}

function expandFloatingTrack(
  track: HTMLElement,
  pointerId: number,
  height = 132,
) {
  const collapsedHeight = Number.parseFloat(track.style.height);
  const delta = collapsedHeight - height;
  const top = panelResizeEdge(track, "top");
  fireEvent.pointerDown(top, { clientX: 100, clientY: 100, pointerId });
  fireEvent.pointerMove(top, { clientX: 100, clientY: 100 + delta, pointerId });
  fireEvent.pointerUp(top, { clientX: 100, clientY: 100 + delta, pointerId });
  expect(track.dataset.trackState).toBe("expanded");
}

describe("Shot workspace interactions", () => {
  beforeEach(() => {
    cleanup();
    localStorage.clear();
    useCanvasStore.getState().reset();
    api.listArtifactRevisions.mockReset();
    api.listShotSlotAssociations.mockReset();
    api.listShotSlotAssociations.mockImplementation(
      async (
        projectId: string,
        shotId: string,
        slot: ShotHistoryTarget["slot"],
        anchorRevisionId: string,
      ) => ({
        project_id: projectId,
        shot_id: shotId,
        slot,
        anchor_graph_revision_id: anchorRevisionId,
        anchor_graph_sequence: 1,
        items: [],
        next_cursor: null,
      }),
    );
    preview.useArtifactPreview.mockImplementation(readyPreview);
    loadCanvas();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps only reset as an explicit layout button on a selected Inspector", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();

    const inspector = screen.getByTestId("shot-inspector");
    expect(panelDragRegion(inspector)).toBeTruthy();
    expect(panelResizeEdge(inspector, "left")).toBeTruthy();
    expect(panelResizeEdge(inspector, "bottom")).toBeTruthy();
    expect(panelResizeEdge(inspector, "bottom-left")).toBeTruthy();
    expect(inspector.querySelector(".floating-panel__move-handle")).toBeNull();
    expect(inspector.querySelector(".floating-panel__resize-handle")).toBeNull();
    expect(screen.getByRole("button", { name: "重置分镜素材检查器布局" })).toBeTruthy();
  });

  it("uses title regions and dock-aware edges instead of Inspector and Track layout buttons", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    render(
      <>
        <ShotInspector selectedShotId={ids.shotOne} onOpenHistory={vi.fn()} />
        <ShotTrack onRequestDelete={vi.fn()} selectedShotId={ids.shotOne} />
      </>,
    );

    const inspector = screen.getByTestId("shot-inspector");
    const track = screen.getByTestId("shot-track");
    expect(inspector.querySelector("[data-floating-panel-drag-region]")).toBeTruthy();
    expect(track.querySelector("[data-floating-panel-drag-region]")).toBeNull();
    expect(panelDragRegion(inspector).getAttribute("tabindex")).toBeNull();
    expect(inspector.querySelector('[data-floating-panel-resize-edge="left"]')).toBeTruthy();
    expect(inspector.querySelector('[data-floating-panel-resize-edge="bottom"]')).toBeTruthy();
    expect(inspector.querySelector('[data-floating-panel-resize-edge="bottom-left"]')).toBeTruthy();
    expect(inspector.querySelector('[data-floating-panel-resize-edge="top"]')).toBeNull();
    expect(inspector.querySelector('[data-floating-panel-resize-edge="top-left"]')).toBeNull();
    expect(track.querySelector('[data-floating-panel-resize-edge="top"]')).toBeTruthy();
    expect(track.querySelector('[data-floating-panel-resize-edge="left"]')).toBeNull();
    expect(track.querySelector('[data-floating-panel-resize-edge="right"]')).toBeNull();
    expect(inspector.querySelector(".floating-panel__move-handle")).toBeNull();
    expect(track.querySelector(".floating-panel__resize-handle")).toBeNull();
  });

  it("fully collapses the desktop Shot Track to a bottom-edge resize bar", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    expect(track.dataset.trackState).toBe("collapsed");
    expect(track.style.top).toBe("888px");
    expect(track.style.height).toBe("12px");
    expect(within(track).queryByRole("heading", { name: "分镜轨道" })).toBeNull();
    expect(within(track).queryByRole("button", { name: "新建分镜" })).toBeNull();
    expect(
      within(track).queryByRole("button", { name: "重置分镜轨道布局" }),
    ).toBeNull();
    expect(panelResizeEdge(track, "top")).toBeTruthy();
  });

  it("exposes an accessible button that expands the fully collapsed Track", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    fireEvent.click(
      within(track).getByRole("button", { name: "展开分镜轨道" }),
      { detail: 1 },
    );

    expect(track.dataset.trackState).toBe("expanded");
    expect(Number.parseFloat(track.style.height)).toBeGreaterThan(
      SHOT_TRACK_COLLAPSED_HEIGHT,
    );
    expect(within(track).getByRole("heading", { name: "分镜轨道" })).toBeTruthy();
    expect(within(track).getByRole("button", { name: "新建分镜" })).toBeTruthy();
    expect(
      within(track).getByRole("button", { name: "重置分镜轨道布局" }),
    ).toBeTruthy();
  });

  it("migrates a saved legacy 60px Track drawer to the bottom edge", () => {
    const workspace = mockWorkspaceBounds();
    const observer = installResizeObserver();
    localStorage.setItem(
      panelStorageKey("project-1", "shot-track"),
      JSON.stringify({
        version: 1,
        rect: { x: 16, y: 824, width: 760, height: 60 },
      }),
    );

    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    expect(track.dataset.trackState).toBe("collapsed");
    expect(track.style.top).toBe("888px");
    expect(track.style.height).toBe(`${SHOT_TRACK_COLLAPSED_HEIGHT}px`);
    expect(within(track).queryByRole("heading", { name: "分镜轨道" })).toBeNull();

    workspace.resize(720, 900);
    act(() => observer.notify());
    expect(track.dataset.trackState).toBe("expanded");
    expect(track.style.top).toBe("16px");
    expect(track.style.height).toBe("128px");

    workspace.resize(1200, 900);
    act(() => observer.notify());
    expect(track.dataset.trackState).toBe("collapsed");
    expect(track.style.top).toBe("888px");
    expect(track.style.height).toBe(`${SHOT_TRACK_COLLAPSED_HEIGHT}px`);
  });

  it("restores a saved 12px desktop collapse to the 128px mobile stack", () => {
    mockWorkspaceBounds(720, 900);
    installResizeObserver();
    localStorage.setItem(
      panelStorageKey("project-1", "shot-track"),
      JSON.stringify({
        version: 1,
        rect: { x: 16, y: 888, width: 544, height: 12 },
      }),
    );

    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    const mobileDefault = defaultPanelRect("shot-track", {
      width: 720,
      height: 900,
      viewportHeight: window.innerHeight,
    });
    expect(track.dataset.trackState).toBe("expanded");
    expect(track.style.left).toBe(`${mobileDefault.x}px`);
    expect(track.style.top).toBe(`${mobileDefault.y}px`);
    expect(track.style.width).toBe(`${mobileDefault.width}px`);
    expect(track.style.height).toBe(`${mobileDefault.height}px`);
    expect(within(track).getByRole("heading", { name: "分镜轨道" })).toBeTruthy();
  });

  it("returns a Pointer-collapsed desktop Track to the safe mobile stack", () => {
    const workspace = mockWorkspaceBounds();
    const observer = installResizeObserver();
    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    expandFloatingTrack(track, 214);
    const top = panelResizeEdge(track, "top");
    fireEvent.pointerDown(top, { clientX: 100, clientY: 100, pointerId: 215 });
    fireEvent.pointerMove(top, { clientX: 100, clientY: 220, pointerId: 215 });
    fireEvent.pointerUp(top, { clientX: 100, clientY: 220, pointerId: 215 });
    expect(track.dataset.trackState).toBe("collapsed");
    expect(track.style.top).toBe("888px");

    workspace.resize(720, 900);
    act(() => observer.notify());

    const mobileDefault = defaultPanelRect("shot-track", {
      width: 720,
      height: 900,
      viewportHeight: window.innerHeight,
    });
    expect(track.dataset.trackState).toBe("expanded");
    expect(track.style.left).toBe(`${mobileDefault.x}px`);
    expect(track.style.top).toBe(`${mobileDefault.y}px`);
    expect(track.style.width).toBe(`${mobileDefault.width}px`);
    expect(track.style.height).toBe(`${mobileDefault.height}px`);
  });

  it("does not reopen after the synthetic Pointer click that follows collapsing", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    expandFloatingTrack(track, 216);
    const top = panelResizeEdge(track, "top");
    fireEvent.pointerDown(top, { clientX: 100, clientY: 100, pointerId: 217 });
    fireEvent.pointerMove(top, { clientX: 100, clientY: 220, pointerId: 217 });
    fireEvent.pointerUp(top, { clientX: 100, clientY: 220, pointerId: 217 });
    expect(track.dataset.trackState).toBe("collapsed");

    fireEvent.click(top, { detail: 1 });

    expect(track.dataset.trackState).toBe("collapsed");
    expect(track.style.height).toBe(`${SHOT_TRACK_COLLAPSED_HEIGHT}px`);
  });

  it("still expands by keyboard after a Pointer expansion and reset", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    expandFloatingTrack(track, 218);
    fireEvent.click(
      within(track).getByRole("button", { name: "重置分镜轨道布局" }),
    );
    expect(track.dataset.trackState).toBe("collapsed");

    fireEvent.click(
      within(track).getByRole("button", { name: "展开分镜轨道" }),
      { detail: 0 },
    );

    expect(track.dataset.trackState).toBe("expanded");
    expect(within(track).getByRole("heading", { name: "分镜轨道" })).toBeTruthy();
  });

  it("pulls the fully collapsed Track upward and restores its header controls", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    expect(track.dataset.trackState).toBe("collapsed");
    expect(track.style.top).toBe("888px");
    expect(track.style.height).toBe(`${SHOT_TRACK_COLLAPSED_HEIGHT}px`);
    expect(track.querySelector("[data-floating-panel-drag-region]")).toBeNull();

    const top = panelResizeEdge(track, "top");
    fireEvent.pointerDown(top, { clientX: 100, clientY: 100, pointerId: 71 });
    fireEvent.pointerMove(top, { clientX: 100, clientY: -20, pointerId: 71 });
    fireEvent.pointerUp(top, { clientX: 100, clientY: -20, pointerId: 71 });
    expect(track.style.top).toBe("768px");
    expect(track.style.height).toBe("132px");
    expect(track.dataset.trackState).toBe("expanded");
    expect(within(track).getByRole("heading", { name: "分镜轨道" })).toBeTruthy();
    expect(within(track).getByRole("button", { name: "新建分镜" })).toBeTruthy();
    expect(
      within(track).getByRole("button", { name: "重置分镜轨道布局" }),
    ).toBeTruthy();

    const left = panelResizeEdge(track, "left");
    fireEvent.pointerDown(left, { clientX: 100, clientY: 100, pointerId: 72 });
    fireEvent.pointerMove(left, { clientX: 110, clientY: 100, pointerId: 72 });
    fireEvent.pointerUp(left, { clientX: 110, clientY: 100, pointerId: 72 });
    expect(track.style.left).toBe("26px");
    expect(track.style.width).toBe("750px");

    const right = panelResizeEdge(track, "right");
    fireEvent.pointerDown(right, { clientX: 100, clientY: 100, pointerId: 73 });
    fireEvent.pointerMove(right, { clientX: 120, clientY: 100, pointerId: 73 });
    fireEvent.pointerUp(right, { clientX: 120, clientY: 100, pointerId: 73 });
    expect(track.style.width).toBe("770px");
  });

  it("reveals the Inspector bottom-right grip only after a real title move", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();

    const inspector = screen.getByTestId("shot-inspector");
    expect(
      inspector.querySelector('[data-floating-panel-resize-edge="bottom-right"]'),
    ).toBeNull();
    commitPanelMove(inspector, 74, { x: 100, y: 100 }, { x: 90, y: 100 });
    expect(inspector.style.left).toBe("842px");

    const bottomRight = panelResizeEdge(inspector, "bottom-right");
    fireEvent.pointerDown(bottomRight, { clientX: 100, clientY: 100, pointerId: 75 });
    fireEvent.pointerMove(bottomRight, { clientX: 110, clientY: 110, pointerId: 75 });
    fireEvent.pointerUp(bottomRight, { clientX: 110, clientY: 110, pointerId: 75 });
    expect(inspector.style.width).toBe("358px");
    expect(inspector.style.height).toBe("302px");
  });

  it("keeps an expanded Track layout across remount", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    const view = renderFloatingTrack();
    const track = screen.getByTestId("shot-track");

    const top = panelResizeEdge(track, "top");
    fireEvent.pointerDown(top, { clientX: 100, clientY: 100, pointerId: 76 });
    fireEvent.pointerMove(top, { clientX: 100, clientY: -20, pointerId: 76 });
    fireEvent.pointerUp(top, { clientX: 100, clientY: -20, pointerId: 76 });
    commitPanelMove(track, 77, { x: 100, y: 100 }, { x: 90, y: 90 });
    expect(track.style.top).toBe("758px");
    expect(track.style.height).toBe("132px");
    const stored = JSON.parse(
      localStorage.getItem(panelStorageKey("project-1", "shot-track")) ?? "",
    );
    expect(stored.rect.height).toBe(132);

    view.unmount();
    renderFloatingTrack();
    expect(screen.getByTestId("shot-track").style.top).toBe("758px");
    expect(screen.getByTestId("shot-track").style.height).toBe("132px");
  });

  it("does not expose keyboard layout controls from the title region", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();

    const inspector = screen.getByTestId("shot-inspector");
    const title = panelDragRegion(inspector);
    const initial = {
      height: inspector.style.height,
      left: inspector.style.left,
      top: inspector.style.top,
      width: inspector.style.width,
    };
    expect(title.getAttribute("tabindex")).toBeNull();
    expect(title.getAttribute("aria-keyshortcuts")).toBeNull();
    expect(inspector.querySelector(".floating-panel__move-handle")).toBeNull();
    expect(inspector.querySelector(".floating-panel__resize-handle")).toBeNull();

    fireEvent.keyDown(title, { key: "ArrowLeft" });
    fireEvent.keyDown(title, { altKey: true, key: "ArrowRight" });
    expect({
      height: inspector.style.height,
      left: inspector.style.left,
      top: inspector.style.top,
      width: inspector.style.width,
    }).toEqual(initial);
  });

  it("ignores non-primary and overlapping Pointer starts on a title drag region", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();

    const inspector = screen.getByTestId("shot-inspector");
    const title = panelDragRegion(inspector);
    const capture = vi.fn();
    Object.defineProperty(title, "setPointerCapture", {
      configurable: true,
      value: capture,
    });

    const rightButtonDown = createEvent.pointerDown(title, {
      clientX: 100,
      clientY: 100,
      pointerId: 81,
    });
    Object.defineProperties(rightButtonDown, {
      button: { configurable: true, value: 2 },
      isPrimary: { configurable: true, value: true },
      pointerType: { configurable: true, value: "mouse" },
    });
    fireEvent(title, rightButtonDown);
    fireEvent.pointerMove(title, { clientX: 60, clientY: 100, pointerId: 81 });
    fireEvent.pointerUp(title, { clientX: 60, clientY: 100, pointerId: 81 });
    const secondaryPointerDown = createEvent.pointerDown(title, {
      clientX: 100,
      clientY: 100,
      pointerId: 82,
    });
    Object.defineProperties(secondaryPointerDown, {
      button: { configurable: true, value: 0 },
      isPrimary: { configurable: true, value: false },
      pointerType: { configurable: true, value: "touch" },
    });
    fireEvent(title, secondaryPointerDown);
    fireEvent.pointerMove(title, { clientX: 60, clientY: 100, pointerId: 82 });
    expect(capture).not.toHaveBeenCalled();
    expect(inspector.style.left).toBe("852px");

    fireEvent.pointerDown(title, { button: 0, clientX: 100, clientY: 100, isPrimary: true, pointerId: 83 });
    fireEvent.pointerMove(title, { clientX: 90, clientY: 100, pointerId: 83 });
    fireEvent.pointerDown(title, { button: 0, clientX: 90, clientY: 100, isPrimary: true, pointerId: 84 });
    fireEvent.pointerMove(title, { clientX: 40, clientY: 100, pointerId: 84 });
    fireEvent.pointerUp(title, { clientX: 40, clientY: 100, pointerId: 84 });
    expect(inspector.style.left).toBe("842px");
    fireEvent.pointerUp(title, { clientX: 90, clientY: 100, pointerId: 83 });
    expect(capture).toHaveBeenCalledTimes(1);
  });

  it("keeps the empty Inspector in the same movable and resizable frame", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector(null);

    expect(screen.getByRole("heading", { name: "素材检查器" })).toBeTruthy();
    expect(screen.getByText("选择一个分镜以检查素材。")).toBeTruthy();
    const inspector = screen.getByTestId("shot-inspector");
    expect(panelDragRegion(inspector)).toBeTruthy();
    expect(panelResizeEdge(inspector, "bottom")).toBeTruthy();
    expect(panelResizeEdge(inspector, "bottom-left")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重置分镜素材检查器布局" })).toBeTruthy();
  });

  it("keeps only reset as an explicit layout button on the expanded Shot Track", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingTrack();

    const track = screen.getByTestId("shot-track");
    const top = panelResizeEdge(track, "top");
    fireEvent.pointerDown(top, { clientX: 100, clientY: 100, pointerId: 78 });
    fireEvent.pointerMove(top, { clientX: 100, clientY: -20, pointerId: 78 });
    fireEvent.pointerUp(top, { clientX: 100, clientY: -20, pointerId: 78 });
    expect(panelDragRegion(track)).toBeTruthy();
    expect(panelResizeEdge(track, "top")).toBeTruthy();
    expect(panelResizeEdge(track, "left")).toBeTruthy();
    expect(panelResizeEdge(track, "right")).toBeTruthy();
    expect(track.querySelector(".floating-panel__move-handle")).toBeNull();
    expect(track.querySelector(".floating-panel__resize-handle")).toBeNull();
    const headerActions = track.querySelector(".shot-track__header-actions");
    expect(headerActions).toBeTruthy();
    const headerButtons = within(headerActions as HTMLElement).getAllByRole("button");
    expect(headerButtons).toHaveLength(2);
    expect(headerButtons[0]?.textContent).toBe("新建分镜");
    expect(headerButtons[1]?.getAttribute("aria-label")).toBe("重置分镜轨道布局");
  });

  it("shows the same thumbnail and stable Shot identity in the Inspector and Track across reorder", async () => {
    installAlwaysVisibleIntersectionObserver();
    useCanvasStore.getState().setShotSlot(ids.shotOne, "image", {
      revisionId: ids.imageOne,
      mediaKind: "image",
    });
    render(
      <>
        <ShotInspector
          onOpenHistory={vi.fn()}
          selectedShotId={ids.shotOne}
        />
        <ShotTrack
          onRequestDelete={vi.fn()}
          selectedShotId={ids.shotOne}
        />
      </>,
    );
    const inspector = screen.getByTestId("shot-inspector");
    const track = screen.getByTestId("shot-track");
    expandFloatingTrack(track, 201);
    await waitFor(() =>
      expect(
        within(track).getByRole("img", {
          name: "分镜 01 缩略图：safe-storyboard.png",
        }),
      ).toBeTruthy(),
    );

    expect(within(inspector).getByText("分镜 01")).toBeTruthy();
    expect(
      within(inspector).getByText("镜头 ID S-UR72CEYHRT"),
    ).toBeTruthy();
    const selectedTrackItem = track.querySelector(
      'li[aria-current="true"]',
    ) as HTMLElement;
    expect(within(selectedTrackItem).getByText("分镜 01")).toBeTruthy();
    expect(
      within(selectedTrackItem).getByText("镜头 ID S-UR72CEYHRT"),
    ).toBeTruthy();
    expect(
      within(selectedTrackItem).getByRole("button", {
        name: "分镜 01",
        description:
          "镜头 ID S-UR72CEYHRT 分镜 01 缩略图：safe-storyboard.png",
      }),
    ).toBeTruthy();
    expect(
      within(selectedTrackItem).getByRole("img", {
        name: "分镜 01 缩略图：safe-storyboard.png",
      }),
    ).toHaveProperty("draggable", false);
    const trackSelect = selectedTrackItem.querySelector(".shot-track__select");
    const trackActions = selectedTrackItem.querySelector(".shot-track__actions");
    expect(trackActions).toBeTruthy();
    expect(trackActions?.previousElementSibling).toBe(trackSelect);
    expect(within(trackActions as HTMLElement).getAllByRole("button")).toHaveLength(3);

    fireEvent.click(
      within(selectedTrackItem).getByRole("button", {
        name: "下移分镜 1",
      }),
    );

    expect(within(inspector).getByText("分镜 02")).toBeTruthy();
    expect(
      within(inspector).getByText("镜头 ID S-UR72CEYHRT"),
    ).toBeTruthy();
    const reorderedTrackItem = track.querySelector(
      'li[aria-current="true"]',
    ) as HTMLElement;
    expect(within(reorderedTrackItem).getByText("分镜 02")).toBeTruthy();
    expect(
      within(reorderedTrackItem).getByText("镜头 ID S-UR72CEYHRT"),
    ).toBeTruthy();
    expect(
      within(reorderedTrackItem).getByRole("button", {
        name: "分镜 02",
        description:
          "镜头 ID S-UR72CEYHRT 分镜 02 缩略图：safe-storyboard.png",
      }),
    ).toBeTruthy();
  });

  it("keeps Shot thumbnail and stable identity styles isolated from B2 styles", () => {
    expect(shotVisualIdentityStyles).toContain(".shot-thumbnail");
    expect(shotVisualIdentityStyles).toContain(".shot-stable-id");
    expect(shotVisualIdentityStyles).toContain("object-fit: cover");
    expect(shotVisualIdentityStyles).toMatch(
      /\.shot-thumbnail:not\(\.shot-thumbnail--compact\)\[data-thumbnail-state="ready"\]\s*\{[^}]*aspect-ratio:\s*auto/s,
    );
    expect(shotVisualIdentityStyles).toMatch(
      /\.shot-thumbnail:not\(\.shot-thumbnail--compact\)\[data-thumbnail-state="ready"\]\s*\{[^}]*width:\s*50%[^}]*margin-inline:\s*auto/s,
    );
    expect(shotVisualIdentityStyles).toMatch(
      /\.shot-thumbnail:not\(\.shot-thumbnail--compact\)\[data-thumbnail-state="ready"\]\s+img\s*\{[^}]*height:\s*auto[^}]*object-fit:\s*contain/s,
    );
    expect(shotVisualIdentityStyles).toContain("grid-template-columns: minmax(0, 1fr)");
    expect(shotVisualIdentityStyles).toContain(".shot-track__actions");
    expect(shotVisualIdentityStyles).toContain(
      "grid-template-columns: repeat(3, minmax(0, 1fr))",
    );
    expect(shotVisualIdentityStyles).toMatch(
      /\.shot-track li \.shot-track__actions > button\s*\{[^}]*text-align:\s*center/s,
    );
    expect(shotVisualIdentityStyles).toContain("min-width: 255px");
    expect(shotVisualIdentityStyles).toMatch(
      /\.shot-track__header-actions\s*\{[^}]*margin-left:\s*auto[^}]*display:\s*flex[^}]*gap:\s*6px/s,
    );
    expect(floatingPanelStyles).toMatch(
      /\.shot-track\[data-track-state="collapsed"\][^{]*ol,[^}]*display:\s*none/s,
    );
    expect(floatingPanelStyles).toMatch(
      /\.shot-track[^\n]*\.floating-panel__edge-grip--top::after\s*\{[^}]*content:\s*""/s,
    );
    expect(shotVisualIdentityStyles).toContain("overflow-x: auto");
    expect(shotVisualIdentityStyles).toContain(".shot-track li.is-drop-before::before");
    expect(shotVisualIdentityStyles).toContain(".shot-inspector__references > li.is-drop-before::before");
    expect(shotVisualIdentityStyles).toContain("height: 3px");
    expect(shotVisualIdentityStyles).toContain("width: 3px");
    expect(shotVisualIdentityStyles).toContain("cursor: grab");
    expect(shotVisualIdentityStyles).toContain("cursor: grabbing");
    expect(shotVisualIdentityStyles).toContain(".shot-track__reorder-status");
    expect(shotVisualIdentityStyles).toContain("position: absolute");
    expect(shotVisualIdentityStyles).not.toContain("aria-grabbed");
    expect(shotVisualIdentityStyles).toContain("@media (prefers-reduced-motion: reduce)");
  });

  it("shows horizontal midpoint placement feedback and reports a stable Shot ID after drag reorder", () => {
    render(<ShotTrack onRequestDelete={vi.fn()} selectedShotId={ids.shotOne} />);
    const track = screen.getByTestId("shot-track");
    expandFloatingTrack(track, 202);
    const [source, target] = within(track).getAllByRole("listitem");
    const values = new Map<string, string>();
    const dataTransfer = {
      dropEffect: "none",
      effectAllowed: "uninitialized",
      getData: vi.fn((type: string) => values.get(type) ?? ""),
      setData: vi.fn((type: string, value: string) => values.set(type, value)),
      types: ["application/x-ai-canvas-shot-id", "text/plain"],
    };
    vi.spyOn(target!, "getBoundingClientRect").mockReturnValue({
      left: 100,
      top: 20,
      width: 80,
      height: 40,
    } as DOMRect);

    fireEvent.dragStart(source!, { dataTransfer });

    expect(dataTransfer.setData).toHaveBeenCalledWith(
      "application/x-ai-canvas-shot-id",
      ids.shotOne,
    );
    expect(dataTransfer.effectAllowed).toBe("move");
    expect(source!.className).toContain("is-dragging");
    expect(values.get("application/x-ai-canvas-shot-id")).toBe(ids.shotOne);
    expect(target!.getBoundingClientRect().width).toBe(80);

    const protectedDragTransfer = {
      dropEffect: "none",
      getData: vi.fn(() => ""),
      types: ["application/x-ai-canvas-shot-id", "text/plain"],
    };

    const dragOver = createEvent.dragOver(target!, { dataTransfer: protectedDragTransfer });
    Object.defineProperty(dragOver, "clientX", { value: 179 });
    Object.defineProperty(dragOver, "clientY", { value: 40 });
    fireEvent(target!, dragOver);

    expect(protectedDragTransfer.getData).not.toHaveBeenCalled();
    expect(dragOver.defaultPrevented).toBe(true);
    expect(protectedDragTransfer.dropEffect).toBe("move");
    expect(target!.className).toContain("is-drop-after");
    expect(within(track).getByRole("status").textContent).toContain("将放到分镜 02之后");

    const drop = createEvent.drop(target!, { dataTransfer });
    Object.defineProperty(drop, "clientX", { value: 179 });
    Object.defineProperty(drop, "clientY", { value: 40 });
    fireEvent(target!, drop);

    expect(dataTransfer.getData).toHaveBeenCalledWith("application/x-ai-canvas-shot-id");

    expect(
      [...useCanvasStore.getState().shots]
        .sort((left, right) => left.order - right.order)
        .map((shot) => shot.id),
    ).toEqual([ids.shotTwo, ids.shotOne]);
    expect(within(track).getByRole("status").textContent).toContain(
      "镜头 ID S-UR72CEYHRT 现在位于第 2 位",
    );
    expect(within(track).queryByText("将放到分镜 02之后")).toBeNull();
  });

  it("does not reorder Shots from an external or tampered transfer and clears drag feedback", () => {
    render(<ShotTrack onRequestDelete={vi.fn()} selectedShotId={ids.shotOne} />);
    const track = screen.getByTestId("shot-track");
    expandFloatingTrack(track, 203);
    const [source, target] = within(track).getAllByRole("listitem");
    const values = new Map<string, string>();
    const dataTransfer = {
      dropEffect: "none",
      effectAllowed: "uninitialized",
      getData: vi.fn((type: string) => values.get(type) ?? ""),
      setData: vi.fn((type: string, value: string) => values.set(type, value)),
      types: ["application/x-ai-canvas-shot-id", "text/plain"],
    };
    vi.spyOn(target!, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 80,
      height: 40,
    } as DOMRect);

    fireEvent.dragOver(target!, { clientX: 70, clientY: 10, dataTransfer });
    expect(target!.className).not.toContain("is-drop-after");

    fireEvent.dragStart(source!, { dataTransfer });
    values.set("application/x-ai-canvas-shot-id", ids.shotTwo);
    fireEvent.dragOver(target!, { clientX: 70, clientY: 10, dataTransfer });
    fireEvent.drop(target!, { clientX: 70, clientY: 10, dataTransfer });

    expect(
      [...useCanvasStore.getState().shots]
        .sort((left, right) => left.order - right.order)
        .map((shot) => shot.id),
    ).toEqual([ids.shotOne, ids.shotTwo]);
    expect(source!.className).not.toContain("is-dragging");
    expect(target!.className).not.toContain("is-drop-after");
  });

  it("clears Shot drag feedback without mutation when the project becomes conflicted", async () => {
    render(<ShotTrack onRequestDelete={vi.fn()} selectedShotId={ids.shotOne} />);
    const track = screen.getByTestId("shot-track");
    expandFloatingTrack(track, 204);
    const [source, target] = within(track).getAllByRole("listitem");
    const values = new Map<string, string>();
    const dataTransfer = {
      dropEffect: "none",
      effectAllowed: "uninitialized",
      getData: vi.fn((type: string) => values.get(type) ?? ""),
      setData: vi.fn((type: string, value: string) => values.set(type, value)),
      types: ["application/x-ai-canvas-shot-id", "text/plain"],
    };
    vi.spyOn(target!, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 80,
      height: 40,
    } as DOMRect);

    fireEvent.dragStart(source!, { dataTransfer });
    const dragOver = createEvent.dragOver(target!, { dataTransfer });
    Object.defineProperty(dragOver, "clientX", { value: 70 });
    Object.defineProperty(dragOver, "clientY", { value: 10 });
    fireEvent(target!, dragOver);
    expect(target!.className).toContain("is-drop-after");
    const beforeConflict = useCanvasStore.getState().shots.map((shot) => shot.id);

    act(() => {
      const current = useCanvasStore.getState();
      current.markConflict(current.loadEpoch, "project-1", "server-revision");
    });

    await waitFor(() => {
      expect(source!.className).not.toContain("is-dragging");
      expect(target!.className).not.toContain("is-drop-after");
      expect(within(track).getByRole("status").textContent).not.toContain("将放到");
    });
    expect(useCanvasStore.getState().shots.map((shot) => shot.id)).toEqual(beforeConflict);
  });

  it("supports direct pointer, remount, and reset behavior on the empty Inspector", () => {
      const panelId = "shot-inspector" as const;
      const renderSurface = () => renderFloatingInspector(null);
      const resetLabel = "重置分镜素材检查器布局";
      const resizeEdge = "left";
      const testId = "shot-inspector";
      mockWorkspaceBounds();
      installResizeObserver();
      const view = renderSurface();
      const panel = screen.getByTestId(testId);
      const initialLeft = Number.parseFloat(panel.style.left);
      const initialTop = Number.parseFloat(panel.style.top);
      const initialWidth = Number.parseFloat(panel.style.width);
      const move = panelDragRegion(panel);
      const setPointerCapture = vi.fn();
      const releasePointerCapture = vi.fn();
      Object.defineProperties(move, {
        setPointerCapture: { configurable: true, value: setPointerCapture },
        releasePointerCapture: { configurable: true, value: releasePointerCapture },
      });

      fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 11 });
      fireEvent.pointerMove(move, { clientX: 90, clientY: 110, pointerId: 11 });
      expect(panel.style.left).toBe(`${initialLeft - 10}px`);
      expect(panel.style.top).toBe(`${initialTop + 10}px`);
      expect(localStorage.getItem(panelStorageKey("project-1", panelId))).toBeNull();
      fireEvent.pointerUp(move, { clientX: 90, clientY: 110, pointerId: 11 });

      expect(setPointerCapture).toHaveBeenCalledWith(11);
      expect(releasePointerCapture).toHaveBeenCalledWith(11);
      const resize = panelResizeEdge(panel, resizeEdge);
      fireEvent.pointerDown(resize, { clientX: 100, clientY: 100, pointerId: 12 });
      fireEvent.pointerMove(resize, { clientX: 110, clientY: 100, pointerId: 12 });
      fireEvent.pointerUp(resize, { clientX: 110, clientY: 100, pointerId: 12 });
      expect(localStorage.getItem(panelStorageKey("project-1", panelId))).not.toBeNull();
      const persistedLeft = panel.style.left;

      view.unmount();
      renderSurface();
      expect(screen.getByTestId(testId).style.left).toBe(persistedLeft);

      fireEvent.click(screen.getByRole("button", { name: resetLabel }));
      expect(screen.getByTestId(testId).style.left).toBe(`${initialLeft}px`);
      expect(screen.getByTestId(testId).style.width).toBe(`${initialWidth}px`);
      expect(localStorage.getItem(panelStorageKey("project-1", panelId))).toBeNull();
  });

  it("supports expand, direct pointer, remount, and reset on the Shot Track", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    const view = renderFloatingTrack();
    const panel = screen.getByTestId("shot-track");
    const initialLeft = Number.parseFloat(panel.style.left);
    const initialWidth = Number.parseFloat(panel.style.width);
    expandFloatingTrack(panel, 205);
    const move = panelDragRegion(panel);

    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 206 });
    fireEvent.pointerMove(move, { clientX: 90, clientY: 110, pointerId: 206 });
    fireEvent.pointerUp(move, { clientX: 90, clientY: 110, pointerId: 206 });
    const resize = panelResizeEdge(panel, "right");
    fireEvent.pointerDown(resize, { clientX: 100, clientY: 100, pointerId: 207 });
    fireEvent.pointerMove(resize, { clientX: 110, clientY: 100, pointerId: 207 });
    fireEvent.pointerUp(resize, { clientX: 110, clientY: 100, pointerId: 207 });
    const persistedLeft = panel.style.left;

    view.unmount();
    renderFloatingTrack();
    expect(screen.getByTestId("shot-track").style.left).toBe(persistedLeft);
    fireEvent.click(screen.getByRole("button", { name: "重置分镜轨道布局" }));
    const resetTrack = screen.getByTestId("shot-track");
    expect(resetTrack.style.left).toBe(`${initialLeft}px`);
    expect(resetTrack.style.width).toBe(`${initialWidth}px`);
    expect(resetTrack.style.height).toBe(`${SHOT_TRACK_COLLAPSED_HEIGHT}px`);
    expect(screen.queryByRole("button", { name: "重置分镜轨道布局" })).toBeNull();
    expect(localStorage.getItem(panelStorageKey("project-1", "shot-track"))).toBeNull();
  });

  it("captures the pointer, clamps drag movement to the workspace, and commits on pointer end", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();
    const inspector = screen.getByTestId("shot-inspector");
    const move = panelDragRegion(inspector);
    const setPointerCapture = vi.fn();
    const releasePointerCapture = vi.fn();
    Object.defineProperties(move, {
      setPointerCapture: { configurable: true, value: setPointerCapture },
      releasePointerCapture: { configurable: true, value: releasePointerCapture },
    });

    expect(inspector.style.left).toBe("852px");
    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 7 });
    fireEvent.pointerMove(move, { clientX: 3000, clientY: 3000, pointerId: 7 });

    expect(setPointerCapture).toHaveBeenCalledWith(7);
    expect(inspector.style.left).toBe("852px");
    expect(inspector.style.top).toBe("608px");
    expect(localStorage.getItem(panelStorageKey("project-1", "shot-inspector"))).toBeNull();

    fireEvent.pointerUp(move, { clientX: 3000, clientY: 3000, pointerId: 7 });

    expect(releasePointerCapture).toHaveBeenCalledWith(7);
    expect(
      JSON.parse(
        localStorage.getItem(panelStorageKey("project-1", "shot-inspector")) ?? "",
      ),
    ).toEqual({
      version: 1,
      rect: { x: 852, y: 608, width: 348, height: 292 },
    });
  });

  it("rolls back to the committed start rect on pointer cancellation without writing storage", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();
    const inspector = screen.getByTestId("shot-inspector");
    const move = panelDragRegion(inspector);
    const releasePointerCapture = vi.fn();
    Object.defineProperties(move, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      releasePointerCapture: { configurable: true, value: releasePointerCapture },
    });
    commitPanelMove(inspector, 16, { x: 100, y: 100 }, { x: 90, y: 100 });
    const storageKey = panelStorageKey("project-1", "shot-inspector");
    const committed = localStorage.getItem(storageKey);
    expect(inspector.style.left).toBe("842px");

    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 17 });
    fireEvent.pointerMove(move, { clientX: 40, clientY: 140, pointerId: 17 });
    expect(inspector.style.left).toBe("782px");
    fireEvent.pointerCancel(move, { clientX: 40, clientY: 140, pointerId: 17 });

    expect(releasePointerCapture).toHaveBeenCalledWith(17);
    expect(inspector.style.left).toBe("842px");
    expect(inspector.style.top).toBe("16px");
    expect(localStorage.getItem(storageKey)).toBe(committed);
  });

  it("rolls back an active interaction when pointer capture is lost", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();
    const inspector = screen.getByTestId("shot-inspector");
    const resize = panelResizeEdge(inspector, "left");
    Object.defineProperty(resize, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    const storageKey = panelStorageKey("project-1", "shot-inspector");

    fireEvent.pointerDown(resize, { clientX: 100, clientY: 100, pointerId: 19 });
    fireEvent.pointerMove(resize, { clientX: 60, clientY: 130, pointerId: 19 });
    expect(inspector.style.width).toBe("388px");
    fireEvent.lostPointerCapture(resize, { pointerId: 19 });

    expect(inspector.style.left).toBe("852px");
    expect(inspector.style.width).toBe("348px");
    expect(inspector.style.height).toBe("292px");
    expect(localStorage.getItem(storageKey)).toBeNull();
  });

  it("clamps a cancelled drag rollback to bounds that shrank during the interaction", () => {
    const workspace = mockWorkspaceBounds();
    const observer = installResizeObserver();
    renderFloatingInspector();
    const inspector = screen.getByTestId("shot-inspector");
    const move = panelDragRegion(inspector);
    const releasePointerCapture = vi.fn();
    Object.defineProperties(move, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      releasePointerCapture: { configurable: true, value: releasePointerCapture },
    });
    commitPanelMove(inspector, 28, { x: 100, y: 100 }, { x: 90, y: 100 });
    const storageKey = panelStorageKey("project-1", "shot-inspector");
    const committed = localStorage.getItem(storageKey);
    expect(committed).not.toBeNull();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    setItem.mockClear();

    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 29 });
    fireEvent.pointerMove(move, { clientX: 40, clientY: 140, pointerId: 29 });
    workspace.resize(800, 600);
    act(() => observer.notify());
    expect(inspector.style.left).toBe("452px");
    fireEvent.pointerCancel(move, { pointerId: 29 });

    expect(releasePointerCapture).toHaveBeenCalledWith(29);
    expect(inspector.style.left).toBe("452px");
    expect(
      Number.parseFloat(inspector.style.left) +
        Number.parseFloat(inspector.style.width),
    ).toBeLessThanOrEqual(800);
    expect(
      Number.parseFloat(inspector.style.top) +
        Number.parseFloat(inspector.style.height),
    ).toBeLessThanOrEqual(600);
    expect(localStorage.getItem(storageKey)).toBe(committed);
    expect(setItem).not.toHaveBeenCalled();
  });

  it("clamps a lost resize rollback to bounds that shrank during the interaction", () => {
    const workspace = mockWorkspaceBounds();
    const observer = installResizeObserver();
    renderFloatingInspector();
    const inspector = screen.getByTestId("shot-inspector");
    const resize = panelResizeEdge(inspector, "left");
    Object.defineProperty(resize, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    fireEvent.pointerDown(resize, { clientX: 100, clientY: 100, pointerId: 30 });
    fireEvent.pointerMove(resize, { clientX: 110, clientY: 100, pointerId: 30 });
    fireEvent.pointerUp(resize, { clientX: 110, clientY: 100, pointerId: 30 });
    const storageKey = panelStorageKey("project-1", "shot-inspector");
    const committed = localStorage.getItem(storageKey);
    expect(committed).not.toBeNull();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    setItem.mockClear();

    fireEvent.pointerDown(resize, { clientX: 100, clientY: 100, pointerId: 31 });
    fireEvent.pointerMove(resize, { clientX: 60, clientY: 140, pointerId: 31 });
    workspace.resize(600, 400);
    act(() => observer.notify());
    expect(inspector.style.left).toBe("222px");
    fireEvent.lostPointerCapture(resize, { pointerId: 31 });

    expect(inspector.style.left).toBe("262px");
    expect(inspector.style.width).toBe("338px");
    expect(
      Number.parseFloat(inspector.style.left) +
        Number.parseFloat(inspector.style.width),
    ).toBeLessThanOrEqual(600);
    expect(
      Number.parseFloat(inspector.style.top) +
        Number.parseFloat(inspector.style.height),
    ).toBeLessThanOrEqual(400);
    expect(localStorage.getItem(storageKey)).toBe(committed);
    expect(setItem).not.toHaveBeenCalled();
  });

  it("releases capture and restores the committed rect after an active unmount", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    const view = renderFloatingInspector();
    const move = panelDragRegion(screen.getByTestId("shot-inspector"));
    const releasePointerCapture = vi.fn();
    Object.defineProperties(move, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      releasePointerCapture: { configurable: true, value: releasePointerCapture },
    });
    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 33 });
    fireEvent.pointerMove(move, { clientX: 90, clientY: 100, pointerId: 33 });
    fireEvent.pointerUp(move, { clientX: 90, clientY: 100, pointerId: 33 });
    const storageKey = panelStorageKey("project-1", "shot-inspector");
    const committed = localStorage.getItem(storageKey);
    expect(committed).not.toBeNull();
    expect(screen.getByTestId("shot-inspector").style.left).toBe("842px");
    releasePointerCapture.mockClear();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    setItem.mockClear();

    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 34 });
    fireEvent.pointerMove(move, { clientX: 40, clientY: 140, pointerId: 34 });
    expect(screen.getByTestId("shot-inspector").style.left).toBe("782px");
    view.unmount();

    expect(releasePointerCapture).toHaveBeenCalledWith(34);
    expect(localStorage.getItem(storageKey)).toBe(committed);
    expect(setItem).not.toHaveBeenCalled();
    renderFloatingInspector();
    expect(screen.getByTestId("shot-inspector").style.left).toBe("842px");
  });

  it("clears an active interaction on project switch so a late pointerup cannot commit it", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    function ProjectHarness({ projectId }: { projectId: string }) {
      return (
        <div className="canvas-workspace">
          <ShotInspector
            onOpenHistory={vi.fn()}
            projectId={projectId}
            selectedShotId={ids.shotOne}
          />
        </div>
      );
    }
    const view = render(<ProjectHarness projectId="project-a" />);
    const move = panelDragRegion(screen.getByTestId("shot-inspector"));
    const releasePointerCapture = vi.fn();
    Object.defineProperties(move, {
      setPointerCapture: { configurable: true, value: vi.fn() },
      releasePointerCapture: { configurable: true, value: releasePointerCapture },
    });
    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 22 });
    fireEvent.pointerMove(move, { clientX: 90, clientY: 100, pointerId: 22 });
    fireEvent.pointerUp(move, { clientX: 90, clientY: 100, pointerId: 22 });
    const projectAKey = panelStorageKey("project-a", "shot-inspector");
    const projectACommitted = localStorage.getItem(projectAKey);
    fireEvent.pointerDown(move, { clientX: 100, clientY: 100, pointerId: 23 });
    fireEvent.pointerMove(move, { clientX: 50, clientY: 120, pointerId: 23 });

    view.rerender(<ProjectHarness projectId="project-b" />);
    fireEvent.pointerUp(
      panelDragRegion(screen.getByTestId("shot-inspector")),
      { clientX: 50, clientY: 120, pointerId: 23 },
    );

    expect(releasePointerCapture).toHaveBeenCalledWith(23);
    expect(screen.getByTestId("shot-inspector").style.left).toBe("852px");
    expect(localStorage.getItem(projectAKey)).toBe(projectACommitted);
    expect(
      localStorage.getItem(panelStorageKey("project-b", "shot-inspector")),
    ).toBeNull();
  });

  it("resizes from the Inspector left and bottom dock edges and exposes a bottom-right grip", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();
    const inspector = screen.getByTestId("shot-inspector");
    const left = panelResizeEdge(inspector, "left");
    const bottom = panelResizeEdge(inspector, "bottom");

    fireEvent.pointerDown(left, { clientX: 100, clientY: 100, pointerId: 35 });
    fireEvent.pointerMove(left, { clientX: 90, clientY: 100, pointerId: 35 });
    fireEvent.pointerUp(left, { clientX: 90, clientY: 100, pointerId: 35 });
    expect(inspector.style.left).toBe("842px");
    expect(inspector.style.width).toBe("358px");

    fireEvent.pointerDown(bottom, { clientX: 100, clientY: 100, pointerId: 36 });
    fireEvent.pointerMove(bottom, { clientX: 100, clientY: 110, pointerId: 36 });
    fireEvent.pointerUp(bottom, { clientX: 100, clientY: 110, pointerId: 36 });
    expect(inspector.style.top).toBe("16px");
    expect(inspector.style.height).toBe("302px");

    const bottomLeft = panelResizeEdge(inspector, "bottom-left");
    fireEvent.pointerDown(bottomLeft, { clientX: 100, clientY: 100, pointerId: 37 });
    fireEvent.pointerMove(bottomLeft, { clientX: 90, clientY: 110, pointerId: 37 });
    fireEvent.pointerUp(bottomLeft, { clientX: 90, clientY: 110, pointerId: 37 });
    expect(inspector.style.left).toBe("832px");
    expect(inspector.style.top).toBe("16px");
    expect(inspector.style.width).toBe("368px");
    expect(inspector.style.height).toBe("312px");
    expect(Number.parseFloat(inspector.style.left) + Number.parseFloat(inspector.style.width)).toBe(1200);
    expect(panelResizeEdge(inspector, "bottom-right")).toBeTruthy();
    expect(localStorage.getItem(panelStorageKey("project-1", "shot-inspector"))).not.toBeNull();
  });

  it("restores committed geometry when the keyed workspace subtree remounts", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    const view = renderFloatingInspector(ids.shotOne, "epoch-1");
    commitPanelMove(
      screen.getByTestId("shot-inspector"),
      37,
      { x: 100, y: 100 },
      { x: 90, y: 100 },
    );
    expect(screen.getByTestId("shot-inspector").style.left).toBe("842px");

    view.rerender(
      <div className="canvas-workspace" data-testid="floating-workspace">
        <ShotInspector
          key="epoch-2"
          onOpenHistory={vi.fn()}
          projectId="project-1"
          selectedShotId={ids.shotOne}
        />
      </div>,
    );

    expect(screen.getByTestId("shot-inspector").style.left).toBe("842px");
  });

  it.each([
    ["malformed", "{bad json"],
    [
      "unsupported version",
      JSON.stringify({
        version: 2,
        rect: { x: 20, y: 20, width: 348, height: 300 },
      }),
    ],
  ])("falls back to the right-edge Inspector default for %s storage", (_case, value) => {
    mockWorkspaceBounds();
    installResizeObserver();
    localStorage.setItem(panelStorageKey("project-1", "shot-inspector"), value);

    renderFloatingInspector();

    const inspector = screen.getByTestId("shot-inspector");
    expect(inspector.style.left).toBe("852px");
    expect(inspector.style.width).toBe("348px");
    expect(Number.parseFloat(inspector.style.left) + Number.parseFloat(inspector.style.width)).toBe(1200);
  });

  it.each([
    {
      label: "Inspector",
      panelId: "shot-inspector" as const,
      renderSurface: renderFloatingInspector,
      stored: { x: 500, y: 60, width: 348, height: 250 },
      testId: "shot-inspector",
      initial: { x: 372, y: 60, width: 348, height: 250 },
      expanded: { x: 372, y: 60, width: 348, height: 250 },
      shrunk: { x: 252, y: 60, width: 348, height: 250 },
    },
    {
      label: "Track",
      panelId: "shot-track" as const,
      renderSurface: renderFloatingTrack,
      stored: { x: 500, y: 700, width: 760, height: 128 },
      testId: "shot-track",
      initial: { x: 0, y: 700, width: 720, height: 128 },
      expanded: { x: 0, y: 700, width: 720, height: 128 },
      shrunk: { x: 0, y: 472, width: 600, height: 128 },
    },
  ])(
    "restores a clamped valid $label layout as customized on a 720px cold start",
    ({
      expanded,
      initial,
      panelId,
      renderSurface,
      shrunk,
      stored,
      testId,
    }) => {
      const workspace = mockWorkspaceBounds(720, 900);
      const observer = installResizeObserver();
      localStorage.setItem(
        panelStorageKey("project-1", panelId),
        JSON.stringify({ version: 1, rect: stored }),
      );

      renderSurface();
      const panel = screen.getByTestId(testId);
      const expectRect = (expected: typeof initial) => {
        expect({
          x: Number.parseFloat(panel.style.left),
          y: Number.parseFloat(panel.style.top),
          width: Number.parseFloat(panel.style.width),
          height: Number.parseFloat(panel.style.height),
        }).toEqual(expected);
      };

      expectRect(initial);

      workspace.resize(1200, 900);
      act(() => observer.notify());
      expectRect(expanded);

      workspace.resize(600, 600);
      act(() => observer.notify());
      expectRect(shrunk);
    },
  );

  it("reclamps live geometry when ResizeObserver reports a smaller workspace", () => {
    const workspace = mockWorkspaceBounds();
    const observer = installResizeObserver();
    renderFloatingInspector();
    expect(observer.observe).toHaveBeenCalledWith(screen.getByTestId("floating-workspace"));

    workspace.resize(800, 600);
    act(() => observer.notify());

    expect(screen.getByTestId("shot-inspector").style.left).toBe(
      `${defaultPanelRect("shot-inspector", {
        width: 800,
        height: 600,
        viewportHeight: window.innerHeight,
      }).x}px`,
    );
  });

  it.each([
    ["without stored geometry", null],
    ["with malformed stored geometry", "{bad json"],
    [
      "with unsupported stored geometry",
      JSON.stringify({
        version: 2,
        rect: { x: 100, y: 100, width: 348, height: 240 },
      }),
    ],
  ])(
    "reflows default panel geometry across the 720px breakpoint %s",
    (_case, stored) => {
      const workspace = mockWorkspaceBounds();
      const observer = installResizeObserver();
      if (stored !== null) {
        localStorage.setItem(
          panelStorageKey("project-1", "shot-inspector"),
          stored,
        );
      }
      renderFloatingInspector();

      workspace.resize(720, 900);
      act(() => observer.notify());

      const expected = defaultPanelRect("shot-inspector", {
        width: 720,
        height: 900,
        viewportHeight: window.innerHeight,
      });
      const inspector = screen.getByTestId("shot-inspector");
      expect(inspector.style.left).toBe(`${expected.x}px`);
      expect(inspector.style.top).toBe(`${expected.y}px`);
      expect(inspector.style.width).toBe(`${expected.width}px`);
      expect(inspector.style.height).toBe(`${expected.height}px`);
    },
  );

  it("clamps valid saved geometry instead of replacing it on resize", () => {
    const workspace = mockWorkspaceBounds();
    const observer = installResizeObserver();
    localStorage.setItem(
      panelStorageKey("project-1", "shot-inspector"),
      JSON.stringify({
        version: 1,
        rect: { x: 500, y: 60, width: 348, height: 250 },
      }),
    );
    renderFloatingInspector();

    workspace.resize(720, 900);
    act(() => observer.notify());

    const inspector = screen.getByTestId("shot-inspector");
    expect(inspector.style.left).toBe("372px");
    expect(inspector.style.top).toBe("60px");
    expect(inspector.style.height).toBe("250px");
  });

  it("keeps a layout committed in this session custom when storage later disappears", () => {
    const workspace = mockWorkspaceBounds();
    const observer = installResizeObserver();
    renderFloatingInspector();
    commitPanelMove(
      screen.getByTestId("shot-inspector"),
      38,
      { x: 100, y: 100 },
      { x: 100, y: 110 },
    );
    expect(screen.getByTestId("shot-inspector").style.top).toBe("26px");
    localStorage.clear();

    workspace.resize(720, 900);
    act(() => observer.notify());

    const inspector = screen.getByTestId("shot-inspector");
    expect(inspector.style.left).toBe("372px");
    expect(inspector.style.top).toBe("26px");
  });

  it("uses the latest viewport height when reset follows a workspace resize", () => {
    vi.stubGlobal("innerHeight", 900);
    const workspace = mockWorkspaceBounds(600, 900);
    const observer = installResizeObserver();
    renderFloatingInspector();

    vi.stubGlobal("innerHeight", 600);
    workspace.resize(600, 560);
    act(() => observer.notify());
    fireEvent.click(
      screen.getByRole("button", { name: "重置分镜素材检查器布局" }),
    );

    const inspector = screen.getByTestId("shot-inspector");
    expect(
      Number.parseFloat(inspector.style.top) +
        Number.parseFloat(inspector.style.height),
    ).toBeLessThanOrEqual(560 - 16 - 600 * 0.48);
  });

  it("resets the panel to its default without mutating canvas business state", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    renderFloatingInspector();
    const before = useCanvasStore.getState();
    const businessState = {
      baseRevisionId: before.baseRevisionId,
      editSequence: before.editSequence,
      savedSequence: before.savedSequence,
      status: before.status,
      viewport: before.viewport,
    };
    commitPanelMove(
      screen.getByTestId("shot-inspector"),
      39,
      { x: 100, y: 100 },
      { x: 90, y: 100 },
    );
    expect(screen.getByTestId("shot-inspector").style.left).toBe("842px");

    fireEvent.click(
      screen.getByRole("button", { name: "重置分镜素材检查器布局" }),
    );

    expect(screen.getByTestId("shot-inspector").style.left).toBe("852px");
    expect(localStorage.getItem(panelStorageKey("project-1", "shot-inspector"))).toBeNull();
    const after = useCanvasStore.getState();
    expect({
      baseRevisionId: after.baseRevisionId,
      editSequence: after.editSequence,
      savedSequence: after.savedSequence,
      status: after.status,
      viewport: after.viewport,
    }).toEqual(businessState);
  });

  it("defines 44px pointer drag and resize targets without outranking B2 modals", () => {
    expect(floatingPanelStyles).toContain("min-width: 44px");
    expect(floatingPanelStyles).toContain("min-height: 44px");
    expect(floatingPanelStyles).toContain("cursor: move");
    expect(floatingPanelStyles).toContain("cursor: nwse-resize");
    expect(floatingPanelStyles).toContain(":focus-visible");
    expect(floatingPanelStyles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(floatingPanelStyles).not.toContain("z-index: 40");
    expect(floatingPanelStyles).not.toContain("z-index: 41");
  });

  it("does not reserve obsolete bottom-button space inside the active Shot Track list", () => {
    const trackListRule = floatingPanelStyles.match(
      /\.shot-track\.floating-panel--active ol\s*\{([^}]*)\}/,
    );
    expect(trackListRule).not.toBeNull();
    expect(trackListRule?.[1]).toMatch(/padding-right:\s*14px\s*;/);
    expect(trackListRule?.[1]).toMatch(/padding-bottom:\s*0(?:px)?\s*;/);
    expect(trackListRule?.[1]).not.toMatch(/padding-bottom:\s*44px\s*;/);
  });

  it("reserves at least 56px at the active History footer inline end for its resize target", () => {
    mockWorkspaceBounds();
    installResizeObserver();
    api.listArtifactRevisions.mockResolvedValue([revision(ids.imageOne)]);
    render(
      <div className="canvas-workspace">
        <ArtifactHistoryDrawer
          onClose={vi.fn()}
          target={boundHistoryTarget("image", ids.imageOne)}
        />
      </div>,
    );

    const history = screen.getByTestId("artifact-history-drawer");
    const apply = screen.getByRole("button", { name: "应用所选素材" });
    const footer = apply.closest("footer");
    const resize = screen.getByRole("button", {
      name: "调整素材版本历史大小",
    });
    expect(history.classList.contains("floating-panel--active")).toBe(true);
    expect(footer?.matches(".shot-history.floating-panel--active footer")).toBe(
      true,
    );
    expect(history.contains(resize)).toBe(true);
    expect(resize.classList.contains("floating-panel__resize-handle")).toBe(
      true,
    );

    const footerRule = floatingPanelStyles.match(
      /\.shot-history\.floating-panel--active footer\s*\{([^}]*)\}/,
    );
    const safePadding = footerRule?.[1]?.match(
      /padding-inline-end:\s*(\d+(?:\.\d+)?)px/,
    );
    expect(safePadding).not.toBeNull();
    expect(Number.parseFloat(safePadding?.[1] ?? "0")).toBeGreaterThanOrEqual(
      56,
    );
  });

  it("loads scoped dual-history layout styles with a scrollable source region and safe footer space", () => {
    expect(canvasPageSource).toContain(
      'import "./components/shotAssociationHistory.css";',
    );
    expect(shotAssociationHistoryStyles).toMatch(
      /\.shot-history__sources\s*\{[^}]*overflow-y:\s*auto/s,
    );
    expect(shotAssociationHistoryStyles).toMatch(
      /\.shot-history__sources\s*\{[^}]*padding-block-end:\s*(?:calc\([^)]*56px[^)]*\)|[6-9]\dpx)/s,
    );
    expect(shotAssociationHistoryStyles).toContain(
      ".shot-history__association-list",
    );
    expect(shotAssociationHistoryStyles).toContain(
      ".shot-history__event-meta",
    );
    expect(shotAssociationHistoryStyles).toContain(
      ".shot-history__load-more",
    );
  });

  it("keeps the live Inspector rect when History keyboard events are ignored and then closes", async () => {
    mockWorkspaceBounds();
    installResizeObserver();
    api.listArtifactRevisions.mockResolvedValue([
      revision(ids.videoOne, {
        media_kind: "video",
        media_type: "video/webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 4,
          frames_per_second: 24,
        },
      }),
    ]);
    function SharedInspectorSlotHarness() {
      const [target, setTarget] = useState<ShotHistoryTarget | null>(null);
      const floatingPanel = useFloatingPanel({
        panelId: "shot-inspector",
        projectId: "project-1",
      });
      return (
        <div className="canvas-workspace">
          {target === null ? (
            <ShotInspectorPanel
              floatingPanel={floatingPanel}
              onOpenHistory={setTarget}
              selectedShotId={ids.shotOne}
            />
          ) : (
            <ArtifactHistoryDrawerPanel
              floatingPanel={floatingPanel}
              onClose={() => setTarget(null)}
              target={{ ...target, anchorRevisionId: "revision-1" }}
            />
          )}
        </div>
      );
    }
    render(<SharedInspectorSlotHarness />);
    const inspector = screen.getByTestId("shot-inspector");
    const initialLeft = inspector.style.left;
    const initialWidth = inspector.style.width;
    const historyTrigger = within(
      screen.getByRole("region", { name: "视频槽位" }),
    ).getByRole("button", { name: "查看历史" });
    historyTrigger.focus();
    fireEvent.click(historyTrigger);

    const history = await screen.findByTestId("artifact-history-drawer");
    expect(screen.queryByTestId("shot-inspector")).toBeNull();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("heading", { name: "选择历史素材" }),
      ),
    );
    expect(history.style.left).toBe(initialLeft);
    expect(history.style.width).toBe(initialWidth);
    const moveHistory = screen.getByRole("button", { name: "移动素材版本历史" });
    const resizeHistory = screen.getByRole("button", { name: "调整素材版本历史大小" });
    expect(moveHistory.getAttribute("aria-keyshortcuts")).toBeNull();
    expect(resizeHistory.getAttribute("aria-keyshortcuts")).toBeNull();
    expect(moveHistory.getAttribute("tabindex")).toBe("-1");
    expect(resizeHistory.getAttribute("tabindex")).toBe("-1");
    fireEvent.keyDown(moveHistory, { key: "ArrowLeft" });
    fireEvent.keyDown(resizeHistory, { key: "ArrowLeft" });
    const historyLeft = history.style.left;
    const historyWidth = history.style.width;
    fireEvent.click(
      screen.getByRole("button", { name: "关闭素材版本历史" }),
    );

    const restoredInspector = screen.getByTestId("shot-inspector");
    expect(screen.queryByTestId("artifact-history-drawer")).toBeNull();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        within(screen.getByRole("region", { name: "视频槽位" })).getByRole(
          "button",
          { name: "查看历史" },
        ),
      ),
    );
    expect(restoredInspector.style.left).toBe(historyLeft);
    expect(restoredInspector.style.width).toBe(historyWidth);
    expect(restoredInspector.style.left).toBe(initialLeft);
    expect(restoredInspector.style.width).toBe(initialWidth);
  });

  it("drops image media only into image and reference slots", () => {
    renderInspector();

    fireEvent.drop(screen.getByRole("region", { name: "图片槽位" }), {
      dataTransfer: mediaTransfer(ids.imageOne, "image"),
    });
    fireEvent.drop(screen.getByRole("region", { name: "视频槽位" }), {
      dataTransfer: mediaTransfer(ids.imageTwo, "image"),
    });
    fireEvent.drop(screen.getByRole("region", { name: "参考槽位" }), {
      dataTransfer: mediaTransfer(ids.imageTwo, "image", ids.secondMediaNode),
    });

    const shot = useCanvasStore.getState().shots[0]!;
    expect(shot.imageRevisionId).toBe(ids.imageOne);
    expect(shot.videoRevisionId).toBe(ids.videoOne);
    expect(shot.referenceRevisionIds).toEqual([ids.imageTwo]);
  });

  it("cancels the matching active association only after an inspector drop succeeds", () => {
    useCanvasStore.getState().beginShotAssociation(ids.shotOne, "image");
    renderInspector();

    fireEvent.drop(screen.getByRole("region", { name: "图片槽位" }), {
      dataTransfer: mediaTransfer(ids.imageOne, "image"),
    });

    expect(useCanvasStore.getState().shots[0]!.imageRevisionId).toBe(ids.imageOne);
    expect(useCanvasStore.getState().associationTarget).toBeNull();
  });

  it("rejects an inspector drop when its source becomes ingress-protected after dragging", () => {
    useCanvasStore.getState().beginShotAssociation(ids.shotOne, "image");
    const targetLocks = renderInspector();
    const beforeDrop = useCanvasStore.getState().editSequence;

    targetLocks.acquire("replace-media", ids.mediaNode);
    fireEvent.drop(screen.getByRole("region", { name: "图片槽位" }), {
      dataTransfer: mediaTransfer(ids.imageOne, "image"),
    });

    expect(useCanvasStore.getState().shots[0]!.imageRevisionId).toBeNull();
    expect(useCanvasStore.getState().associationTarget).toEqual({
      shotId: ids.shotOne,
      slot: "image",
    });
    expect(useCanvasStore.getState().editSequence).toBe(beforeDrop);
  });

  it("rejects an inspector drop when its source revision changes after dragging", () => {
    useCanvasStore.getState().beginShotAssociation(ids.shotOne, "image");
    renderInspector();
    act(() => {
      useCanvasStore.getState().onNodesChange([
        {
          type: "replace",
          id: ids.mediaNode,
          item: {
            id: ids.mediaNode,
            type: "media_input",
            position: { x: 0, y: 0 },
            data: {
              kind: "media_input",
              artifactRevisionId: ids.imageTwo,
              mediaKind: "image",
            },
          },
        },
      ]);
    });
    const beforeDrop = useCanvasStore.getState().editSequence;

    fireEvent.drop(screen.getByRole("region", { name: "图片槽位" }), {
      dataTransfer: mediaTransfer(ids.imageOne, "image"),
    });

    expect(useCanvasStore.getState().shots[0]!.imageRevisionId).toBeNull();
    expect(useCanvasStore.getState().associationTarget).toEqual({
      shotId: ids.shotOne,
      slot: "image",
    });
    expect(useCanvasStore.getState().editSequence).toBe(beforeDrop);
  });

  it("lets a selected Shot inspector collapse without losing its accessible control", () => {
    renderInspector();

    fireEvent.click(screen.getByRole("button", { name: "收起检查器" }));
    expect(screen.getByRole("complementary", { name: "分镜检查器" }).className).toContain(
      "shot-inspector--collapsed",
    );
    expect(screen.getByRole("button", { name: "展开检查器" })).toBeTruthy();
  });

  it("shows the current safe revision sequence without rendering path or hash fields", () => {
    useCanvasStore.getState().setShotSlot(ids.shotOne, "image", {
      revisionId: ids.imageOne,
      mediaKind: "image",
    });
    useCanvasStore.getState().setShotSlot(ids.shotOne, "video", null);
    preview.useArtifactPreview.mockImplementation((revisionId) => {
      const result = readyPreview(revisionId);
      return {
        ...result,
        revision: Object.assign(
          { ...result.revision, sequence: 2 },
          {
            localPath: "C:\\private\\reference.png",
            sha256: "sha256:do-not-render-this",
          },
        ),
      };
    });

    renderInspector();

    expect(screen.getByText("版本 2")).toBeTruthy();
    expect(document.body.textContent).not.toContain("C:\\private\\reference.png");
    expect(document.body.textContent).not.toContain("sha256:do-not-render-this");
  });

  it("renders labelled safe provenance and applicable metadata in inspector and history previews", async () => {
    const sourceProjectId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const originProjectId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const createdAt = "2026-07-28T08:00:00Z";
    const privatePath = "C:\\private\\storyboard.webm";
    const privateHash = "sha256:do-not-render-this";
    const privateToken = "token:do-not-render-this";
    const safeRevision = Object.assign(
      revision(ids.videoOne, {
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "safe-motion.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 4,
          frames_per_second: 24,
        },
        source_kind: "import",
        source_project_id: sourceProjectId,
        origin_project_id: originProjectId,
        created_at: createdAt,
      }),
      {
        localPath: privatePath,
        sha256: privateHash,
        accessToken: privateToken,
      },
    );
    preview.useArtifactPreview.mockImplementation((revisionId) => ({
      ...readyPreview(revisionId),
      revision: revisionId === ids.videoOne ? safeRevision : readyPreview(revisionId).revision,
    }));

    renderInspector();

    const inspectorProvenance = screen.getByRole("region", { name: "素材来源信息" });
    expect(inspectorProvenance.textContent).toContain(sourceProjectId);
    expect(inspectorProvenance.textContent).toContain(originProjectId);
    expect(inspectorProvenance.textContent).toContain("2026-07-28 08:00:00 UTC");
    expect(inspectorProvenance.textContent).toContain("时长 4 秒");
    expect(inspectorProvenance.textContent).toContain("帧率 24 fps");
    expect(inspectorProvenance.querySelector("time")?.getAttribute("dateTime")).toBe(createdAt);
    for (const unsafe of [privatePath, privateHash, privateToken]) {
      expect(document.body.textContent).not.toContain(unsafe);
    }

    cleanup();
    api.listArtifactRevisions.mockResolvedValue([safeRevision]);
    render(
      <ArtifactHistoryDrawer
        onClose={vi.fn()}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );
    await waitFor(() =>
      expect(api.listArtifactRevisions).toHaveBeenCalledWith(
        ids.artifact,
        expect.any(AbortSignal),
      ),
    );

    const historyProvenance = screen.getByRole("region", { name: "素材来源信息" });
    expect(historyProvenance.textContent).toContain(sourceProjectId);
    expect(historyProvenance.textContent).toContain(originProjectId);
    expect(historyProvenance.textContent).toContain("2026-07-28 08:00:00 UTC");
    expect(historyProvenance.textContent).toContain("时长 4 秒");
    expect(historyProvenance.textContent).toContain("帧率 24 fps");
    expect(historyProvenance.querySelector("time")?.getAttribute("dateTime")).toBe(createdAt);
    for (const unsafe of [privatePath, privateHash, privateToken]) {
      expect(document.body.textContent).not.toContain(unsafe);
    }
  });

  it("uses vertical midpoint feedback and reports the reference filename after drag reorder", () => {
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageOne,
      mediaKind: "image",
    });
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageTwo,
      mediaKind: "image",
    });
    renderInspector();
    const inspector = screen.getByTestId("shot-inspector");
    const referenceSlot = screen.getByRole("region", { name: "参考槽位" });
    const [target, source] = within(referenceSlot).getAllByRole("listitem");
    const dragData = new Map<string, string>();
    const dataTransfer = {
      dropEffect: "none",
      effectAllowed: "uninitialized",
      setData: vi.fn((type: string, value: string) => dragData.set(type, value)),
      getData: vi.fn((type: string) => dragData.get(type) ?? ""),
      types: ["application/x-ai-canvas-shot-reference", "text/plain"],
    };
    vi.spyOn(target!, "getBoundingClientRect").mockReturnValue({
      left: 20,
      top: 80,
      width: 320,
      height: 80,
    } as DOMRect);

    fireEvent.dragStart(source!, {
      dataTransfer,
    });
    expect(dataTransfer.setData).toHaveBeenCalledWith(
      "application/x-ai-canvas-shot-reference",
      ids.imageTwo,
    );
    expect(dataTransfer.effectAllowed).toBe("move");
    expect(source!.className).toContain("is-dragging");
    const protectedDragTransfer = {
      dropEffect: "none",
      getData: vi.fn(() => ""),
      types: ["application/x-ai-canvas-shot-reference", "text/plain"],
    };
    const dragOver = createEvent.dragOver(target!, { dataTransfer: protectedDragTransfer });
    Object.defineProperty(dragOver, "clientX", { value: 100 });
    Object.defineProperty(dragOver, "clientY", { value: 81 });
    fireEvent(target!, dragOver);

    expect(protectedDragTransfer.getData).not.toHaveBeenCalled();
    expect(dragOver.defaultPrevented).toBe(true);
    expect(protectedDragTransfer.dropEffect).toBe("move");
    expect(target!.className).toContain("is-drop-before");
    expect(within(inspector).getByRole("status").textContent).toContain("将放到safe-storyboard.png之前");
    expect(dataTransfer.getData).not.toHaveBeenCalled();

    const drop = createEvent.drop(target!, { dataTransfer });
    Object.defineProperty(drop, "clientX", { value: 100 });
    Object.defineProperty(drop, "clientY", { value: 81 });
    fireEvent(target!, drop);

    expect(dataTransfer.getData).toHaveBeenCalledWith("application/x-ai-canvas-shot-reference");

    expect(useCanvasStore.getState().shots[0]!.referenceRevisionIds).toEqual([
      ids.imageTwo,
      ids.imageOne,
    ]);
    expect(within(inspector).getByRole("status").textContent).toContain(
      "参考文件 safe-storyboard.png 现在位于第 1 位",
    );
  });

  it("lets external media dropped on a reference row bubble to its reference slot", () => {
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageOne,
      mediaKind: "image",
    });
    renderInspector();
    const referenceSlot = screen.getByRole("region", { name: "参考槽位" });
    const existingReference = within(referenceSlot).getByRole("listitem");

    fireEvent.drop(existingReference, {
      dataTransfer: mediaTransfer(ids.imageTwo, "image", ids.secondMediaNode),
    });

    expect(useCanvasStore.getState().shots[0]!.referenceRevisionIds).toEqual([
      ids.imageOne,
      ids.imageTwo,
    ]);
  });

  it("keeps an external media drop available when an internal reference drag fails final MIME verification", () => {
    const thirdImage = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    act(() => {
      useCanvasStore.getState().onNodesChange([
        {
          type: "replace",
          id: ids.secondMediaNode,
          item: {
            id: ids.secondMediaNode,
            type: "media_input",
            position: { x: 360, y: 0 },
            data: {
              kind: "media_input",
              artifactRevisionId: thirdImage,
              mediaKind: "image",
            },
          },
        },
      ]);
    });
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageOne,
      mediaKind: "image",
    });
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageTwo,
      mediaKind: "image",
    });
    renderInspector();
    const referenceSlot = screen.getByRole("region", { name: "参考槽位" });
    const [target, source] = within(referenceSlot).getAllByRole("listitem");
    const reorderTransfer = {
      dropEffect: "none",
      effectAllowed: "uninitialized",
      getData: vi.fn(() => ""),
      setData: vi.fn(),
      types: ["application/x-ai-canvas-shot-reference", "text/plain"],
    };

    fireEvent.dragStart(source!, { dataTransfer: reorderTransfer });
    fireEvent.drop(target!, {
      dataTransfer: mediaTransfer(thirdImage, "image", ids.secondMediaNode),
    });

    expect(useCanvasStore.getState().shots[0]!.referenceRevisionIds).toEqual([
      ids.imageOne,
      ids.imageTwo,
      thirdImage,
    ]);
  });

  it("clears reference drag feedback without mutation when the project becomes conflicted", async () => {
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageOne,
      mediaKind: "image",
    });
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageTwo,
      mediaKind: "image",
    });
    renderInspector();
    const referenceSlot = screen.getByRole("region", { name: "参考槽位" });
    const [target, source] = within(referenceSlot).getAllByRole("listitem");
    const values = new Map<string, string>();
    const dataTransfer = {
      dropEffect: "none",
      effectAllowed: "uninitialized",
      getData: vi.fn((type: string) => values.get(type) ?? ""),
      setData: vi.fn((type: string, value: string) => values.set(type, value)),
      types: ["application/x-ai-canvas-shot-reference", "text/plain"],
    };
    vi.spyOn(target!, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 320,
      height: 80,
    } as DOMRect);

    fireEvent.dragStart(source!, { dataTransfer });
    const dragOver = createEvent.dragOver(target!, { dataTransfer });
    Object.defineProperty(dragOver, "clientX", { value: 100 });
    Object.defineProperty(dragOver, "clientY", { value: 70 });
    fireEvent(target!, dragOver);
    expect(target!.className).toContain("is-drop-after");
    const beforeConflict = useCanvasStore.getState().shots[0]!.referenceRevisionIds;

    act(() => {
      const current = useCanvasStore.getState();
      current.markConflict(current.loadEpoch, "project-1", "server-revision");
    });

    await waitFor(() => {
      expect(source!.className).not.toContain("is-dragging");
      expect(target!.className).not.toContain("is-drop-after");
    });
    expect(useCanvasStore.getState().shots[0]!.referenceRevisionIds).toEqual(beforeConflict);
  });

  it("reserves page geometry for the MiniMap and B2 batch card without using z-index as the solution", () => {
    expect(shotWorkspaceStyles).toContain("--shot-batch-inline-reserve");
    expect(shotWorkspaceStyles).toContain("--shot-batch-block-reserve");
    expect(shotWorkspaceStyles).toContain(".canvas-workspace .react-flow__minimap");
    expect(shotWorkspaceStyles).toContain(
      "right: calc(var(--shot-batch-inline-reserve) + 2rem);",
    );
    expect(shotWorkspaceStyles).toContain(
      "bottom: calc(var(--shot-batch-block-reserve) + 16px);",
    );
    expect(shotWorkspaceStyles).toContain("@media (min-width: 1201px)");
    expect(shotWorkspaceStyles).toContain("@media (min-width: 721px) and (max-width: 1200px)");
    expect(shotWorkspaceStyles).toContain("@media (max-width: 720px)");
  });

  it("resets the 721-820 inspector and collapsed-track cascade before reserving the batch lane", () => {
    const mediumStart = shotWorkspaceStyles.indexOf(
      "@media (min-width: 721px) and (max-width: 1200px)",
    );
    const mobileStart = shotWorkspaceStyles.indexOf("@media (max-width: 720px)", mediumStart);
    const mediumStyles = shotWorkspaceStyles.slice(mediumStart, mobileStart);
    const tabletResetStart = shotWorkspaceStyles.indexOf(
      "@media (min-width: 721px) and (max-width: 820px)",
      mobileStart,
    );
    const tabletResetStyles = shotWorkspaceStyles.slice(tabletResetStart);

    expect(mediumStart).toBeGreaterThan(-1);
    expect(mobileStart).toBeGreaterThan(mediumStart);
    expect(mediumStyles).toContain("right: calc(var(--shot-batch-inline-reserve) + 2rem);");
    expect(tabletResetStart).toBeGreaterThan(mobileStart);
    expect(tabletResetStyles).toMatch(/\.shot-inspector\s*\{[^}]*bottom:\s*144px;/s);
    expect(tabletResetStyles).toMatch(
      /\.shot-inspector--collapsed\s*~\s*\.shot-track\s*\{[^}]*bottom:\s*16px;/s,
    );
  });

  it("starts both histories together and keeps repeated association events in their own list", async () => {
    const versions = deferred<ArtifactRevision[]>();
    const associations = deferred<{
      project_id: string;
      shot_id: string;
      slot: "video";
      anchor_graph_revision_id: string;
      anchor_graph_sequence: number;
      items: Array<Record<string, unknown>>;
      next_cursor: string | null;
    }>();
    api.listArtifactRevisions.mockReturnValue(versions.promise);
    api.listShotSlotAssociations.mockReturnValue(associations.promise);
    const target = {
      shotId: ids.shotOne,
      slot: "video" as const,
      revisionId: ids.videoOne,
      anchorRevisionId: "revision-1",
    };
    render(
      <ArtifactHistoryDrawer
        identity={{ projectId: "project-1", loadEpoch: useCanvasStore.getState().loadEpoch }}
        onClose={vi.fn()}
        target={target}
      />,
    );

    await waitFor(() => {
      expect(api.listArtifactRevisions).toHaveBeenCalledWith(
        ids.artifact,
        expect.any(AbortSignal),
      );
      expect(api.listShotSlotAssociations).toHaveBeenCalledWith(
        "project-1",
        ids.shotOne,
        "video",
        "revision-1",
        undefined,
        20,
        expect.any(AbortSignal),
      );
    });

    const repeatedRevision = revision(ids.videoTwo, {
      artifact_id: ids.otherArtifact,
      media_kind: "video",
      media_type: "video/webm",
      display_filename: "repeated-safe.webm",
      metadata: {
        kind: "video",
        width: 1280,
        height: 720,
        duration_seconds: 3,
        frames_per_second: 24,
      },
    });
    await act(async () => {
      versions.resolve([readyPreview(ids.videoOne).revision]);
      associations.resolve({
        project_id: "project-1",
        shot_id: ids.shotOne,
        slot: "video",
        anchor_graph_revision_id: "revision-1",
        anchor_graph_sequence: 9,
        items: [
          {
            graph_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            graph_revision_sequence: 9,
            event_ordinal: 0,
            occurred_at: "2026-07-30T09:00:00Z",
            action: "associated",
            reference_index: null,
            artifact_revision: repeatedRevision,
          },
          {
            graph_revision_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            graph_revision_sequence: 8,
            event_ordinal: 1,
            occurred_at: "2026-07-30T08:00:00Z",
            action: "associated",
            reference_index: null,
            artifact_revision: repeatedRevision,
          },
          {
            graph_revision_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            graph_revision_sequence: 7,
            event_ordinal: 0,
            occurred_at: "2026-07-30T07:00:00Z",
            action: "cleared",
            reference_index: null,
            artifact_revision: null,
          },
        ],
        next_cursor: "older-page",
      });
      await Promise.all([versions.promise, associations.promise]);
    });

    expect(screen.getByRole("heading", { name: "素材版本" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "关联记录" })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "关联记录列表" })).getAllByRole("listitem"))
      .toHaveLength(3);
    expect(screen.getAllByText("repeated-safe.webm")).toHaveLength(2);
    expect(screen.getByText("已清除，不能应用")).toBeTruthy();
    expect(document.body.textContent).toContain("图序列 9");
    expect(document.body.textContent).toContain("2026-07-30 09:00:00 UTC");
  });

  it("previews and explicitly applies a cross-artifact association without deduplicating its source", async () => {
    const crossArtifactRevision = revision(ids.videoTwo, {
      artifact_id: ids.otherArtifact,
      sequence: 6,
      media_kind: "video",
      media_type: "video/webm",
      display_filename: "cross-artifact.webm",
      metadata: {
        kind: "video",
        width: 1920,
        height: 1080,
        duration_seconds: 6,
        frames_per_second: 30,
      },
    });
    api.listArtifactRevisions.mockResolvedValue([readyPreview(ids.videoOne).revision]);
    api.listShotSlotAssociations.mockResolvedValue(
      associationPage("video", [
        associationEvent(
          "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          9,
          0,
          "associated",
          crossArtifactRevision,
        ),
      ]),
    );
    preview.useArtifactPreview.mockImplementation((revisionId) => {
      if (revisionId !== ids.videoTwo) return readyPreview(revisionId);
      return {
        phase: "ready" as const,
        revision: crossArtifactRevision,
        objectUrl: `blob:${ids.videoTwo}`,
        errorMessage: null,
        retryable: false,
        retry: vi.fn(),
      };
    });
    const onClose = vi.fn();
    render(
      <ArtifactHistoryDrawer
        identity={{ projectId: "project-1", loadEpoch: useCanvasStore.getState().loadEpoch }}
        onClose={onClose}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );

    const associations = await screen.findByRole("list", { name: "关联记录列表" });
    fireEvent.click(
      await within(associations).findByRole("button", { name: "cross-artifact.webm" }),
    );
    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(ids.videoOne);
    expect(
      screen.getAllByText("cross-artifact.webm").every((item) => item.closest("bdi") !== null),
    ).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "应用所选素材" }));
    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(ids.videoTwo);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("keeps loaded association pages while a failed older-page request is retried", async () => {
    const firstRevision = revision(ids.videoTwo, {
      artifact_id: ids.otherArtifact,
      media_kind: "video",
      media_type: "video/webm",
      display_filename: "first-event.webm",
      metadata: {
        kind: "video",
        width: 1280,
        height: 720,
        duration_seconds: 3,
        frames_per_second: 24,
      },
    });
    const olderRevision = revision("99999999-9999-4999-8999-999999999999", {
      artifact_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      media_kind: "video",
      media_type: "video/webm",
      display_filename: "older-event.webm",
      metadata: {
        kind: "video",
        width: 1280,
        height: 720,
        duration_seconds: 2,
        frames_per_second: 24,
      },
    });
    const failedPage = deferred<ReturnType<typeof associationPage>>();
    api.listArtifactRevisions.mockResolvedValue([readyPreview(ids.videoOne).revision]);
    api.listShotSlotAssociations
      .mockResolvedValueOnce(
        associationPage(
          "video",
          [associationEvent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", 9, 0, "associated", firstRevision)],
          "cursor-a",
        ),
      )
      .mockReturnValueOnce(failedPage.promise)
      .mockResolvedValueOnce(associationPage("video", [], "cursor-b"))
      .mockResolvedValueOnce(
        associationPage("video", [
          associationEvent("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", 8, 0, "associated", olderRevision),
        ]),
      );
    render(
      <ArtifactHistoryDrawer
        identity={{ projectId: "project-1", loadEpoch: useCanvasStore.getState().loadEpoch }}
        onClose={vi.fn()}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );

    const older = await screen.findByRole("button", { name: "加载更早记录" });
    fireEvent.click(older);
    expect(older).toHaveProperty("disabled", true);
    expect(older.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("first-event.webm")).toBeTruthy();
    await act(async () => {
      failedPage.reject(new Error("offline"));
      await failedPage.promise.catch(() => undefined);
    });

    expect(screen.getByRole("alert").textContent).toContain("加载更早关联记录失败");
    expect(screen.getByText("first-event.webm")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试加载更早记录" }));
    await waitFor(() =>
      expect(api.listShotSlotAssociations).toHaveBeenNthCalledWith(
        3,
        "project-1",
        ids.shotOne,
        "video",
        "revision-1",
        "cursor-a",
        20,
        expect.any(AbortSignal),
      ),
    );
    const afterEmptyPage = await screen.findByRole("button", { name: "加载更早记录" });
    fireEvent.click(afterEmptyPage);
    await screen.findByText("older-event.webm");
    expect(api.listShotSlotAssociations).toHaveBeenNthCalledWith(
      4,
      "project-1",
      ids.shotOne,
      "video",
      "revision-1",
      "cursor-b",
      20,
      expect.any(AbortSignal),
    );
    expect(screen.queryByRole("button", { name: "加载更早记录" })).toBeNull();
  });

  it.each([
    ["重复事件", "duplicate"],
    ["分页游标循环", "cursor-loop"],
  ] as const)("rejects %s without polluting an already loaded association page", async (message, failure) => {
    const firstRevision = revision(ids.videoTwo, {
      artifact_id: ids.otherArtifact,
      media_kind: "video",
      media_type: "video/webm",
      display_filename: "kept-event.webm",
      metadata: {
        kind: "video",
        width: 1280,
        height: 720,
        duration_seconds: 3,
        frames_per_second: 24,
      },
    });
    const firstEvent = associationEvent(
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      9,
      0,
      "associated",
      firstRevision,
    );
    const secondEvent = associationEvent(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      8,
      0,
      "associated",
      revision("99999999-9999-4999-8999-999999999999", {
        artifact_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "must-not-append.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 2,
          frames_per_second: 24,
        },
      }),
    );
    api.listArtifactRevisions.mockResolvedValue([readyPreview(ids.videoOne).revision]);
    api.listShotSlotAssociations
      .mockResolvedValueOnce(associationPage("video", [firstEvent], "cursor-a"))
      .mockResolvedValueOnce(
        associationPage(
          "video",
          failure === "duplicate" ? [firstEvent] : [secondEvent],
          failure === "cursor-loop" ? "cursor-a" : "cursor-b",
        ),
      );
    render(
      <ArtifactHistoryDrawer
        identity={{ projectId: "project-1", loadEpoch: useCanvasStore.getState().loadEpoch }}
        onClose={vi.fn()}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "加载更早记录" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain(message));
    const list = screen.getByRole("list", { name: "关联记录列表" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(within(list).getByText("kept-event.webm")).toBeTruthy();
    expect(within(list).queryByText("must-not-append.webm")).toBeNull();
  });

  it("aborts an in-flight association pagination request when the drawer unmounts", async () => {
    const olderPage = deferred<ReturnType<typeof associationPage>>();
    api.listArtifactRevisions.mockResolvedValue([readyPreview(ids.videoOne).revision]);
    api.listShotSlotAssociations
      .mockResolvedValueOnce(associationPage("video", [], "cursor-a"))
      .mockReturnValueOnce(olderPage.promise);
    const { unmount } = render(
      <ArtifactHistoryDrawer
        identity={{ projectId: "project-1", loadEpoch: useCanvasStore.getState().loadEpoch }}
        onClose={vi.fn()}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "加载更早记录" }));
    await waitFor(() => expect(api.listShotSlotAssociations).toHaveBeenCalledTimes(2));
    const paginationSignal = api.listShotSlotAssociations.mock.calls[1]?.[6] as AbortSignal;
    expect(paginationSignal.aborted).toBe(false);

    unmount();

    expect(paginationSignal.aborted).toBe(true);
  });

  it("aborts the old request generation and ignores its late response", async () => {
    const oldPage = deferred<ReturnType<typeof associationPage>>();
    api.listArtifactRevisions.mockResolvedValue([readyPreview(ids.videoOne).revision]);
    api.listShotSlotAssociations
      .mockReturnValueOnce(oldPage.promise)
      .mockResolvedValueOnce(associationPage("image", [
        associationEvent(
          "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          8,
          0,
          "associated",
          revision(ids.imageTwo, { display_filename: "new-target.png" }),
        ),
      ]));
    act(() => {
      useCanvasStore.getState().setShotSlot(ids.shotOne, "image", {
        revisionId: ids.imageOne,
        mediaKind: "image",
      });
    });
    const identity = { projectId: "project-1", loadEpoch: useCanvasStore.getState().loadEpoch };
    const { rerender } = render(
      <ArtifactHistoryDrawer
        identity={identity}
        onClose={vi.fn()}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );
    await waitFor(() => expect(api.listShotSlotAssociations).toHaveBeenCalledOnce());
    const oldSignal = api.listShotSlotAssociations.mock.calls[0]?.[6] as AbortSignal;

    rerender(
      <ArtifactHistoryDrawer
        identity={identity}
        onClose={vi.fn()}
        target={boundHistoryTarget("image", ids.imageOne)}
      />,
    );
    await screen.findByText("new-target.png");
    expect(oldSignal.aborted).toBe(true);
    await act(async () => {
      oldPage.resolve(associationPage("video", [
        associationEvent(
          "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          9,
          0,
          "associated",
          revision(ids.videoTwo, {
            media_kind: "video",
            media_type: "video/webm",
            display_filename: "late-old.webm",
            metadata: {
              kind: "video",
              width: 1280,
              height: 720,
              duration_seconds: 3,
              frames_per_second: 24,
            },
          }),
        ),
      ]));
      await oldPage.promise;
    });
    expect(screen.queryByText("late-old.webm")).toBeNull();
  });

  it("loads revision history only after opening and applies a selected compatible revision explicitly", async () => {
    const target = boundHistoryTarget("video", ids.videoOne);
    api.listArtifactRevisions.mockResolvedValue([
      revision(ids.videoOne, {
        media_kind: "video",
        media_type: "video/webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 4,
          frames_per_second: 24,
        },
      }),
      revision("99999999-9999-4999-8999-999999999999", {
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "next-safe.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 5,
          frames_per_second: 24,
        },
      }),
    ]);
    preview.useArtifactPreview.mockImplementation((revisionId) => {
      const item = revision(revisionId ?? ids.videoOne, {
        media_kind: "video",
        media_type: "video/webm",
        display_filename:
          revisionId === "99999999-9999-4999-8999-999999999999"
            ? "next-safe.webm"
            : "safe-motion.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 5,
          frames_per_second: 24,
        },
      });
      return {
        phase: "ready" as const,
        revision: item,
        objectUrl: `blob:${item.revision_id}`,
        errorMessage: null,
        retryable: false,
        retry: vi.fn(),
      };
    });
    const { rerender } = render(<ArtifactHistoryDrawer onClose={vi.fn()} target={null} />);
    expect(api.listArtifactRevisions).not.toHaveBeenCalled();

    rerender(<ArtifactHistoryDrawer onClose={vi.fn()} target={target} />);
    await waitFor(() =>
      expect(api.listArtifactRevisions).toHaveBeenCalledWith(
        ids.artifact,
        expect.any(AbortSignal),
      ),
    );
    await Promise.resolve();
    expect(api.listArtifactRevisions).toHaveBeenCalledOnce();
    expect(document.body.textContent).not.toContain("token");
    expect(document.body.textContent).not.toContain("/private/path");

    fireEvent.click(screen.getByRole("button", { name: "next-safe.webm" }));
    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(ids.videoOne);
    fireEvent.click(screen.getByRole("button", { name: "应用所选素材" }));
    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(
      "99999999-9999-4999-8999-999999999999",
    );
  });

  it("exposes revision history as a labelled non-modal drawer", async () => {
    api.listArtifactRevisions.mockResolvedValue([revision(ids.imageOne)]);
    render(
      <ArtifactHistoryDrawer
        onClose={vi.fn()}
        target={boundHistoryTarget("image", ids.imageOne)}
      />,
    );

    await waitFor(() => expect(screen.getByRole("region", { name: "素材版本历史" })).toBeTruthy());
    expect(screen.queryByRole("dialog", { name: "素材版本历史" })).toBeNull();
  });

  it("rejects an entire history payload when any revision belongs to another artifact or media kind", async () => {
    api.listArtifactRevisions.mockResolvedValue([
      revision(ids.videoOne, {
        media_kind: "video",
        media_type: "video/webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 4,
          frames_per_second: 24,
        },
      }),
      revision("99999999-9999-4999-8999-999999999999", {
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "next-safe.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 5,
          frames_per_second: 24,
        },
      }),
      revision("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", {
        artifact_id: ids.otherArtifact,
        display_filename: "mixed-artifact.png",
      }),
      revision("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", {
        media_kind: "image",
        display_filename: "wrong-kind.png",
      }),
    ]);
    render(
      <ArtifactHistoryDrawer
        onClose={vi.fn()}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );

    await waitFor(() =>
      expect(api.listArtifactRevisions).toHaveBeenCalledWith(
        ids.artifact,
        expect.any(AbortSignal),
      ),
    );

    expect(screen.getByRole("alert").textContent).toContain("素材历史完整性错误");
    expect(screen.queryByRole("button", { name: "next-safe.webm" })).toBeNull();
    expect(screen.getByRole("button", { name: "应用所选素材" }).hasAttribute("disabled")).toBe(true);
    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(ids.videoOne);
  });

  it("keeps a history preview local when closing and reopening before an explicit apply", async () => {
    const target = boundHistoryTarget("video", ids.videoOne);
    api.listArtifactRevisions.mockResolvedValue([
      revision(ids.videoOne, {
        media_kind: "video",
        media_type: "video/webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 4,
          frames_per_second: 24,
        },
      }),
      revision("99999999-9999-4999-8999-999999999999", {
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "next-safe.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 5,
          frames_per_second: 24,
        },
      }),
    ]);
    const onClose = vi.fn();
    const { rerender } = render(<ArtifactHistoryDrawer onClose={onClose} target={target} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "next-safe.webm" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "next-safe.webm" }));
    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(ids.videoOne);
    fireEvent.click(screen.getByRole("button", { name: "关闭素材版本历史" }));
    expect(onClose).toHaveBeenCalledOnce();
    rerender(<ArtifactHistoryDrawer onClose={onClose} target={null} />);
    rerender(<ArtifactHistoryDrawer onClose={onClose} target={target} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "next-safe.webm" })).toBeTruthy());

    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(ids.videoOne);
    expect(screen.getByRole("button", { name: "应用所选素材" }).hasAttribute("disabled")).toBe(true);
  });

  it("rejects an image or video history apply when that slot no longer has the expected revision", async () => {
    const target = boundHistoryTarget("video", ids.videoOne);
    api.listArtifactRevisions.mockResolvedValue([
      revision(ids.videoOne, {
        media_kind: "video",
        media_type: "video/webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 4,
          frames_per_second: 24,
        },
      }),
      revision("99999999-9999-4999-8999-999999999999", {
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "next-safe.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 5,
          frames_per_second: 24,
        },
      }),
    ]);
    const onClose = vi.fn();
    render(<ArtifactHistoryDrawer onClose={onClose} target={target} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "next-safe.webm" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "next-safe.webm" }));
    act(() => {
      useCanvasStore.getState().setShotSlot(ids.shotOne, "video", {
        revisionId: ids.videoTwo,
        mediaKind: "video",
      });
    });
    fireEvent.click(screen.getByRole("button", { name: "应用所选素材" }));

    expect(useCanvasStore.getState().shots[0]!.videoRevisionId).toBe(ids.videoTwo);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("rejects a history apply after the target Shot is deleted", async () => {
    const replacementRevisionId = "99999999-9999-4999-8999-999999999999";
    api.listArtifactRevisions.mockResolvedValue([
      readyPreview(ids.videoOne).revision,
      revision(replacementRevisionId, {
        media_kind: "video",
        media_type: "video/webm",
        display_filename: "orphaned-choice.webm",
        metadata: {
          kind: "video",
          width: 1280,
          height: 720,
          duration_seconds: 5,
          frames_per_second: 24,
        },
      }),
    ]);
    preview.useArtifactPreview.mockImplementation((revisionId) => {
      if (revisionId !== replacementRevisionId) return readyPreview(revisionId);
      return {
        ...readyPreview(ids.videoOne),
        revision: revision(replacementRevisionId, {
          media_kind: "video",
          media_type: "video/webm",
          display_filename: "orphaned-choice.webm",
          metadata: {
            kind: "video",
            width: 1280,
            height: 720,
            duration_seconds: 5,
            frames_per_second: 24,
          },
        }),
      };
    });
    const onClose = vi.fn();
    render(
      <ArtifactHistoryDrawer
        onClose={onClose}
        target={boundHistoryTarget("video", ids.videoOne)}
      />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "orphaned-choice.webm" }),
    );

    act(() => {
      useCanvasStore.getState().deleteShot(ids.shotOne);
    });

    const apply = screen.getByRole("button", { name: "应用所选素材" });
    expect(apply).toHaveProperty("disabled", true);
    fireEvent.click(apply);
    expect(useCanvasStore.getState().shots.some((shot) => shot.id === ids.shotOne)).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("disables a reference history revision already used by another reference on the same Shot", async () => {
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageOne,
      mediaKind: "image",
    });
    useCanvasStore.getState().addShotReference(ids.shotOne, {
      revisionId: ids.imageTwo,
      mediaKind: "image",
    });
    api.listArtifactRevisions.mockResolvedValue([
      revision(ids.imageOne, { sequence: 1 }),
      revision(ids.imageTwo, { sequence: 2, display_filename: "already-used.png" }),
    ]);
    const onClose = vi.fn();
    render(
      <ArtifactHistoryDrawer
        onClose={onClose}
        target={boundHistoryTarget("reference", ids.imageOne)}
      />,
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "already-used.png" })).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "already-used.png" }));
    const apply = screen.getByRole("button", { name: "应用所选素材" });
    expect(apply).toHaveProperty("disabled", true);
    fireEvent.click(apply);

    expect(useCanvasStore.getState().shots[0]!.referenceRevisionIds).toEqual([
      ids.imageOne,
      ids.imageTwo,
    ]);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("inserts a new shot after the selected shot and keeps source media when deletion is confirmed", () => {
    function DeleteHarness() {
      const [deletingShotId, setDeletingShotId] = useState<string | null>(null);
      return (
        <>
          <ShotTrack
            onRequestDelete={setDeletingShotId}
            selectedShotId={ids.shotOne}
          />
          <ShotDeleteDialog
            onClose={() => setDeletingShotId(null)}
            shotId={deletingShotId}
          />
        </>
      );
    }

    render(<DeleteHarness />);
    expandFloatingTrack(screen.getByTestId("shot-track"), 208);
    fireEvent.click(screen.getByRole("button", { name: "新建分镜" }));

    const ordered = [...useCanvasStore.getState().shots].sort((a, b) => a.order - b.order);
    expect(ordered[0]!.id).toBe(ids.shotOne);
    expect(ordered[2]!.id).toBe(ids.shotTwo);
    const createdNode = useCanvasStore
      .getState()
      .nodes.find((node) => node.data.kind === "shot" && node.data.shotId === ordered[1]!.id);
    expect(createdNode?.position).toEqual({ x: 420, y: 700 });

    fireEvent.click(screen.getByRole("button", { name: "删除分镜 1" }));
    expect(screen.getByRole("dialog", { name: "删除分镜" }).textContent).toContain(
      "媒体节点和本地素材会保留"
    );
    fireEvent.click(screen.getByRole("button", { name: "确认删除分镜" }));
    expect(useCanvasStore.getState().shots.some((shot) => shot.id === ids.shotOne)).toBe(false);
    expect(
      useCanvasStore.getState().nodes.some((node) => node.id === ids.mediaNode),
    ).toBe(true);
  });

  it("keeps consecutively created Shots clear of media and existing Shots", () => {
    useCanvasStore.getState().load({
      projectId: "project-1",
      revisionId: "revision-consecutive-shot-placement",
      viewport: { x: 0, y: 0, zoom: 1 },
      nodes: [
        {
          id: "anchor-media",
          type: "media_input",
          position: { x: 240, y: 180 },
          data: {
            kind: "media_input",
            artifactRevisionId: null,
            mediaKind: null,
          },
        },
        {
          id: "right-side-blocker",
          type: "note",
          position: { x: 560, y: 180 },
          data: { kind: "note", text: "block right" },
        },
        {
          id: "selected-offset-media",
          type: "media_input",
          position: { x: 560, y: 680 },
          data: {
            kind: "media_input",
            artifactRevisionId: null,
            mediaKind: null,
          },
        },
      ],
      edges: [],
      shots: [],
    });
    function LiveSelectionTrack() {
      const selectedShotId = useCanvasStore(
        (state) => state.selectedShotId,
      );
      return (
        <ShotTrack
          onRequestDelete={vi.fn()}
          selectedShotId={selectedShotId}
        />
      );
    }
    render(<LiveSelectionTrack />);
    expandFloatingTrack(screen.getByTestId("shot-track"), 209);

    fireEvent.click(screen.getByRole("button", { name: "新建分镜" }));
    fireEvent.click(screen.getByRole("button", { name: "新建分镜" }));

    const bounds = useCanvasStore.getState().nodes.map((node) => {
      const size =
        node.data.kind === "shot"
          ? { width: 280, height: 380 }
          : node.data.kind === "media_input"
            ? { width: 312, height: 360 }
            : { width: 248, height: 180 };
      return {
        id: node.id,
        kind: node.data.kind,
        x: node.position.x,
        y: node.position.y,
        ...size,
      };
    });
    const createdShots = bounds.filter((node) => node.kind === "shot");
    expect(createdShots).toHaveLength(2);
    for (const shot of createdShots) {
      for (const obstacle of bounds) {
        if (shot.id === obstacle.id) continue;
        expect(
          shot.x < obstacle.x + obstacle.width &&
            shot.x + shot.width > obstacle.x &&
            shot.y < obstacle.y + obstacle.height &&
            shot.y + shot.height > obstacle.y,
          `${shot.id} must not overlap ${obstacle.id}`,
        ).toBe(false);
      }
    }
  });

  it("reserves the full unmeasured Shot card height when finding a vacant slot", () => {
    useCanvasStore.getState().load({
      projectId: "project-1",
      revisionId: "revision-unmeasured-shot-height",
      viewport: { x: 0, y: 0, zoom: 1 },
      nodes: [
        {
          id: "existing-shot-node",
          type: "shot",
          position: { x: 240, y: 180 },
          data: { kind: "shot", shotId: ids.shotOne },
        },
        {
          id: "right-side-blocker",
          type: "note",
          position: { x: 560, y: 180 },
          data: { kind: "note", text: "blocker" },
        },
      ],
      edges: [],
      shots: [
        {
          id: ids.shotOne,
          order: 0,
          imageRevisionId: ids.imageOne,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
    });
    render(<ShotTrack onRequestDelete={vi.fn()} selectedShotId={null} />);
    expandFloatingTrack(screen.getByTestId("shot-track"), 210);

    fireEvent.click(screen.getByRole("button", { name: "新建分镜" }));

    const createdNode = useCanvasStore
      .getState()
      .nodes.find(
        (node) =>
          node.data.kind === "shot" &&
          node.data.shotId !== ids.shotOne,
      );
    expect(createdNode?.position).toEqual({ x: 240, y: 600 });
  });

  it("places unselected new shots in deterministic vacant slots around existing canvas nodes", () => {
    const current = useCanvasStore.getState();
    current.load({
      projectId: "project-1",
      revisionId: "revision-vacant-slots",
      viewport: { x: 100, y: 80, zoom: 2 },
      nodes: current.nodes.map((node) => {
        if (node.data.kind !== "shot") return node;
        return {
          ...node,
          position:
            node.data.shotId === ids.shotOne
              ? { x: 70, y: 50 }
              : { x: 390, y: 50 },
          measured: { width: 280, height: 180 },
        };
      }),
      edges: current.edges,
      shots: current.shots,
    });
    useCanvasStore.getState().selectShot(null);
    render(<ShotTrack onRequestDelete={vi.fn()} selectedShotId={null} />);
    expandFloatingTrack(screen.getByTestId("shot-track"), 211);

    fireEvent.click(screen.getByRole("button", { name: "新建分镜" }));
    fireEvent.click(screen.getByRole("button", { name: "新建分镜" }));

    const createdNodes = useCanvasStore
      .getState()
      .nodes.filter(
        (node) =>
          node.data.kind === "shot" &&
          node.data.shotId !== ids.shotOne &&
          node.data.shotId !== ids.shotTwo,
      );
    expect(createdNodes).toHaveLength(2);
    expect(createdNodes.map((node) => node.position)).toEqual([
      { x: 70, y: 470 },
      { x: 390, y: 470 },
    ]);
  });

  it("uses conservative kind-aware bounds for unselected media and text obstacles", () => {
    useCanvasStore.getState().load({
      projectId: "project-1",
      revisionId: "revision-all-node-obstacles",
      viewport: { x: 0, y: 0, zoom: 1 },
      nodes: [
        {
          id: "media-obstacle",
          type: "media_input",
          position: { x: 240, y: 180 },
          data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
        },
        {
          id: "text-obstacle",
          type: "text_source",
          position: { x: 560, y: 180 },
          data: { kind: "text_source", text: "source" },
        },
        {
          id: "prompt-obstacle",
          type: "prompt",
          position: { x: 240, y: 400 },
          data: {
            kind: "prompt",
            document: documentFromText(
              "prompt",
              "11111111-1111-4111-8111-111111111111",
            ),
          },
        },
        {
          id: "note-obstacle",
          type: "note",
          position: { x: -80, y: 180 },
          data: { kind: "note", text: "note" },
        },
      ],
      edges: [],
      shots: [],
    });
    render(<ShotTrack onRequestDelete={vi.fn()} selectedShotId={null} />);
    expandFloatingTrack(screen.getByTestId("shot-track"), 212);

    fireEvent.click(screen.getByRole("button", { name: "新建分镜" }));

    const createdNode = useCanvasStore
      .getState()
      .nodes.find((node) => node.data.kind === "shot");
    expect(createdNode?.position).toEqual({ x: 240, y: 600 });
  });

  it("focuses, traps keyboard navigation, and restores focus for Shot deletion", async () => {
    function DeleteA11yHarness() {
      const [deletingShotId, setDeletingShotId] = useState<string | null>(null);
      const triggerRef = useRef<HTMLButtonElement>(null);
      const fallbackRef = useRef<HTMLDivElement>(null);
      return (
        <>
          <button onClick={() => setDeletingShotId(ids.shotOne)} ref={triggerRef} type="button">
            打开删除分镜
          </button>
          <div data-testid="delete-focus-fallback" ref={fallbackRef} tabIndex={-1} />
          <ShotDeleteDialog
            fallbackFocusRef={fallbackRef}
            getTriggerElement={() => triggerRef.current}
            onClose={() => setDeletingShotId(null)}
            shotId={deletingShotId}
          />
        </>
      );
    }

    render(<DeleteA11yHarness />);
    const trigger = screen.getByRole("button", { name: "打开删除分镜" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "删除分镜" });
    const cancel = screen.getByRole("button", { name: "取消" });
    const confirm = screen.getByRole("button", { name: "确认删除分镜" });

    expect(document.activeElement).toBe(cancel);
    confirm.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(cancel);
    cancel.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirm);

    fireEvent.keyDown(dialog, { key: "Escape" });
    await act(async () => undefined);
    expect(screen.queryByRole("dialog", { name: "删除分镜" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("settles deletion when the store removes the Shot but reports a null return value", async () => {
    const onClose = vi.fn();
    function DeleteNullReturnHarness() {
      const [deletingShotId, setDeletingShotId] = useState<string | null>(null);
      const triggerRef = useRef<HTMLButtonElement>(null);
      return (
        <>
          <button onClick={() => setDeletingShotId(ids.shotOne)} ref={triggerRef} type="button">
            打开空返回删除
          </button>
          <ShotDeleteDialog
            getTriggerElement={() => triggerRef.current}
            onClose={() => {
              onClose();
              setDeletingShotId(null);
            }}
            shotId={deletingShotId}
          />
        </>
      );
    }

    const originalDeleteShot = useCanvasStore.getState().deleteShot;
    vi.spyOn(useCanvasStore.getState(), "deleteShot").mockImplementation((shotId) => {
      originalDeleteShot(shotId);
      return null;
    });
    render(<DeleteNullReturnHarness />);
    const trigger = screen.getByRole("button", { name: "打开空返回删除" });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "确认删除分镜" }));
    await act(async () => undefined);

    expect(useCanvasStore.getState().shots.some((shot) => shot.id === ids.shotOne)).toBe(false);
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog", { name: "删除分镜" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("disables Shot mutations while the canvas is in conflict", async () => {
    act(() => {
      const state = useCanvasStore.getState();
      state.markConflict(state.loadEpoch, "project-1", "server-revision");
    });
    api.listArtifactRevisions.mockResolvedValue([revision(ids.imageOne)]);
    render(
      <>
        <ShotInspector selectedShotId={ids.shotOne} onOpenHistory={vi.fn()} />
        <ShotTrack onRequestDelete={vi.fn()} selectedShotId={ids.shotOne} />
        <ArtifactHistoryDrawer
          onClose={vi.fn()}
          target={boundHistoryTarget("reference", ids.imageOne)}
        />
      </>,
    );
    await waitFor(() => expect(api.listArtifactRevisions).toHaveBeenCalled());
    expandFloatingTrack(screen.getByTestId("shot-track"), 213);

    expect(screen.getByRole("button", { name: "关联图片" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "新建分镜" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "删除分镜 1" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "应用所选素材" }).hasAttribute("disabled")).toBe(true);
  });
});
