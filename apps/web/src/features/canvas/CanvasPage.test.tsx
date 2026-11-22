import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { StrictMode } from "react";
import { DirectorySelectionCancelled } from "../handoff/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ArtifactImportConflictError,
  type ArtifactImportDeclaration,
  type ArtifactImportSession,
  type ArtifactRevision,
} from "../../api/client";
import { useCanvasStore } from "./canvasStore";
import type { MediaImportControls } from "./useMediaImport";
import type { MediaIngressRuntimePort } from "./mediaIngress/mediaIngressContext";
import { CanvasPage } from "./CanvasPage";
import type { ShotAssociationCandidate } from "./CanvasSurface";

const api = vi.hoisted(() => ({
  createProject: vi.fn(),
  createArtifactImport: vi.fn(),
  getArtifactImport: vi.fn(),
  getBranchGraph: vi.fn(),
  getGraph: vi.fn(),
  getProject: vi.fn(),
  listArtifactRevisions: vi.fn(),
  listBranches: vi.fn(),
  listProjects: vi.fn(),
  listShotSlotAssociations: vi.fn(),
  proposePromotion: vi.fn(),
  proposePromotionUndo: vi.fn(),
  decidePromotion: vi.fn(),
  applyPromotion: vi.fn(),
  proposeGraphChangeSet: vi.fn(),
  approveGraphChangeSet: vi.fn(),
  applyGraphChangeSet: vi.fn(),
  saveViewport: vi.fn(),
  uploadArtifactContent: vi.fn(),
}));

const preview = vi.hoisted(() => ({ useArtifactPreview: vi.fn() }));
const createDirectory = vi.hoisted(() => vi.fn());
vi.mock("../handoff/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../handoff/client")>()),
  createDirectoryProject: createDirectory,
}));

const surface = vi.hoisted(() => ({
  receivedProps: null as Record<string, unknown> | null,
  onMoveEnd: null as ((viewport: { x: number; y: number; zoom: number }) => void) | null,
  mediaControls: null as MediaImportControls | null,
  secondaryMediaControls: null as MediaImportControls | null,
  runtime: null as MediaIngressRuntimePort | null,
}));

const historyDrawer = vi.hoisted(() => ({
  receivedProps: null as Record<string, unknown> | null,
}));

const inspector = vi.hoisted(() => ({
  receivedProps: null as Record<string, unknown> | null,
}));

const NOTE_NODE_ID = "99999999-9999-4999-8999-999999999999";

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  ...api,
}));
vi.mock("./useArtifactPreview", () => ({ useArtifactPreview: preview.useArtifactPreview }));
vi.mock("./components/ArtifactHistoryDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./components/ArtifactHistoryDrawer")>();
  return {
    ...actual,
    ArtifactHistoryDrawer: (props: Parameters<typeof actual.ArtifactHistoryDrawer>[0]) => {
      historyDrawer.receivedProps = props as unknown as Record<string, unknown>;
      return <actual.ArtifactHistoryDrawer {...props} />;
    },
  };
});
vi.mock("./components/ShotInspector", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./components/ShotInspector")>();
  return {
    ...actual,
    ShotInspector: (props: Parameters<typeof actual.ShotInspector>[0]) => {
      inspector.receivedProps = props as unknown as Record<string, unknown>;
      return <actual.ShotInspector {...props} />;
    },
  };
});
vi.mock("./CanvasSurface", async () => {
  const { useContext } = await import("react");
  const { useMediaImport } = await import("./useMediaImport");
  const { MediaIngressContext } = await import(
    "./mediaIngress/mediaIngressContext"
  );
  return {
    CanvasSurface: (props: {
      onMoveEnd(viewport: { x: number; y: number; zoom: number }): void;
    }) => {
      surface.receivedProps = props as unknown as Record<string, unknown>;
      surface.onMoveEnd = props.onMoveEnd;
      surface.runtime = useContext(MediaIngressContext);
      surface.mediaControls = useMediaImport(
        "88888888-8888-4888-8888-888888888888",
      );
      surface.secondaryMediaControls = useMediaImport(
        "99999999-9999-4999-8999-999999999998",
      );
      return <div data-testid="mock-canvas-surface" />;
    },
  };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function project(id: string, name = `项目 ${id}`) {
  return {
    id,
    name,
    graph_document_id: `graph-${id}`,
    active_revision_id: `revision-${id}`,
  };
}

type SnapshotFixture = {
  schema_version: 1 | 2;
  nodes?: Array<{
    id: string;
    kind: "text_source" | "prompt" | "note" | "media_input" | "shot";
    position: { x: number; y: number };
    data:
      | { text: string }
      | { artifact_revision_id?: string | null; media_kind?: "image" | "video" | null }
      | { shot_id: string };
  }>;
  edges?: never[];
  shots?: Array<{
    id: string;
    order: number;
    image_revision_id?: string | null;
    video_revision_id?: string | null;
    reference_revision_ids?: string[];
  }>;
};

function graph(
  id: string,
  snapshot: SnapshotFixture = { schema_version: 2 },
) {
  return {
    revision_id: `revision-${id}`,
    graph_document_id: `graph-${id}`,
    sequence: 1,
    snapshot_hash: `hash-${id}`,
    snapshot,
    viewport: { x: 10, y: 20, zoom: 1.5 },
  };
}

function branchWorkspace(id: string) {
  return {
    project_id: id,
    graph_document_id: `graph-${id}`,
    branches: [
      {
        id: `branch-${id}`,
        graph_document_id: `graph-${id}`,
        name: "主线",
        kind: "MAIN" as const,
        forked_from_branch_id: null,
        forked_from_revision_id: `revision-${id}`,
        head: {
          graph_revision_id: `revision-${id}`,
          version: 1,
          graph_revision_sha256: "a".repeat(64),
        },
        created_at: "2026-08-30T00:00:00Z",
        is_selected: true,
      },
    ],
    selection: {
      graph_document_id: `graph-${id}`,
      branch_id: `branch-${id}`,
      graph_revision_id: `revision-${id}`,
      version: 1,
    },
    latest_promotion: null,
    can_undo_latest_promotion: false,
  };
}

function graphChangeSet(projectId: string) {
  return {
    id: "graph-change-p1",
    project_id: projectId,
    correlation_id: "77777777-7777-4777-8777-777777777777",
    base_graph_revision_id: `revision-${projectId}`,
    changeset_sha256: "a".repeat(64),
    snapshot_hash: "b".repeat(64),
    diff: { op: "replace_snapshot" },
    risk_summary: { classes: ["GRAPH_WRITE"] },
    application_target: {
      mode: "DIRECT" as const,
      branch_id: `branch-${projectId}`,
      expected_branch_head_revision_id: `revision-${projectId}`,
      expected_branch_head_version: 1,
      expected_selection_version: 1,
    },
    impact: null,
    state: "PENDING_APPROVAL",
    applied_graph_revision_id: null,
    created_branch_id: null,
    applied_branch_id: null,
    created_at: "2026-08-27T00:00:00Z",
  };
}

function mediaRevision(projectId: string): ArtifactRevision {
  return {
    revision_id: "77777777-7777-4777-8777-777777777777",
    artifact_id: "66666666-6666-4666-8666-666666666666",
    sequence: 2,
    media_kind: "image",
    media_type: "image/png",
    byte_size: 3,
    display_filename: "safe.png",
    metadata: { kind: "image", width: 2, height: 1 },
    source_kind: "import",
    source_project_id: projectId,
    origin_project_id: projectId,
    created_at: "2026-07-21T01:02:03Z",
  };
}

function mediaSession(
  projectId: string,
  declaration: ArtifactImportDeclaration,
  state: ArtifactImportSession["state"],
): ArtifactImportSession {
  return {
    import_id: "55555555-5555-4555-8555-555555555555",
    source_project_id: projectId,
    client_import_id: declaration.clientImportId,
    replace_revision_id: declaration.replaceRevisionId,
    requested_media_kind: declaration.requestedMediaKind,
    original_filename: declaration.filename,
    declared_content_type: declaration.declaredContentType,
    expected_byte_size: declaration.expectedByteSize,
    state,
    received_byte_size: state === "COMPLETED" ? declaration.expectedByteSize : 0,
    failure_code: null,
    created_at: "2026-07-21T01:02:03Z",
    updated_at: "2026-07-21T01:02:04Z",
    artifact_revision: state === "COMPLETED" ? mediaRevision(projectId) : null,
  };
}

async function openProject(id = "p1", name = `项目 ${id}`): Promise<void> {
  history.replaceState({ preserved: true }, "", `/?project=${id}`);
  api.getProject.mockImplementation(async (projectId: string) => project(projectId, name));
  api.getGraph.mockImplementation(async (projectId: string) => graph(projectId));
  render(<CanvasPage />);
  await screen.findByRole("heading", { name });
}

async function openAssociationHarness(name: string) {
  const shotId = "11111111-1111-4111-8111-111111111111";
  const shotNodeId = "33333333-3333-4333-8333-333333333333";
  const mediaNodeId = "44444444-4444-4444-8444-444444444444";
  const mediaRevisionId = "22222222-2222-4222-8222-222222222222";
  api.getProject.mockResolvedValue(project("p1", name));
  api.getGraph.mockResolvedValue(
    graph("p1", {
      schema_version: 2,
      nodes: [
        {
          id: shotNodeId,
          kind: "shot",
          position: { x: 20, y: 30 },
          data: { shot_id: shotId },
        },
        {
          id: mediaNodeId,
          kind: "media_input",
          position: { x: 140, y: 30 },
          data: {
            artifact_revision_id: mediaRevisionId,
            media_kind: "image",
          },
        },
      ],
      shots: [
        {
          id: shotId,
          order: 0,
          image_revision_id: null,
          video_revision_id: null,
          reference_revision_ids: [],
        },
      ],
    }),
  );
  history.replaceState({}, "", "/?project=p1");
  render(<CanvasPage />);
  await screen.findByRole("heading", { name });
  const select = surface.receivedProps?.onShotSelectionChange as
    | ((id: string | null) => void)
    | undefined;
  act(() => {
    select?.(shotId);
    useCanvasStore.getState().beginShotAssociation(shotId, "image");
  });
  await waitFor(() =>
    expect(surface.receivedProps?.associationTarget).toEqual({
      shotId,
      slot: "image",
    }),
  );
  const associate = surface.receivedProps?.onAssociateMedia as
    | ((candidate: ShotAssociationCandidate) => void)
    | undefined;
  if (!associate) throw new Error("association callback unavailable");
  const candidate: ShotAssociationCandidate = {
    nodeId: mediaNodeId,
    target: { shotId, slot: "image" },
    revision: { revisionId: mediaRevisionId, mediaKind: "image" },
    targetProtectedHint: false,
  };
  return {
    associate,
    candidate,
    mediaNodeId,
    mediaRevisionId,
    shotId,
  };
}

async function startUnknownControllerImport() {
  api.createArtifactImport.mockImplementation(
    async (projectId: string, declaration: ArtifactImportDeclaration) =>
      mediaSession(projectId, declaration, "CREATED"),
  );
  api.uploadArtifactContent.mockImplementation(
    (
      _projectId: string,
      _importId: string,
      _file: File,
      signal: AbortSignal,
    ) =>
      new Promise<ArtifactRevision>((_resolve, reject) => {
        const abort = () =>
          reject(new DOMException("Aborted", "AbortError"));
        if (signal.aborted) abort();
        else signal.addEventListener("abort", abort, { once: true });
      }),
  );
  api.getArtifactImport.mockRejectedValue(new TypeError("observer offline"));
  const controller = surface.runtime?.controller;
  if (!controller) throw new Error("controller unavailable");
  let batchId = "";
  act(() => {
    const admission = controller.enqueue({
      projectId: "p1",
      loadEpoch: useCanvasStore.getState().loadEpoch,
      source: "multi_picker",
      candidates: [{
        kind: "file",
        file: new File(["batch"], "batch.png", { type: "image/png" }),
        displayName: "batch.png",
      }],
      anchor: { x: 0, y: 0 },
      targetMode: "create",
      targetBinding: null,
    });
    expect(admission.kind).toBe("accepted");
    if (admission.kind === "accepted") batchId = admission.batchId;
  });
  await waitFor(() => expect(api.uploadArtifactContent).toHaveBeenCalledOnce());
  const item = controller.getSnapshot().batches[0].slots[0].currentItem;
  act(() => controller.cancelItem(batchId, item.itemId));
  await waitFor(() =>
    expect(controller.getSnapshot().batches[0].slots[0].currentItem).toMatchObject({
      state: "CANCELLING",
      canForceRefresh: true,
    }),
  );
  return { batchId, controller };
}

describe("CanvasPage", () => {
  beforeEach(() => {
    cleanup();
    useCanvasStore.getState().reset();
    history.replaceState({ preserved: true }, "", "/");
    for (const mock of Object.values(api)) mock.mockReset();
    surface.onMoveEnd = null;
    surface.receivedProps = null;
    surface.mediaControls = null;
    surface.secondaryMediaControls = null;
    surface.runtime = null;
    historyDrawer.receivedProps = null;
    inspector.receivedProps = null;
    api.listProjects.mockResolvedValue([]);
    api.listBranches.mockImplementation(async (id: string) => branchWorkspace(id));
    api.getBranchGraph.mockImplementation(async (id: string) => ({
      branch: branchWorkspace(id).branches[0],
      selection: branchWorkspace(id).selection,
      revision: graph(id),
      materialization: { unmaterialized: 0, current: 0, stale: 0 },
    }));
    api.listArtifactRevisions.mockResolvedValue([]);
    api.listShotSlotAssociations.mockImplementation(
      async (
        projectId: string,
        shotId: string,
        slot: "image" | "video" | "reference",
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
    api.proposeGraphChangeSet.mockResolvedValue(graphChangeSet("p1"));
    api.approveGraphChangeSet.mockResolvedValue({
      ...graphChangeSet("p1"),
      state: "APPROVED",
    });
    api.applyGraphChangeSet.mockResolvedValue({
      ...graphChangeSet("p1"),
      state: "APPLIED",
      applied_graph_revision_id: "saved-revision",
      applied_branch_id: "branch-p1",
    });
    api.saveViewport.mockResolvedValue(undefined);
    preview.useArtifactPreview.mockImplementation((revisionId: string | null) => ({
      phase: "ready",
      revision: {
        revision_id: revisionId ?? "11111111-1111-4111-8111-111111111111",
        artifact_id: "22222222-2222-4222-8222-222222222222",
        media_kind: "image",
        media_type: "image/png",
        byte_size: 1,
        display_filename: "safe.png",
        metadata: { kind: "image", width: 1, height: 1 },
        source_kind: "import",
        source_project_id: "p1",
        origin_project_id: "p1",
        created_at: "2026-07-29T00:00:00Z",
      },
      objectUrl: null,
      errorMessage: null,
      retryable: false,
      retry: vi.fn(),
    }));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("forwards Shot selection and completes only a valid media association", async () => {
    const shotId = "11111111-1111-4111-8111-111111111111";
    const mediaRevisionId = "22222222-2222-4222-8222-222222222222";
    api.getProject.mockResolvedValue(project("p1", "分镜关联"));
    api.getGraph.mockResolvedValue(
      graph("p1", {
        schema_version: 2,
        nodes: [
          {
            id: "33333333-3333-4333-8333-333333333333",
            kind: "shot",
            position: { x: 20, y: 30 },
            data: { shot_id: shotId },
          },
          {
            id: "44444444-4444-4444-8444-444444444444",
            kind: "media_input",
            position: { x: 140, y: 30 },
            data: { artifact_revision_id: mediaRevisionId, media_kind: "image" },
          },
        ],
        shots: [
          {
            id: shotId,
            order: 0,
            image_revision_id: null,
            video_revision_id: null,
            reference_revision_ids: [],
          },
        ],
      }),
    );
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "分镜关联" });

    const select = surface.receivedProps?.onShotSelectionChange as
      | ((id: string | null) => void)
      | undefined;
    expect(select).toBeTypeOf("function");
    expect(screen.queryByTestId("shot-inspector")).toBeNull();
    act(() => select?.(shotId));
    expect(useCanvasStore.getState().selectedShotId).toBe(shotId);
    const inspector = screen.getByTestId("shot-inspector");
    expect(
      within(inspector).getByText("镜头 ID S-UR72CEYHRT"),
    ).toBeTruthy();

    act(() => useCanvasStore.getState().beginShotAssociation(shotId, "image"));
    await waitFor(() =>
      expect(surface.receivedProps?.associationTarget).toEqual({ shotId, slot: "image" }),
    );
    const associate = surface.receivedProps?.onAssociateMedia as
      | ((candidate: ShotAssociationCandidate) => void)
      | undefined;
    expect(associate).toBeTypeOf("function");
    const edgesBefore = useCanvasStore.getState().edges;
    act(() =>
      associate?.({
        nodeId: "44444444-4444-4444-8444-444444444444",
        target: { shotId, slot: "image" },
        revision: { revisionId: mediaRevisionId, mediaKind: "image" },
        targetProtectedHint: false,
      }),
    );
    expect(useCanvasStore.getState().shots[0]?.imageRevisionId).toBe(mediaRevisionId);
    expect(useCanvasStore.getState().associationTarget).toBeNull();
    expect(useCanvasStore.getState().selectedShotId).toBe(shotId);
    expect(useCanvasStore.getState().edges).toBe(edgesBefore);
    expect(screen.getByTestId("shot-inspector")).toBe(inspector);
    expect(
      within(inspector).getByRole("region", { name: "图片槽位" }).textContent,
    ).toContain("safe.png");

    act(() => select?.(null));
    expect(useCanvasStore.getState().selectedShotId).toBeNull();
    expect(screen.queryByTestId("shot-inspector")).toBeNull();
  });

  it("rechecks the current media lock instead of trusting the Surface association hint", async () => {
    const shotId = "11111111-1111-4111-8111-111111111111";
    const mediaNodeId = "44444444-4444-4444-8444-444444444444";
    const mediaRevisionId = "22222222-2222-4222-8222-222222222222";
    api.getProject.mockResolvedValue(project("p1", "实时锁关联"));
    api.getGraph.mockResolvedValue(
      graph("p1", {
        schema_version: 2,
        nodes: [
          {
            id: "33333333-3333-4333-8333-333333333333",
            kind: "shot",
            position: { x: 20, y: 30 },
            data: { shot_id: shotId },
          },
          {
            id: mediaNodeId,
            kind: "media_input",
            position: { x: 140, y: 30 },
            data: { artifact_revision_id: mediaRevisionId, media_kind: "image" },
          },
        ],
        shots: [
          {
            id: shotId,
            order: 0,
            image_revision_id: null,
            video_revision_id: null,
            reference_revision_ids: [],
          },
        ],
      }),
    );
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "实时锁关联" });

    const select = surface.receivedProps?.onShotSelectionChange as
      | ((id: string | null) => void)
      | undefined;
    act(() => select?.(shotId));
    act(() => useCanvasStore.getState().beginShotAssociation(shotId, "image"));
    await waitFor(() =>
      expect(surface.receivedProps?.associationTarget).toEqual({ shotId, slot: "image" }),
    );
    const lease = surface.runtime?.targetLocks.acquire("late-lock", mediaNodeId);
    expect(lease).not.toBeNull();
    const associate = surface.receivedProps?.onAssociateMedia as
      | ((candidate: ShotAssociationCandidate) => void)
      | undefined;
    const before = useCanvasStore.getState();

    act(() =>
      associate?.({
        nodeId: mediaNodeId,
        target: { shotId, slot: "image" },
        revision: { revisionId: mediaRevisionId, mediaKind: "image" },
        targetProtectedHint: false,
      }),
    );

    expect(useCanvasStore.getState().nodes).toBe(before.nodes);
    expect(useCanvasStore.getState().shots).toBe(before.shots);
    expect(useCanvasStore.getState().edges).toBe(before.edges);
    expect(useCanvasStore.getState().associationTarget).toEqual({ shotId, slot: "image" });
    expect(useCanvasStore.getState().selectedShotId).toBe(shotId);
    lease?.release();
  });

  it("rejects a rendered association candidate after its media revision drifts", async () => {
    const {
      associate,
      candidate,
      mediaNodeId,
      mediaRevisionId,
      shotId,
    } = await openAssociationHarness("拒绝旧素材候选");
    const replacementRevisionId =
      "55555555-5555-4555-8555-555555555555";
    let drifted = false;
    act(() => {
      const current = useCanvasStore.getState();
      drifted = current.attachMediaRevision({
        projectId: "p1",
        loadEpoch: current.loadEpoch,
        nodeId: mediaNodeId,
        expectedRevisionId: mediaRevisionId,
        revisionId: replacementRevisionId,
        mediaKind: "image",
      });
    });
    expect(drifted).toBe(true);
    const before = useCanvasStore.getState();

    act(() => associate(candidate));

    const after = useCanvasStore.getState();
    expect(after.nodes).toBe(before.nodes);
    expect(after.shots).toBe(before.shots);
    expect(after.edges).toBe(before.edges);
    expect(after.editSequence).toBe(before.editSequence);
    expect(after.associationTarget).toEqual({ shotId, slot: "image" });
    expect(after.selectedShotId).toBe(shotId);
  });

  it("rejects a rendered association candidate after its target drifts", async () => {
    const { associate, candidate, shotId } =
      await openAssociationHarness("拒绝旧分镜目标");
    act(() =>
      useCanvasStore
        .getState()
        .beginShotAssociation(shotId, "reference"),
    );
    const before = useCanvasStore.getState();

    act(() => associate(candidate));

    const after = useCanvasStore.getState();
    expect(after.nodes).toBe(before.nodes);
    expect(after.shots).toBe(before.shots);
    expect(after.edges).toBe(before.edges);
    expect(after.editSequence).toBe(before.editSequence);
    expect(after.associationTarget).toEqual({
      shotId,
      slot: "reference",
    });
    expect(after.selectedShotId).toBe(shotId);
  });

  it.each([
    ["an unattached node", null, null],
    ["an incompatible node", "22222222-2222-4222-8222-222222222222", "video"],
  ] as const)(
    "keeps the association target when the callback receives %s",
    async (_case, artifactRevisionId, mediaKind) => {
      const shotId = "11111111-1111-4111-8111-111111111111";
      const mediaNodeId = "44444444-4444-4444-8444-444444444444";
      api.getProject.mockResolvedValue(project("p1", "拒绝关联"));
      api.getGraph.mockResolvedValue(
        graph("p1", {
          schema_version: 2,
          nodes: [
            {
              id: "33333333-3333-4333-8333-333333333333",
              kind: "shot",
              position: { x: 20, y: 30 },
              data: { shot_id: shotId },
            },
            {
              id: mediaNodeId,
              kind: "media_input",
              position: { x: 140, y: 30 },
              data: { artifact_revision_id: artifactRevisionId, media_kind: mediaKind },
            },
          ],
          shots: [
            {
              id: shotId,
              order: 0,
              image_revision_id: null,
              video_revision_id: null,
              reference_revision_ids: [],
            },
          ],
        }),
      );
      history.replaceState({}, "", "/?project=p1");
      render(<CanvasPage />);
      await screen.findByRole("heading", { name: "拒绝关联" });
      const select = surface.receivedProps?.onShotSelectionChange as
        | ((id: string | null) => void)
        | undefined;
      act(() => select?.(shotId));
      act(() => useCanvasStore.getState().beginShotAssociation(shotId, "image"));
      const associate = surface.receivedProps?.onAssociateMedia as
        | ((candidate: ShotAssociationCandidate) => void)
        | undefined;
      const before = useCanvasStore.getState();

      act(() =>
        associate?.({
          nodeId: mediaNodeId,
          target: { shotId, slot: "image" },
          revision: {
            revisionId: "22222222-2222-4222-8222-222222222222",
            mediaKind: mediaKind ?? "image",
          },
          targetProtectedHint: false,
        }),
      );

      expect(useCanvasStore.getState().nodes).toBe(before.nodes);
      expect(useCanvasStore.getState().shots).toBe(before.shots);
      expect(useCanvasStore.getState().edges).toBe(before.edges);
      expect(useCanvasStore.getState().associationTarget).toEqual({ shotId, slot: "image" });
      expect(useCanvasStore.getState().selectedShotId).toBe(shotId);
    },
  );

  it("clears bound history and deletion targets across same-shot project and epoch changes", async () => {
    const shotId = "11111111-1111-4111-8111-111111111111";
    const imageRevisionId = "22222222-2222-4222-8222-222222222222";
    const snapshot = {
      schema_version: 2 as const,
      nodes: [{
        id: "33333333-3333-4333-8333-333333333333",
        kind: "shot" as const,
        position: { x: 20, y: 30 },
        data: { shot_id: shotId },
      }],
      shots: [{
        id: shotId,
        order: 0,
        image_revision_id: imageRevisionId,
        video_revision_id: null,
        reference_revision_ids: [],
      }],
    };
    api.getProject.mockImplementation(async (projectId: string) => project(projectId, `项目 ${projectId}`));
    api.getGraph.mockImplementation(async (projectId: string) => graph(projectId, snapshot));
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目 p1" });

    let trackPointerId = 301;
    const openWorkspaceTargets = () => {
      const select = surface.receivedProps?.onShotSelectionChange as
        | ((id: string | null) => void)
        | undefined;
      act(() => select?.(shotId));
      const historyButton = screen
        .getAllByRole("button", { name: "查看历史" })
        .find((button) => !button.hasAttribute("disabled"));
      if (!historyButton) throw new Error("history trigger unavailable");
      fireEvent.click(historyButton);
      const track = screen.getByTestId("shot-track");
      const topResizeEdge = track.querySelector(
        '[data-floating-panel-resize-edge="top"]',
      );
      if (!(topResizeEdge instanceof HTMLElement)) {
        throw new Error("Shot Track top resize edge unavailable");
      }
      const startY = 100;
      const endY = startY + Number.parseFloat(track.style.height) - 132;
      fireEvent.pointerDown(topResizeEdge, {
        clientX: 100,
        clientY: startY,
        pointerId: trackPointerId,
      });
      fireEvent.pointerMove(topResizeEdge, {
        clientX: 100,
        clientY: endY,
        pointerId: trackPointerId,
      });
      fireEvent.pointerUp(topResizeEdge, {
        clientX: 100,
        clientY: endY,
        pointerId: trackPointerId,
      });
      trackPointerId += 1;
      fireEvent.click(screen.getByRole("button", { name: "删除分镜 1" }));
    };

    openWorkspaceTargets();
    await screen.findByRole("region", { name: "素材版本历史" });
    expect(screen.getByRole("dialog", { name: "删除分镜" })).toBeTruthy();

    const p1Epoch = useCanvasStore.getState().loadEpoch;
    act(() => {
      history.replaceState({}, "", "/?project=p2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByRole("heading", { name: "项目 p2" });
    expect(useCanvasStore.getState().loadEpoch).toBeGreaterThan(p1Epoch);
    expect(screen.queryByRole("region", { name: "素材版本历史" })).toBeNull();
    expect(screen.queryByRole("dialog", { name: "删除分镜" })).toBeNull();
    expect(useCanvasStore.getState().shots.map((shot) => shot.id)).toEqual([shotId]);

    openWorkspaceTargets();
    await screen.findByRole("region", { name: "素材版本历史" });
    expect(screen.getByRole("dialog", { name: "删除分镜" })).toBeTruthy();
    const p2Epoch = useCanvasStore.getState().loadEpoch;
    act(() => useCanvasStore.getState().markConflict(p2Epoch, "p2", "server-revision"));
    fireEvent.click(screen.getByRole("button", { name: "重新加载服务端版本" }));
    await waitFor(() => expect(useCanvasStore.getState().loadEpoch).toBeGreaterThan(p2Epoch));

    expect(screen.queryByRole("region", { name: "素材版本历史" })).toBeNull();
    expect(screen.queryByRole("dialog", { name: "删除分镜" })).toBeNull();
    expect(useCanvasStore.getState().shots.map((shot) => shot.id)).toEqual([shotId]);
  });

  it("captures one association-history anchor and keeps it stable after autosave", async () => {
    const shotId = "11111111-1111-4111-8111-111111111111";
    const imageRevisionId = "22222222-2222-4222-8222-222222222222";
    const anchorRevisionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const savedRevisionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const snapshot = {
      schema_version: 2 as const,
      nodes: [{
        id: "33333333-3333-4333-8333-333333333333",
        kind: "shot" as const,
        position: { x: 20, y: 30 },
        data: { shot_id: shotId },
      }],
      shots: [{
        id: shotId,
        order: 0,
        image_revision_id: imageRevisionId,
        video_revision_id: null,
        reference_revision_ids: [],
      }],
    };
    api.getProject.mockResolvedValue(project("p1", "Anchor project"));
    api.getGraph.mockResolvedValue({
      ...graph("p1", snapshot),
      revision_id: anchorRevisionId,
    });
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "Anchor project" });

    act(() => {
      (surface.receivedProps?.onShotSelectionChange as ((id: string) => void) | undefined)?.(
        shotId,
      );
    });
    await waitFor(() => expect(inspector.receivedProps).not.toBeNull());
    const openHistory = inspector.receivedProps?.onOpenHistory as
      | ((target: { shotId: string; slot: "image"; revisionId: string }) => void)
      | undefined;
    act(() => openHistory?.({ shotId, slot: "image", revisionId: imageRevisionId }));

    await waitFor(() =>
      expect(historyDrawer.receivedProps?.target).toMatchObject({
        shotId,
        slot: "image",
        revisionId: imageRevisionId,
        projectId: "p1",
        anchorRevisionId,
      }),
    );
    const current = useCanvasStore.getState();
    act(() =>
      current.completeSave(
        current.editSequence,
        current.loadEpoch,
        "p1",
        savedRevisionId,
      ),
    );
    expect(useCanvasStore.getState().baseRevisionId).toBe(savedRevisionId);
    expect(historyDrawer.receivedProps?.target).toMatchObject({ anchorRevisionId });
  });

  it("does not create a history target after the slot drifts or without a base revision", async () => {
    const shotId = "11111111-1111-4111-8111-111111111111";
    const imageRevisionId = "22222222-2222-4222-8222-222222222222";
    const replacementRevisionId = "44444444-4444-4444-8444-444444444444";
    const snapshot = {
      schema_version: 2 as const,
      nodes: [{
        id: "33333333-3333-4333-8333-333333333333",
        kind: "shot" as const,
        position: { x: 20, y: 30 },
        data: { shot_id: shotId },
      }],
      shots: [{
        id: shotId,
        order: 0,
        image_revision_id: imageRevisionId,
        video_revision_id: null,
        reference_revision_ids: [],
      }],
    };
    api.getProject.mockResolvedValue(project("p1", "Drift project"));
    api.getGraph.mockResolvedValue(graph("p1", snapshot));
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "Drift project" });
    act(() => {
      (surface.receivedProps?.onShotSelectionChange as ((id: string) => void) | undefined)?.(
        shotId,
      );
    });
    await waitFor(() => expect(inspector.receivedProps).not.toBeNull());
    const openHistory = inspector.receivedProps?.onOpenHistory as
      | ((target: { shotId: string; slot: "image"; revisionId: string }) => void)
      | undefined;

    act(() => {
      useCanvasStore.getState().setShotSlot(shotId, "image", {
        revisionId: replacementRevisionId,
        mediaKind: "image",
      });
      openHistory?.({ shotId, slot: "image", revisionId: imageRevisionId });
    });
    expect(historyDrawer.receivedProps).toBeNull();

    act(() => {
      useCanvasStore.setState({ baseRevisionId: null });
      openHistory?.({ shotId, slot: "image", revisionId: replacementRevisionId });
    });
    expect(historyDrawer.receivedProps).toBeNull();
  });

  it("shows recent projects and creates a trimmed, length-limited blank project", async () => {
    api.listProjects.mockResolvedValue([project("recent", "最近项目")]);
    api.createProject.mockResolvedValue(project("created", "新的创作"));
    api.getProject.mockResolvedValue(project("created", "新的创作"));
    api.getGraph.mockResolvedValue(graph("created"));
    render(<CanvasPage />);

    const input = screen.getByRole("textbox", { name: "项目名称" });
    expect(input).toHaveProperty("value", "未命名创作");
    expect(input).toHaveProperty("maxLength", 200);
    expect(await screen.findByRole("button", { name: "最近项目" })).toBeTruthy();
    fireEvent.change(input, { target: { value: "  新的创作  " } });
    fireEvent.click(screen.getByRole("button", { name: "创建空白项目" }));

    await waitFor(() => expect(api.createProject).toHaveBeenCalledWith("新的创作"));
    expect(new URLSearchParams(location.search).get("project")).toBe("created");
    expect(history.state).toEqual({ preserved: true });
    expect(await screen.findByRole("heading", { name: "新的创作" })).toBeTruthy();
  });

  it("leaves the new-project form available when the native directory picker is cancelled", async () => {
    api.listProjects.mockResolvedValue([]);
    createDirectory.mockRejectedValueOnce(new DirectorySelectionCancelled());
    render(<CanvasPage />);
    const submit = screen.getByRole("button", { name: "创建镜头项目" });
    fireEvent.click(submit);
    await waitFor(() => expect(createDirectory).toHaveBeenCalledOnce());
    await waitFor(() => expect(submit).toHaveProperty("disabled", false));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.createProject).not.toHaveBeenCalled();
    expect(new URLSearchParams(location.search).has("project")).toBe(false);
  });

  it("loads optional snapshot arrays as empty and exposes the explicit graph review control", async () => {
    await openProject("p1", "项目一");

    expect(useCanvasStore.getState().nodes).toEqual([]);
    expect(useCanvasStore.getState().edges).toEqual([]);
    expect(useCanvasStore.getState().shots).toEqual([]);
    expect(useCanvasStore.getState().projectId).toBe("p1");
    expect(useCanvasStore.getState().baseRevisionId).toBe("revision-p1");
    expect(useCanvasStore.getState().viewport).toEqual({ x: 10, y: 20, zoom: 1.5 });
    expect(surface.receivedProps).not.toHaveProperty("mediaImportActive");

    const reviewButton = screen.getByRole("button", { name: "审阅并应用画布变更" });
    expect(reviewButton).toHaveProperty("disabled", true);
    expect(api.proposeGraphChangeSet).not.toHaveBeenCalled();
  });

  it("upgrades a legacy V1 graph in memory before a reviewed change set is proposed", async () => {
    history.replaceState({}, "", "/?project=p1");
    api.getProject.mockResolvedValue(project("p1", "旧项目"));
    api.getGraph.mockResolvedValue(
      graph("p1", {
        schema_version: 1,
        nodes: [
          {
            id: "11111111-1111-4111-8111-111111111111",
            kind: "note",
            position: { x: 1, y: 2 },
            data: { text: "旧草稿" },
          },
        ],
      }),
    );
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "旧项目" });

    const input = useCanvasStore.getState().beginSave();
    expect(input.projectId).toBe("p1");
    expect(input.snapshot).toEqual({
      schema_version: 3,
      nodes: [
        {
          id: "11111111-1111-4111-8111-111111111111",
          kind: "note",
          position: { x: 1, y: 2 },
          data: { text: "旧草稿" },
        },
      ],
      edges: [],
      shots: [],
      resource_bindings: [],
    });
  });

  it("loads complete V2 media and Shot state without losing slot reference order", async () => {
    const shotId = "22222222-2222-4222-8222-222222222222";
    const imageRevision = "33333333-3333-4333-8333-333333333333";
    const referenceA = "44444444-4444-4444-8444-444444444444";
    const referenceB = "55555555-5555-4555-8555-555555555555";
    history.replaceState({}, "", "/?project=p1");
    api.getProject.mockResolvedValue(project("p1", "媒体项目"));
    api.getGraph.mockResolvedValue(
      graph("p1", {
        schema_version: 2,
        nodes: [
          {
            id: "66666666-6666-4666-8666-666666666666",
            kind: "media_input",
            position: { x: 1, y: 2 },
            data: { artifact_revision_id: imageRevision, media_kind: "image" },
          },
          {
            id: "77777777-7777-4777-8777-777777777777",
            kind: "shot",
            position: { x: 3, y: 4 },
            data: { shot_id: shotId },
          },
        ],
        shots: [
          {
            id: shotId,
            order: 0,
            image_revision_id: imageRevision,
            video_revision_id: null,
            reference_revision_ids: [referenceB, referenceA],
          },
        ],
      }),
    );
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "媒体项目" });

    const state = useCanvasStore.getState();
    expect(state.nodes.map((node) => node.data)).toEqual([
      {
        kind: "media_input",
        artifactRevisionId: imageRevision,
        mediaKind: "image",
      },
      { kind: "shot", shotId },
    ]);
    expect(state.shots).toEqual([
      {
        id: shotId,
        order: 0,
        imageRevisionId: imageRevision,
        videoRevisionId: null,
        referenceRevisionIds: [referenceB, referenceA],
      },
    ]);
  });

  it("returns from a clean canvas to recent projects without editing the URL", async () => {
    api.listProjects.mockResolvedValue([project("p1", "项目一")]);
    await openProject("p1", "项目一");

    const returnButton = screen.getByRole("button", { name: "返回项目列表" });
    expect(returnButton).toHaveProperty("disabled", false);
    fireEvent.click(returnButton);

    expect(await screen.findByRole("heading", { name: "最近项目" })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "项目一" })).toBeTruthy();
    expect(new URLSearchParams(location.search).get("project")).toBeNull();
    expect(history.state).toEqual({ preserved: true });
    expect(useCanvasStore.getState().baseRevisionId).toBeNull();
  });

  it("waits for autosave to finish before returning to recent projects", async () => {
    await openProject("p1", "项目一");
    act(() => useCanvasStore.setState({ status: "dirty" }));

    const returnButton = screen.getByRole("button", { name: "返回项目列表" });
    fireEvent.click(returnButton);

    expect(new URLSearchParams(location.search).get("project")).toBe("p1");
    expect(returnButton.textContent).toContain("保存后返回");
    expect(returnButton).toHaveProperty("disabled", true);

    act(() => useCanvasStore.setState({ status: "saving" }));
    expect(new URLSearchParams(location.search).get("project")).toBe("p1");

    act(() => useCanvasStore.setState({ status: "clean" }));
    expect(await screen.findByRole("heading", { name: "最近项目" })).toBeTruthy();
    expect(new URLSearchParams(location.search).get("project")).toBeNull();
  });

  it("keeps conflict and save-error canvases open from project navigation", async () => {
    await openProject("p1", "项目一");

    for (const blockedStatus of ["conflict", "error"] as const) {
      act(() => useCanvasStore.setState({ status: blockedStatus }));
      const returnButton = screen.getByRole("button", { name: "返回项目列表" });
      expect(returnButton).toHaveProperty("disabled", true);
      fireEvent.click(returnButton);
      expect(new URLSearchParams(location.search).get("project")).toBe("p1");
      expect(screen.getByTestId("mock-canvas-surface")).toBeTruthy();
    }
  });

  it("blocks return, popstate, conflict actions, and beforeunload while any media import is active", async () => {
    const pending = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockReturnValue(pending.promise);
    history.replaceState({ preserved: true }, "", "/?project=p1");
    api.getProject.mockResolvedValue(project("p1", "项目一"));
    api.getGraph.mockResolvedValue(
      graph("p1", {
        schema_version: 2,
        nodes: [
          {
            id: "88888888-8888-4888-8888-888888888888",
            kind: "media_input",
            position: { x: 1, y: 2 },
            data: { artifact_revision_id: null, media_kind: null },
          },
        ],
      }),
    );
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目一" });
    const file = new File(["raw"], "safe.png", { type: "image/png" });
    act(() => surface.mediaControls?.selectFile(file));
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledOnce());

    const returnButton = screen.getByRole("button", { name: "返回项目列表" });
    expect(returnButton).toHaveProperty("disabled", true);
    expect(returnButton.textContent).toContain("导入中");
    expect(returnButton.getAttribute("title")).toContain("导入");
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    act(() => {
      history.replaceState({ preserved: true }, "", "/?project=p2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(new URLSearchParams(location.search).get("project")).toBe("p1");
    expect(api.getProject).toHaveBeenCalledTimes(1);
    expect(useCanvasStore.getState().projectId).toBe("p1");

    act(() => {
      useCanvasStore
        .getState()
        .markConflict(useCanvasStore.getState().loadEpoch, "p1", "revision-p2");
    });
    const reload = screen.getByRole("button", { name: "重新加载服务端版本" });
    const copy = screen.getByRole("button", { name: "保留本地副本" });
    expect(reload).toHaveProperty("disabled", true);
    expect(copy).toHaveProperty("disabled", true);
    expect(reload.getAttribute("title")).toContain("导入");
    fireEvent.click(reload);
    fireEvent.click(copy);
    expect(api.createProject).not.toHaveBeenCalled();

    const declaration = api.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    await act(async () => {
      pending.resolve(mediaSession("p1", declaration, "COMPLETED"));
      await pending.promise;
    });
    await waitFor(() => expect(reload).toHaveProperty("disabled", false));
    expect(copy).toHaveProperty("disabled", false);
  });

  it("keeps a SavedUnplaced result on the page guard until it is placed or explicitly discarded", async () => {
    const upload = deferred<ArtifactRevision>();
    api.createArtifactImport.mockImplementation(
      async (projectId: string, declaration: ArtifactImportDeclaration) =>
        mediaSession(projectId, declaration, "CREATED"),
    );
    api.uploadArtifactContent.mockReturnValue(upload.promise);
    history.replaceState({ preserved: true }, "", "/?project=p1");
    api.getProject.mockResolvedValue(project("p1", "项目一"));
    api.getGraph.mockResolvedValue(graph("p1"));
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目一" });
    const controller = surface.runtime?.controller;
    if (!controller) throw new Error("controller unavailable");
    let batchId = "";
    act(() => {
      const admission = controller.enqueue({
        projectId: "p1",
        loadEpoch: useCanvasStore.getState().loadEpoch,
        source: "multi_picker",
        candidates: [{
          kind: "file",
          file: new File(["raw"], "saved.png", { type: "image/png" }),
          displayName: "saved.png",
        }],
        anchor: { x: 0, y: 0 },
        targetMode: "create",
        targetBinding: null,
      });
      expect(admission.kind).toBe("accepted");
      if (admission.kind === "accepted") batchId = admission.batchId;
    });
    await waitFor(() => expect(api.uploadArtifactContent).toHaveBeenCalledOnce());
    const reservation = controller.getSnapshot().batches
      .find((batch) => batch.batchId === batchId)!
      .slots[0].reservation;
    act(() => {
      const current = useCanvasStore.getState();
      useCanvasStore.setState({
        nodes: [
          ...current.nodes,
          {
            id: reservation.nodeId,
            type: "media_input",
            position: reservation.position,
            data: {
              kind: "media_input",
              artifactRevisionId: null,
              mediaKind: null,
            },
          },
        ],
        status: "clean",
      });
    });
    await act(async () => {
      upload.resolve(mediaRevision("p1"));
      await upload.promise;
    });
    await waitFor(() =>
      expect(
        controller.getSnapshot().batches[0].slots[0].currentItem.state,
      ).toBe("SAVED_UNPLACED"),
    );

    const returnButton = screen.getByRole("button", { name: "返回项目列表" });
    expect(useCanvasStore.getState().status).toBe("clean");
    expect(returnButton).toHaveProperty("disabled", true);
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    act(() => {
      history.replaceState({ preserved: true }, "", "/?project=p2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(new URLSearchParams(location.search).get("project")).toBe("p1");
    expect(
      screen.getByRole("alert", { name: "页面错误" }).textContent,
    ).toContain("批次中心");

    act(() => {
      useCanvasStore
        .getState()
        .markConflict(useCanvasStore.getState().loadEpoch, "p1", "revision-p2");
    });
    const reload = screen.getByRole("button", { name: "重新加载服务端版本" });
    const copy = screen.getByRole("button", { name: "保留本地副本" });
    expect(reload).toHaveProperty("disabled", true);
    expect(copy).toHaveProperty("disabled", true);
  });

  it("leaves the page, guard, and controller untouched when the explicit force-refresh confirmation is refused", async () => {
    await openProject("p1", "项目一");
    const { controller } = await startUnknownControllerImport();
    act(() => useCanvasStore.setState({ status: "dirty" }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const reload = vi.spyOn(window.history, "go").mockImplementation(() => undefined);
    const before = controller.getSnapshot();

    act(() => controller.requestForceRefresh());

    expect(confirm).toHaveBeenCalledOnce();
    expect(String(confirm.mock.calls[0][0])).toContain("未保存");
    expect(String(confirm.mock.calls[0][0])).toContain("内存批次");
    expect(controller.getSnapshot()).toBe(before);
    expect(reload).not.toHaveBeenCalled();
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);
  });

  it("clears the aggregate guard before one approved reload and bypasses beforeunload exactly once", async () => {
    await openProject("p1", "项目一");
    const { controller } = await startUnknownControllerImport();
    act(() => useCanvasStore.setState({ status: "dirty" }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let unloadDuringReload: Event | null = null;
    const reload = vi
      .spyOn(window.history, "go")
      .mockImplementation(() => {
        unloadDuringReload = new Event("beforeunload", {
          cancelable: true,
        });
        window.dispatchEvent(unloadDuringReload);
      });

    act(() => controller.requestForceRefresh());

    expect(controller.getSnapshot().guardState.pendingOperationCount).toBe(0);
    expect(reload).toHaveBeenCalledWith(0);
    expect(unloadDuringReload?.defaultPrevented).toBe(false);
    const secondUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(secondUnload);
    expect(secondUnload.defaultPrevented).toBe(true);
  });

  it("ignores the old provider's delayed zero after a same-project reload starts a new import", async () => {
    const sourceGraph = graph("p1", {
      schema_version: 2,
      nodes: [
        {
          id: "88888888-8888-4888-8888-888888888888",
          kind: "media_input",
          position: { x: 1, y: 2 },
          data: { artifact_revision_id: null, media_kind: null },
        },
      ],
    });
    history.replaceState({ preserved: true }, "", "/?project=p1");
    api.getProject.mockResolvedValue(project("p1", "项目一"));
    api.getGraph.mockResolvedValueOnce(sourceGraph);
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目一" });
    const firstEpoch = useCanvasStore.getState().loadEpoch;

    vi.useFakeTimers();
    act(() => {
      useCanvasStore
        .getState()
        .markConflict(firstEpoch, "p1", "server-revision");
    });
    const reloadPending = deferred<ReturnType<typeof graph>>();
    api.getGraph.mockReturnValueOnce(reloadPending.promise);
    fireEvent.click(screen.getByRole("button", { name: "重新加载服务端版本" }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.getGraph).toHaveBeenCalledTimes(2);

    await act(async () => {
      reloadPending.resolve(sourceGraph);
      await reloadPending.promise;
    });
    expect(useCanvasStore.getState().loadEpoch).toBeGreaterThan(firstEpoch);

    const newRuntimeImport = deferred<ArtifactImportSession>();
    api.createArtifactImport.mockReturnValueOnce(newRuntimeImport.promise);
    act(() => {
      surface.mediaControls?.selectFile(
        new File(["raw"], "safe.png", { type: "image/png" }),
      );
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.createArtifactImport).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "返回项目列表" })).toHaveProperty(
      "disabled",
      true,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const returnButton = screen.getByRole("button", { name: "返回项目列表" });
    expect(returnButton).toHaveProperty("disabled", true);
    expect(returnButton.textContent).toContain("导入中");
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    const declaration = api.createArtifactImport.mock.calls[0][1] as ArtifactImportDeclaration;
    await act(async () => {
      newRuntimeImport.resolve(mediaSession("p1", declaration, "COMPLETED"));
      await newRuntimeImport.promise;
    });
  });

  it("keeps terminal reload recovery after failure and clears it only after a successful same-project reload", async () => {
    const sourceGraph = graph("p1", {
      schema_version: 2,
      nodes: [
        {
          id: "88888888-8888-4888-8888-888888888888",
          kind: "media_input",
          position: { x: 1, y: 2 },
          data: {
            artifact_revision_id: "44444444-4444-4444-8444-444444444444",
            media_kind: "image",
          },
        },
        {
          id: "99999999-9999-4999-8999-999999999998",
          kind: "media_input",
          position: { x: 4, y: 5 },
          data: { artifact_revision_id: null, media_kind: null },
        },
      ],
    });
    history.replaceState({ preserved: true }, "", "/?project=p1");
    api.getProject.mockResolvedValue(project("p1", "项目一"));
    api.getGraph.mockResolvedValueOnce(sourceGraph);
    api.createArtifactImport.mockRejectedValue(
      new ArtifactImportConflictError(
        "55555555-5555-4555-8555-555555555555",
        "invalid_replacement",
      ),
    );
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目一" });
    const firstEpoch = useCanvasStore.getState().loadEpoch;
    act(() =>
      surface.mediaControls?.selectFile(
        new File(["raw"], "safe.png", { type: "image/png" }),
      ),
    );
    await waitFor(() => expect(surface.mediaControls?.phase).toBe("final_error"));
    expect(surface.mediaControls?.recoveryKind).toBe("reload_project");

    api.getGraph.mockRejectedValueOnce(new Error("刷新失败"));
    act(() => surface.mediaControls?.requestReload());
    expect(
      (await screen.findByRole("alert", { name: "页面错误" })).textContent,
    ).toContain("刷新失败");
    expect(useCanvasStore.getState().loadEpoch).toBe(firstEpoch);
    expect(surface.mediaControls?.phase).toBe("final_error");
    expect(surface.mediaControls?.recoveryKind).toBe("reload_project");

    act(() =>
      useCanvasStore.setState({ status: "dirty", editSequence: 1, savedSequence: 0 }),
    );
    act(() => surface.mediaControls?.requestReload());
    expect(api.getGraph).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole("alert", { name: "页面错误" }).textContent,
    ).toContain("保存");
    act(() =>
      useCanvasStore.setState({ status: "clean", editSequence: 1, savedSequence: 1 }),
    );

    const activeRaceReload = deferred<ReturnType<typeof graph>>();
    const secondaryImport = deferred<ArtifactImportSession>();
    api.getGraph.mockReturnValueOnce(activeRaceReload.promise);
    act(() => surface.mediaControls?.requestReload());
    await waitFor(() => expect(api.getGraph).toHaveBeenCalledTimes(3));
    api.createArtifactImport.mockReturnValueOnce(secondaryImport.promise);
    act(() =>
      surface.secondaryMediaControls?.selectFile(
        new File(["raw"], "second.png", { type: "image/png" }),
      ),
    );
    await waitFor(() => expect(api.createArtifactImport).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "返回项目列表" }).textContent).toContain(
      "导入中",
    );
    await act(async () => {
      activeRaceReload.resolve(sourceGraph);
      await activeRaceReload.promise;
    });

    expect(useCanvasStore.getState().loadEpoch).toBe(firstEpoch);
    expect(surface.mediaControls?.phase).toBe("final_error");
    expect(surface.secondaryMediaControls?.active).toBe(true);
    expect(
      screen.getByRole("alert", { name: "页面错误" }).textContent,
    ).toContain("导入");
    await act(async () => {
      secondaryImport.reject(new TypeError("secondary network interruption"));
      await secondaryImport.promise.catch(() => undefined);
    });
    await waitFor(() =>
      expect(surface.secondaryMediaControls?.phase).toBe("retryable_error"),
    );

    const racingReload = deferred<ReturnType<typeof graph>>();
    api.getGraph.mockReturnValueOnce(racingReload.promise);
    act(() => surface.mediaControls?.requestReload());
    await waitFor(() => expect(api.getGraph).toHaveBeenCalledTimes(4));
    act(() => useCanvasStore.getState().addNode("note", { x: 20, y: 30 }));
    const editedNodes = useCanvasStore.getState().nodes;
    await act(async () => {
      racingReload.resolve(sourceGraph);
      await racingReload.promise;
    });

    expect(useCanvasStore.getState().loadEpoch).toBe(firstEpoch);
    expect(useCanvasStore.getState().nodes).toBe(editedNodes);
    expect(surface.mediaControls?.phase).toBe("final_error");
    expect(
      screen.getByRole("alert", { name: "页面错误" }).textContent,
    ).toContain("画布已变化");

    act(() => {
      const current = useCanvasStore.getState();
      useCanvasStore.setState({
        status: "clean",
        savedSequence: current.editSequence,
      });
    });
    api.getGraph.mockResolvedValueOnce(sourceGraph);
    act(() => surface.mediaControls?.requestReload());

    await waitFor(() => expect(api.getGraph).toHaveBeenCalledTimes(5));
    await waitFor(() =>
      expect(useCanvasStore.getState().loadEpoch).toBeGreaterThan(firstEpoch),
    );
    await waitFor(() => expect(surface.mediaControls?.phase).toBe("idle"));
    expect(new URLSearchParams(location.search).get("project")).toBe("p1");
  });

  it("loads normally through the StrictMode effect setup-cleanup-setup cycle", async () => {
    history.replaceState({}, "", "/?project=p1");
    api.getProject.mockResolvedValue(project("p1", "严格模式项目"));
    api.getGraph.mockResolvedValue(graph("p1"));

    render(
      <StrictMode>
        <CanvasPage />
      </StrictMode>,
    );

    expect(await screen.findByRole("heading", { name: "严格模式项目" })).toBeTruthy();
  });

  it("does not let a late create response overwrite navigation", async () => {
    const creating = deferred<ReturnType<typeof project>>();
    api.listProjects.mockResolvedValue([project("recent", "最近项目")]);
    api.createProject.mockReturnValue(creating.promise);
    api.getProject.mockImplementation(async (id: string) => project(id, `项目 ${id}`));
    api.getGraph.mockImplementation(async (id: string) => graph(id));
    render(<CanvasPage />);
    await screen.findByRole("button", { name: "最近项目" });

    fireEvent.click(screen.getByRole("button", { name: "创建空白项目" }));
    expect(screen.getByRole("button", { name: "最近项目" })).toHaveProperty(
      "disabled",
      true,
    );
    act(() => {
      history.replaceState({}, "", "/?project=recent");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByRole("heading", { name: "项目 recent" });

    await act(async () => {
      creating.resolve(project("created", "晚到项目"));
      await creating.promise;
    });

    expect(new URLSearchParams(location.search).get("project")).toBe("recent");
    expect(screen.getByRole("heading", { name: "项目 recent" })).toBeTruthy();
  });

  it("ignores a late load after navigation selects another project", async () => {
    const oldProject = deferred<ReturnType<typeof project>>();
    const oldGraph = deferred<ReturnType<typeof graph>>();
    api.getProject.mockImplementation((id: string) =>
      id === "old" ? oldProject.promise : Promise.resolve(project("new", "新项目")),
    );
    api.getGraph.mockImplementation((id: string) =>
      id === "old" ? oldGraph.promise : Promise.resolve(graph("new")),
    );
    history.replaceState({}, "", "/?project=old");
    render(<CanvasPage />);

    act(() => {
      history.replaceState({}, "", "/?project=new");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByRole("heading", { name: "新项目" })).toBeTruthy();

    await act(async () => {
      oldProject.resolve(project("old", "旧项目"));
      oldGraph.resolve(graph("old"));
      await Promise.all([oldProject.promise, oldGraph.promise]);
    });
    expect(screen.getByRole("heading", { name: "新项目" })).toBeTruthy();
    expect(useCanvasStore.getState().baseRevisionId).toBe("revision-new");
  });

  it("saves a conflict copy before switching and caps its suffixed name at 200 characters", async () => {
    const longName = "长".repeat(200);
    const shotId = "11111111-1111-4111-8111-111111111111";
    const imageRevision = "22222222-2222-4222-8222-222222222222";
    const referenceA = "33333333-3333-4333-8333-333333333333";
    const referenceB = "44444444-4444-4444-8444-444444444444";
    const sourceGraph = graph("p1", {
      schema_version: 2,
      nodes: [
        {
          id: "55555555-5555-4555-8555-555555555555",
          kind: "note",
          position: { x: 1, y: 2 },
          data: { text: "本地草稿" },
        },
        {
          id: "66666666-6666-4666-8666-666666666666",
          kind: "media_input",
          position: { x: 3, y: 4 },
          data: { artifact_revision_id: imageRevision, media_kind: "image" },
        },
        {
          id: "77777777-7777-4777-8777-777777777777",
          kind: "shot",
          position: { x: 5, y: 6 },
          data: { shot_id: shotId },
        },
      ],
      shots: [
        {
          id: shotId,
          order: 0,
          image_revision_id: imageRevision,
          video_revision_id: null,
          reference_revision_ids: [referenceB, referenceA],
        },
      ],
    });
    api.getProject.mockImplementation(async (id: string) =>
      id === "copy" ? project("copy", "冲突副本") : project("p1", longName),
    );
    api.getGraph.mockImplementation(async (id: string) =>
      id === "copy" ? graph("copy", sourceGraph.snapshot) : sourceGraph,
    );
    api.createProject.mockResolvedValue(project("copy", "冲突副本"));
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: longName });
    act(() => {
      useCanvasStore
        .getState()
        .markConflict(useCanvasStore.getState().loadEpoch, "p1", "server-revision");
    });

    fireEvent.click(screen.getByRole("button", { name: "保留本地副本" }));

    await waitFor(() => expect(api.createProject).toHaveBeenCalledOnce());
    const copyName = api.createProject.mock.calls[0][0] as string;
    expect(copyName.length).toBeLessThanOrEqual(200);
    expect(copyName.endsWith("（冲突副本）")).toBe(true);
    await waitFor(() => expect(api.proposeGraphChangeSet).toHaveBeenCalledOnce());
    expect(api.proposeGraphChangeSet.mock.calls[0][0]).toBe("copy");
    const copiedSnapshot = api.proposeGraphChangeSet.mock.calls[0][1].snapshot;
    expect(copiedSnapshot.schema_version).toBe(3);
    expect(copiedSnapshot.nodes).toEqual(sourceGraph.snapshot.nodes);
    expect(copiedSnapshot.shots).toEqual(sourceGraph.snapshot.shots);
    expect(copiedSnapshot.resource_bindings).toEqual([]);
    expect(copiedSnapshot.shots[0].reference_revision_ids).toEqual([
      referenceB,
      referenceA,
    ]);
    expect(api.approveGraphChangeSet).not.toHaveBeenCalled();
    expect(api.applyGraphChangeSet).not.toHaveBeenCalled();
    const review = await screen.findByRole("dialog", {
      name: "冲突副本变更审阅",
    });
    expect(review.textContent).toContain("GRAPH_WRITE");
    expect(review.textContent).toContain("a".repeat(12));
    fireEvent.click(screen.getByRole("button", { name: "确认创建副本" }));
    await waitFor(() =>
      expect(api.approveGraphChangeSet).toHaveBeenCalledWith(
        "copy",
        "graph-change-p1",
        "a".repeat(64),
      ),
    );
    await waitFor(() =>
      expect(api.applyGraphChangeSet).toHaveBeenCalledWith(
        "copy",
        "graph-change-p1",
        "a".repeat(64),
      ),
    );
    await waitFor(() =>
      expect(new URLSearchParams(location.search).get("project")).toBe("copy"),
    );
  });

  it("does not switch or reset if the local graph changes while a conflict copy is saving", async () => {
    const copySave = deferred<Record<string, unknown>>();
    api.getProject.mockResolvedValue(project("p1", "项目一"));
    api.getGraph.mockResolvedValue(
      graph("p1", {
        schema_version: 1,
        nodes: [
          {
            id: NOTE_NODE_ID,
            kind: "note",
            position: { x: 1, y: 2 },
            data: { text: "点击时草稿" },
          },
        ],
      }),
    );
    api.createProject.mockResolvedValue(project("copy", "冲突副本"));
    api.applyGraphChangeSet.mockReturnValue(copySave.promise);
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目一" });
    act(() => {
      useCanvasStore
        .getState()
        .markConflict(useCanvasStore.getState().loadEpoch, "p1", "server-revision");
    });
    fireEvent.click(screen.getByRole("button", { name: "保留本地副本" }));
    await screen.findByRole("dialog", { name: "冲突副本变更审阅" });
    fireEvent.click(screen.getByRole("button", { name: "确认创建副本" }));
    await waitFor(() => expect(api.applyGraphChangeSet).toHaveBeenCalledOnce());

    act(() =>
      useCanvasStore.getState().updateNodeText(NOTE_NODE_ID, "保存期间的新编辑"),
    );
    await act(async () => {
      copySave.resolve({
        ...graphChangeSet("copy"),
        state: "APPLIED",
        applied_graph_revision_id: "copy-revision-2",
      });
      await copySave.promise;
    });

    expect(new URLSearchParams(location.search).get("project")).toBe("p1");
    expect(useCanvasStore.getState().nodes[0].data.text).toBe("保存期间的新编辑");
    expect(useCanvasStore.getState().baseRevisionId).toBe("revision-p1");
  });

  it("keeps the original canvas and URL when preserving a copy fails", async () => {
    await openProject("p1", "项目一");
    act(() => {
      useCanvasStore.getState().addNode("note", { x: 0, y: 0 });
      useCanvasStore
        .getState()
        .markConflict(useCanvasStore.getState().loadEpoch, "p1", "server-revision");
    });
    const localNodes = useCanvasStore.getState().nodes;
    api.createProject.mockRejectedValue(new Error("副本创建失败"));

    fireEvent.click(screen.getByRole("button", { name: "保留本地副本" }));

    expect(
      (await screen.findByRole("alert", { name: "页面错误" })).textContent,
    ).toContain("副本创建失败");
    expect(new URLSearchParams(location.search).get("project")).toBe("p1");
    expect(useCanvasStore.getState().nodes).toBe(localNodes);
  });

  it("keeps the conflict draft visible when reloading the server graph fails", async () => {
    const sourceGraph = graph("p1", {
      schema_version: 1,
      nodes: [
        {
          id: NOTE_NODE_ID,
          kind: "note",
          position: { x: 1, y: 2 },
          data: { text: "本地冲突草稿" },
        },
      ],
    });
    api.getProject.mockResolvedValue(project("p1", "项目一"));
    api.getGraph
      .mockResolvedValueOnce(sourceGraph)
      .mockRejectedValueOnce(new Error("服务端图读取失败"));
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目一" });

    act(() => {
      useCanvasStore
        .getState()
        .markConflict(useCanvasStore.getState().loadEpoch, "p1", "server-revision");
    });
    const localState = useCanvasStore.getState();
    const localNodes = localState.nodes;
    const baseRevisionId = localState.baseRevisionId;

    fireEvent.click(screen.getByRole("button", { name: "重新加载服务端版本" }));

    expect(
      (await screen.findByRole("alert", { name: "页面错误" })).textContent,
    ).toContain("服务端图读取失败");
    expect(new URLSearchParams(location.search).get("project")).toBe("p1");
    expect(useCanvasStore.getState().nodes).toBe(localNodes);
    expect(useCanvasStore.getState().nodes).toEqual([
      expect.objectContaining({
        id: NOTE_NODE_ID,
        data: expect.objectContaining({ text: "本地冲突草稿" }),
      }),
    ]);
    expect(useCanvasStore.getState().baseRevisionId).toBe(baseRevisionId);
    expect(useCanvasStore.getState().status).toBe("conflict");
    expect(screen.getByRole("heading", { name: "项目一" })).toBeTruthy();
    expect(screen.getByTestId("mock-canvas-surface")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新加载服务端版本" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "保留本地副本" })).toBeTruthy();
    expect(screen.queryByRole("alert", { name: "项目加载失败" })).toBeNull();
  });

  it("keeps viewport queues independent and ignores an old-project failure", async () => {
    const oldViewport = deferred<void>();
    api.getProject.mockImplementation(async (id: string) => project(id, `项目 ${id}`));
    api.getGraph.mockImplementation(async (id: string) => graph(id));
    api.saveViewport.mockImplementation((id: string) =>
      id === "a" ? oldViewport.promise : Promise.resolve(undefined),
    );
    history.replaceState({}, "", "/?project=a");
    render(<CanvasPage />);
    await screen.findByRole("heading", { name: "项目 a" });
    act(() => surface.onMoveEnd?.({ x: 1, y: 2, zoom: 1 }));
    await waitFor(() => expect(api.saveViewport).toHaveBeenCalledWith("a", { x: 1, y: 2, zoom: 1 }));
    act(() => surface.onMoveEnd?.({ x: 9, y: 9, zoom: 1.25 }));

    act(() => {
      history.replaceState({}, "", "/?project=b");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await screen.findByRole("heading", { name: "项目 b" });
    act(() => surface.onMoveEnd?.({ x: 3, y: 4, zoom: 2 }));
    await waitFor(() => expect(api.saveViewport).toHaveBeenCalledWith("b", { x: 3, y: 4, zoom: 2 }));

    await act(async () => {
      oldViewport.reject(new Error("旧项目视图失败"));
      await oldViewport.promise.catch(() => undefined);
    });
    await waitFor(() => {
      expect(api.saveViewport.mock.calls.filter(([id]) => id === "a")).toHaveLength(1);
    });
    expect(screen.queryByText(/旧项目视图失败/)).toBeNull();
  });

  it("protects every unsaved/error state with beforeunload", async () => {
    await openProject("p1", "项目一");
    const cleanEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(cleanEvent);
    expect(cleanEvent.defaultPrevented).toBe(false);

    for (const status of ["dirty", "saving", "conflict", "error"] as const) {
      act(() => useCanvasStore.setState({ status }));
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    }
  });

  it("shows an accessible load failure with retry", async () => {
    history.replaceState({}, "", "/?project=p1");
    api.getProject.mockRejectedValueOnce(new Error("项目读取失败"));
    api.getGraph.mockResolvedValue(graph("p1"));
    render(<CanvasPage />);

    expect(
      (await screen.findByRole("alert", { name: "项目加载失败" })).textContent,
    ).toContain("项目读取失败");
    api.getProject.mockResolvedValue(project("p1", "项目一"));
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("heading", { name: "项目一" })).toBeTruthy();
  });

  it("opens an experiment for viewing without moving the formal selection", async () => {
    const initial = branchWorkspace("p1");
    const experiment = {
      ...initial.branches[0],
      id: "branch-experiment",
      name: "探索 A",
      kind: "EXPERIMENT" as const,
      forked_from_branch_id: initial.branches[0].id,
      head: {
        graph_revision_id: "revision-experiment",
        version: 2,
        graph_revision_sha256: "b".repeat(64),
      },
      is_selected: false,
    };
    api.getProject.mockResolvedValue(project("p1", "分支项目"));
    api.getGraph.mockResolvedValue(graph("p1"));
    api.listBranches.mockResolvedValue({
      ...initial,
      branches: [initial.branches[0], experiment],
    });
    api.getBranchGraph.mockResolvedValue({
      branch: experiment,
      selection: initial.selection,
      revision: {
        ...graph("experiment"),
        revision_id: "revision-experiment",
        graph_document_id: initial.graph_document_id,
      },
      materialization: { unmaterialized: 0, current: 0, stale: 0 },
    });
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);

    await screen.findByRole("heading", { name: "分支项目" });
    const branchTrigger = screen.getByRole("button", {
      name: "查看/正式：主线",
    });
    fireEvent.click(branchTrigger);
    fireEvent.click(
      screen.getByRole("button", { name: "探索 A 实验分支" }),
    );

    await waitFor(() =>
      expect(useCanvasStore.getState().baseRevisionId).toBe("revision-experiment"),
    );
    expect(api.getBranchGraph).toHaveBeenCalledWith("p1", "branch-experiment");
    expect(api.proposePromotion).not.toHaveBeenCalled();
    expect(initial.selection.branch_id).toBe("branch-p1");
    expect(
      screen.getByRole("button", {
        name: "查看：探索 A｜正式：主线",
      }),
    ).toBeTruthy();
  });

  it("reloads the applied branch snapshot after a reviewed direct apply", async () => {
    await openProject("p1", "直接应用项目");
    const createdShot = useCanvasStore.getState().createShot({
      afterShotId: null,
      position: { x: 10, y: 20 },
    });
    expect(createdShot).not.toBeNull();
    act(() => useCanvasStore.getState().selectShot(createdShot?.shotId ?? null));
    const loadEpochBeforeApply = useCanvasStore.getState().loadEpoch;
    const appliedSnapshot = graph("applied", {
      schema_version: 2,
      nodes: [
        {
          id: createdShot?.nodeId ?? "shot-node",
          kind: "shot",
          position: { x: 10, y: 20 },
          data: { shot_id: createdShot?.shotId ?? "shot-id" },
        },
        {
          id: NOTE_NODE_ID,
          kind: "note",
          position: { x: 40, y: 50 },
          data: { text: "服务端直接应用结果" },
        },
      ],
      shots: [
        {
          id: createdShot?.shotId ?? "shot-id",
          order: 0,
          image_revision_id: null,
          video_revision_id: null,
          reference_revision_ids: [],
        },
      ],
    });
    api.getBranchGraph.mockResolvedValueOnce({
      branch: branchWorkspace("p1").branches[0],
      selection: branchWorkspace("p1").selection,
      revision: {
        ...appliedSnapshot,
        graph_document_id: "graph-p1",
        revision_id: "saved-revision",
      },
      materialization: { unmaterialized: 1, current: 0, stale: 0 },
    });

    act(() => useCanvasStore.getState().addNode("note", { x: 1, y: 2 }));
    fireEvent.click(screen.getByRole("button", { name: "审阅并应用画布变更" }));
    await screen.findByRole("dialog", { name: "图谱变更审阅" });
    fireEvent.click(screen.getByRole("button", { name: "确认应用变更" }));

    await waitFor(() =>
      expect(api.getBranchGraph).toHaveBeenCalledWith("p1", "branch-p1"),
    );
    await waitFor(() =>
      expect(
        useCanvasStore
          .getState()
          .nodes.find((node) => node.id === NOTE_NODE_ID)?.data,
      ).toMatchObject({ text: "服务端直接应用结果" }),
    );
    expect(useCanvasStore.getState().baseRevisionId).toBe("saved-revision");
    expect(useCanvasStore.getState().selectedShotId).toBe(createdShot?.shotId);
    expect(useCanvasStore.getState().loadEpoch).toBe(loadEpochBeforeApply);
  });

  it("ignores a late branch response after a newer branch has been opened", async () => {
    const initial = branchWorkspace("p1");
    const makeExperiment = (id: string, name: string) => ({
      ...initial.branches[0],
      id,
      name,
      kind: "EXPERIMENT" as const,
      forked_from_branch_id: initial.branches[0].id,
      head: {
        graph_revision_id: `revision-${id}`,
        version: 2,
        graph_revision_sha256: id === "branch-a" ? "b".repeat(64) : "c".repeat(64),
      },
      is_selected: false,
    });
    const branchA = makeExperiment("branch-a", "探索 A");
    const branchB = makeExperiment("branch-b", "探索 B");
    const responseA = deferred<ReturnType<typeof api.getBranchGraph>>();
    const responseB = deferred<ReturnType<typeof api.getBranchGraph>>();
    api.getProject.mockResolvedValue(project("p1", "竞态项目"));
    api.getGraph.mockResolvedValue(graph("p1"));
    api.listBranches.mockResolvedValue({
      ...initial,
      branches: [initial.branches[0], branchA, branchB],
    });
    api.getBranchGraph.mockImplementation((_projectId: string, branchId: string) =>
      branchId === "branch-a" ? responseA.promise : responseB.promise,
    );
    history.replaceState({}, "", "/?project=p1");
    render(<CanvasPage />);

    await screen.findByRole("heading", { name: "竞态项目" });
    fireEvent.click(
      screen.getByRole("button", { name: "查看/正式：主线" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "探索 A 实验分支" }));
    fireEvent.click(screen.getByRole("button", { name: "探索 B 实验分支" }));

    await act(async () => {
      responseB.resolve({
        branch: branchB,
        selection: initial.selection,
        revision: {
          ...graph("branch-b"),
          graph_document_id: "graph-p1",
          revision_id: "revision-branch-b",
        },
        materialization: { unmaterialized: 0, current: 0, stale: 0 },
      });
      await responseB.promise;
    });
    await waitFor(() =>
      expect(useCanvasStore.getState().baseRevisionId).toBe("revision-branch-b"),
    );
    await act(async () => {
      responseA.resolve({
        branch: branchA,
        selection: initial.selection,
        revision: {
          ...graph("branch-a"),
          graph_document_id: "graph-p1",
          revision_id: "revision-branch-a",
        },
        materialization: { unmaterialized: 0, current: 0, stale: 0 },
      });
      await responseA.promise;
    });

    expect(useCanvasStore.getState().baseRevisionId).toBe("revision-branch-b");
  });
});
