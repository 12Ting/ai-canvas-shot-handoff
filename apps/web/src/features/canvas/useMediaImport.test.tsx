import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  ArtifactDeclarationValidationError,
  ArtifactImportConflictError,
  ArtifactImportFailedError,
  SessionExpiredError,
  type ArtifactImportDeclaration,
  type ArtifactImportSession,
  type ArtifactRevision,
} from "../../api/client";
import type {
  CreateMediaImportAttemptInput,
  MediaImportAttempt,
  MediaImportNetworkPhase,
} from "./mediaIngress/mediaImportCore";
import { useCanvasStore } from "./canvasStore";
import type { CanvasNode } from "./canvasTypes";
import {
  createMediaImportRuntime,
  createMediaImportRuntimeDependencies,
  useMediaImport,
  type MediaImportControls,
  type MediaImportRuntimeDependencies,
} from "./useMediaImport";
import { MediaIngressProvider } from "./mediaIngress/MediaIngressProvider";

const api = vi.hoisted(() => ({
  createArtifactImport: vi.fn(),
  getArtifactImport: vi.fn(),
  uploadArtifactContent: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  ...api,
}));

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const NODE_ID = "22222222-2222-4222-8222-222222222222";
const COPY_NODE_ID = "33333333-3333-4333-8333-333333333333";
const OLD_REVISION_ID = "44444444-4444-4444-8444-444444444444";
const NEW_REVISION_ID = "55555555-5555-4555-8555-555555555555";
const OTHER_REVISION_ID = "66666666-6666-4666-8666-666666666666";
const IMPORT_ID = "77777777-7777-4777-8777-777777777777";

function MediaImportProvider({
  projectId,
  dependencies,
  onActiveCountChange = () => undefined,
  onRequestReload,
  children,
}: {
  projectId: string;
  dependencies?: MediaImportRuntimeDependencies;
  onActiveCountChange?(count: number): void;
  onRequestReload?(): void;
  children: ReactNode;
}) {
  const loadEpoch = useCanvasStore.getState().loadEpoch;
  return (
    <MediaIngressProvider
      dependencies={dependencies}
      generation={`${projectId}:${loadEpoch}`}
      loadEpoch={loadEpoch}
      onPendingCountChange={(_generation, count) => onActiveCountChange(count)}
      onRequestReload={onRequestReload}
      projectId={projectId}
    >
      {children}
    </MediaIngressProvider>
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function revision(
  revisionId = NEW_REVISION_ID,
  mediaKind: "image" | "video" = "image",
): ArtifactRevision {
  return {
    revision_id: revisionId,
    artifact_id: "88888888-8888-4888-8888-888888888888",
    sequence: revisionId === OLD_REVISION_ID ? 1 : 2,
    media_kind: mediaKind,
    media_type: mediaKind === "image" ? "image/png" : "video/webm",
    byte_size: 3,
    display_filename: mediaKind === "image" ? "safe.png" : "safe.webm",
    metadata:
      mediaKind === "image"
        ? { kind: "image", width: 2, height: 1 }
        : {
            kind: "video",
            width: 2,
            height: 1,
            duration_seconds: 1,
            frames_per_second: 24,
          },
    source_kind: "import",
    source_project_id: PROJECT_ID,
    origin_project_id: PROJECT_ID,
    created_at: "2026-07-21T01:02:03Z",
  };
}

function session(
  declaration: ArtifactImportDeclaration,
  state: ArtifactImportSession["state"],
  options: {
    artifactRevision?: ArtifactRevision | null;
    failureCode?: ArtifactImportSession["failure_code"];
    importId?: string;
  } = {},
): ArtifactImportSession {
  const completedRevision = options.artifactRevision ?? null;
  const failed = state === "FAILED_RETRYABLE" || state === "FAILED_FINAL";
  return {
    import_id: options.importId ?? IMPORT_ID,
    source_project_id: PROJECT_ID,
    client_import_id: declaration.clientImportId,
    replace_revision_id: declaration.replaceRevisionId,
    requested_media_kind: declaration.requestedMediaKind,
    original_filename: declaration.filename,
    declared_content_type: declaration.declaredContentType,
    expected_byte_size: declaration.expectedByteSize,
    state,
    received_byte_size: state === "COMPLETED" ? declaration.expectedByteSize : 0,
    failure_code: failed ? (options.failureCode ?? "upload_interrupted") : null,
    created_at: "2026-07-21T01:02:03Z",
    updated_at: "2026-07-21T01:02:04Z",
    artifact_revision: state === "COMPLETED" ? completedRevision : null,
  };
}

function mediaNode(
  id: string,
  artifactRevisionId: string | null = null,
  mediaKind: "image" | "video" | null = null,
): CanvasNode {
  return {
    id,
    type: "media_input",
    position: { x: 0, y: 0 },
    data: { kind: "media_input", artifactRevisionId, mediaKind },
  } as CanvasNode;
}

function loadNodes(nodes: CanvasNode[]): void {
  useCanvasStore.getState().load({
    projectId: PROJECT_ID,
    nodes,
    edges: [],
    shots: [],
    revisionId: "99999999-9999-4999-8999-999999999999",
  });
}

function mediaDataAt(index: number) {
  const data = useCanvasStore.getState().nodes[index]?.data;
  if (!data || data.kind !== "media_input") {
    throw new Error(`Expected media node at index ${index}`);
  }
  return data;
}

let controls: MediaImportControls | null = null;
const controlsByNode = new Map<string, MediaImportControls>();

function Harness({ nodeId }: { nodeId: string }) {
  controls = useMediaImport(nodeId);
  controlsByNode.set(nodeId, controls);
  return (
    <div>
      <span data-testid="phase">{controls.phase}</span>
      <span data-testid="active">{String(controls.active)}</span>
      <span data-testid="replacement-block">{controls.replacementBlockedReason ?? ""}</span>
      {controls.errorMessage ? <span role="alert">{controls.errorMessage}</span> : null}
    </div>
  );
}

function renderHarness(
  nodeId = NODE_ID,
  onActiveCountChange: (count: number) => void = () => undefined,
  dependencies?: MediaImportRuntimeDependencies,
) {
  return render(
    <MediaImportProvider
      dependencies={dependencies}
      onActiveCountChange={onActiveCountChange}
      projectId={PROJECT_ID}
    >
      <Harness nodeId={nodeId} />
    </MediaImportProvider>,
  );
}

function lastDeclaration(): ArtifactImportDeclaration {
  return api.createArtifactImport.mock.calls.at(-1)?.[1] as ArtifactImportDeclaration;
}

const imageFile = () => new File(["png"], "unsafe/../safe.png", { type: "image/png" });

function dependenciesWithAttempt(
  executeAttempt: (input: {
    signal: AbortSignal;
    onPhase(phase: MediaImportNetworkPhase): void;
  }) => Promise<ArtifactRevision>,
): MediaImportRuntimeDependencies {
  const dependencies = createMediaImportRuntimeDependencies();
  return Object.freeze({
    ...dependencies,
    core: {
      discardImport: async () => ({
        httpStatus: 200 as const,
        outcome: "discarded" as const,
        contentCleanup: "not_applicable" as const,
      }),
      createAttempt(input: CreateMediaImportAttemptInput): MediaImportAttempt {
        const immutableDeclaration = Object.freeze({ ...input.declaration });
        return {
          operationId: input.operationId,
          declaration: immutableDeclaration,
          getImportId: () => null,
          wasCreateDispatched: () => false,
          wasContentPutDispatched: () => false,
          recoverImportId: () => Promise.reject(new Error("not used")),
          execute: executeAttempt,
          reconcileCancellation: async () => ({
            kind: "unconfirmed",
            reason: "observer_failed",
            errorMessage: "not observed by this fake attempt",
          }),
          releaseFile: () => undefined,
        };
      },
    },
  });
}

function fileWithSize(size: number): File {
  const file = new File(["x"], "safe.png", { type: "image/png" });
  Object.defineProperty(file, "size", { configurable: true, value: size });
  return file;
}

describe("useMediaImport", () => {
  beforeEach(() => {
    cleanup();
    controls = null;
    controlsByNode.clear();
    useCanvasStore.getState().reset();
    for (const mock of Object.values(api)) mock.mockReset();
  });

  it("keeps a fourth import queued until a shared permit is released", async () => {
    const nodeIds = [NODE_ID, COPY_NODE_ID, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"];
    loadNodes(nodeIds.map((nodeId) => mediaNode(nodeId)));
    const pending = nodeIds.map(() => deferred<ArtifactImportSession>());
    api.createArtifactImport.mockImplementation(
      () => pending[api.createArtifactImport.mock.calls.length - 1].promise,
    );
    render(
      <MediaImportProvider projectId={PROJECT_ID}>
        {nodeIds.map((nodeId) => <Harness key={nodeId} nodeId={nodeId} />)}
      </MediaImportProvider>,
    );
    for (const nodeId of nodeIds) act(() => controlsByNode.get(nodeId)?.selectFile(imageFile()));

    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledTimes(3));
    expect(controlsByNode.get(nodeIds[3])?.phase).toBe("queued");

    const firstDeclaration = api.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    await act(async () => {
      pending[0].resolve(
        session(firstDeclaration, "COMPLETED", { artifactRevision: revision() }),
      );
      await pending[0].promise;
    });
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledTimes(4));
    expect(api.createArtifactImport.mock.calls[3][1]).toMatchObject({
      clientImportId: expect.any(String),
    });

    await act(async () => {
      for (const index of [1, 2, 3]) {
        const current = api.createArtifactImport.mock.calls[index][1] as ArtifactImportDeclaration;
        pending[index].resolve(
          session(current, "COMPLETED", { artifactRevision: revision() }),
        );
      }
      await Promise.all(pending.slice(1).map((entry) => entry.promise));
    });
    await waitFor(() =>
      expect(nodeIds.every((nodeId) => controlsByNode.get(nodeId)?.phase === "completed")).toBe(true),
    );
  });

  it("removes an unmounted queued operation without POST and lets the next waiter continue", async () => {
    const nodeIds = [
      NODE_ID,
      COPY_NODE_ID,
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    ];
    loadNodes(nodeIds.map((nodeId) => mediaNode(nodeId)));
    const pending = Array.from({ length: 4 }, () => deferred<ArtifactImportSession>());
    api.createArtifactImport.mockImplementation(
      () => pending[api.createArtifactImport.mock.calls.length - 1].promise,
    );
    const view = render(
      <MediaImportProvider projectId={PROJECT_ID}>
        {nodeIds.map((nodeId) => <Harness key={nodeId} nodeId={nodeId} />)}
      </MediaImportProvider>,
    );
    act(() => {
      nodeIds.forEach((nodeId, index) =>
        controlsByNode
          .get(nodeId)
          ?.selectFile(
            new File(["png"], `${index}.png`, { type: "image/png" }),
          ),
      );
    });
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledTimes(3));
    expect(controlsByNode.get(nodeIds[3])?.phase).toBe("queued");
    expect(controlsByNode.get(nodeIds[4])?.phase).toBe("queued");

    view.rerender(
      <MediaImportProvider projectId={PROJECT_ID}>
        {nodeIds
          .filter((nodeId) => nodeId !== nodeIds[3])
          .map((nodeId) => <Harness key={nodeId} nodeId={nodeId} />)}
      </MediaImportProvider>,
    );
    const firstDeclaration = api.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    await act(async () => {
      pending[0].resolve(
        session(firstDeclaration, "COMPLETED", { artifactRevision: revision() }),
      );
      await pending[0].promise;
    });

    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledTimes(4));
    expect(
      (api.createArtifactImport.mock.calls[3][1] as ArtifactImportDeclaration)
        .filename,
    ).toBe("4.png");

    await act(async () => {
      for (const index of [1, 2, 3]) {
        const current = api.createArtifactImport.mock.calls[index][1] as ArtifactImportDeclaration;
        pending[index].resolve(
          session(current, "COMPLETED", { artifactRevision: revision() }),
        );
      }
      await Promise.all(pending.slice(1).map((entry) => entry.promise));
    });
    await waitFor(() => expect(controlsByNode.get(nodeIds[4])?.phase).toBe("completed"));
    expect(api.createArtifactImport).toHaveBeenCalledTimes(4);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("runs init then raw upload and atomically attaches exactly once", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "CREATED"),
    );
    api.uploadArtifactContent.mockResolvedValue(revision());
    const activeCounts: number[] = [];
    renderHarness(NODE_ID, (count) => activeCounts.push(count));

    act(() => controls?.selectFile(imageFile()));

    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));
    expect(api.createArtifactImport).toHaveBeenCalledOnce();
    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();
    expect(api.uploadArtifactContent.mock.calls[0][2]).toBeInstanceOf(File);
    expect(useCanvasStore.getState().nodes[0].data).toEqual({
      kind: "media_input",
      artifactRevisionId: NEW_REVISION_ID,
      mediaKind: "image",
    });
    expect(useCanvasStore.getState().editSequence).toBe(1);
    expect(activeCounts).toContain(1);
    expect(activeCounts.at(-1)).toBe(0);
  });

  it.each([
    [
      "declaration validation",
      new ArtifactDeclarationValidationError("raw validation error"),
      "所选文件的名称或媒体类型声明无效，请重新选择文件。",
    ],
    [
      "upload interrupted",
      new ArtifactImportFailedError(IMPORT_ID, "upload_interrupted"),
      "文件传输中断，可以使用同一文件重试。",
    ],
    [
      "probe timeout",
      new ArtifactImportFailedError(IMPORT_ID, "probe_timeout"),
      "素材检查超时，可以重试。",
    ],
    [
      "probe unavailable",
      new ArtifactImportFailedError(IMPORT_ID, "probe_unavailable"),
      "本地素材检查工具暂不可用，可以重试。",
    ],
    [
      "size mismatch",
      new ArtifactImportFailedError(IMPORT_ID, "size_mismatch"),
      "文件大小与选择时不一致，请重新选择文件。",
    ],
    [
      "corrupt media",
      new ArtifactImportFailedError(IMPORT_ID, "corrupt_media"),
      "文件已损坏或不完整，请重新选择可用素材。",
    ],
    [
      "unsupported media",
      new ArtifactImportFailedError(IMPORT_ID, "unsupported_media"),
      "当前文件格式或编码不受支持，请重新选择。",
    ],
    [
      "incompatible declaration",
      new ArtifactImportFailedError(IMPORT_ID, "incompatible_declaration"),
      "文件内容与导入声明不一致，请重新选择文件。",
    ],
    [
      "declaration mismatch",
      new ArtifactImportConflictError(IMPORT_ID, "declaration_mismatch"),
      "这次导入标识已用于另一份文件，请重新选择。",
    ],
    [
      "content replay mismatch",
      new ArtifactImportConflictError(IMPORT_ID, "content_replay_mismatch"),
      "已完成的导入会话与所选文件不一致，请重新选择文件。",
    ],
    [
      "upload in progress",
      new ArtifactImportConflictError(IMPORT_ID, "upload_in_progress"),
      "同一文件仍在处理中，请稍候。",
    ],
    [
      "invalid replacement",
      new ArtifactImportConflictError(IMPORT_ID, "invalid_replacement"),
      "原素材已不再是可替换版本，请重新加载项目后再替换。",
    ],
    [
      "invalid state",
      new ArtifactImportConflictError(IMPORT_ID, "invalid_state"),
      "这次导入已结束，请重新选择文件。",
    ],
    [
      "untrusted API fallback",
      new ApiRequestError(
        503,
        "ffprobe.exe C:\\Users\\author\\private\\clip.mp4\n    at internal-upload",
        true,
      ),
      "素材导入失败，请重试。",
    ],
    [
      "unknown Error fallback",
      new Error("C:\\Users\\author\\private\\clip.mp4 failed in internal-upload"),
      "素材导入失败，请重试。",
    ],
    [
      "expired local session",
      new SessionExpiredError(),
      "本地会话已失效，请从 AI Canvas 启动器重新打开。",
    ],
    ["unknown fallback", "opaque failure", "素材导入失败，请重试。"],
  ])("renders the complete B1 %s message from real Hook state", async (_case, error, expected) => {
    loadNodes([mediaNode(NODE_ID)]);
    const dependencies = dependenciesWithAttempt(async () => {
      throw error;
    });
    renderHarness(NODE_ID, () => undefined, dependencies);

    act(() => controls?.selectFile(imageFile()));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe(expected));
    expect(controls?.errorMessage).toBe(expected);
    expect(controls?.phase).toMatch(/_error$/);
  });

  it.each([
    [
      "unsupported kind",
      new File(["text"], "notes.txt", { type: "text/plain" }),
      "请选择支持的 PNG、JPEG、WebP、MP4、WebM、MOV 或 MKV 文件。",
    ],
    ["empty file", fileWithSize(0), "文件为空或超过 10 GiB，请重新选择。"],
    [
      "oversized file",
      fileWithSize(10 * 1024 * 1024 * 1024 + 1),
      "文件为空或超过 10 GiB，请重新选择。",
    ],
    [
      "filename boundary",
      new File(["png"], "x".repeat(513), { type: "image/png" }),
      "文件名或媒体类型声明过长，请重新选择文件。",
    ],
    [
      "MIME boundary",
      new File(["png"], "safe.png", { type: "x".repeat(256) }),
      "文件名或媒体类型声明过长，请重新选择文件。",
    ],
  ])("renders the complete B1 local %s message", (_case, file, expected) => {
    loadNodes([mediaNode(NODE_ID)]);
    renderHarness();

    act(() => controls?.selectFile(file));

    expect(screen.getByRole("alert").textContent).toBe(expected);
    expect(controls?.errorMessage).toBe(expected);
    expect(api.createArtifactImport).not.toHaveBeenCalled();
  });

  it("renders the complete B1 replacement type mismatch message", () => {
    loadNodes([mediaNode(NODE_ID, OLD_REVISION_ID, "image")]);
    renderHarness();

    act(() =>
      controls?.selectFile(
        new File(["video"], "clip.mp4", { type: "video/mp4" }),
      ),
    );

    expect(screen.getByRole("alert").textContent).toBe(
      "替换素材必须选择相同类型的文件；如需切换图片与视频，请新建媒体节点。",
    );
    expect(api.createArtifactImport).not.toHaveBeenCalled();
  });

  it("does not rerender when Core reports the exact same phase twice", async () => {
    const firstPhase = deferred<void>();
    const secondPhase = deferred<void>();
    const finish = deferred<void>();
    const renderedPhases: string[] = [];
    const dependencies = dependenciesWithAttempt(async ({ onPhase }) => {
      await firstPhase.promise;
      onPhase("receiving");
      await secondPhase.promise;
      onPhase("receiving");
      await finish.promise;
      return revision();
    });
    function CountingHarness() {
      const current = useMediaImport(NODE_ID);
      controls = current;
      renderedPhases.push(current.phase);
      return <span data-testid="counting-phase">{current.phase}</span>;
    }
    loadNodes([mediaNode(NODE_ID)]);
    render(
      <MediaImportProvider dependencies={dependencies} projectId={PROJECT_ID}>
        <CountingHarness />
      </MediaImportProvider>,
    );
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(renderedPhases.at(-1)).toBe("queued"));

    await act(async () => {
      firstPhase.resolve();
      await firstPhase.promise;
    });
    await waitFor(() => expect(renderedPhases.at(-1)).toBe("receiving"));
    const rendersAfterFirstReceiving = renderedPhases.length;

    await act(async () => {
      secondPhase.resolve();
      await secondPhase.promise;
    });
    expect(renderedPhases).toHaveLength(rendersAfterFirstReceiving);

    await act(async () => {
      finish.resolve();
      await finish.promise;
    });
    await waitFor(() => expect(renderedPhases.at(-1)).toBe("completed"));
  });

  it("observes RECEIVING and VALIDATING while one authoritative PUT remains pending", async () => {
    vi.useFakeTimers();
    loadNodes([mediaNode(NODE_ID)]);
    const uploadPending = deferred<ArtifactRevision>();
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "CREATED"),
    );
    api.uploadArtifactContent.mockReturnValue(uploadPending.promise);
    api.getArtifactImport
      .mockImplementationOnce(async () => session(lastDeclaration(), "RECEIVING"))
      .mockImplementationOnce(async () => session(lastDeclaration(), "VALIDATING"))
      .mockImplementationOnce(async () =>
        session(lastDeclaration(), "COMPLETED", { artifactRevision: revision() }),
      );
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(screen.getByTestId("phase").textContent).toBe("validating");
    expect(api.getArtifactImport).toHaveBeenCalledTimes(2);
    expect(mediaDataAt(0).artifactRevisionId).toBeNull();
    expect(useCanvasStore.getState().editSequence).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(api.getArtifactImport).toHaveBeenCalledTimes(3);
    expect(mediaDataAt(0).artifactRevisionId).toBeNull();
    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();

    await act(async () => {
      uploadPending.resolve(revision());
      await uploadPending.promise;
    });

    expect(screen.getByTestId("phase").textContent).toBe("completed");
    expect(mediaDataAt(0).artifactRevisionId).toBe(NEW_REVISION_ID);
    expect(useCanvasStore.getState().editSequence).toBe(1);
    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();
  });

  it("keeps the authoritative PUT alive when read-only observation fails", async () => {
    vi.useFakeTimers();
    loadNodes([mediaNode(NODE_ID)]);
    const uploadPending = deferred<ArtifactRevision>();
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "CREATED"),
    );
    api.uploadArtifactContent.mockReturnValue(uploadPending.promise);
    api.getArtifactImport.mockRejectedValueOnce(new TypeError("observer offline"));
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(250);
    });

    expect(api.getArtifactImport).toHaveBeenCalledOnce();
    expect(screen.getByTestId("phase").textContent).toBe("receiving");
    expect(controls?.active).toBe(true);

    await act(async () => {
      uploadPending.resolve(revision());
      await uploadPending.promise;
    });

    expect(screen.getByTestId("phase").textContent).toBe("completed");
    expect(mediaDataAt(0).artifactRevisionId).toBe(NEW_REVISION_ID);
    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();
  });

  it("ignores a mismatched observer session without rebinding or cancelling the PUT", async () => {
    vi.useFakeTimers();
    loadNodes([mediaNode(NODE_ID)]);
    const uploadPending = deferred<ArtifactRevision>();
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "CREATED"),
    );
    api.uploadArtifactContent.mockReturnValue(uploadPending.promise);
    api.getArtifactImport.mockImplementationOnce(async () => ({
      ...session(lastDeclaration(), "VALIDATING"),
      client_import_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    }));
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(250);
    });

    expect(api.getArtifactImport).toHaveBeenCalledOnce();
    expect(screen.getByTestId("phase").textContent).toBe("receiving");
    expect(screen.queryByRole("alert")).toBeNull();

    await act(async () => {
      uploadPending.resolve(revision());
      await uploadPending.promise;
    });
    expect(screen.getByTestId("phase").textContent).toBe("completed");
    expect(useCanvasStore.getState().editSequence).toBe(1);
  });

  it("aborts PUT and observer together and ignores both late results after unmount", async () => {
    vi.useFakeTimers();
    loadNodes([mediaNode(NODE_ID)]);
    const uploadPending = deferred<ArtifactRevision>();
    const observerPending = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "CREATED"),
    );
    api.uploadArtifactContent.mockReturnValue(uploadPending.promise);
    api.getArtifactImport.mockReturnValue(observerPending.promise);
    const rendered = renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();
    expect(api.getArtifactImport).toHaveBeenCalledOnce();
    const putSignal = api.uploadArtifactContent.mock.calls[0][3] as AbortSignal;
    const observerSignal = api.getArtifactImport.mock.calls[0][2] as AbortSignal;

    rendered.unmount();
    expect(putSignal.aborted).toBe(true);
    expect(observerSignal.aborted).toBe(true);
    await act(async () => {
      observerPending.resolve(
        session(lastDeclaration(), "COMPLETED", { artifactRevision: revision() }),
      );
      uploadPending.resolve(revision());
      await Promise.all([observerPending.promise, uploadPending.promise]);
    });

    expect(useCanvasStore.getState().editSequence).toBe(0);
    expect(mediaDataAt(0).artifactRevisionId).toBeNull();
  });

  it("attaches a completed init replay without uploading and ignores duplicate completion", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "COMPLETED", { artifactRevision: revision() }),
    );
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));

    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().editSequence).toBe(1);
    expect(mediaDataAt(0).artifactRevisionId).toBe(
      NEW_REVISION_ID,
    );
  });

  it.each([
    ["byte size", { byte_size: 4 }],
    ["source kind", { source_kind: "external" }],
    [
      "source project",
      { source_project_id: "ffffffff-ffff-4fff-8fff-ffffffffffff" },
    ],
  ])("rejects completed %s context before attach", async (_case, override) => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "COMPLETED", {
          artifactRevision: { ...revision(), ...override },
        }),
    );
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await waitFor(() =>
      expect(screen.getByTestId("phase").textContent).toBe("final_error"),
    );

    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
    expect(mediaDataAt(0).artifactRevisionId).toBeNull();
    expect(useCanvasStore.getState().editSequence).toBe(0);
  });

  it("retries a retryable failure with the same client id and single-flights double retry", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(
          declaration,
          api.createArtifactImport.mock.calls.length === 1
            ? "CREATED"
            : "FAILED_RETRYABLE",
        ),
    );
    api.uploadArtifactContent
      .mockRejectedValueOnce(new ArtifactImportFailedError(IMPORT_ID, "probe_timeout"))
      .mockResolvedValueOnce(revision());
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await screen.findByRole("alert");
    expect(screen.getByTestId("phase").textContent).toBe("retryable_error");
    expect(screen.getByTestId("active").textContent).toBe("false");
    const originalClientId = lastDeclaration().clientImportId;

    act(() => {
      controls?.retry();
      controls?.retry();
    });
    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));

    expect(api.createArtifactImport).toHaveBeenCalledTimes(2);
    expect(lastDeclaration().clientImportId).toBe(originalClientId);
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(2);
    expect(useCanvasStore.getState().editSequence).toBe(1);
  });

  it("keeps retryable state when another operation owns the target, then atomically reacquires with the same id", async () => {
    const dependencies = createMediaImportRuntimeDependencies();
    const createAttempt = vi.spyOn(dependencies.core, "createAttempt");
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, current: ArtifactImportDeclaration) =>
        session(
          current,
          api.createArtifactImport.mock.calls.length === 1
            ? "CREATED"
            : "FAILED_RETRYABLE",
        ),
    );
    api.uploadArtifactContent
      .mockRejectedValueOnce(
        new ArtifactImportFailedError(IMPORT_ID, "upload_interrupted"),
      )
      .mockResolvedValueOnce(revision());
    renderHarness(NODE_ID, () => undefined, dependencies);
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(controls?.phase).toBe("retryable_error"));
    const originalClientId = lastDeclaration().clientImportId;
    const contender = dependencies.targetLocks.acquire("contender", NODE_ID);
    expect(contender).not.toBeNull();
    const brokerBefore = dependencies.permits.getSnapshot();

    act(() => controls?.retry());

    expect(controls?.phase).toBe("retryable_error");
    expect(controls?.errorMessage).toContain("目标正被其他导入操作使用");
    expect(controls?.recoveryKind).toBe("retry");
    expect(createAttempt).toHaveBeenCalledTimes(1);
    expect(api.createArtifactImport).toHaveBeenCalledTimes(1);
    expect(dependencies.permits.getSnapshot()).toBe(brokerBefore);

    contender?.release();
    act(() => controls?.retry());
    await waitFor(() => expect(controls?.phase).toBe("completed"));
    expect(lastDeclaration().clientImportId).toBe(originalClientId);
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(2);
  });

  it.each([
    "node removed",
    "revision changed",
    "load epoch changed",
    "project changed",
  ] as const)(
    "rechecks %s inside the retry lease immediately before POST and requires reselection",
    async (staleCase) => {
      const dependencies = createMediaImportRuntimeDependencies();
      loadNodes([mediaNode(NODE_ID)]);
      api.createArtifactImport.mockImplementation(
        async (_projectId: string, current: ArtifactImportDeclaration) =>
          session(current, "CREATED"),
      );
      api.uploadArtifactContent.mockRejectedValueOnce(
        new ArtifactImportFailedError(IMPORT_ID, "upload_interrupted"),
      );
      renderHarness(NODE_ID, () => undefined, dependencies);
      act(() => controls?.selectFile(imageFile()));
      await waitFor(() => expect(controls?.phase).toBe("retryable_error"));
      const originalClientId = lastDeclaration().clientImportId;
      dependencies.permits.holdQueue("test-preflight");

      act(() => controls?.retry());
      expect(controls?.phase).toBe("queued");
      act(() => {
        if (staleCase === "node removed") {
          useCanvasStore.getState().deleteNode(NODE_ID);
        } else if (staleCase === "revision changed") {
          useCanvasStore.setState({
            nodes: [mediaNode(NODE_ID, OTHER_REVISION_ID, "image")],
          });
        } else if (staleCase === "load epoch changed") {
          loadNodes([mediaNode(NODE_ID)]);
        } else {
          useCanvasStore.getState().load({
            projectId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            nodes: [mediaNode(NODE_ID)],
            edges: [],
            shots: [],
            revisionId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
          });
        }
        dependencies.permits.releaseQueueHold("test-preflight");
      });

      await waitFor(() => expect(controls?.phase).toBe("final_error"));
      expect(controls?.recoveryKind).toBe("reselect");
      expect(api.createArtifactImport).toHaveBeenCalledTimes(1);
      expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
      expect(dependencies.targetLocks.getSnapshot().protectedNodeIds).toEqual([]);

      act(() => loadNodes([mediaNode(NODE_ID)]));
      api.createArtifactImport.mockImplementationOnce(
        async (_projectId: string, current: ArtifactImportDeclaration) =>
          session(current, "COMPLETED", { artifactRevision: revision() }),
      );
      act(() => controls?.selectFile(imageFile()));
      await waitFor(() => expect(controls?.phase).toBe("completed"));
      expect(lastDeclaration().clientImportId).not.toBe(originalClientId);
    },
  );

  it("classifies a typed declaration failure as final, releases it, and reselection gets a new id", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockRejectedValueOnce(
      new ArtifactDeclarationValidationError("invalid local declaration"),
    );
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await waitFor(() =>
      expect(screen.getByTestId("phase").textContent).toBe("final_error"),
    );
    const rejectedClientId = lastDeclaration().clientImportId;
    expect(controls?.recoveryKind).toBe("reselect");
    act(() => controls?.retry());
    expect(api.createArtifactImport).toHaveBeenCalledOnce();

    api.createArtifactImport.mockImplementationOnce(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "COMPLETED", { artifactRevision: revision() }),
    );
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() =>
      expect(screen.getByTestId("phase").textContent).toBe("completed"),
    );

    expect(api.createArtifactImport).toHaveBeenCalledTimes(2);
    expect(lastDeclaration().clientImportId).not.toBe(rejectedClientId);
    expect(mediaDataAt(0).artifactRevisionId).toBe(NEW_REVISION_ID);
  });

  it("requires a new file selection and client id after a final failure", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "CREATED", {
          importId:
            api.createArtifactImport.mock.calls.length === 1
              ? IMPORT_ID
              : "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        }),
    );
    api.uploadArtifactContent
      .mockRejectedValueOnce(new ArtifactImportFailedError(IMPORT_ID, "corrupt_media"))
      .mockResolvedValueOnce(revision());
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await screen.findByRole("alert");
    expect(screen.getByTestId("phase").textContent).toBe("final_error");
    const firstClientId = lastDeclaration().clientImportId;
    act(() => controls?.retry());
    expect(api.createArtifactImport).toHaveBeenCalledOnce();

    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));
    expect(api.createArtifactImport).toHaveBeenCalledTimes(2);
    expect(lastDeclaration().clientImportId).not.toBe(firstClientId);
  });

  it.each(["load epoch", "expected old revision"] as const)(
    "does not retry an obsolete operation after %s changes",
    async (changedContext) => {
      loadNodes([mediaNode(NODE_ID)]);
      api.createArtifactImport.mockImplementation(
        async (_projectId: string, declaration: ArtifactImportDeclaration) =>
          session(declaration, "CREATED"),
      );
      api.uploadArtifactContent.mockRejectedValueOnce(
        new ArtifactImportFailedError(IMPORT_ID, "upload_interrupted"),
      );
      renderHarness();
      act(() => controls?.selectFile(imageFile()));
      await waitFor(() =>
        expect(screen.getByTestId("phase").textContent).toBe("retryable_error"),
      );
      const createCalls = api.createArtifactImport.mock.calls.length;
      const uploadCalls = api.uploadArtifactContent.mock.calls.length;

      act(() => {
        if (changedContext === "load epoch") {
          loadNodes([mediaNode(NODE_ID)]);
        } else {
          useCanvasStore.setState({
            nodes: [mediaNode(NODE_ID, OTHER_REVISION_ID, "image")],
            status: "clean",
            editSequence: 0,
            savedSequence: 0,
          });
        }
        controls?.retry();
      });

      expect(api.createArtifactImport).toHaveBeenCalledTimes(createCalls);
      expect(api.uploadArtifactContent).toHaveBeenCalledTimes(uploadCalls);
      expect(controls?.recoveryKind).toBe("reselect");
    },
  );

  it.each(["graph conflict", "dirty replacement"] as const)(
    "does not enter Broker/Core when retry is blocked by %s",
    async (blockedBy) => {
      const dependencies = createMediaImportRuntimeDependencies();
      loadNodes([mediaNode(NODE_ID, OLD_REVISION_ID, "image")]);
      api.createArtifactImport.mockImplementation(
        async (_projectId: string, current: ArtifactImportDeclaration) =>
          session(current, "CREATED"),
      );
      api.uploadArtifactContent.mockRejectedValueOnce(
        new ArtifactImportFailedError(IMPORT_ID, "upload_interrupted"),
      );
      renderHarness(NODE_ID, () => undefined, dependencies);
      act(() => controls?.selectFile(imageFile()));
      await waitFor(() => expect(controls?.phase).toBe("retryable_error"));
      const brokerBefore = dependencies.permits.getSnapshot();

      act(() => {
        if (blockedBy === "graph conflict") {
          useCanvasStore.getState().markConflict(
            useCanvasStore.getState().loadEpoch,
            PROJECT_ID,
            "server-revision",
          );
        } else {
          useCanvasStore.setState({
            status: "dirty",
            editSequence: 1,
            savedSequence: 0,
          });
        }
        controls?.retry();
      });
      await act(async () => {
        await Promise.resolve();
      });

      expect(controls?.phase).toBe("retryable_error");
      expect(api.createArtifactImport).toHaveBeenCalledTimes(1);
      expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
      expect(dependencies.permits.getSnapshot()).toBe(brokerBefore);
    },
  );

  it.each(["graph conflict", "dirty replacement"] as const)(
    "rechecks %s after retry queueing and before POST, preserving the retry",
    async (blockedBy) => {
      const dependencies = createMediaImportRuntimeDependencies();
      loadNodes([mediaNode(NODE_ID, OLD_REVISION_ID, "image")]);
      api.createArtifactImport.mockImplementation(
        async (_projectId: string, current: ArtifactImportDeclaration) =>
          session(current, "CREATED"),
      );
      api.uploadArtifactContent
        .mockRejectedValueOnce(
          new ArtifactImportFailedError(IMPORT_ID, "upload_interrupted"),
        )
        .mockResolvedValueOnce(revision());
      renderHarness(NODE_ID, () => undefined, dependencies);
      act(() => controls?.selectFile(imageFile()));
      await waitFor(() => expect(controls?.phase).toBe("retryable_error"));
      const originalClientId = lastDeclaration().clientImportId;
      dependencies.permits.holdQueue("retry-gate");
      act(() => controls?.retry());
      expect(controls?.phase).toBe("queued");

      act(() => {
        if (blockedBy === "graph conflict") {
          useCanvasStore.getState().markConflict(
            useCanvasStore.getState().loadEpoch,
            PROJECT_ID,
            "server-revision",
          );
        } else {
          useCanvasStore.setState({
            status: "dirty",
            editSequence: 1,
            savedSequence: 0,
          });
        }
        dependencies.permits.releaseQueueHold("retry-gate");
      });

      await waitFor(() => expect(controls?.phase).toBe("retryable_error"));
      expect(api.createArtifactImport).toHaveBeenCalledTimes(1);
      expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
      expect(dependencies.targetLocks.getSnapshot().protectedNodeIds).toEqual([]);

      act(() =>
        useCanvasStore.setState({
          status: "clean",
          conflictRevisionId: null,
          editSequence: 0,
          savedSequence: 0,
        }),
      );
      act(() => controls?.retry());
      await waitFor(() => expect(controls?.phase).toBe("completed"));
      expect(lastDeclaration().clientImportId).toBe(originalClientId);
      expect(api.uploadArtifactContent).toHaveBeenCalledTimes(2);
    },
  );

  it("reconciles an ambiguous upload by POSTing the same declaration and never sends a second PUT", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        api.createArtifactImport.mock.calls.length === 1
          ? session(declaration, "CREATED")
          : session(declaration, "COMPLETED", { artifactRevision: revision() }),
    );
    api.uploadArtifactContent.mockRejectedValueOnce(new TypeError("connection lost"));
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await screen.findByRole("alert");
    const clientId = lastDeclaration().clientImportId;
    act(() => controls?.retry());
    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));

    expect(api.createArtifactImport).toHaveBeenCalledTimes(2);
    expect(lastDeclaration().clientImportId).toBe(clientId);
    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();
  });

  it.each(["content_replay_mismatch", "invalid_state"] as const)(
    "does not attach a different completed revision after %s",
    async (reason) => {
      loadNodes([mediaNode(NODE_ID)]);
      api.createArtifactImport.mockImplementation(
        async (_projectId: string, declaration: ArtifactImportDeclaration) =>
          session(declaration, "CREATED"),
      );
      api.uploadArtifactContent.mockRejectedValue(
        new ArtifactImportConflictError(IMPORT_ID, reason),
      );
      renderHarness();

      act(() => controls?.selectFile(imageFile()));
      await waitFor(() =>
        expect(screen.getByTestId("phase").textContent).toBe("final_error"),
      );

      expect(api.getArtifactImport).not.toHaveBeenCalled();
      expect(api.uploadArtifactContent).toHaveBeenCalledOnce();
      expect(screen.getByRole("alert").textContent).toContain("重新选择");
      expect(controls?.recoveryKind).toBe("reselect");
      expect(mediaDataAt(0).artifactRevisionId).toBeNull();
      expect(useCanvasStore.getState().editSequence).toBe(0);
    },
  );

  it("polls upload_in_progress and attaches the completed server result without another PUT", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "CREATED"),
    );
    api.uploadArtifactContent.mockRejectedValue(
      new ArtifactImportConflictError(IMPORT_ID, "upload_in_progress"),
    );
    api.getArtifactImport.mockImplementation(async () =>
      session(lastDeclaration(), "COMPLETED", { artifactRevision: revision() }),
    );
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));

    expect(api.uploadArtifactContent).toHaveBeenCalledOnce();
    expect(api.getArtifactImport).toHaveBeenCalledOnce();
    expect(mediaDataAt(0).artifactRevisionId).toBe(
      NEW_REVISION_ID,
    );
  });

  it.each([
    ["source project", { source_project_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }],
    ["client import id", { client_import_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }],
    ["import id", { import_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }],
    ["filename", { original_filename: "another.png" }],
    ["byte size", { expected_byte_size: 4 }],
    ["media kind", { requested_media_kind: "video" }],
    ["content type", { declared_content_type: "image/jpeg" }],
    ["replacement", { replace_revision_id: OLD_REVISION_ID }],
  ] as const)(
    "rejects an upload_in_progress reconciliation with a mismatched %s binding",
    async (_label, mismatch) => {
      loadNodes([mediaNode(NODE_ID)]);
      api.createArtifactImport.mockRejectedValue(
        new ArtifactImportConflictError(IMPORT_ID, "upload_in_progress"),
      );
      api.getArtifactImport.mockImplementation(async () => ({
        ...session(lastDeclaration(), "COMPLETED", { artifactRevision: revision() }),
        ...mismatch,
      }));
      renderHarness();

      act(() => controls?.selectFile(imageFile()));
      await waitFor(() =>
        expect(screen.getByTestId("phase").textContent).toBe("final_error"),
      );

      expect(api.uploadArtifactContent).not.toHaveBeenCalled();
      expect(mediaDataAt(0).artifactRevisionId).toBeNull();
      expect(useCanvasStore.getState().editSequence).toBe(0);
      expect(controls?.recoveryKind).toBe("reselect");
    },
  );

  it("checks the initial session binding before uploading", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) => ({
        ...session(declaration, "CREATED"),
        original_filename: "another.png",
      }),
    );
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await waitFor(() =>
      expect(screen.getByTestId("phase").textContent).toBe("final_error"),
    );

    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
    expect(mediaDataAt(0).artifactRevisionId).toBeNull();
  });

  it("checks every polled session binding before following its state", async () => {
    vi.useFakeTimers();
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "RECEIVING"),
    );
    api.getArtifactImport.mockImplementation(async () => ({
      ...session(lastDeclaration(), "COMPLETED", { artifactRevision: revision() }),
      expected_byte_size: 4,
    }));
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(screen.getByTestId("phase").textContent).toBe("final_error");
    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
    expect(mediaDataAt(0).artifactRevisionId).toBeNull();
  });

  it.each(["declaration_mismatch", "invalid_replacement"] as const)(
    "treats init conflict %s as final without upload or poll",
    async (reason) => {
      loadNodes([mediaNode(NODE_ID)]);
      api.createArtifactImport.mockRejectedValue(
        new ArtifactImportConflictError(IMPORT_ID, reason),
      );
      renderHarness();

      act(() => controls?.selectFile(imageFile()));
      await waitFor(() =>
        expect(screen.getByTestId("phase").textContent).toBe("final_error"),
      );

      expect(api.uploadArtifactContent).not.toHaveBeenCalled();
      expect(api.getArtifactImport).not.toHaveBeenCalled();
      if (reason === "invalid_replacement") {
        expect(screen.getByRole("alert").textContent).toContain("重新加载");
        expect(controls?.recoveryKind).toBe("reload_project");
      } else {
        expect(screen.getByRole("alert").textContent).toContain("重新选择");
        expect(controls?.recoveryKind).toBe("reselect");
      }
    },
  );

  it("polls RECEIVING and VALIDATING sessions without starting a second upload", async () => {
    vi.useFakeTimers();
    loadNodes([mediaNode(NODE_ID)]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "RECEIVING"),
    );
    api.getArtifactImport
      .mockImplementationOnce(async () =>
        session(lastDeclaration(), "VALIDATING"),
      )
      .mockImplementationOnce(async () =>
        session(lastDeclaration(), "COMPLETED", { artifactRevision: revision() }),
      );
    renderHarness();

    act(() => controls?.selectFile(imageFile()));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(screen.getByTestId("phase").textContent).toBe("completed");
    expect(api.getArtifactImport).toHaveBeenCalledTimes(2);
    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
  });

  it("ignores late completion after project epoch changes, node removal, or old revision changes", async () => {
    for (const staleCase of ["epoch", "removed", "old-changed"] as const) {
      cleanup();
      controls = null;
      loadNodes([mediaNode(NODE_ID)]);
      const pending = deferred<ArtifactImportSession>();
      api.createArtifactImport.mockReset();
      api.createArtifactImport.mockReturnValue(pending.promise);
      renderHarness();
      act(() => controls?.selectFile(imageFile()));
      await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledOnce());
      const declaration = lastDeclaration();

      act(() => {
        if (staleCase === "epoch") {
          useCanvasStore.getState().load({
            projectId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            nodes: [mediaNode(NODE_ID)],
            edges: [],
            shots: [],
            revisionId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          });
        } else if (staleCase === "removed") {
          useCanvasStore.getState().deleteNode(NODE_ID);
        } else {
          useCanvasStore.setState({
            nodes: [mediaNode(NODE_ID, OTHER_REVISION_ID, "image")],
          });
        }
      });
      await act(async () => {
        pending.resolve(
          session(declaration, "COMPLETED", { artifactRevision: revision() }),
        );
        await pending.promise;
      });

      const state = useCanvasStore.getState();
      expect(
        state.nodes.some(
          (node) =>
            node.data.kind === "media_input" &&
            node.data.artifactRevisionId === NEW_REVISION_ID,
        ),
      ).toBe(false);
    }
  });

  it("replaces only the selected clean copy and sends its current revision", async () => {
    loadNodes([
      mediaNode(NODE_ID, OLD_REVISION_ID, "image"),
      mediaNode(COPY_NODE_ID, OLD_REVISION_ID, "image"),
    ]);
    api.createArtifactImport.mockImplementation(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "COMPLETED", { artifactRevision: revision() }),
    );
    renderHarness();

    expect(controls?.replacementBlockedReason).toBeNull();
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));

    expect(lastDeclaration().replaceRevisionId).toBe(OLD_REVISION_ID);
    expect(mediaDataAt(0).artifactRevisionId).toBe(NEW_REVISION_ID);
    expect(mediaDataAt(1).artifactRevisionId).toBe(OLD_REVISION_ID);
  });

  it.each([
    ["image", "video", new File(["vid"], "clip.mp4", { type: "video/mp4" })],
    ["video", "image", new File(["png"], "still.png", { type: "image/png" })],
    ["image", "video", new File([], "empty.mp4", { type: "video/mp4" })],
  ] as const)(
    "rejects replacing an attached %s with a selected %s before POST",
    (attachedKind, _selectedKind, selectedFile) => {
      loadNodes([mediaNode(NODE_ID, OLD_REVISION_ID, attachedKind)]);
      renderHarness();

      act(() => controls?.selectFile(selectedFile));

      expect(screen.getByTestId("phase").textContent).toBe("final_error");
      expect(controls?.recoveryKind).toBe("reselect");
      expect(screen.getByRole("alert").textContent).toContain("相同类型");
      expect(screen.getByRole("alert").textContent).toContain("新建");
      expect(api.createArtifactImport).not.toHaveBeenCalled();
      expect(api.getArtifactImport).not.toHaveBeenCalled();
      expect(api.uploadArtifactContent).not.toHaveBeenCalled();
      expect(mediaDataAt(0).artifactRevisionId).toBe(OLD_REVISION_ID);
      expect(mediaDataAt(0).mediaKind).toBe(attachedKind);
      expect(useCanvasStore.getState().editSequence).toBe(0);
    },
  );

  it.each([
    ["filename", new File(["png"], `${"x".repeat(513)}.png`, { type: "image/png" })],
    ["MIME", new File(["png"], "safe.png", { type: "x".repeat(256) })],
  ])(
    "treats an invalid local declaration %s as final and releases it for reselection",
    async (_case, invalidFile) => {
      loadNodes([mediaNode(NODE_ID)]);
      renderHarness();

      act(() => controls?.selectFile(invalidFile));

      expect(screen.getByTestId("phase").textContent).toBe("final_error");
      expect(controls?.recoveryKind).toBe("reselect");
      expect(api.createArtifactImport).not.toHaveBeenCalled();
      expect(api.getArtifactImport).not.toHaveBeenCalled();
      expect(api.uploadArtifactContent).not.toHaveBeenCalled();

      api.createArtifactImport.mockImplementationOnce(
        async (_projectId: string, declaration: ArtifactImportDeclaration) =>
          session(declaration, "COMPLETED", { artifactRevision: revision() }),
      );
      act(() => controls?.selectFile(imageFile()));
      await waitFor(() =>
        expect(screen.getByTestId("phase").textContent).toBe("completed"),
      );
      expect(api.createArtifactImport).toHaveBeenCalledOnce();
      expect(mediaDataAt(0).artifactRevisionId).toBe(NEW_REVISION_ID);
    },
  );

  it("blocks replacement until the graph is clean and blocks any new session in conflict", () => {
    loadNodes([mediaNode(NODE_ID, OLD_REVISION_ID, "image")]);
    useCanvasStore.setState({ status: "dirty", editSequence: 1, savedSequence: 0 });
    renderHarness();

    expect(screen.getByTestId("replacement-block").textContent).toContain("保存");
    act(() => controls?.selectFile(imageFile()));
    expect(api.createArtifactImport).not.toHaveBeenCalled();

    act(() => {
      useCanvasStore.getState().markConflict(
        useCanvasStore.getState().loadEpoch,
        PROJECT_ID,
        "server-revision",
      );
      controls?.selectFile(imageFile());
    });
    expect(api.createArtifactImport).not.toHaveBeenCalled();
  });

  it("blocks selection when the provider project no longer matches the loaded canvas", () => {
    useCanvasStore.getState().load({
      projectId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      nodes: [mediaNode(NODE_ID)],
      edges: [],
      shots: [],
      revisionId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    });
    renderHarness();

    expect(controls?.selectionDisabled).toBe(true);
    expect(controls?.replacementBlockedReason).toContain("项目");
    act(() => controls?.selectFile(imageFile()));
    expect(api.createArtifactImport).not.toHaveBeenCalled();
  });

  it("lets an in-flight completion attach while preserving a later graph conflict", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    const pending = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockReturnValue(pending.promise);
    renderHarness();
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledOnce());
    const declaration = lastDeclaration();

    act(() =>
      useCanvasStore.getState().markConflict(
        useCanvasStore.getState().loadEpoch,
        PROJECT_ID,
        "server-revision",
      ),
    );
    await act(async () => {
      pending.resolve(
        session(declaration, "COMPLETED", { artifactRevision: revision() }),
      );
      await pending.promise;
    });

    expect(useCanvasStore.getState()).toMatchObject({
      status: "conflict",
      conflictRevisionId: "server-revision",
      editSequence: 1,
    });
    expect(mediaDataAt(0).artifactRevisionId).toBe(
      NEW_REVISION_ID,
    );
  });

  it("aborts an intentional in-flight request on node unmount without surfacing an error", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    const pending = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockReturnValue(pending.promise);
    const rendered = renderHarness();
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledOnce());
    const signal = api.createArtifactImport.mock.calls[0][2] as AbortSignal;

    rendered.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      pending.reject(new DOMException("Aborted", "AbortError"));
      await pending.promise.catch(() => undefined);
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("synchronously and idempotently abandons every B1 resource and ignores a late completion", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    const pending = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockReturnValue(pending.promise);
    const dependencies = createMediaImportRuntimeDependencies();
    const createAttempt = vi.spyOn(dependencies.core, "createAttempt");
    const activeCounts: number[] = [];
    const runtime = createMediaImportRuntime(PROJECT_ID, dependencies);
    runtime.setActiveCallback((count) => activeCounts.push(count));
    runtime.mount();
    runtime.selectFile(NODE_ID, imageFile());
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledOnce());
    const declaration = lastDeclaration();
    const signal = api.createArtifactImport.mock.calls[0][2] as AbortSignal;
    const attempt = createAttempt.mock.results[0].value;
    const releaseFile = vi.spyOn(attempt, "releaseFile");
    expect(dependencies.permits.getSnapshot().runningOperationIds).toHaveLength(1);
    expect(dependencies.targetLocks.getSnapshot().protectedNodeIds).toEqual([
      NODE_ID,
    ]);
    expect(activeCounts).toContain(1);

    expect(runtime.abandonAllNow()).toBe(0);
    expect(runtime.abandonAllNow()).toBe(0);

    expect(signal.aborted).toBe(true);
    expect(releaseFile).toHaveBeenCalledOnce();
    expect(dependencies.permits.getSnapshot().runningOperationIds).toEqual([]);
    expect(dependencies.permits.getSnapshot().queuedOperationIds).toEqual([]);
    expect(dependencies.targetLocks.getSnapshot().protectedNodeIds).toEqual([]);
    expect(activeCounts.at(-1)).toBe(0);
    expect(runtime.getPendingOperationCount()).toBe(0);

    runtime.mount();
    runtime.selectFile(NODE_ID, imageFile());
    expect(createAttempt).toHaveBeenCalledOnce();
    expect(runtime.getPendingOperationCount()).toBe(0);
    expect(dependencies.permits.getSnapshot().runningOperationIds).toEqual([]);
    expect(dependencies.permits.getSnapshot().queuedOperationIds).toEqual([]);

    await act(async () => {
      pending.resolve(
        session(declaration, "COMPLETED", { artifactRevision: revision() }),
      );
      await pending.promise;
    });
    expect(mediaDataAt(0).artifactRevisionId).toBeNull();
    expect(useCanvasStore.getState().editSequence).toBe(0);
  });

  it("counts simultaneous imports and releases only the operation that leaves", async () => {
    loadNodes([mediaNode(NODE_ID), mediaNode(COPY_NODE_ID)]);
    const first = deferred<ArtifactImportSession>();
    const second = deferred<ArtifactImportSession>();
    api.createArtifactImport
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const activeCounts: number[] = [];
    render(
      <MediaImportProvider
        onActiveCountChange={(count) => activeCounts.push(count)}
        projectId={PROJECT_ID}
      >
        <Harness nodeId={NODE_ID} />
        <Harness nodeId={COPY_NODE_ID} />
      </MediaImportProvider>,
    );

    act(() => {
      controlsByNode.get(NODE_ID)?.selectFile(imageFile());
      controlsByNode
        .get(COPY_NODE_ID)
        ?.selectFile(new File(["png"], "second.png", { type: "image/png" }));
    });
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledTimes(2));
    expect(activeCounts).toContain(2);

    const firstDeclaration = api.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    await act(async () => {
      first.resolve(
        session(firstDeclaration, "COMPLETED", { artifactRevision: revision() }),
      );
      await first.promise;
    });
    expect(activeCounts.at(-1)).toBe(1);

    cleanup();
    expect(activeCounts.at(-1)).toBe(0);
    const secondSignal = api.createArtifactImport.mock.calls[1][2] as AbortSignal;
    expect(secondSignal.aborted).toBe(true);
    second.reject(new DOMException("Aborted", "AbortError"));
    await second.promise.catch(() => undefined);
  });

  it("ignores a late old promise after the same node remounts and completes a new operation", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    const oldPending = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockReturnValueOnce(oldPending.promise);
    const firstRender = renderHarness();
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledOnce());
    const oldDeclaration = api.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    const oldSignal = api.createArtifactImport.mock.calls[0][2] as AbortSignal;
    firstRender.unmount();
    expect(oldSignal.aborted).toBe(true);

    controls = null;
    api.createArtifactImport.mockImplementationOnce(
      async (_projectId: string, declaration: ArtifactImportDeclaration) =>
        session(declaration, "COMPLETED", {
          importId: "abababab-abab-4bab-8bab-abababababab",
          artifactRevision: revision(OTHER_REVISION_ID),
        }),
    );
    renderHarness();
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(screen.getByTestId("phase").textContent).toBe("completed"));
    expect(mediaDataAt(0).artifactRevisionId).toBe(
      OTHER_REVISION_ID,
    );

    await act(async () => {
      oldPending.resolve(
        session(oldDeclaration, "COMPLETED", { artifactRevision: revision() }),
      );
      await oldPending.promise;
    });

    expect(mediaDataAt(0).artifactRevisionId).toBe(
      OTHER_REVISION_ID,
    );
    expect(useCanvasStore.getState().editSequence).toBe(1);
  });

  it("survives StrictMode's delayed cleanup and settles without leaking a late operation", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    const pending = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockReturnValue(pending.promise);
    const view = render(
      <StrictMode>
        <MediaImportProvider projectId={PROJECT_ID}>
          <Harness nodeId={NODE_ID} />
        </MediaImportProvider>
      </StrictMode>,
    );
    act(() => controls?.selectFile(imageFile()));
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledOnce());
    const current = lastDeclaration();

    await act(async () => {
      pending.resolve(
        session(current, "COMPLETED", { artifactRevision: revision() }),
      );
      await pending.promise;
    });

    expect(controls?.phase).toBe("completed");
    expect(mediaDataAt(0).artifactRevisionId).toBe(NEW_REVISION_ID);
    view.unmount();
    expect(useCanvasStore.getState().editSequence).toBe(1);
  });

  it("does not let the old project's delayed dispose poison the replacement runtime", async () => {
    loadNodes([mediaNode(NODE_ID)]);
    const view = render(
      <MediaImportProvider projectId={PROJECT_ID}>
        <Harness nodeId={NODE_ID} />
      </MediaImportProvider>,
    );
    const nextProjectId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    act(() => {
      useCanvasStore.getState().load({
        projectId: nextProjectId,
        nodes: [mediaNode(NODE_ID)],
        edges: [],
        shots: [],
        revisionId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      });
      view.rerender(
        <MediaImportProvider projectId={nextProjectId}>
          <Harness nodeId={NODE_ID} />
        </MediaImportProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    api.createArtifactImport.mockImplementationOnce(
      async (_projectId: string, current: ArtifactImportDeclaration) => ({
        ...session(current, "COMPLETED", {
          artifactRevision: {
            ...revision(),
            source_project_id: nextProjectId,
          },
        }),
        source_project_id: nextProjectId,
      }),
    );

    act(() => controls?.selectFile(imageFile()));

    await waitFor(() => expect(controls?.phase).toBe("completed"));
    expect(api.createArtifactImport).toHaveBeenCalledOnce();
  });
});
