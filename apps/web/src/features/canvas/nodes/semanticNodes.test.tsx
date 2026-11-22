import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Node, NodeProps } from "@xyflow/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ArtifactImportConflictError,
  type ArtifactImportDeclaration,
  type ArtifactImportSession,
  type ArtifactRevision,
} from "../../../api/client";
import { useCanvasStore } from "../canvasStore";
import type {
  CanvasNode,
  MediaInputNodeData,
  ShotNodeData,
  TextNodeKind,
} from "../canvasTypes";
import {
  MediaIngressContext,
  type MediaIngressRuntimePort,
} from "../mediaIngress/mediaIngressContext";
import type {
  MediaIngressController,
  MediaIngressSnapshot,
} from "../mediaIngress/mediaIngressTypes";
import { MediaTargetLocks } from "../mediaIngress/mediaTargetLocks";
import {
  ArtifactPreviewProvider,
  createArtifactPreviewCache,
  type ArtifactPreviewCache,
  type ArtifactPreviewDependencies,
} from "../useArtifactPreview";
import { ShotThumbnail } from "../components/ShotThumbnail";
import { ShotTrack } from "../components/ShotTrack";
import {
  useFloatingPanel,
  type FloatingPanelController,
} from "../components/useFloatingPanel";
import {
  createMediaImportRuntimeDependencies,
  MediaImportProvider,
  type MediaImportRuntimeDependencies,
} from "../useMediaImport";
import * as shotMediaTransfer from "../shotMediaTransfer";
import { stableShotCode } from "../components/shotIdentity";
import { documentFromText } from "../../prompting/promptDocument";
import { MediaInputNode } from "./MediaInputNode";
import { NoteNode } from "./NoteNode";
import { PromptNode } from "./PromptNode";
import { ShotNode } from "./ShotNode";
import { TextSourceNode } from "./TextSourceNode";

const artifactApi = vi.hoisted(() => ({
  createArtifactImport: vi.fn(),
  getArtifactImport: vi.fn(),
  uploadArtifactContent: vi.fn(),
}));

vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  ...artifactApi,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function installControlledIntersectionObserver() {
  const instances: Array<{
    callback: IntersectionObserverCallback;
    observed: Set<Element>;
    options?: IntersectionObserverInit;
  }> = [];
  const observe = vi.fn<(element: Element) => void>();
  const unobserve = vi.fn<(element: Element) => void>();
  const disconnect = vi.fn<() => void>();

  class ControlledIntersectionObserver {
    private readonly record: (typeof instances)[number];

    constructor(
      callback: IntersectionObserverCallback,
      options?: IntersectionObserverInit,
    ) {
      this.record = { callback, observed: new Set(), options };
      instances.push(this.record);
    }

    observe(element: Element) {
      this.record.observed.add(element);
      observe(element);
    }

    unobserve(element: Element) {
      this.record.observed.delete(element);
      unobserve(element);
    }

    disconnect() {
      this.record.observed.clear();
      disconnect();
    }

    takeRecords() {
      return [];
    }

    readonly root = null;
    readonly rootMargin = "160px";
    readonly thresholds = [0];
  }

  vi.stubGlobal("IntersectionObserver", ControlledIntersectionObserver);
  return {
    disconnect,
    instances,
    observe,
    setIntersecting(element: Element, isIntersecting: boolean) {
      const instance = instances.find((candidate) =>
        candidate.observed.has(element),
      );
      if (!instance) throw new Error("Element is not observed");
      const rect = element.getBoundingClientRect();
      act(() => {
        instance.callback(
          [
            {
              boundingClientRect: rect,
              intersectionRatio: isIntersecting ? 1 : 0,
              intersectionRect: isIntersecting ? rect : new DOMRectReadOnly(),
              isIntersecting,
              rootBounds: null,
              target: element,
              time: 0,
            },
          ],
          instances.at(0) as unknown as IntersectionObserver,
        );
      });
    },
    unobserve,
  };
}

function installControlledAnimationFrame() {
  let nextFrameId = 1;
  const callbacks = new Map<number, FrameRequestCallback>();
  vi.stubGlobal(
    "requestAnimationFrame",
    vi.fn((callback: FrameRequestCallback) => {
      const frameId = nextFrameId;
      nextFrameId += 1;
      callbacks.set(frameId, callback);
      return frameId;
    }),
  );
  vi.stubGlobal(
    "cancelAnimationFrame",
    vi.fn((frameId: number) => callbacks.delete(frameId)),
  );
  return {
    flush() {
      act(() => {
        while (callbacks.size > 0) {
          const pending = [...callbacks.values()];
          callbacks.clear();
          for (const callback of pending) callback(0);
        }
      });
    },
    pendingCount() {
      return callbacks.size;
    },
  };
}

function StaticShotTrack({
  selectedShotId = null,
}: {
  selectedShotId?: string | null;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const floatingPanel: FloatingPanelController = {
    active: false,
    customized: false,
    directMoveHandleProps: {},
    directResizeHandleProps: () => ({}),
    expand: () => undefined,
    moveHandleProps: {},
    panelRef,
    rect: null,
    reset: () => undefined,
    resizeHandleProps: {},
    style: undefined,
  };
  return (
    <ShotTrack
      floatingPanel={floatingPanel}
      onRequestDelete={() => undefined}
      selectedShotId={selectedShotId}
    />
  );
}

function FloatingShotTrack() {
  const floatingPanel = useFloatingPanel({
    panelId: "shot-track",
    projectId: "project-1",
  });
  return (
    <ShotTrack
      floatingPanel={floatingPanel}
      onRequestDelete={() => undefined}
      selectedShotId={null}
    />
  );
}

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Handle: ({ type, id, ...props }: { type: string; id: string; [key: string]: unknown }) => (
      <span {...props} data-handle-id={id} data-handle-type={type} />
    ),
    Position: { Left: "left", Right: "right" },
  };
});

function canvasNode(id: string, kind: TextNodeKind, text = ""): CanvasNode {
  if (kind === "prompt") {
    return {
      id,
      type: "prompt",
      position: { x: 0, y: 0 },
      data: { kind: "prompt", document: documentFromText(text, id) },
    };
  }
  return {
    id,
    type: kind,
    position: { x: 0, y: 0 },
    data: { kind, text },
  } as CanvasNode;
}

function loadNode(node: CanvasNode): void {
  useCanvasStore.getState().load({
    projectId: "project-1",
    nodes: [node],
    edges: [],
    shots: [],
    revisionId: "revision-1",
  });
}

function propsFor(node: CanvasNode): NodeProps<CanvasNode> {
  return { id: node.id, data: node.data } as NodeProps<CanvasNode>;
}

type MediaCanvasNode = Node<MediaInputNodeData, "media_input">;
type ShotCanvasNode = Node<ShotNodeData, "shot">;

function mediaPropsFor(node: CanvasNode): NodeProps<MediaCanvasNode> {
  return propsFor(node) as unknown as NodeProps<MediaCanvasNode>;
}

function shotPropsFor(node: CanvasNode): NodeProps<ShotCanvasNode> {
  return propsFor(node) as unknown as NodeProps<ShotCanvasNode>;
}

function mediaDataAt(index: number) {
  const data = useCanvasStore.getState().nodes[index]?.data;
  if (!data || data.kind !== "media_input") {
    throw new Error(`Expected media node at index ${index}`);
  }
  return data;
}

const previewCaches: ArtifactPreviewCache[] = [];

function artifactRevision(kind: "image" | "video"): ArtifactRevision {
  return {
    revision_id: `revision-${kind}`,
    artifact_id: "artifact-1",
    sequence: 1,
    media_kind: kind,
    media_type: kind === "image" ? "image/png" : "video/webm",
    byte_size: 3,
    display_filename: kind === "image" ? "安全的画面.png" : "安全的镜头.webm",
    metadata:
      kind === "image"
        ? { kind: "image", width: 1920, height: 1080 }
        : {
            kind: "video",
            width: 1280,
            height: 720,
            duration_seconds: 2.5,
            frames_per_second: 24,
          },
    source_kind: "import",
    source_project_id: "project-1",
    origin_project_id: "project-1",
    created_at: "2026-07-21T01:02:03Z",
  };
}

function importSession(
  declaration: ArtifactImportDeclaration,
  state: ArtifactImportSession["state"],
): ArtifactImportSession {
  return {
    import_id: "import-1",
    source_project_id: "project-1",
    client_import_id: declaration.clientImportId,
    replace_revision_id: declaration.replaceRevisionId,
    requested_media_kind: declaration.requestedMediaKind,
    original_filename: declaration.filename,
    declared_content_type: declaration.declaredContentType,
    expected_byte_size: declaration.expectedByteSize,
    state,
    received_byte_size: 0,
    failure_code: null,
    created_at: "2026-07-21T01:02:03Z",
    updated_at: "2026-07-21T01:02:04Z",
    artifact_revision: null,
  };
}

function renderMediaNode(
  node: CanvasNode,
  summary = artifactRevision("image"),
  onRequestReload: () => void = () => undefined,
  previewOverrides: Partial<ArtifactPreviewDependencies> = {},
  importDependencies?: MediaImportRuntimeDependencies,
  ingressRuntime?: MediaIngressRuntimePort,
) {
  const createObjectURL = vi.fn().mockReturnValue(`blob:${summary.media_kind}-preview`);
  const revokeObjectURL = vi.fn();
  const cache = createArtifactPreviewCache({
    getArtifactRevision:
      previewOverrides.getArtifactRevision ?? vi.fn().mockResolvedValue(summary),
    getArtifactRevisionContent:
      previewOverrides.getArtifactRevisionContent ??
      vi.fn().mockResolvedValue(new Blob(["raw"], { type: summary.media_type })),
    createObjectURL,
    revokeObjectURL,
    createMediaStreamUrl:
      previewOverrides.createMediaStreamUrl ??
      vi.fn().mockImplementation(async (revisionId: string) => `/api/v1/media/revisions/${revisionId}`),
  });
  previewCaches.push(cache);
  const mediaNode = (
    <ArtifactPreviewProvider cache={cache}>
      <MediaInputNode {...mediaPropsFor(node)} />
    </ArtifactPreviewProvider>
  );
  render(
    ingressRuntime ? (
      <MediaIngressContext.Provider value={ingressRuntime}>
        {mediaNode}
      </MediaIngressContext.Provider>
    ) : (
      <MediaImportProvider
        dependencies={importDependencies}
        onRequestReload={onRequestReload}
        projectId="project-1"
      >
        {mediaNode}
      </MediaImportProvider>
    ),
  );
  return { createObjectURL, revokeObjectURL };
}

describe("semantic canvas nodes", () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    for (const mock of Object.values(artifactApi)) mock.mockReset();
  });
  afterEach(() => {
    cleanup();
    for (const cache of previewCaches.splice(0)) cache.dispose();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("derives the exact stable Shot code from normalized UUID ASCII", () => {
    const canonical = "11111111-1111-4111-8111-111111111111";
    expect(stableShotCode(canonical)).toBe("S-UR72CEYHRT");
    expect(stableShotCode(canonical.toUpperCase())).toBe("S-UR72CEYHRT");
    expect(stableShotCode(canonical.replaceAll("-", ""))).toBe(
      "S-UR72CEYHRT",
    );
  });

  it("renders an explicit empty Shot thumbnail without starting a preview request", () => {
    const getArtifactRevision = vi.fn();
    const getArtifactRevisionContent = vi.fn();
    const cache = createArtifactPreviewCache({
      getArtifactRevision,
      getArtifactRevisionContent,
      createObjectURL: vi.fn(),
      revokeObjectURL: vi.fn(),
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <ShotThumbnail
          compact={false}
          id="shot-thumbnail-empty"
          revisionId={null}
          shotLabel="分镜 01"
        />
      </ArtifactPreviewProvider>,
    );

    expect(
      screen.getByText("暂无分镜图").closest(".shot-thumbnail")?.tagName,
    ).toBe("SPAN");
    expect(
      screen.getByText("暂无分镜图").closest(".shot-thumbnail")?.id,
    ).toBe("shot-thumbnail-empty");
    expect(screen.queryByRole("img")).toBeNull();
    expect(getArtifactRevision).not.toHaveBeenCalled();
    expect(getArtifactRevisionContent).not.toHaveBeenCalled();
  });

  it("does not request unique offscreen Shot previews until each thumbnail nears the viewport", async () => {
    const visibility = installControlledIntersectionObserver();
    const revisionIds = [
      "11111111-1111-4111-8111-111111111111",
      "22222222-2222-4222-8222-222222222222",
      "33333333-3333-4333-8333-333333333333",
    ];
    const getArtifactRevision = vi.fn().mockImplementation(
      async (revisionId: string) => ({
        ...artifactRevision("image"),
        revision_id: revisionId,
      }),
    );
    const getArtifactRevisionContent = vi
      .fn()
      .mockResolvedValue(new Blob(["raw"], { type: "image/png" }));
    const cache = createArtifactPreviewCache({
      getArtifactRevision,
      getArtifactRevisionContent,
      createObjectURL: vi.fn().mockReturnValue("blob:near-shot"),
      revokeObjectURL: vi.fn(),
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        {revisionIds.map((revisionId, index) => (
          <ShotThumbnail
            compact={index !== 0}
            key={revisionId}
            revisionId={revisionId}
            shotLabel={`分镜 0${index + 1}`}
          />
        ))}
      </ArtifactPreviewProvider>,
    );

    expect(getArtifactRevision).not.toHaveBeenCalled();
    expect(getArtifactRevisionContent).not.toHaveBeenCalled();

    const thumbnails = document.querySelectorAll(".shot-thumbnail");
    expect(thumbnails).toHaveLength(3);
    visibility.setIntersecting(thumbnails[1], true);

    await waitFor(() =>
      expect(getArtifactRevision).toHaveBeenCalledWith(
        revisionIds[1],
        expect.any(AbortSignal),
      ),
    );
    expect(getArtifactRevision).toHaveBeenCalledOnce();
    expect(getArtifactRevisionContent).toHaveBeenCalledOnce();
  });

  it("shares one visible Shot preview and revokes it only after the final thumbnail leaves", async () => {
    const visibility = installControlledIntersectionObserver();
    const revisionId = "11111111-1111-4111-8111-111111111111";
    const getArtifactRevision = vi.fn().mockResolvedValue({
      ...artifactRevision("image"),
      revision_id: revisionId,
    });
    const getArtifactRevisionContent = vi
      .fn()
      .mockResolvedValue(new Blob(["raw"], { type: "image/png" }));
    const createObjectURL = vi.fn().mockReturnValue("blob:shared-visible-shot");
    const revokeObjectURL = vi.fn();
    const cache = createArtifactPreviewCache({
      getArtifactRevision,
      getArtifactRevisionContent,
      createObjectURL,
      revokeObjectURL,
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <ShotThumbnail
          compact={false}
          revisionId={revisionId}
          shotLabel="分镜 01"
        />
        <ShotThumbnail compact revisionId={revisionId} shotLabel="分镜 01" />
      </ArtifactPreviewProvider>,
    );

    expect(getArtifactRevision).not.toHaveBeenCalled();
    const thumbnails = document.querySelectorAll(".shot-thumbnail");
    expect(thumbnails).toHaveLength(2);
    expect(visibility.instances).toHaveLength(1);
    expect(visibility.instances[0]?.options).toEqual({
      root: null,
      rootMargin: "160px",
      threshold: 0,
    });

    visibility.setIntersecting(thumbnails[0], true);
    visibility.setIntersecting(thumbnails[1], true);
    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(getArtifactRevision).toHaveBeenCalledOnce();
    expect(getArtifactRevisionContent).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();

    visibility.setIntersecting(thumbnails[0], false);
    expect(revokeObjectURL).not.toHaveBeenCalled();

    visibility.setIntersecting(thumbnails[1], false);
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith(
        "blob:shared-visible-shot",
      ),
    );
    expect(revokeObjectURL).toHaveBeenCalledOnce();
  });

  it("uses a clipped geometry fallback without eagerly loading every thumbnail when IntersectionObserver is unavailable", async () => {
    vi.stubGlobal("innerWidth", 1024);
    vi.stubGlobal("innerHeight", 768);
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    let nearTop = 850;
    let clippedTop = 200;
    const originalRect = HTMLElement.prototype.getBoundingClientRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function () {
        if (this.getAttribute("data-visibility") === "clip-root") {
          return new DOMRect(0, 0, 200, 100);
        }
        if (this.classList.contains("shot-thumbnail")) {
          const region = this.closest("[data-visibility]") as HTMLElement | null;
          const top =
            region?.dataset.visibility === "near" ? nearTop : clippedTop;
          return new DOMRect(0, top, 64, 42);
        }
        return originalRect.call(this);
      },
    );

    const revisionIds = [
      "11111111-1111-4111-8111-111111111111",
      "22222222-2222-4222-8222-222222222222",
    ];
    const getArtifactRevision = vi.fn().mockImplementation(
      async (revisionId: string) => ({
        ...artifactRevision("image"),
        revision_id: revisionId,
      }),
    );
    const getArtifactRevisionContent = vi
      .fn()
      .mockResolvedValue(new Blob(["raw"], { type: "image/png" }));
    const createObjectURL = vi
      .fn()
      .mockReturnValueOnce("blob:fallback-near")
      .mockReturnValueOnce("blob:fallback-scrolled");
    const revokeObjectURL = vi.fn();
    const cache = createArtifactPreviewCache({
      getArtifactRevision,
      getArtifactRevisionContent,
      createObjectURL,
      revokeObjectURL,
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <div data-visibility="near">
          <ShotThumbnail
            compact
            revisionId={revisionIds[0]}
            shotLabel="分镜 01"
          />
        </div>
        <div data-visibility="clip-root" style={{ overflow: "auto" }}>
          <div data-visibility="clipped">
            <ShotThumbnail
              compact
              revisionId={revisionIds[1]}
              shotLabel="分镜 02"
            />
          </div>
        </div>
      </ArtifactPreviewProvider>,
    );

    const clipRoot = document.querySelector<HTMLElement>(
      '[data-visibility="clip-root"]',
    );
    const clippedThumbnail = clipRoot?.querySelector<HTMLElement>(
      ".shot-thumbnail",
    );
    expect(clipRoot).not.toBeNull();
    expect(clippedThumbnail).not.toBeNull();
    expect(getComputedStyle(clipRoot as HTMLElement).overflow).toBe("auto");
    expect(clipRoot?.getBoundingClientRect().bottom).toBe(100);
    expect(clippedThumbnail?.getBoundingClientRect().top).toBe(200);

    await waitFor(() => expect(getArtifactRevision).toHaveBeenCalledOnce());
    expect(getArtifactRevision).toHaveBeenCalledWith(
      revisionIds[0],
      expect.any(AbortSignal),
    );
    expect(getArtifactRevision).not.toHaveBeenCalledWith(
      revisionIds[1],
      expect.anything(),
    );

    nearTop = 1200;
    clippedTop = 40;
    act(() => window.dispatchEvent(new Event("scroll")));

    await waitFor(() => expect(getArtifactRevision).toHaveBeenCalledTimes(2));
    expect(getArtifactRevision).toHaveBeenLastCalledWith(
      revisionIds[1],
      expect.any(AbortSignal),
    );
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:fallback-near"),
    );
  });

  it("remeasures no-IntersectionObserver previews after a keyed Track reorder changes clipping", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    const frames = installControlledAnimationFrame();
    const originalRect = HTMLElement.prototype.getBoundingClientRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function () {
        if (this.matches(".shot-track ol")) {
          return new DOMRect(0, 0, 100, 100);
        }
        if (this.classList.contains("shot-thumbnail")) {
          const item = this.closest("li");
          const list = item?.parentElement;
          const index = item && list ? [...list.children].indexOf(item) : -1;
          return new DOMRect(index * 400, 10, 64, 42);
        }
        return originalRect.call(this);
      },
    );

    const firstRevisionId = "11111111-1111-4111-8111-111111111111";
    const secondRevisionId = "22222222-2222-4222-8222-222222222222";
    useCanvasStore.getState().load({
      projectId: "project-1",
      revisionId: "revision-track-fallback-reorder",
      nodes: [],
      edges: [],
      shots: [
        {
          id: "33333333-3333-4333-8333-333333333333",
          order: 0,
          imageRevisionId: firstRevisionId,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
        {
          id: "44444444-4444-4444-8444-444444444444",
          order: 1,
          imageRevisionId: secondRevisionId,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
    });
    const getArtifactRevision = vi.fn().mockImplementation(
      async (revisionId: string) => ({
        ...artifactRevision("image"),
        revision_id: revisionId,
      }),
    );
    const getArtifactRevisionContent = vi
      .fn()
      .mockResolvedValue(new Blob(["raw"], { type: "image/png" }));
    const createObjectURL = vi
      .fn()
      .mockReturnValueOnce("blob:fallback-first")
      .mockReturnValueOnce("blob:fallback-second");
    const revokeObjectURL = vi.fn();
    const cache = createArtifactPreviewCache({
      getArtifactRevision,
      getArtifactRevisionContent,
      createObjectURL,
      revokeObjectURL,
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <StaticShotTrack />
      </ArtifactPreviewProvider>,
    );
    frames.flush();
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());
    frames.flush();
    expect(frames.pendingCount()).toBe(0);
    expect(getArtifactRevision).toHaveBeenCalledWith(
      firstRevisionId,
      expect.any(AbortSignal),
    );
    expect(getArtifactRevision).not.toHaveBeenCalledWith(
      secondRevisionId,
      expect.anything(),
    );
    const before = Array.from(
      document.querySelectorAll<HTMLElement>(".shot-thumbnail"),
    );

    fireEvent.click(screen.getByRole("button", { name: "上移分镜 2" }));
    const after = Array.from(
      document.querySelectorAll<HTMLElement>(".shot-thumbnail"),
    );
    expect(after).toEqual([before[1], before[0]]);
    frames.flush();

    await waitFor(() => expect(getArtifactRevision).toHaveBeenCalledTimes(2));
    expect(getArtifactRevision).toHaveBeenLastCalledWith(
      secondRevisionId,
      expect.any(AbortSignal),
    );
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:fallback-first"),
    );
  });

  it("remeasures no-IntersectionObserver previews after pointer Track resize changes clipping", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    vi.stubGlobal("innerWidth", 1200);
    vi.stubGlobal("innerHeight", 900);
    const frames = installControlledAnimationFrame();
    const originalRect = HTMLElement.prototype.getBoundingClientRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function () {
        if (this.classList.contains("canvas-workspace")) {
          return new DOMRect(0, 0, 1200, 900);
        }
        if (this.matches(".shot-track ol")) {
          const track = (this as HTMLElement).closest<HTMLElement>(
            ".shot-track",
          );
          const width = Number.parseFloat(track?.style.width ?? "") || 760;
          return new DOMRect(0, 0, width, 100);
        }
        if (this.classList.contains("shot-thumbnail")) {
          return new DOMRect(925, 10, 64, 42);
        }
        return originalRect.call(this);
      },
    );

    const revisionId = "11111111-1111-4111-8111-111111111111";
    useCanvasStore.getState().load({
      projectId: "project-1",
      revisionId: "revision-track-fallback-resize",
      nodes: [],
      edges: [],
      shots: [
        {
          id: "33333333-3333-4333-8333-333333333333",
          order: 0,
          imageRevisionId: revisionId,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
    });
    const getArtifactRevision = vi.fn().mockResolvedValue({
      ...artifactRevision("image"),
      revision_id: revisionId,
    });
    const getArtifactRevisionContent = vi
      .fn()
      .mockResolvedValue(new Blob(["raw"], { type: "image/png" }));
    const createObjectURL = vi.fn().mockReturnValue("blob:fallback-resize");
    const revokeObjectURL = vi.fn();
    const cache = createArtifactPreviewCache({
      getArtifactRevision,
      getArtifactRevisionContent,
      createObjectURL,
      revokeObjectURL,
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <div className="canvas-workspace">
          <FloatingShotTrack />
        </div>
      </ArtifactPreviewProvider>,
    );
    frames.flush();
    expect(screen.getByTestId("shot-track").style.width).toBe("760px");
    expect(getArtifactRevision).not.toHaveBeenCalled();

    const track = screen.getByTestId("shot-track");
    const topEdge = track.querySelector<HTMLElement>(
      '[data-floating-panel-resize-edge="top"]',
    );
    if (topEdge === null) throw new Error("Track top resize edge is unavailable");
    fireEvent.pointerDown(topEdge, { clientX: 100, clientY: 100, pointerId: 60 });
    fireEvent.pointerMove(topEdge, { clientX: 100, clientY: -20, pointerId: 60 });
    fireEvent.pointerUp(topEdge, { clientX: 100, clientY: -20, pointerId: 60 });
    const rightEdge = track.querySelector<HTMLElement>(
      '[data-floating-panel-resize-edge="right"]',
    );
    if (rightEdge === null) throw new Error("Track right resize edge is unavailable");
    fireEvent.pointerDown(rightEdge, { clientX: 100, clientY: 100, pointerId: 61 });
    fireEvent.pointerMove(rightEdge, { clientX: 110, clientY: 100, pointerId: 61 });
    fireEvent.pointerUp(rightEdge, { clientX: 110, clientY: 100, pointerId: 61 });
    expect(screen.getByTestId("shot-track").style.width).toBe("770px");
    frames.flush();

    await waitFor(() => expect(getArtifactRevision).toHaveBeenCalledOnce());
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());
    frames.flush();
    expect(frames.pendingCount()).toBe(0);

    fireEvent.pointerDown(rightEdge, { clientX: 110, clientY: 100, pointerId: 62 });
    fireEvent.pointerMove(rightEdge, { clientX: 100, clientY: 100, pointerId: 62 });
    fireEvent.pointerUp(rightEdge, { clientX: 100, clientY: 100, pointerId: 62 });
    expect(screen.getByTestId("shot-track").style.width).toBe("760px");
    frames.flush();

    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:fallback-resize"),
    );
  });

  it("renders a loading Shot thumbnail while the authenticated summary is pending", () => {
    const visibility = installControlledIntersectionObserver();
    const pending = deferred<ArtifactRevision>();
    const cache = createArtifactPreviewCache({
      getArtifactRevision: vi.fn().mockReturnValue(pending.promise),
      getArtifactRevisionContent: vi.fn(),
      createObjectURL: vi.fn(),
      revokeObjectURL: vi.fn(),
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <ShotThumbnail
          compact={false}
          id="shot-thumbnail-loading"
          revisionId="11111111-1111-4111-8111-111111111111"
          shotLabel="分镜 01"
        />
      </ArtifactPreviewProvider>,
    );

    const thumbnail = document.querySelector(".shot-thumbnail");
    if (!thumbnail) throw new Error("Shot thumbnail is unavailable");
    visibility.setIntersecting(thumbnail, true);

    const loadingThumbnail = screen.getByRole("status");
    expect(loadingThumbnail.textContent).toBe("正在加载分镜图…");
    expect(loadingThumbnail.tagName).toBe("SPAN");
    expect(loadingThumbnail.id).toBe("shot-thumbnail-loading");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("renders an explicit error when a Shot thumbnail cannot be loaded", async () => {
    const visibility = installControlledIntersectionObserver();
    const cache = createArtifactPreviewCache({
      getArtifactRevision: vi
        .fn()
        .mockRejectedValue(new TypeError("本地预览连接中断")),
      getArtifactRevisionContent: vi.fn(),
      createObjectURL: vi.fn(),
      revokeObjectURL: vi.fn(),
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <ShotThumbnail
          compact
          id="shot-thumbnail-error"
          revisionId="11111111-1111-4111-8111-111111111111"
          shotLabel="分镜 01"
        />
      </ArtifactPreviewProvider>,
    );

    const thumbnail = document.querySelector(".shot-thumbnail");
    if (!thumbnail) throw new Error("Shot thumbnail is unavailable");
    visibility.setIntersecting(thumbnail, true);

    const errorThumbnail = await screen.findByRole("alert");
    expect(errorThumbnail.textContent).toBe(
      "分镜图预览不可用",
    );
    expect(errorThumbnail.tagName).toBe("SPAN");
    expect(errorThumbnail.id).toBe("shot-thumbnail-error");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("shares one authenticated ready preview and renders only non-draggable images", async () => {
    const visibility = installControlledIntersectionObserver();
    const summary = {
      ...artifactRevision("image"),
      revision_id: "11111111-1111-4111-8111-111111111111",
    };
    const getArtifactRevision = vi.fn().mockResolvedValue(summary);
    const getArtifactRevisionContent = vi
      .fn()
      .mockResolvedValue(new Blob(["raw"], { type: "image/png" }));
    const createObjectURL = vi.fn().mockReturnValue("blob:shared-shot-image");
    const revokeObjectURL = vi.fn();
    const cache = createArtifactPreviewCache({
      getArtifactRevision,
      getArtifactRevisionContent,
      createObjectURL,
      revokeObjectURL,
    });
    previewCaches.push(cache);

    render(
      <ArtifactPreviewProvider cache={cache}>
        <ShotThumbnail
          compact={false}
          id="shot-thumbnail-node"
          revisionId={summary.revision_id}
          shotLabel="分镜 01"
        />
        <ShotThumbnail
          compact
          id="shot-thumbnail-track"
          revisionId={summary.revision_id}
          shotLabel="分镜 01"
        />
      </ArtifactPreviewProvider>,
    );

    const observedThumbnails = document.querySelectorAll(".shot-thumbnail");
    expect(observedThumbnails).toHaveLength(2);
    visibility.setIntersecting(observedThumbnails[0], true);
    visibility.setIntersecting(observedThumbnails[1], true);

    const thumbnails = await screen.findAllByRole("img", {
      name: "分镜 01 缩略图：安全的画面.png",
    });
    expect(thumbnails).toHaveLength(2);
    for (const [index, thumbnail] of thumbnails.entries()) {
      expect(thumbnail).toHaveProperty("draggable", false);
      expect(thumbnail.getAttribute("src")).toBe("blob:shared-shot-image");
      expect(thumbnail.closest(".shot-thumbnail")?.tagName).toBe("SPAN");
      expect(thumbnail.closest(".shot-thumbnail")?.id).toBe(
        index === 0 ? "shot-thumbnail-node" : "shot-thumbnail-track",
      );
    }
    expect(getArtifactRevision).toHaveBeenCalledOnce();
    expect(getArtifactRevisionContent).toHaveBeenCalledOnce();
    expect(createObjectURL).toHaveBeenCalledOnce();

    cleanup();
    cache.dispose();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:shared-shot-image");
  });

  it("renders a controlled text source with its exact output handle and actions", () => {
    const node = canvasNode("source-1", "text_source", "开场");
    loadNode(node);
    render(<TextSourceNode {...propsFor(node)} />);

    const textarea = screen.getByRole("textbox", { name: "文案内容" });
    expect(textarea).toHaveProperty("maxLength", 100_000);
    expect(textarea.className).toContain("nodrag");
    const handle = screen.getByTestId("source-text-source-1");
    expect(handle.getAttribute("data-handle-type")).toBe("source");
    expect(screen.queryByTestId("target-text-source-1")).toBeNull();
    const copy = screen.getByRole("button", { name: "复制文案节点" });
    const remove = screen.getByRole("button", { name: "删除文案节点" });
    expect(copy.getAttribute("type")).toBe("button");
    expect(remove.getAttribute("type")).toBe("button");

    fireEvent.change(textarea, { target: { value: "新的开场" } });
    expect(useCanvasStore.getState().nodes[0].data.text).toBe("新的开场");
    fireEvent.click(copy);
    expect(useCanvasStore.getState().nodes).toHaveLength(2);
    fireEvent.click(remove);
    expect(useCanvasStore.getState().nodes).toHaveLength(1);
  });

  it("opens the structured prompt editor and exposes the primary prompt output", () => {
    const node = canvasNode("11111111-1111-4111-8111-111111111111", "prompt", "原生提示词");
    loadNode(node);
    render(<PromptNode {...propsFor(node)} />);

    expect(screen.getByText("原生提示词")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "编辑结构化提示词" }));
    expect(screen.getByRole("dialog", { name: "结构化提示词编辑器" })).toBeTruthy();
    expect(screen.getByTestId("target-text-11111111-1111-4111-8111-111111111111").getAttribute("data-handle-type")).toBe(
      "target",
    );
    expect(screen.getByTestId("source-prompt-document-11111111-1111-4111-8111-111111111111").getAttribute("data-handle-type")).toBe(
      "source",
    );
  });

  it("renders a handle-free note and keeps Delete inside the textarea local", () => {
    const node = canvasNode("note-1", "note", "想法");
    loadNode(node);
    render(<NoteNode {...propsFor(node)} />);

    const textarea = screen.getByRole("textbox", { name: "便签内容" });
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    fireEvent(textarea, paste);
    expect(paste.defaultPrevented).toBe(false);
    fireEvent.change(textarea, { target: { value: "粘贴后的便签" } });
    expect(useCanvasStore.getState().nodes[0].data.text).toBe("粘贴后的便签");
    fireEvent.keyDown(textarea, { key: "Delete" });
    expect(useCanvasStore.getState().nodes).toHaveLength(1);
    expect(screen.queryByTestId(/(?:source|target)-text-note-1/)).toBeNull();
  });

  it("locks every graph-editing control while a conflict awaits a choice", () => {
    const node = canvasNode("note-1", "note", "本地草稿");
    loadNode(node);
    useCanvasStore
      .getState()
      .markConflict(useCanvasStore.getState().loadEpoch, "project-1", "revision-2");
    render(<NoteNode {...propsFor(node)} />);

    expect(screen.getByRole("textbox", { name: "便签内容" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "复制便签节点" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: "删除便签节点" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("renders an empty media node with one visible native chooser and a skipped hidden input", () => {
    const node = {
      id: "media-1",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadNode(node);
    renderMediaNode(node);

    const choose = screen.getByRole("button", { name: "选择本地图片或视频" });
    const input = screen.getByLabelText("选择要导入的本地媒体文件") as HTMLInputElement;
    expect(choose.getAttribute("type")).toBe("button");
    expect(input.type).toBe("file");
    expect(input.multiple).toBe(false);
    expect(input.accept).toBe(
      "image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime,video/x-matroska",
    );
    expect(input.className).toContain("media-node__file-input");
    expect(input.tabIndex).toBe(-1);
    const click = vi.spyOn(input, "click");
    choose.focus();
    expect(document.activeElement).toBe(choose);
    choose.click();
    expect(click).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /分镜/ })).toBeNull();
  });

  it("does not allocate an object URL for an unpromoted empty media node", () => {
    const node = {
      id: "media-empty-preview",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadNode(node);

    const { createObjectURL } = renderMediaNode(node);

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByLabelText(/视频预览/)).toBeNull();
  });

  it("sends a selected File through the real import hook, clears the chooser, and attaches it", async () => {
    const node = {
      id: "media-1",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadNode(node);
    const created = deferred<ArtifactImportSession>();
    const uploaded = deferred<ArtifactRevision>();
    artifactApi.createArtifactImport.mockReturnValue(created.promise);
    artifactApi.uploadArtifactContent.mockReturnValue(uploaded.promise);
    renderMediaNode(node);
    const input = screen.getByLabelText(
      "选择要导入的本地媒体文件",
    ) as HTMLInputElement;
    const file = new File(["raw"], "同名素材.png", { type: "image/png" });
    const setChooserValue = vi.fn();
    Object.defineProperty(input, "value", {
      configurable: true,
      get: () => "",
      set: setChooserValue,
    });

    fireEvent.change(input, { target: { files: [file] } });

    expect((await screen.findByRole("status")).textContent).toContain(
      "正在创建本地导入会话",
    );
    expect(setChooserValue).toHaveBeenCalledWith("");
    const declaration = artifactApi.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    await act(async () => {
      created.resolve(importSession(declaration, "CREATED"));
      await created.promise;
    });
    expect((await screen.findByRole("status")).textContent).toContain("正在传输素材");
    await waitFor(() => expect(artifactApi.uploadArtifactContent).toHaveBeenCalledOnce());
    expect(artifactApi.uploadArtifactContent.mock.calls[0][2]).toBe(file);
    await act(async () => {
      uploaded.resolve(artifactRevision("image"));
      await uploaded.promise;
    });
    await waitFor(() =>
      expect(mediaDataAt(0).artifactRevisionId).toBe(
        "revision-image",
      ),
    );
    expect(screen.getByRole("status").textContent).toContain("导入完成");
    expect(screen.getByRole("status").textContent).toContain("自动保存");
  });

  it("announces server-side validation without inventing progress", async () => {
    vi.useFakeTimers();
    const node = {
      id: "media-1",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadNode(node);
    artifactApi.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        importSession(declaration, "VALIDATING"),
    );
    artifactApi.getArtifactImport.mockImplementation(() => new Promise(() => undefined));
    renderMediaNode(node);

    fireEvent.change(screen.getByLabelText("选择要导入的本地媒体文件"), {
      target: {
        files: [new File(["raw"], "safe.png", { type: "image/png" })],
      },
    });

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("status").textContent).toContain("正在检查素材");
  });

  it("renders retry, reselect, and reload recovery actions from real import failures", async () => {
    const emptyNode = {
      id: "media-1",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    const file = new File(["raw"], "同名素材.png", { type: "image/png" });
    loadNode(emptyNode);
    artifactApi.createArtifactImport.mockRejectedValueOnce(new TypeError("网络中断"));
    renderMediaNode(emptyNode);
    let input = screen.getByLabelText("选择要导入的本地媒体文件") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    const retry = await screen.findByRole("button", { name: "重试导入" });
    const declaration = artifactApi.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    artifactApi.createArtifactImport.mockResolvedValueOnce(
      importSession(declaration, "CREATED"),
    );
    artifactApi.uploadArtifactContent.mockRejectedValueOnce(
      new ArtifactImportConflictError("import-1", "content_replay_mismatch"),
    );
    fireEvent.click(retry);
    const reselect = await screen.findByRole("button", { name: "重新选择文件" });
    const chooseClick = vi.spyOn(input, "click");
    fireEvent.click(reselect);
    expect(chooseClick).toHaveBeenCalledOnce();
    expect(input.value).toBe("");

    cleanup();
    const attachedNode = {
      ...emptyNode,
      id: "media-replace",
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    loadNode(attachedNode);
    const requestReload = vi.fn();
    artifactApi.createArtifactImport.mockRejectedValueOnce(
      new ArtifactImportConflictError("import-2", "invalid_replacement"),
    );
    renderMediaNode(attachedNode, artifactRevision("image"), requestReload);
    input = screen.getByLabelText("选择要导入的本地媒体文件") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    const reload = await screen.findByRole("button", { name: "重新加载项目" });
    expect(screen.getByRole("alert").textContent).toContain("重新加载");
    act(() =>
      useCanvasStore.setState({ status: "dirty", editSequence: 1, savedSequence: 0 }),
    );
    expect(reload).toHaveProperty("disabled", true);
    expect(reload.getAttribute("title")).toContain("保存");
    fireEvent.click(reload);
    expect(requestReload).not.toHaveBeenCalled();
    act(() =>
      useCanvasStore.setState({ status: "clean", editSequence: 1, savedSequence: 1 }),
    );
    expect(reload).toHaveProperty("disabled", false);
    fireEvent.click(reload);
    expect(requestReload).toHaveBeenCalledOnce();
  });

  it("renders authenticated image metadata without provenance or local identifiers", async () => {
    const node = {
      id: "media-image",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    loadNode(node);
    renderMediaNode(node, artifactRevision("image"));

    const image = await screen.findByRole("img", { name: "安全的画面.png" });
    expect(image.getAttribute("src")).toBe("blob:image-preview");
    const filename = screen.getByText("安全的画面.png");
    expect(filename.tagName).toBe("BDI");
    expect(filename.getAttribute("dir")).toBe("auto");
    expect(screen.getByText(/1920 × 1080/)).toBeTruthy();
    expect(screen.getByText(/3 B/)).toBeTruthy();
    expect(screen.getByText("图片")).toBeTruthy();
    expect(document.body.textContent).not.toContain("artifact-1");
    expect(document.body.textContent).not.toContain("project-1");
    expect(document.body.textContent).not.toContain("revision-image");
  });

  it("renders video controls with metadata preload, muted playsInline, and no autoplay", async () => {
    const node = {
      id: "media-video",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-video",
        mediaKind: "video",
      },
    } as CanvasNode;
    loadNode(node);
    renderMediaNode(node, artifactRevision("video"));

    const video = (await screen.findByLabelText("视频预览：安全的镜头.webm")) as HTMLVideoElement;
    expect(video.controls).toBe(true);
    expect(video.preload).toBe("metadata");
    expect(video.muted).toBe(true);
    expect(video.playsInline).toBe(true);
    expect(video.autoplay).toBe(false);
    expect(video.hasAttribute("autoplay")).toBe(false);
    expect(screen.getByText("视频")).toBeTruthy();
    expect(screen.getByText(/1280 × 720/)).toBeTruthy();
    expect(screen.getByText(/3 B/)).toBeTruthy();
    expect(screen.getByText(/2.5 秒/)).toBeTruthy();
  });

  it("keeps copying available while disabling target mutation and selection during an active import", async () => {
    const node = {
      id: "media-1",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadNode(node);
    useCanvasStore
      .getState()
      .markConflict(useCanvasStore.getState().loadEpoch, "project-1", "revision-2");
    renderMediaNode(node);
    expect(screen.getByRole("button", { name: "复制媒体节点" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: "删除媒体节点" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: "选择本地图片或视频" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByLabelText("选择要导入的本地媒体文件")).toHaveProperty(
      "disabled",
      true,
    );

    cleanup();
    loadNode(node);
    artifactApi.createArtifactImport.mockImplementation(() => new Promise(() => undefined));
    renderMediaNode(node);
    const input = screen.getByLabelText("选择要导入的本地媒体文件");
    fireEvent.change(input, {
      target: { files: [new File(["raw"], "safe.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(artifactApi.createArtifactImport).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "复制媒体节点" })).toHaveProperty(
      "disabled",
      false,
    );
    expect(screen.getByRole("button", { name: "删除媒体节点" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: "选择本地图片或视频" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(input).toHaveProperty("disabled", true);
  });

  it("shows queued status while keeping copy enabled and disabling replace and delete", () => {
    const node = {
      id: "media-queued",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    loadNode(node);
    const dependencies = createMediaImportRuntimeDependencies();
    dependencies.permits.holdQueue("test-queue");
    renderMediaNode(
      node,
      artifactRevision("image"),
      () => undefined,
      {},
      dependencies,
    );

    fireEvent.change(
      screen.getByLabelText("选择要导入的本地媒体文件"),
      {
        target: {
          files: [new File(["raw"], "safe.png", { type: "image/png" })],
        },
      },
    );

    expect(screen.getByText("排队中").getAttribute("role")).toBe("status");
    expect(
      screen.getByRole("button", { name: "复制媒体节点" }),
    ).toHaveProperty("disabled", false);
    expect(
      screen.getByRole("button", { name: "删除媒体节点" }),
    ).toHaveProperty("disabled", true);
    expect(
      screen.getByRole("button", { name: "替换媒体" }),
    ).toHaveProperty("disabled", true);
    expect(artifactApi.createArtifactImport).not.toHaveBeenCalled();
  });

  it("shows controller target progress inside the existing node without hiding its old preview or disabling copy", async () => {
    const node = {
      id: "media-target",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    loadNode(node);
    const targetLocks = new MediaTargetLocks();
    targetLocks.acquire("target-operation", "media-target");
    const snapshot: MediaIngressSnapshot = {
      batches: [
        {
          batchId: "batch-target",
          source: "drop",
          createdOrder: 1,
          collapsed: false,
          counts: {
            total: 1,
            queued: 1,
            processing: 0,
            placed: 0,
            failed: 0,
            cancelled: 0,
            savedUnplaced: 0,
            ignored: 0,
            terminal: 0,
          },
          slots: [
            {
              slotId: "slot-target",
              reservation: {
                nodeId: "fallback-node",
                position: { x: 352, y: 0 },
                width: 312,
                height: 360,
              },
              currentItem: {
                itemId: "target-operation",
                clientImportId: "client-target",
                state: "CANCELLING",
                displayName: "replacement.png",
                mediaKind: "image",
                errorMessage: null,
                discardErrorMessage: null,
                canCancel: false,
                canRetryCancellation: true,
                canRetryDiscard: false,
                canForceRefresh: true,
              },
              previousAttempts: [],
              attemptCount: 1,
              targetNodeId: "media-target",
              canReselect: false,
              canIgnore: false,
              canPlaceSavedUnplaced: false,
            },
          ],
          canStop: true,
          canClose: false,
        },
      ],
      pendingTargetChoices: [],
      protectedTargetNodeIds: ["media-target"],
      guardState: { pendingOperationCount: 1, hasSavedUnplaced: false },
    };
    const idleNodeImportSnapshot = {
      phase: "idle",
      active: false,
      errorMessage: null,
      recoveryKind: null,
    } as const;
    const runtime: MediaIngressRuntimePort = {
      projectId: "project-1",
      loadEpoch: useCanvasStore.getState().loadEpoch,
      nodeImport: {
        projectId: "project-1",
        getSnapshot: () => idleNodeImportSnapshot,
        subscribe: () => () => undefined,
        selectFile: vi.fn(),
        retry: vi.fn(),
        requestReload: vi.fn(),
      },
      controller: {
        getSnapshot: () => snapshot,
        subscribe: () => () => undefined,
      } as unknown as MediaIngressController,
      targetLocks,
    };

    renderMediaNode(
      node,
      artifactRevision("image"),
      () => undefined,
      {},
      undefined,
      runtime,
    );

    expect(
      screen.getByText("正在确认取消结果…").getAttribute("role"),
    ).toBe("status");
    expect(
      screen.getByRole("button", { name: "复制媒体节点" }),
    ).toHaveProperty("disabled", false);
    expect(
      screen.getByRole("button", { name: "删除媒体节点" }),
    ).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "替换媒体" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(
      screen.getByLabelText("选择要导入的本地媒体文件"),
    ).toHaveProperty("disabled", true);
    expect(
      (
        await screen.findByRole("img", { name: "安全的画面.png" })
      ).getAttribute("src"),
    ).toBe("blob:image-preview");
    expect(
      screen.queryByRole("button", {
        name: /重试|重新选择|忽略|放入画布|强制刷新/,
      }),
    ).toBeNull();
  });

  it("reports a browser codec failure without clearing the saved revision", async () => {
    const node = {
      id: "media-video",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-video",
        mediaKind: "video",
      },
    } as CanvasNode;
    loadNode(node);
    renderMediaNode(node, artifactRevision("video"));
    const video = await screen.findByLabelText("视频预览：安全的镜头.webm");

    fireEvent.error(video);

    expect(screen.getByRole("alert").textContent).toContain("素材已保存");
    expect(screen.getByRole("alert").textContent).toContain("浏览器无法预览");
    expect(mediaDataAt(0).artifactRevisionId).toBe(
      "revision-video",
    );
  });

  it("shows an authenticated preview fetch error and retries the shared cache entry", async () => {
    const node = {
      id: "media-image",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    loadNode(node);
    const getSummary = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("本地预览连接中断"))
      .mockResolvedValueOnce(artifactRevision("image"));
    const getContent = vi
      .fn()
      .mockResolvedValue(new Blob(["raw"], { type: "image/png" }));
    renderMediaNode(node, artifactRevision("image"), () => undefined, {
      getArtifactRevision: getSummary,
      getArtifactRevisionContent: getContent,
    });

    expect((await screen.findByRole("alert")).textContent).toContain(
      "本地预览连接中断",
    );
    fireEvent.click(screen.getByRole("button", { name: "重试预览" }));

    expect(await screen.findByRole("img", { name: "安全的画面.png" })).toBeTruthy();
    expect(getSummary).toHaveBeenCalledTimes(2);
    expect(getContent).toHaveBeenCalledOnce();
  });

  it("shows explicit copy/delete/replace buttons and waits for clean save before replacement", async () => {
    const node = {
      id: "media-image",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    loadNode(node);
    useCanvasStore.setState({ status: "dirty", editSequence: 1, savedSequence: 0 });
    renderMediaNode(node, artifactRevision("image"));
    await screen.findByRole("img");

    const copy = screen.getByRole("button", { name: "复制媒体节点" });
    const remove = screen.getByRole("button", { name: "删除媒体节点" });
    const replace = screen.getByRole("button", { name: "替换媒体" });
    expect(copy.getAttribute("type")).toBe("button");
    expect(remove.getAttribute("type")).toBe("button");
    expect(replace.getAttribute("type")).toBe("button");
    expect(replace).toHaveProperty("disabled", true);
    expect(replace.getAttribute("title")).toContain("保存");

    fireEvent.click(copy);
    expect(useCanvasStore.getState().nodes).toHaveLength(2);
    expect(mediaDataAt(1).artifactRevisionId).toBe(
      "revision-image",
    );
    fireEvent.click(remove);
    expect(useCanvasStore.getState().nodes).toHaveLength(1);
  });

  it("renders a compact Shot card without duplicating Inspector slot details", () => {
    const node = {
      id: "shot-node",
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: "shot-1" },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [node],
      edges: [],
      shots: [
        {
          id: "shot-1",
          order: 0,
          imageRevisionId: "revision-image",
          videoRevisionId: null,
          referenceRevisionIds: ["reference-1", "reference-2"],
        },
        {
          id: "shot-2",
          order: 1,
          imageRevisionId: null,
          videoRevisionId: "revision-video",
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    useCanvasStore.getState().selectShot("shot-2");
    render(<ShotNode {...shotPropsFor(node)} />);

    const card = screen.getByRole("article", { name: "分镜 1" });
    expect(screen.getByTestId("target-prompt-document-shot-node").getAttribute("data-handle-type")).toBe(
      "target",
    );
    expect(card.className).not.toContain("semantic-node--shot-selected");
    expect(card.querySelector(".shot-thumbnail")).toBeTruthy();
    expect(card.querySelector(".shot-node__summary")).toBeNull();
    expect(screen.queryByText("图片已关联")).toBeNull();
    expect(screen.queryByText("未关联视频")).toBeNull();
    expect(screen.queryByText("2 个参考素材")).toBeNull();

    fireEvent.click(card);
    expect(useCanvasStore.getState().selectedShotId).toBe("shot-2");
    fireEvent.click(screen.getByRole("button", { name: "下移分镜 1" }));
    expect(useCanvasStore.getState().shots.map((shot) => shot.id)).toEqual([
      "shot-2",
      "shot-1",
    ]);
    expect(useCanvasStore.getState().selectedShotId).toBe("shot-2");
    expect(screen.queryByRole("button", { name: /删除分镜/ })).toBeNull();
    expect(document.body.textContent).not.toContain("只读");
  });

  it("shows the Shot image thumbnail and stable identity while only its order label changes", async () => {
    const visibility = installControlledIntersectionObserver();
    const shotId = "11111111-1111-4111-8111-111111111111";
    const imageRevisionId = "33333333-3333-4333-8333-333333333333";
    const node = {
      id: "shot-node",
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [node],
      edges: [],
      shots: [
        {
          id: shotId,
          order: 0,
          imageRevisionId,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
        {
          id: "22222222-2222-4222-8222-222222222222",
          order: 1,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
      revisionId: "revision-1",
    });
    const summary = {
      ...artifactRevision("image"),
      revision_id: imageRevisionId,
    };
    const cache = createArtifactPreviewCache({
      getArtifactRevision: vi.fn().mockResolvedValue(summary),
      getArtifactRevisionContent: vi
        .fn()
        .mockResolvedValue(new Blob(["raw"], { type: "image/png" })),
      createObjectURL: vi.fn().mockReturnValue("blob:shot-node-image"),
      revokeObjectURL: vi.fn(),
    });
    previewCaches.push(cache);
    render(
      <ArtifactPreviewProvider cache={cache}>
        <ShotNode {...shotPropsFor(node)} />
      </ArtifactPreviewProvider>,
    );
    const thumbnail = document.querySelector(".shot-thumbnail");
    if (!thumbnail) throw new Error("Shot thumbnail is unavailable");
    visibility.setIntersecting(thumbnail, true);

    expect(
      await screen.findByRole("img", {
        name: "分镜 01 缩略图：安全的画面.png",
      }),
    ).toHaveProperty("draggable", false);
    expect(screen.getByText("分镜 01")).toBeTruthy();
    expect(screen.getByText("镜头 ID S-UR72CEYHRT")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下移分镜 1" }));

    expect(screen.getByText("分镜 02")).toBeTruthy();
    expect(screen.getByText("镜头 ID S-UR72CEYHRT")).toBeTruthy();
  });

  it("makes only an attached, unprotected media asset strip draggable for Shot transfer", async () => {
    const node = {
      id: "media-image",
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
      nodes: [node],
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
    useCanvasStore.getState().beginShotAssociation("shot-1", "image");
    const transfer = vi.spyOn(shotMediaTransfer, "writeShotMediaTransfer");
    renderMediaNode(node, artifactRevision("image"));

    const image = await screen.findByRole("img");
    const strip = image.closest(".media-node__asset-strip");
    expect(strip).toBeTruthy();
    expect(strip?.getAttribute("draggable")).toBe("true");
    expect(strip?.className).toContain("nodrag");
    expect(screen.getByRole("article", { name: "媒体节点" }).className).toContain(
      "semantic-node--media-association-candidate",
    );
    fireEvent.dragStart(strip!, {
      dataTransfer: { setData: vi.fn() },
    });
    expect(transfer).toHaveBeenCalledWith(expect.anything(), {
      revisionId: "revision-image",
      mediaKind: "image",
      nodeId: "media-image",
    });
  });

  it("does not expose an empty or protected media node as a Shot transfer source", async () => {
    const empty = {
      id: "empty-media",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadNode(empty);
    renderMediaNode(empty);
    expect(document.querySelector(".media-node__asset-strip")).toBeNull();

    cleanup();
    const protectedNode = {
      id: "protected-media",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-image",
        mediaKind: "image",
      },
    } as CanvasNode;
    loadNode(protectedNode);
    const targetLocks = new MediaTargetLocks();
    targetLocks.acquire("operation-1", "protected-media");
    const idleNodeImportSnapshot = {
      phase: "idle",
      active: false,
      errorMessage: null,
      recoveryKind: null,
    } as const;
    const ingressSnapshot: MediaIngressSnapshot = {
      batches: [],
      pendingTargetChoices: [],
      protectedTargetNodeIds: ["protected-media"],
      guardState: { pendingOperationCount: 1, hasSavedUnplaced: false },
    };
    const ingressRuntime: MediaIngressRuntimePort = {
      projectId: "project-1",
      loadEpoch: useCanvasStore.getState().loadEpoch,
      nodeImport: {
        projectId: "project-1",
        getSnapshot: () => idleNodeImportSnapshot,
        subscribe: () => () => undefined,
        selectFile: vi.fn(),
        retry: vi.fn(),
        requestReload: vi.fn(),
      },
      controller: {
        getSnapshot: () => ingressSnapshot,
        subscribe: () => () => undefined,
      } as unknown as MediaIngressController,
      targetLocks,
    };
    renderMediaNode(
      protectedNode,
      artifactRevision("image"),
      () => undefined,
      {},
      undefined,
      ingressRuntime,
    );
    await screen.findByRole("img");
    expect(
      document.querySelector(".media-node__asset-strip")?.getAttribute("draggable"),
    ).toBe("false");
  });

  it("marks attached incompatible media while association mode is active", async () => {
    const node = {
      id: "media-video",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: "revision-video",
        mediaKind: "video",
      },
    } as CanvasNode;
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [node],
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
    useCanvasStore.getState().beginShotAssociation("shot-1", "reference");
    renderMediaNode(node, artifactRevision("video"));

    await screen.findByLabelText(/视频预览/);
    expect(screen.getByRole("article", { name: "媒体节点" }).className).toContain(
      "semantic-node--media-association-incompatible",
    );
  });
});
