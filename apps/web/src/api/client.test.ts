import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  applyPromotion,
  apiFetch,
  ArtifactDeclarationValidationError,
  ArtifactImportConflictError,
  ArtifactImportFailedError,
  bootstrapSession,
  createArtifactImport,
  abortLargeUpload,
  createLargeUpload,
  createMediaStreamUrl,
  createProject,
  createResource,
  createResourceRevision,
  discardArtifactImportResult,
  getArtifactImport,
  getLargeUpload,
  getArtifactRevision,
  getArtifactRevisionContent,
  getBranchGraph,
  getGraph,
  getProject,
  getSessionToken,
  inferRequestedMediaKind,
  listArtifactRevisions,
  listBranches,
  listShotSlotAssociations,
  listProjects,
  listResources,
  listProjectResourceUpdates,
  parseGraphSnapshot,
  proposeGraphChangeSet,
  proposeGraphChangeSetUndo,
  proposePromotion,
  proposePromotionUndo,
  approveGraphChangeSet,
  applyGraphChangeSet,
  acknowledgeExperienceEventCursor,
  applyConfigChangeSet,
  approveConfigChangeSet,
  approveNoEgressDryRun,
  executeNoEgressDryRun,
  getActiveControlConfig,
  proposeConfigChangeSet,
  readExperienceEventCursor,
  requestNoEgressDryRun,
  saveViewport,
  SessionExpiredError,
  uploadArtifactContent,
  putLargeUploadPart,
  completeLargeUpload,
  confirmGcPlan,
  createExternalReference,
  createGcPlan,
  grantExternalRoot,
  importExternalCopy,
  reconfirmExternalReference,
  requestRendition,
  restoreQuarantine,
  ResourceRevisionConflictError,
  BranchHeadStaleError,
  C2ApiError,
  C3ApiError,
  SelectionPointerStaleError,
  storageSummary,
  TranscodeConfirmationRequiredError,
  verifyExternalReference,
  listModelProfiles,
  getPromptBatch,
  getPromptProfileExtraction,
  requestAgentProposal,
} from "./client";

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

function invalidJsonResponse(status: number): Response {
  return {
    ok: false,
    status,
    json: vi.fn().mockRejectedValue(new SyntaxError("invalid json")),
  } as unknown as Response;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const emptySnapshotV1 = { schema_version: 1 as const, nodes: [], edges: [] };
const emptySnapshot = { schema_version: 2 as const, nodes: [], edges: [], shots: [] };
const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const TEXT_NODE_ID = "22222222-2222-4222-8222-222222222222";
const MEDIA_NODE_ID = "33333333-3333-4333-8333-333333333333";
const SHOT_NODE_ID = "44444444-4444-4444-8444-444444444444";
const SHOT_ID = "55555555-5555-4555-8555-555555555555";
const IMAGE_REVISION_ID = "66666666-6666-4666-8666-666666666666";
const EDGE_ID = "77777777-7777-4777-8777-777777777777";
const PROMPT_NODE_ID = "88888888-8888-4888-8888-888888888888";
const IMPORT_ID = "99999999-9999-4999-8999-999999999999";
const CLIENT_IMPORT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ARTIFACT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function graphResponse(snapshot: unknown, revisionId = "revision-id") {
  return {
    revision_id: revisionId,
    graph_document_id: "graph-id",
    sequence: 1,
    snapshot_hash: "hash",
    snapshot,
    viewport: { x: 0, y: 0, zoom: 1 },
  };
}

describe("C3 strict client boundary", () => {
  beforeEach(() => {
    sessionStorage.setItem("ai-canvas-session", "c3-token");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("accepts an exact profile list and rejects credential-bearing extras", async () => {
    const profile = {
      id: "11111111-1111-4111-8111-111111111111",
      profile_key: "canvas-agent",
      role: "CANVAS_AGENT",
      active_revision_id: "22222222-2222-4222-8222-222222222222",
      created_at: "2026-09-03T00:00:00Z",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ profiles: [profile] }))
      .mockResolvedValueOnce(response({ profiles: [{ ...profile, role: "PROMPT_EDITOR" }] }))
      .mockResolvedValueOnce(response({ profiles: [{ ...profile, api_key: "forbidden" }] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listModelProfiles()).resolves.toEqual([profile]);
    await expect(listModelProfiles()).resolves.toEqual([
      { ...profile, role: "PROMPT_EDITOR" },
    ]);
    await expect(listModelProfiles()).rejects.toThrow("Invalid API response");
  });

  it("cross-checks the project id on an Agent proposal response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ id: "forged" })));
    await expect(requestAgentProposal(PROJECT_ID, {} as never)).rejects.toThrow(
      "Invalid API response",
    );
  });

  it("keeps C3 problem codes structured without exposing provider messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ code: "model_credential_unavailable" }, 409)));
    const error = await requestAgentProposal(PROJECT_ID, {} as never).catch((reason) => reason);
    expect(error).toBeInstanceOf(C3ApiError);
    expect(error).toMatchObject({ status: 409, code: "model_credential_unavailable" });
  });
});

describe("C4 strict client boundary", () => {
  beforeEach(() => {
    sessionStorage.setItem("ai-canvas-session", "c4-token");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const promptDocument = {
    schema_version: 1,
    blocks: [{ id: TEXT_NODE_ID, kind: "FREEFORM", text: "base", locked: false }],
    rendered_text: "base",
    spans: [{ block_id: TEXT_NODE_ID, start_utf16: 0, end_utf16: 4 }],
  };

  function agentRun(operationKind: "PROMPT_PROFILE_EXTRACTION" | "PROMPT_BATCH_ITEM") {
    return {
      id: "12111111-1111-4111-8111-111111111111",
      project_id: PROJECT_ID,
      correlation_id: "13111111-1111-4111-8111-111111111111",
      operation_kind: operationKind,
      model_profile_revision_id: "14111111-1111-4111-8111-111111111111",
      branch_id: "15111111-1111-4111-8111-111111111111",
      branch_head_version: 1,
      selection_version: 1,
      scope_sha256: "a".repeat(64),
      request_sha256: "b".repeat(64),
      egress_plan_sha256: "c".repeat(64),
      egress_plan: {
        mode: "MODEL_PROVIDER",
        endpoint: "http://127.0.0.1:8765/v1/chat/completions",
        locality: "LOCAL_LOOPBACK",
        endpoint_identity_sha256: "d".repeat(64),
        external_data_classes: ["USER_INSTRUCTION"],
        task_count: 1,
        gpu_count: 0,
        media_execution: false,
        cost_mode: "HARD",
        currency: "CNY",
        cost_bound_minor: 0,
        data_residency: "LOCAL_DEVICE",
        retention_policy: "NO_RETENTION_DECLARED",
        max_retries: 0,
        request_sha256: "b".repeat(64),
        plan_sha256: "c".repeat(64),
        preview: {
          contexts: [{ category: "USER_INSTRUCTION", content: "Improve", characters: 7 }],
          truncated: false,
        },
      },
      state: "PENDING_APPROVAL",
    };
  }

  it("rejects malformed nested extraction approval evidence", async () => {
    const extraction = {
      id: "16111111-1111-4111-8111-111111111111",
      project_id: PROJECT_ID,
      run_request: agentRun("PROMPT_PROFILE_EXTRACTION"),
      source_node_id: PROMPT_NODE_ID,
      source_document_sha256: "e".repeat(64),
      state: "PENDING_APPROVAL",
      output: null,
      output_sha256: null,
      compiled_profile: null,
      materialized_resource_revision_id: null,
      failure_code: null,
      created_at: "2026-09-12T00:00:00Z",
      updated_at: "2026-09-12T00:00:00Z",
    };
    const malformed = structuredClone(extraction);
    delete malformed.run_request.egress_plan.preview.contexts[0].content;
    const counted = { category: "USER_INSTRUCTION", content: "🙂", original_characters: 5, sent_characters: 1, truncated: true };
    const withContext = (context: typeof counted) => ({
      ...extraction,
      run_request: { ...extraction.run_request, egress_plan: {
        ...extraction.run_request.egress_plan, preview: { contexts: [context] },
      } },
    });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(extraction))
      .mockResolvedValueOnce(response(malformed))
      .mockResolvedValueOnce(response(withContext(counted)))
      .mockResolvedValueOnce(response(withContext({ ...counted, truncated: false }))));

    await expect(getPromptProfileExtraction(PROJECT_ID, extraction.id)).resolves.toEqual(extraction);
    await expect(getPromptProfileExtraction(PROJECT_ID, extraction.id)).rejects.toThrow("Invalid API response");
    await expect(getPromptProfileExtraction(PROJECT_ID, extraction.id)).resolves.toEqual(withContext(counted));
    await expect(getPromptProfileExtraction(PROJECT_ID, extraction.id)).rejects.toThrow("Invalid API response");
  });

  it("requires current attempts to appear in the append-only batch history", async () => {
    const attempt = {
      id: "17111111-1111-4111-8111-111111111111",
      attempt_number: 1,
      parent_attempt_id: null,
      run_request: agentRun("PROMPT_BATCH_ITEM"),
      state: "PENDING_APPROVAL",
      output: null,
      output_sha256: null,
      compiled_document_sha256: null,
      compiled_document: null,
      failure_code: null,
      created_at: "2026-09-12T00:00:00Z",
      updated_at: "2026-09-12T00:00:00Z",
    };
    const item = {
      id: "18111111-1111-4111-8111-111111111111",
      shot_id: SHOT_ID,
      shot_node_id: SHOT_NODE_ID,
      prompt_node_id: PROMPT_NODE_ID,
      shot_order: 0,
      base_document_sha256: "f".repeat(64),
      base_document: promptDocument,
      protected_manifest_sha256: "1".repeat(64),
      protected_manifest: [],
      editable_manifest_sha256: "2".repeat(64),
      editable_manifest: [{ block_id: TEXT_NODE_ID, kind: "FREEFORM", locked: false,
        index: 0, text_sha256: "3".repeat(64) }],
      prompt_profile_resource_revision_id: IMAGE_REVISION_ID,
      prompt_profile_revision_sha256: "4".repeat(64),
      prompt_profile_scope_kind: "PROJECT",
      prompt_profile_scope_id: null,
      decision_version: 0,
      current_attempt: attempt,
      latest_decision: null,
      attempts: [attempt],
      decisions: [],
    };
    const batch = {
      id: "19111111-1111-4111-8111-111111111111",
      project_id: PROJECT_ID,
      correlation_id: "20111111-1111-4111-8111-111111111111",
      selection_mode: "ALL",
      binding_key: "visual-profile",
      base_graph_revision_id: "21111111-1111-4111-8111-111111111111",
      base_graph_revision_sha256: "5".repeat(64),
      branch_id: "22111111-1111-4111-8111-111111111111",
      expected_branch_head_version: 1,
      expected_selection_version: 1,
      application_target: { mode: "NEW_BRANCH" },
      model_profile_revision_id: "23111111-1111-4111-8111-111111111111",
      config_revision_id: "24111111-1111-4111-8111-111111111111",
      item_set_sha256: "6".repeat(64),
      request_set_sha256: "7".repeat(64),
      retry_request_set_sha256: null,
      version: 1,
      phase: "PENDING_APPROVAL",
      items: [item],
      graph_change_set: null,
      final_decision_set_sha256: null,
      failure_code: null,
      created_at: "2026-09-12T00:00:00Z",
      updated_at: "2026-09-12T00:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(batch))
      .mockResolvedValueOnce(response({ ...batch, items: [{ ...item, attempts: [] }] })));

    await expect(getPromptBatch(PROJECT_ID, batch.id)).resolves.toEqual(batch);
    await expect(getPromptBatch(PROJECT_ID, batch.id)).rejects.toThrow("Invalid API response");
  });
});

describe("bootstrapSession", () => {
  beforeEach(() => {
    window.dispatchEvent(new Event("pagehide"));
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    history.replaceState({}, "", "/");
  });

  it("creates and attests a development session from a naked URL before committing it", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), location.href).pathname;
      if (path === "/api/v1/session/development") {
        expect(init?.method).toBe("POST");
        expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
        expect(init?.body).toBe("{}");
        expect(getSessionToken()).toBeNull();
        return response({ access_token: "development-token" });
      }
      if (path === "/api/v1/session/status") {
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          "Bearer development-token",
        );
        expect(getSessionToken()).toBeNull();
        return response({ mode: "development-auto-session" });
      }
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapSession()).resolves.toBe("development-auto-session");

    expect(getSessionToken()).toBe("development-token");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("uses the bootstrap fragment first, scrubs it synchronously, and attests before commit", async () => {
    const preservedState = { draftId: "draft-1" };
    sessionStorage.setItem("ai-canvas-session", "stale-token");
    history.replaceState(
      preservedState,
      "",
      "/?panel=assets#mode=review&bootstrap=one-time&selection=node-1",
    );
    const exchange = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(exchange.promise)
      .mockResolvedValueOnce(response({ mode: "session" }));
    vi.stubGlobal("fetch", fetchMock);

    const pendingBootstrap = bootstrapSession();
    expect(location.hash).toBe("#mode=review&selection=node-1");
    expect(history.state).toEqual(preservedState);
    exchange.resolve(response({ access_token: "launcher-token" }));

    await expect(pendingBootstrap).resolves.toBe("session");
    expect(getSessionToken()).toBe("launcher-token");
    const [statusUrl, statusInit] = fetchMock.mock.calls[1] as unknown as [URL, RequestInit];
    expect(new URL(String(statusUrl), location.href).pathname).toBe("/api/v1/session/status");
    expect(new Headers(statusInit.headers).get("Authorization")).toBe(
      "Bearer launcher-token",
    );
  });

  it("single-flights concurrent StrictMode bootstrap calls", async () => {
    history.replaceState({}, "", "/#bootstrap=one-time");
    const exchange = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(exchange.promise)
      .mockResolvedValueOnce(response({ mode: "session" }));
    vi.stubGlobal("fetch", fetchMock);

    const first = bootstrapSession();
    const second = bootstrapSession();

    expect(first).toBe(second);
    expect(fetchMock).toHaveBeenCalledOnce();
    exchange.resolve(response({ access_token: "shared-token" }));
    await expect(Promise.all([first, second])).resolves.toEqual(["session", "session"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(getSessionToken()).toBe("shared-token");
  });

  it("attests a stored token on every page start", async () => {
    sessionStorage.setItem("ai-canvas-session", "stored-token");
    const fetchMock = vi.fn().mockResolvedValue(response({ mode: "session" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapSession()).resolves.toBe("session");

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(new URL(String(url), location.href).pathname).toBe("/api/v1/session/status");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer stored-token");
  });

  it("rejects status 201 even when it carries a valid session mode", async () => {
    sessionStorage.setItem("ai-canvas-session", "provisional-token");
    const fetchMock = vi.fn().mockResolvedValue(
      response({ mode: "session" }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapSession()).rejects.toThrow("201");

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(getSessionToken()).toBeNull();
  });

  it("clears a stored token rejected with 401 and continues to development auto-session", async () => {
    sessionStorage.setItem("ai-canvas-session", "expired-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({}, 401))
      .mockResolvedValueOnce(response({ access_token: "replacement-token" }))
      .mockResolvedValueOnce(response({ mode: "development-auto-session" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapSession()).resolves.toBe("development-auto-session");

    expect(getSessionToken()).toBe("replacement-token");
    expect(
      fetchMock.mock.calls.map(([url]) => new URL(String(url), location.href).pathname),
    ).toEqual([
      "/api/v1/session/status",
      "/api/v1/session/development",
      "/api/v1/session/status",
    ]);
  });

  it.each([
    ["network failure", () => Promise.reject(new Error("status offline")), "status offline"],
    ["non-401 response", () => Promise.resolve(response({}, 503)), "503"],
    ["malformed body", () => Promise.resolve(response({})), "Invalid local session status"],
    ["unknown mode", () => Promise.resolve(response({ mode: "forged" })), "Invalid local session status"],
  ] as const)(
    "rejects a stored token after a %s without falling back",
    async (_case, statusResult, expectedMessage) => {
      sessionStorage.setItem("ai-canvas-session", "provisional-token");
      const fetchMock = vi.fn(statusResult);
      vi.stubGlobal("fetch", fetchMock);

      await expect(bootstrapSession()).rejects.toThrow(expectedMessage);

      expect(getSessionToken()).toBeNull();
      expect(fetchMock).toHaveBeenCalledOnce();
    },
  );

  it.each([201, 202])(
    "rejects development status %s even when it carries a valid access token",
    async (status) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(response({ access_token: "unexpected-token" }, status))
        .mockResolvedValueOnce(response({ mode: "development-auto-session" }));
      vi.stubGlobal("fetch", fetchMock);

      await expect(bootstrapSession()).rejects.toThrow(String(status));

      expect(fetchMock).toHaveBeenCalledOnce();
      expect(getSessionToken()).toBeNull();
    },
  );

  it("continues to peer handoff only for the exact disabled development response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ code: "development_session_disabled" }, 403),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapSession()).rejects.toThrow(
      "Missing local bootstrap token. Relaunch AI Canvas.",
    );
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(getSessionToken()).toBeNull();
  });

  it.each([
    ["other 403", () => Promise.resolve(response({ code: "origin_denied" }, 403)), "origin_denied"],
    ["server error", () => Promise.resolve(response({}, 500)), "500"],
    ["connection failure", () => Promise.reject(new Error("development offline")), "development offline"],
    ["missing token", () => Promise.resolve(response({})), "Invalid local session response"],
    ["blank token", () => Promise.resolve(response({ access_token: "   " })), "Invalid local session response"],
  ] as const)(
    "treats a development endpoint %s as fatal and stores nothing",
    async (_case, developmentResult, expectedMessage) => {
      vi.stubGlobal("fetch", vi.fn(developmentResult));

      await expect(bootstrapSession()).rejects.toThrow(expectedMessage);
      expect(getSessionToken()).toBeNull();
    },
  );

  it("does not commit or broadcast an exchange token until protected status attests it", async () => {
    history.replaceState({}, "", "/#bootstrap=secret");
    const status = deferred<Response>();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ access_token: "provisional-token" }))
      .mockReturnValueOnce(status.promise);
    vi.stubGlobal("fetch", fetchMock);

    const pendingBootstrap = bootstrapSession();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(getSessionToken()).toBeNull();
    status.resolve(response({ mode: "session" }));

    await expect(pendingBootstrap).resolves.toBe("session");
    expect(getSessionToken()).toBe("provisional-token");
  });

  it("scrubs and rejects an empty bootstrap fragment without fetching", async () => {
    history.replaceState({}, "", "/#mode=review&bootstrap=");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const pendingBootstrap = bootstrapSession();
    expect(location.hash).toBe("#mode=review");
    await expect(pendingBootstrap).rejects.toThrow("Missing local bootstrap token");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an invalid exchange status without retaining the provisional token", async () => {
    history.replaceState({}, "", "/#bootstrap=secret");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ access_token: "provisional-token" }))
      .mockResolvedValueOnce(response({ mode: "forged" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(bootstrapSession()).rejects.toThrow("Invalid local session status");
    expect(getSessionToken()).toBeNull();
    expect(location.hash).toBe("");
  });
});

describe("createMediaStreamUrl", () => {
  beforeEach(() => {
    sessionStorage.clear();
    sessionStorage.setItem("ai-canvas-session", "media-token");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("issues a cookie media session before returning a token-free stream URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        revision_id: IMAGE_REVISION_ID,
        idle_expires_at: "2026-08-18T00:10:00Z",
        absolute_expires_at: "2026-08-18T01:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(createMediaStreamUrl(IMAGE_REVISION_ID)).resolves.toBe(
      `/api/v1/media/revisions/${IMAGE_REVISION_ID}`,
    );

    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe("/api/v1/media-sessions");
    expect(init.credentials).toBe("include");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer media-token");
    expect(String(url)).not.toContain("token=");
  });
});

describe("large upload lifecycle client", () => {
  beforeEach(() => {
    sessionStorage.clear();
    sessionStorage.setItem("ai-canvas-session", "upload-token");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("creates an 8 MiB session, sends a checked part, then completes it", async () => {
    const uploadId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const session = {
      upload_id: uploadId,
      source_project_id: PROJECT_ID,
      client_upload_id: CLIENT_IMPORT_ID,
      requested_media_kind: "video",
      original_filename: "large.mov",
      expected_byte_size: 3,
      received_byte_size: 0,
      part_size: 8 * 1024 * 1024,
      state: "CREATED",
      failure_code: null,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
      expires_at: "2026-08-18T01:00:00Z",
      artifact_revision: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(session))
      .mockResolvedValueOnce(response({ upload_id: uploadId, part_number: 0, received_byte_size: 3 }))
      .mockResolvedValueOnce(response({ ...session, received_byte_size: 3, state: "COMPLETED", artifact_revision: { revision_id: IMAGE_REVISION_ID } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createLargeUpload(PROJECT_ID, {
        clientImportId: CLIENT_IMPORT_ID,
        filename: "large.mov",
        expectedByteSize: 3,
        requestedMediaKind: "video",
        declaredContentType: "video/quicktime",
        replaceRevisionId: null,
      }),
    ).resolves.toMatchObject({ upload_id: uploadId, part_size: 8 * 1024 * 1024 });
    await expect(
      putLargeUploadPart(uploadId, 0, new Blob(["abc"]), "bytes 0-2/3", "a".repeat(64)),
    ).resolves.toEqual({ uploadId, partNumber: 0, receivedByteSize: 3 });
    await expect(completeLargeUpload(uploadId)).resolves.toMatchObject({ state: "COMPLETED" });

    const [createdUrl, createdInit] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(createdUrl.pathname).toBe(`/api/v1/projects/${PROJECT_ID}/large-uploads`);
    expect(createdInit.body).toContain('"client_upload_id"');
    const [partUrl, partInit] = fetchMock.mock.calls[1] as unknown as [URL, RequestInit];
    expect(partUrl.pathname).toBe(`/api/v1/large-uploads/${uploadId}/parts/0`);
    expect(new Headers(partInit.headers).get("Content-Range")).toBe("bytes 0-2/3");
    expect(new Headers(partInit.headers).get("X-Content-SHA256")).toBe("a".repeat(64));
    expect(new Headers(partInit.headers).get("Content-Type")).toBe("application/octet-stream");
  });

  it("gets and aborts the exact large-upload session with same-origin JSON requests", async () => {
    const uploadId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const session = {
      upload_id: uploadId,
      source_project_id: PROJECT_ID,
      client_upload_id: CLIENT_IMPORT_ID,
      requested_media_kind: "video",
      original_filename: "large.mov",
      expected_byte_size: 65 * 1024 * 1024,
      received_byte_size: 8 * 1024 * 1024,
      part_size: 8 * 1024 * 1024,
      state: "RECEIVING",
      failure_code: null,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
      expires_at: "2026-08-18T01:00:00Z",
      artifact_revision: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(session))
      .mockResolvedValueOnce(response({ ...session, state: "ABORTED", failure_code: "UPLOAD_ABORTED" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getLargeUpload(uploadId)).resolves.toMatchObject({ state: "RECEIVING" });
    await expect(abortLargeUpload(uploadId)).resolves.toMatchObject({ state: "ABORTED" });

    const [getUrl, getInit] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(getUrl.pathname).toBe(`/api/v1/large-uploads/${uploadId}`);
    expect(getInit.method).toBeUndefined();
    const [abortUrl, abortInit] = fetchMock.mock.calls[1] as unknown as [URL, RequestInit];
    expect(abortUrl.pathname).toBe(`/api/v1/large-uploads/${uploadId}`);
    expect(abortInit.method).toBe("DELETE");
    expect(abortInit.body).toBe("{}");
    expect(new Headers(abortInit.headers).get("Content-Type")).toBe("application/json");
  });
});

describe("B4 lifecycle client", () => {
  beforeEach(() => {
    sessionStorage.clear();
    sessionStorage.setItem("ai-canvas-session", "b4-token");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses native-picker, explicit-confirmation, and two-step GC paths without local file paths", async () => {
    const grantId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const snapshotId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const renditionId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
    const jobId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
    const planId = "12121212-1212-4121-8121-121212121212";
    const quarantineId = "13131313-1313-4131-8131-131313131313";
    const confirmationToken = "a".repeat(64);
    const external = {
      revision_id: IMAGE_REVISION_ID,
      artifact_id: ARTIFACT_ID,
      health: "DRIFTED",
      storage_kind: "EXTERNAL_REFERENCE",
      snapshot_id: snapshotId,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ grant_id: grantId, display_name: "media-root" }))
      .mockResolvedValueOnce(response(external))
      .mockResolvedValueOnce(response(external))
      .mockResolvedValueOnce(response({ ...external, health: "AVAILABLE" }))
      .mockResolvedValueOnce(
        response({ revision_id: MEDIA_NODE_ID, artifact_id: ARTIFACT_ID, storage_kind: "MANAGED_BLOB" }),
      )
      .mockResolvedValueOnce(
        response({
          code: "transcode_confirmation_required",
          confirmation_token: confirmationToken,
          profile: "preview_720p",
          width: 1280,
          height: 720,
          estimated_output_bytes: 1234,
        }, 409),
      )
      .mockResolvedValueOnce(
        response({
          job_id: jobId,
          rendition_id: renditionId,
          profile: "preview_720p",
          width: 1280,
          height: 720,
          frames_per_second: 30,
          estimated_output_bytes: 1234,
          reused: false,
        }),
      )
      .mockResolvedValueOnce(
        response({
          workspace_quota_bytes: 100 * 1024 ** 3,
          managed_blob_bytes: 10,
          staging_bytes: 2,
          quarantine_bytes: 3,
          active_reservation_bytes: 4,
          physical_free_bytes: 200 * 1024 ** 3,
        }),
      )
      .mockResolvedValueOnce(
        response({
          plan_id: planId,
          summary_sha256: "b".repeat(64),
          entries: [
            {
              entry_id: "14141414-1414-4141-8141-141414141414",
              target_id: IMAGE_REVISION_ID,
              reason: "discarded_unreferenced_revision",
              logical_reclaim_bytes: 3,
              physical_reclaim_bytes: 3,
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        response([{ quarantine_id: quarantineId, target_id: IMAGE_REVISION_ID }]),
      )
      .mockResolvedValueOnce(response({ restored: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(grantExternalRoot()).resolves.toMatchObject({ grant_id: grantId });
    await expect(
      createExternalReference(grantId, { projectId: PROJECT_ID, requestedMediaKind: "video" }),
    ).resolves.toMatchObject({ health: "DRIFTED" });
    await expect(verifyExternalReference(IMAGE_REVISION_ID)).resolves.toMatchObject({
      health: "DRIFTED",
    });
    await expect(reconfirmExternalReference(IMAGE_REVISION_ID, grantId)).resolves.toMatchObject({
      health: "AVAILABLE",
    });
    await expect(importExternalCopy(IMAGE_REVISION_ID)).resolves.toMatchObject({
      storage_kind: "MANAGED_BLOB",
    });
    await expect(requestRendition(IMAGE_REVISION_ID, "preview_720p")).rejects.toMatchObject({
      confirmationToken,
    });
    await expect(
      requestRendition(IMAGE_REVISION_ID, "preview_720p", confirmationToken),
    ).resolves.toMatchObject({ job_id: jobId });
    await expect(storageSummary()).resolves.toMatchObject({ managed_blob_bytes: 10 });
    const plan = await createGcPlan();
    await expect(confirmGcPlan(plan.plan_id, plan.summary_sha256)).resolves.toEqual([
      { quarantine_id: quarantineId, target_id: IMAGE_REVISION_ID },
    ]);
    await expect(restoreQuarantine(quarantineId)).resolves.toBe(true);

    expect(TranscodeConfirmationRequiredError).toBeDefined();
    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname);
    expect(paths).toEqual([
      "/api/v1/external-root-grants/select",
      `/api/v1/external-root-grants/${grantId}/references/select`,
      `/api/v1/artifact-revisions/${IMAGE_REVISION_ID}/external-verify`,
      `/api/v1/artifact-revisions/${IMAGE_REVISION_ID}/external-reconfirm`,
      `/api/v1/artifact-revisions/${IMAGE_REVISION_ID}/external-import-copy`,
      `/api/v1/artifact-revisions/${IMAGE_REVISION_ID}/renditions`,
      `/api/v1/artifact-revisions/${IMAGE_REVISION_ID}/renditions`,
      "/api/v1/storage/summary",
      "/api/v1/gc-plans",
      `/api/v1/gc-plans/${planId}/confirm`,
      `/api/v1/quarantine/${quarantineId}/restore`,
    ]);
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("C:\\\\");
  });
});

describe("apiFetch", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    history.replaceState({}, "", "/");
    sessionStorage.setItem("ai-canvas-session", "session-token");
  });

  it("rejects without a committed token before making any request", async () => {
    sessionStorage.clear();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch("/api/v1/projects")).rejects.toBeInstanceOf(
      SessionExpiredError,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    "https://evil.example/api/x",
    "//evil.example/api/x",
  ])("rejects off-origin URL %s without calling fetch", async (url) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiFetch(url)).rejects.toThrow("same-origin /api/ URL");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["/api", "/api-evil", "/projects"])(
    "rejects non-API path %s without calling fetch",
    async (path) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      await expect(apiFetch(path)).rejects.toThrow("same-origin /api/ URL");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("adds the bearer only after validation and rejects redirects", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 200 });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/v1/projects?limit=10", { redirect: "follow" });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [request, init] = fetchMock.mock.calls[0] as unknown as [string | URL, RequestInit];
    expect(new URL(String(request), location.href).pathname).toBe("/api/v1/projects");
    expect(init.redirect).toBe("error");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("defaults JSON content type only for string request bodies", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 200 });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/v1/projects", { method: "POST", body: "" });
    await apiFetch("/api/v1/projects", { method: "POST", body: "{}" });
    await apiFetch("/api/v1/projects", {
      method: "PUT",
      body: new Blob(["raw"], { type: "image/png" }),
    });

    const emptyStringHeaders = new Headers(fetchMock.mock.calls[0][1]?.headers);
    const jsonHeaders = new Headers(fetchMock.mock.calls[1][1]?.headers);
    const blobHeaders = new Headers(fetchMock.mock.calls[2][1]?.headers);
    expect(emptyStringHeaders.get("Content-Type")).toBe("application/json");
    expect(jsonHeaders.get("Content-Type")).toBe("application/json");
    expect(blobHeaders.has("Content-Type")).toBe(false);
    expect(blobHeaders.get("Authorization")).toBe("Bearer session-token");
  });

  it("preserves an explicit octet-stream header and bearer for raw Blob uploads", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ status: 200 });
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/v1/projects/raw", {
      method: "PUT",
      headers: { "content-type": "application/octet-stream" },
      body: new Blob(["raw"]),
    });

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Content-Type")).toBe("application/octet-stream");
    expect(headers.get("Authorization")).toBe("Bearer session-token");
  });

  it("clears an expired bearer and tells the UI to relaunch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 401 }));
    await expect(apiFetch("/api/v1/projects")).rejects.toBeInstanceOf(SessionExpiredError);
    expect(getSessionToken()).toBeNull();
  });
});

describe("typed project and graph helpers", () => {
  beforeEach(() => {
    sessionStorage.clear();
    sessionStorage.setItem("ai-canvas-session", "session-token");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    history.replaceState({}, "", "/");
  });

  it("uses the validated and encoded project id as one path segment", async () => {
    const project = {
      id: PROJECT_ID,
      name: "Project",
      graph_document_id: "graph-id",
      active_revision_id: "revision-id",
    };
    const graph = {
      revision_id: "revision-id",
      graph_document_id: "graph-id",
      sequence: 1,
      snapshot_hash: "hash",
      snapshot: emptySnapshot,
      viewport: { x: 0, y: 0, zoom: 1 },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(project))
      .mockResolvedValueOnce(response(graph))
      .mockResolvedValueOnce(response({ x: 1, y: 2, zoom: 1.5 }));
    vi.stubGlobal("fetch", fetchMock);
    const projectId = PROJECT_ID;

    await getProject(projectId);
    await getGraph(projectId);
    await saveViewport(projectId, { x: 1, y: 2, zoom: 1.5 });

    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname);
    expect(paths).toEqual([
      `/api/v1/projects/${PROJECT_ID}`,
      `/api/v1/projects/${PROJECT_ID}/graph`,
      `/api/v1/projects/${PROJECT_ID}/view-state`,
    ]);
  });

  it("uses the GraphChangeSet proposal, approval, and application routes", async () => {
    const proposed = {
      id: "66666666-6666-4666-8666-666666666666",
      project_id: PROJECT_ID,
      correlation_id: "77777777-7777-4777-8777-777777777777",
      base_graph_revision_id: "88888888-8888-4888-8888-888888888888",
      changeset_sha256: "a".repeat(64),
      snapshot_hash: "b".repeat(64),
      diff: { op: "replace_snapshot" },
      risk_summary: { classes: ["GRAPH_WRITE"] },
      state: "PENDING_APPROVAL",
      applied_graph_revision_id: null,
      application_target: {
        mode: "DIRECT",
        branch_id: "55555555-5555-4555-8555-555555555555",
        expected_branch_head_revision_id: "88888888-8888-4888-8888-888888888888",
        expected_branch_head_version: 1,
        expected_selection_version: 1,
      },
      impact: {
        items: [],
        summary: { unmaterialized: 0, current: 0, stale: 0 },
        impact_sha256: "c".repeat(64),
        confirmation_required: false,
      },
      created_branch_id: null,
      applied_branch_id: null,
      created_at: "2026-08-27T00:00:00Z",
    };
    const approved = { ...proposed, state: "APPROVED" };
    const applied = {
      ...approved,
      state: "APPLIED",
      applied_graph_revision_id: "99999999-9999-4999-8999-999999999999",
      applied_branch_id: proposed.application_target.branch_id,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(proposed, 201))
      .mockResolvedValueOnce(response(approved))
      .mockResolvedValueOnce(response(applied))
      .mockResolvedValueOnce(response(proposed, 201));
    vi.stubGlobal("fetch", fetchMock);

    const proposal = await proposeGraphChangeSet(PROJECT_ID, {
      baseRevisionId: proposed.base_graph_revision_id,
      clientMutationId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      snapshot: emptySnapshot,
    });
    await approveGraphChangeSet(PROJECT_ID, proposal.id, proposal.changeset_sha256);
    const result = await applyGraphChangeSet(
      PROJECT_ID,
      proposal.id,
      proposal.changeset_sha256,
    );
    await proposeGraphChangeSetUndo(
      PROJECT_ID,
      proposal.id,
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    );

    expect(result.applied_graph_revision_id).toBe(applied.applied_graph_revision_id);
    expect(result.risk_summary).toEqual({ classes: ["GRAPH_WRITE"] });
    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname);
    expect(paths).toEqual([
      `/api/v1/projects/${PROJECT_ID}/graph-change-sets`,
      `/api/v1/projects/${PROJECT_ID}/graph-change-sets/${proposed.id}/approval-decisions`,
      `/api/v1/projects/${PROJECT_ID}/graph-change-sets/${proposed.id}/apply`,
      `/api/v1/projects/${PROJECT_ID}/graph-change-sets/${proposed.id}/undo-proposals`,
    ]);
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual({
      kind: "graph_change_set",
      schema_version: 1,
      base_graph_revision_id: proposed.base_graph_revision_id,
      client_mutation_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      operations: [{ op: "replace_snapshot", snapshot: emptySnapshot }],
    });
    expect(JSON.parse(String((fetchMock.mock.calls[3][1] as RequestInit).body))).toEqual({
      client_mutation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    });
  });

  it("accepts a GraphChangeSet proposal returned by the live API", async () => {
    const liveProposal = {
      id: "f613723b-d622-472b-87a3-c227256566d7",
      project_id: "caaa5835-a803-4943-ae6e-24941b9c8707",
      correlation_id: "d6b64a08-8ec0-472c-a33a-1bf5b3644acf",
      base_graph_revision_id: "d7ceccb8-148c-4d28-ac09-1707347d06fc",
      changeset_sha256: "71458b85e820db084a9470b280b4fa90bc5a0c126e046798aa32249d6e89b8f3",
      snapshot_hash: "fd5c4f1dde35822ff47e74c04835f40ea0a0d7656dceb2779717d03f6a7dee00",
      diff: {
        from_snapshot_hash:
          "3a41dffcb3bb39d8726e700124d07e33aa1d156e58dbf694c8dfce56428415ed",
        op: "replace_snapshot",
        to_snapshot_hash:
          "fd5c4f1dde35822ff47e74c04835f40ea0a0d7656dceb2779717d03f6a7dee00",
      },
      risk_summary: { classes: ["GRAPH_WRITE"] },
      state: "PENDING_APPROVAL" as const,
      applied_graph_revision_id: null,
      application_target: {
        mode: "DIRECT" as const,
        branch_id: "331727d8-5eab-4f98-8b89-62bf9717c391",
        expected_branch_head_revision_id: "d7ceccb8-148c-4d28-ac09-1707347d06fc",
        expected_branch_head_version: 1,
        expected_selection_version: 1,
      },
      impact: {
        items: [],
        summary: { unmaterialized: 0, current: 0, stale: 0 },
        impact_sha256: "71a65c26077fd727e74809d5ff41e385083f1944a82ba34a73d261f9dcc53f25",
        confirmation_required: false,
      },
      created_branch_id: null,
      applied_branch_id: null,
      created_at: "2026-08-27T15:42:21.227451Z",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(liveProposal, 201)));

    await expect(
      proposeGraphChangeSet(liveProposal.project_id, {
        baseRevisionId: liveProposal.base_graph_revision_id,
        clientMutationId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        snapshot: emptySnapshot,
      }),
    ).resolves.toEqual(liveProposal);
  });

  it("strictly validates C2 branch, graph, impact, and promotion responses", async () => {
    const graphDocumentId = "22222222-2222-4222-8222-222222222222";
    const branchId = "33333333-3333-4333-8333-333333333333";
    const revisionId = "44444444-4444-4444-8444-444444444444";
    const promotionId = "55555555-5555-4555-8555-555555555555";
    const branch = {
      id: branchId,
      graph_document_id: graphDocumentId,
      name: "主线",
      kind: "MAIN" as const,
      forked_from_branch_id: null,
      forked_from_revision_id: revisionId,
      head: {
        graph_revision_id: revisionId,
        version: 1,
        graph_revision_sha256: "a".repeat(64),
      },
      created_at: "2026-08-30T00:00:00Z",
      is_selected: true,
    };
    const promotion = {
      id: promotionId,
      project_id: PROJECT_ID,
      correlation_id: "66666666-6666-4666-8666-666666666666",
      client_mutation_id: "77777777-7777-4777-8777-777777777777",
      kind: "PROMOTE" as const,
      source_branch_id: branchId,
      source_graph_revision_id: revisionId,
      source_branch_head_version: 1,
      target_branch_id: "88888888-8888-4888-8888-888888888888",
      target_graph_revision_id: "99999999-9999-4999-8999-999999999999",
      target_branch_head_version: 2,
      expected_selection_version: 1,
      undo_of_promotion_id: null,
      impact: {
        source_branch_id: branchId,
        source_graph_revision_id: revisionId,
        source_graph_revision_sha256: "d".repeat(64),
        target_branch_id: "88888888-8888-4888-8888-888888888888",
        target_graph_revision_id: "99999999-9999-4999-8999-999999999999",
        target_graph_revision_sha256: "e".repeat(64),
        materialization: { unmaterialized: 1, current: 0, stale: 0 },
      },
      impact_sha256: "b".repeat(64),
      promotion_sha256: "c".repeat(64),
      risk_summary: { classes: ["SELECTION_CHANGE"] },
      state: "PENDING_APPROVAL" as const,
      approval_id: null,
      created_at: "2026-08-30T00:00:00Z",
      applied_at: null,
    };
    const workspace = {
      project_id: PROJECT_ID,
      graph_document_id: graphDocumentId,
      branches: [branch],
      selection: {
        graph_document_id: graphDocumentId,
        branch_id: branchId,
        graph_revision_id: revisionId,
        version: 1,
      },
      latest_promotion: promotion,
      can_undo_latest_promotion: false,
    };
    const branchGraph = {
      branch,
      selection: workspace.selection,
      revision: {
        revision_id: revisionId,
        graph_document_id: graphDocumentId,
        sequence: 1,
        snapshot_hash: "d".repeat(64),
        snapshot: emptySnapshot,
        viewport: { x: 0, y: 0, zoom: 1 },
      },
      materialization: { unmaterialized: 1, current: 0, stale: 0 },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(workspace))
      .mockResolvedValueOnce(response(branchGraph))
      .mockResolvedValueOnce(response(promotion, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listBranches(PROJECT_ID)).resolves.toEqual(workspace);
    await expect(getBranchGraph(PROJECT_ID, branchId)).resolves.toEqual(branchGraph);
    await expect(
      proposePromotion(PROJECT_ID, {
        client_mutation_id: promotion.client_mutation_id,
        target_branch_id: promotion.target_branch_id,
        expected_target_branch_head_revision_id: promotion.target_graph_revision_id,
        expected_target_branch_head_version: 2,
        expected_selection_version: 1,
      }),
    ).resolves.toEqual(promotion);

    for (const malformed of [
      { ...workspace, branches: [{ ...branch, head: { ...branch.head, version: 0 } }] },
      { ...workspace, project_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" },
      { ...workspace, branches: [{ ...branch, kind: "ARCHIVED" }] },
      { ...workspace, latest_promotion: { ...promotion, promotion_sha256: undefined } },
    ]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(malformed)));
      await expect(listBranches(PROJECT_ID)).rejects.toMatchObject({ status: 502 });
    }
  });

  it("maps every C2 conflict family to an error that preserves current CAS state", async () => {
    const branchId = "33333333-3333-4333-8333-333333333333";
    const revisionId = "44444444-4444-4444-8444-444444444444";
    const promotionId = "55555555-5555-4555-8555-555555555555";
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          response(
            {
              code: "branch_head_stale",
              branch_id: branchId,
              current_revision_id: revisionId,
              current_version: 4,
            },
            409,
          ),
        )
        .mockResolvedValueOnce(
          response(
            {
              code: "selection_pointer_stale",
              current_branch_id: branchId,
              current_revision_id: revisionId,
              current_version: 5,
            },
            409,
          ),
        )
        .mockResolvedValueOnce(response({ code: "promotion_stale" }, 409))
        .mockResolvedValueOnce(
          response(
            { code: "stale_impact_confirmation_required", impact_sha256: "a".repeat(64) },
            422,
          ),
        ),
    );

    const branchError = await proposeGraphChangeSet(PROJECT_ID, {
      baseRevisionId: revisionId,
      clientMutationId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      snapshot: emptySnapshot,
    }).catch((reason: unknown) => reason);
    expect(branchError).toBeInstanceOf(BranchHeadStaleError);
    expect(branchError).toMatchObject({
      code: "branch_head_stale",
      branchId,
      currentRevisionId: revisionId,
      currentVersion: 4,
    });

    const selectionError = await applyPromotion(
      PROJECT_ID,
      promotionId,
      "b".repeat(64),
    ).catch((reason: unknown) => reason);
    expect(selectionError).toBeInstanceOf(SelectionPointerStaleError);
    expect(selectionError).toMatchObject({
      code: "selection_pointer_stale",
      currentBranchId: branchId,
      currentRevisionId: revisionId,
      currentVersion: 5,
    });

    const promotionError = await proposePromotionUndo(
      PROJECT_ID,
      promotionId,
    ).catch((reason: unknown) => reason);
    expect(promotionError).toBeInstanceOf(C2ApiError);
    expect(promotionError).toMatchObject({ code: "promotion_stale", status: 409 });

    const impactError = await approveGraphChangeSet(
      PROJECT_ID,
      "66666666-6666-4666-8666-666666666666",
      "c".repeat(64),
    ).catch((reason: unknown) => reason);
    expect(impactError).toBeInstanceOf(C2ApiError);
    expect(impactError).toMatchObject({
      code: "stale_impact_confirmation_required",
      status: 422,
    });
  });

  it("preserves the current graph revision when a GraphChangeSet proposal is stale", async () => {
    const currentRevisionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(
          { code: "graph_base_revision_stale", current_revision_id: currentRevisionId },
          409,
        ),
      ),
    );

    const error = await proposeGraphChangeSet(PROJECT_ID, {
      baseRevisionId: "88888888-8888-4888-8888-888888888888",
      clientMutationId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      snapshot: emptySnapshot,
    }).catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      status: 409,
      message: "graph_base_revision_stale",
      currentRevisionId,
    });
  });

  it("keeps config, no-egress run, and event cursor calls on their separate contracts", async () => {
    const config = {
      id: "11111111-1111-4111-8111-111111111111",
      sequence: 1,
      dry_run_max_tasks: 1,
      active_canvas_agent_profile_revision_id: null,
      active_prompt_editor_profile_revision_id: null,
      config_sha256: "a".repeat(64),
      created_at: "2026-08-27T00:00:00Z",
    };
    const configChange = {
      id: "22222222-2222-4222-8222-222222222222",
      audit_project_id: PROJECT_ID,
      correlation_id: "33333333-3333-4333-8333-333333333333",
      base_config_revision_id: config.id,
      changeset_sha256: "b".repeat(64),
      operations: [{ op: "replace", path: "/dry_run_max_tasks", value: 2 }],
      risk_summary: { classes: ["CONFIG_WRITE"] },
      state: "PENDING_APPROVAL",
      created_at: "2026-08-27T00:00:00Z",
    };
    const run = {
      id: "44444444-4444-4444-8444-444444444444",
      project_id: PROJECT_ID,
      correlation_id: "55555555-5555-4555-8555-555555555555",
      request_sha256: "c".repeat(64),
      egress_plan_sha256: "d".repeat(64),
      egress_plan: {
        mode: "NONE",
        endpoint: null,
        external_data_classes: [],
        task_count: 1,
        cost_bound: 0,
        gpu_count: 0,
        request_sha256: "c".repeat(64),
        plan_sha256: "d".repeat(64),
      },
      state: "PENDING_APPROVAL",
    };
    const event = {
      event_id: "66666666-6666-4666-8666-666666666666",
      stream_position: 1,
      occurred_at: "2026-08-27T00:00:00Z",
      project_id: PROJECT_ID,
      correlation_id: run.correlation_id,
      causation_event_id: null,
      actor_kind: "HUMAN",
      actor_id: "local-session",
      subject_type: "run_request",
      subject_id: run.id,
      event_type: "run_request.requested",
      schema_version: 1,
      payload: {},
      payload_sha256: "e".repeat(64),
      envelope_sha256: "f".repeat(64),
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(config))
      .mockResolvedValueOnce(response(configChange, 201))
      .mockResolvedValueOnce(response({ ...configChange, state: "APPROVED" }))
      .mockResolvedValueOnce(response({ ...configChange, state: "APPLIED" }))
      .mockResolvedValueOnce(response(run, 201))
      .mockResolvedValueOnce(
        response({
          id: "77777777-7777-4777-8777-777777777777",
          state: "APPROVED",
          expires_at: "2026-08-27T00:05:00Z",
        }),
      )
      .mockResolvedValueOnce(
        response({
          id: "88888888-8888-4888-8888-888888888888",
          run_request_id: run.id,
          terminal_state: "DRY_RUN_SUCCEEDED",
          created_at: "2026-08-27T00:00:01Z",
        }),
      )
      .mockResolvedValueOnce(response({ events: [event], next_after: 1, high_watermark: 1 }))
      .mockResolvedValueOnce(response({ consumer_key: "f1-web", last_ack_stream_position: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    const active = await getActiveControlConfig();
    const proposal = await proposeConfigChangeSet(PROJECT_ID, {
      baseConfigRevisionId: active.id,
      dryRunMaxTasks: 2,
    });
    await approveConfigChangeSet(proposal.id, proposal.changeset_sha256);
    await applyConfigChangeSet(proposal.id, proposal.changeset_sha256);
    const request = await requestNoEgressDryRun(PROJECT_ID, 1);
    await approveNoEgressDryRun(PROJECT_ID, request.id, request.request_sha256);
    await executeNoEgressDryRun(PROJECT_ID, request.id);
    const page = await readExperienceEventCursor(PROJECT_ID, "f1-web");
    await acknowledgeExperienceEventCursor(PROJECT_ID, "f1-web", page.next_after);

    const paths = fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname);
    expect(paths).toEqual([
      "/api/v1/control/config-revision",
      "/api/v1/control/config-change-sets",
      `/api/v1/control/config-change-sets/${configChange.id}/approval-decisions`,
      `/api/v1/control/config-change-sets/${configChange.id}/apply`,
      `/api/v1/projects/${PROJECT_ID}/run-requests`,
      `/api/v1/projects/${PROJECT_ID}/run-requests/${run.id}/approval-decisions`,
      `/api/v1/projects/${PROJECT_ID}/run-requests/${run.id}/dry-run`,
      `/api/v1/projects/${PROJECT_ID}/experience-event-cursors/f1-web/events`,
      `/api/v1/projects/${PROJECT_ID}/experience-event-cursors/f1-web`,
    ]);
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toMatchObject({
      kind: "config_change_set",
      audit_project_id: PROJECT_ID,
      base_config_revision_id: config.id,
      operations: [{ op: "replace", path: "/dry_run_max_tasks", value: 2 }],
    });
    expect(JSON.parse(String((fetchMock.mock.calls[4][1] as RequestInit).body))).toMatchObject({
      operation_kind: "DRY_RUN",
      task_count: 1,
      endpoint: null,
      external_data_classes: [],
      cost_bound: 0,
      gpu_count: 0,
      real_media: false,
      max_retries: 0,
    });
  });

  it.each(["", ".", "..", "project-id", "project/with?query#fragment"]) (
    "rejects the invalid project id %j before fetch",
    async (projectId) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      await expect(getProject(projectId)).rejects.toThrow("single path segment");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("accepts numeric V1/V2/V3 graph responses without accepting generated string discriminators", async () => {
    const v2 = {
      schema_version: 2 as const,
      nodes: [
        {
          id: TEXT_NODE_ID,
          kind: "note" as const,
          position: { x: 1, y: 2 },
          data: { text: "draft" },
        },
        {
          id: MEDIA_NODE_ID,
          kind: "media_input" as const,
          position: { x: 3, y: 4 },
          data: {
            artifact_revision_id: IMAGE_REVISION_ID,
            media_kind: "image" as const,
          },
        },
        {
          id: SHOT_NODE_ID,
          kind: "shot" as const,
          position: { x: 5, y: 6 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [],
      shots: [
        {
          id: SHOT_ID,
          order: 0,
          image_revision_id: IMAGE_REVISION_ID,
          video_revision_id: null,
          reference_revision_ids: [IMAGE_REVISION_ID],
        },
      ],
    };
    const v3 = {
      schema_version: 3 as const,
      nodes: [
        {
          id: PROMPT_NODE_ID,
          kind: "prompt" as const,
          position: { x: 7, y: 8 },
          data: {
            document: {
              schema_version: 1 as const,
              blocks: [
                {
                  id: TEXT_NODE_ID,
                  kind: "FREEFORM" as const,
                  text: "原文🙂",
                  locked: false,
                },
              ],
              rendered_text: "原文🙂",
              spans: [{ block_id: TEXT_NODE_ID, start_utf16: 0, end_utf16: 4 }],
            },
          },
        },
      ],
      edges: [],
      shots: [],
      resource_bindings: [
        {
          id: EDGE_ID,
          resource_kind: "PROMPT" as const,
          binding_key: "primary_prompt",
          scope_kind: "NODE" as const,
          scope_id: PROMPT_NODE_ID,
          resource_revision_id: IMAGE_REVISION_ID,
        },
      ],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(graphResponse(emptySnapshotV1)))
      .mockResolvedValueOnce(response(graphResponse(v2)))
      .mockResolvedValueOnce(response(graphResponse(v3)))
      .mockResolvedValueOnce(response(graphResponse({ ...emptySnapshotV1, schema_version: "1" })))
      .mockResolvedValueOnce(response(graphResponse({ ...emptySnapshot, schema_version: "2" })))
      .mockResolvedValueOnce(response(graphResponse({ ...v3, schema_version: "3" })));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getGraph(PROJECT_ID)).resolves.toMatchObject({ snapshot: emptySnapshotV1 });
    await expect(getGraph(PROJECT_ID)).resolves.toMatchObject({ snapshot: v2 });
    await expect(getGraph(PROJECT_ID)).resolves.toMatchObject({ snapshot: v3 });
    for (let index = 0; index < 3; index += 1) {
      await expect(getGraph(PROJECT_ID)).rejects.toMatchObject({
        status: 502,
        retryable: true,
      });
    }
  });

  it("accepts the C4 prompt-document edge in a V3 graph", () => {
    const snapshot = {
      schema_version: 3,
      nodes: [
        {
          id: PROMPT_NODE_ID,
          kind: "prompt",
          position: { x: 0, y: 0 },
          data: {
            document: {
              schema_version: 1,
              blocks: [{ id: TEXT_NODE_ID, kind: "FREEFORM", text: "提示", locked: false }],
              rendered_text: "提示",
              spans: [{ block_id: TEXT_NODE_ID, start_utf16: 0, end_utf16: 2 }],
            },
          },
        },
        {
          id: SHOT_NODE_ID,
          kind: "shot",
          position: { x: 0, y: 200 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [{
        id: EDGE_ID,
        source: PROMPT_NODE_ID,
        source_port: "prompt_document",
        target: SHOT_NODE_ID,
        target_port: "prompt_document",
      }],
      shots: [{
        id: SHOT_ID,
        order: 0,
        image_revision_id: null,
        video_revision_id: null,
        reference_revision_ids: [],
      }],
      resource_bindings: [],
    };

    expect(parseGraphSnapshot(snapshot)).toEqual(snapshot);
  });

  it.each([
    ["V1 nodes", { schema_version: 1, nodes: null, edges: [] }],
    ["V1 edges", { schema_version: 1, nodes: [], edges: null }],
    ["V2 nodes", { schema_version: 2, nodes: null, edges: [], shots: [] }],
    ["V2 edges", { schema_version: 2, nodes: [], edges: null, shots: [] }],
    ["V2 shots", { schema_version: 2, nodes: [], edges: [], shots: null }],
    [
      "Shot reference revisions",
      {
        schema_version: 2,
        nodes: [
          {
            id: SHOT_NODE_ID,
            kind: "shot",
            position: { x: 0, y: 0 },
            data: { shot_id: SHOT_ID },
          },
        ],
        edges: [],
        shots: [
          {
            id: SHOT_ID,
            order: 0,
            reference_revision_ids: null,
          },
        ],
      },
    ],
  ])("rejects an explicit null %s collection", async (_case, snapshot) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(graphResponse(snapshot))));

    await expect(getGraph(PROJECT_ID)).rejects.toMatchObject({ status: 502, retryable: true });
  });

  it.each([
    ["single-hole", new Array<string>(1)],
    ["multiple-hole", new Array<string>(3)],
  ])("rejects a %s sparse Shot reference collection", (_case, referenceRevisionIds) => {
    expect(() =>
      parseGraphSnapshot({
        schema_version: 2,
        nodes: [
          {
            id: SHOT_NODE_ID,
            kind: "shot",
            position: { x: 0, y: 0 },
            data: { shot_id: SHOT_ID },
          },
        ],
        edges: [],
        shots: [
          {
            id: SHOT_ID,
            order: 0,
            reference_revision_ids: referenceRevisionIds,
          },
        ],
      }),
    ).toThrow("Invalid graph snapshot");
  });

  it("rejects an accessor discriminator without invoking its getter", () => {
    let getterCalls = 0;
    const snapshot: Record<string, unknown> = { nodes: [], edges: [] };
    Object.defineProperty(snapshot, "schema_version", {
      configurable: true,
      enumerable: true,
      get: () => {
        getterCalls += 1;
        return 1;
      },
    });

    expect(() => parseGraphSnapshot(snapshot)).toThrow("Invalid graph snapshot");
    expect(getterCalls).toBe(0);
  });

  it("rejects a custom prototype carrying inherited graph data", () => {
    const snapshot = Object.assign(Object.create({ inherited_extra: true }), emptySnapshotV1);

    expect(() => parseGraphSnapshot(snapshot)).toThrow("Invalid graph snapshot");
  });

  it.each([
    ["transparent Proxy", () => new Proxy({ ...emptySnapshotV1 }, {})],
    [
      "typed-array collection",
      () => ({ schema_version: 1, nodes: new Uint8Array(), edges: [] }),
    ],
    [
      "custom-prototype array",
      () => {
        const nodes: unknown[] = [];
        Object.setPrototypeOf(nodes, {});
        return { schema_version: 1, nodes, edges: [] };
      },
    ],
  ])("rejects a %s at the graph boundary", (_case, snapshot) => {
    expect(() => parseGraphSnapshot(snapshot())).toThrow("Invalid graph snapshot");
  });

  it.each([
    [
      "non-enumerable",
      () => {
        const snapshot = { ...emptySnapshotV1 };
        Object.defineProperty(snapshot, "hidden", {
          configurable: true,
          enumerable: false,
          value: true,
        });
        return snapshot;
      },
    ],
    [
      "symbol",
      () => {
        const snapshot = { ...emptySnapshotV1 } as Record<PropertyKey, unknown>;
        snapshot[Symbol("hidden")] = true;
        return snapshot;
      },
    ],
  ])("rejects a %s graph extra", (_case, snapshot) => {
    expect(() => parseGraphSnapshot(snapshot())).toThrow("Invalid graph snapshot");
  });

  it("returns a normalized deep copy isolated from later caller mutation", () => {
    const snapshot = {
      schema_version: 2,
      nodes: [
        {
          id: TEXT_NODE_ID,
          kind: "text_source",
          position: { x: 1, y: 2 },
          data: { text: "before" },
        },
        {
          id: PROMPT_NODE_ID,
          kind: "prompt",
          position: { x: 3, y: 4 },
          data: { text: "prompt" },
        },
        {
          id: SHOT_NODE_ID,
          kind: "shot",
          position: { x: 5, y: 6 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [
        {
          id: EDGE_ID,
          source: TEXT_NODE_ID,
          source_port: "text",
          target: PROMPT_NODE_ID,
          target_port: "text",
        },
      ],
      shots: [
        {
          id: SHOT_ID,
          order: 0,
          image_revision_id: IMAGE_REVISION_ID,
          video_revision_id: null,
          reference_revision_ids: [IMAGE_REVISION_ID],
        },
      ],
    };

    const parsed = parseGraphSnapshot(snapshot);
    snapshot.nodes[0].id = PROMPT_NODE_ID;
    snapshot.nodes[0].position.x = 99;
    (snapshot.nodes[0].data as { text: string }).text = "after";
    snapshot.edges[0].source_port = "changed";
    snapshot.shots[0].order = 9;
    snapshot.shots[0].reference_revision_ids[0] = SHOT_ID;
    snapshot.nodes.length = 0;
    snapshot.edges.length = 0;
    snapshot.shots.length = 0;

    expect(parsed).toEqual({
      schema_version: 2,
      nodes: [
        {
          id: TEXT_NODE_ID,
          kind: "text_source",
          position: { x: 1, y: 2 },
          data: { text: "before" },
        },
        {
          id: PROMPT_NODE_ID,
          kind: "prompt",
          position: { x: 3, y: 4 },
          data: { text: "prompt" },
        },
        {
          id: SHOT_NODE_ID,
          kind: "shot",
          position: { x: 5, y: 6 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [
        {
          id: EDGE_ID,
          source: TEXT_NODE_ID,
          source_port: "text",
          target: PROMPT_NODE_ID,
          target_port: "text",
        },
      ],
      shots: [
        {
          id: SHOT_ID,
          order: 0,
          image_revision_id: IMAGE_REVISION_ID,
          video_revision_id: null,
          reference_revision_ids: [IMAGE_REVISION_ID],
        },
      ],
    });
    expect(parsed).not.toBe(snapshot);
  });

  it.each([
    [
      "missing V1",
      { schema_version: 1 },
      { schema_version: 1, nodes: [], edges: [] },
    ],
    [
      "undefined V1",
      { schema_version: 1, nodes: undefined, edges: undefined },
      { schema_version: 1, nodes: [], edges: [] },
    ],
    [
      "missing V2",
      { schema_version: 2 },
      { schema_version: 2, nodes: [], edges: [], shots: [] },
    ],
    [
      "undefined V2",
      { schema_version: 2, nodes: undefined, edges: undefined, shots: undefined },
      { schema_version: 2, nodes: [], edges: [], shots: [] },
    ],
    [
      "missing V3",
      { schema_version: 3 },
      { schema_version: 3, nodes: [], edges: [], shots: [], resource_bindings: [] },
    ],
    [
      "undefined V3",
      {
        schema_version: 3,
        nodes: undefined,
        edges: undefined,
        shots: undefined,
        resource_bindings: undefined,
      },
      { schema_version: 3, nodes: [], edges: [], shots: [], resource_bindings: [] },
    ],
  ])("normalizes %s optional collections to explicit dense arrays", (_case, snapshot, expected) => {
    expect(parseGraphSnapshot(snapshot)).toEqual(expected);
  });

  it.each([
    [
      "rendered text drift",
      {
        schema_version: 3,
        nodes: [
          {
            id: PROMPT_NODE_ID,
            kind: "prompt",
            position: { x: 0, y: 0 },
            data: {
              document: {
                schema_version: 1,
                blocks: [
                  { id: TEXT_NODE_ID, kind: "FREEFORM", text: "原文", locked: false },
                ],
                rendered_text: "漂移",
                spans: [{ block_id: TEXT_NODE_ID, start_utf16: 0, end_utf16: 2 }],
              },
            },
          },
        ],
        edges: [],
        shots: [],
        resource_bindings: [],
      },
    ],
    [
      "missing binding target",
      {
        schema_version: 3,
        nodes: [],
        edges: [],
        shots: [],
        resource_bindings: [
          {
            id: EDGE_ID,
            resource_kind: "STYLE",
            binding_key: "style",
            scope_kind: "NODE",
            scope_id: PROMPT_NODE_ID,
            resource_revision_id: IMAGE_REVISION_ID,
          },
        ],
      },
    ],
  ])("rejects V3 %s", (_case, snapshot) => {
    expect(() => parseGraphSnapshot(snapshot)).toThrow("Invalid graph snapshot");
  });

  it.each([
    [
      "V1 media kind",
      {
        schema_version: 1,
        nodes: [
          {
            id: MEDIA_NODE_ID,
            kind: "media_input",
            position: { x: 0, y: 0 },
            data: { text: "" },
          },
        ],
        edges: [],
      },
    ],
    [
      "extra text data",
      {
        schema_version: 2,
        nodes: [
          {
            id: TEXT_NODE_ID,
            kind: "note",
            position: { x: 0, y: 0 },
            data: { text: "draft", metadata: {} },
          },
        ],
        edges: [],
        shots: [],
      },
    ],
    [
      "non-finite position",
      {
        schema_version: 2,
        nodes: [
          {
            id: TEXT_NODE_ID,
            kind: "note",
            position: { x: Number.POSITIVE_INFINITY, y: 0 },
            data: { text: "draft" },
          },
        ],
        edges: [],
        shots: [],
      },
    ],
    [
      "oversized text",
      {
        schema_version: 2,
        nodes: [
          {
            id: TEXT_NODE_ID,
            kind: "note",
            position: { x: 0, y: 0 },
            data: { text: "x".repeat(100_001) },
          },
        ],
        edges: [],
        shots: [],
      },
    ],
    [
      "half-present media pair",
      {
        schema_version: 2,
        nodes: [
          {
            id: MEDIA_NODE_ID,
            kind: "media_input",
            position: { x: 0, y: 0 },
            data: { artifact_revision_id: IMAGE_REVISION_ID, media_kind: null },
          },
        ],
        edges: [],
        shots: [],
      },
    ],
    [
      "orphan Shot node",
      {
        schema_version: 2,
        nodes: [
          {
            id: SHOT_NODE_ID,
            kind: "shot",
            position: { x: 0, y: 0 },
            data: { shot_id: SHOT_ID },
          },
        ],
        edges: [],
        shots: [],
      },
    ],
    [
      "duplicate Shot order",
      {
        schema_version: 2,
        nodes: [
          {
            id: SHOT_NODE_ID,
            kind: "shot",
            position: { x: 0, y: 0 },
            data: { shot_id: SHOT_ID },
          },
          {
            id: "77777777-7777-4777-8777-777777777777",
            kind: "shot",
            position: { x: 1, y: 1 },
            data: { shot_id: "88888888-8888-4888-8888-888888888888" },
          },
        ],
        edges: [],
        shots: [
          { id: SHOT_ID, order: 0 },
          { id: "88888888-8888-4888-8888-888888888888", order: 0 },
        ],
      },
    ],
    [
      "duplicate Shot reference",
      {
        schema_version: 2,
        nodes: [
          {
            id: SHOT_NODE_ID,
            kind: "shot",
            position: { x: 0, y: 0 },
            data: { shot_id: SHOT_ID },
          },
        ],
        edges: [],
        shots: [
          {
            id: SHOT_ID,
            order: 0,
            reference_revision_ids: [IMAGE_REVISION_ID, IMAGE_REVISION_ID],
          },
        ],
      },
    ],
    [
      "invalid edge port",
      {
        schema_version: 2,
        nodes: [],
        edges: [
          {
            id: "99999999-9999-4999-8999-999999999999",
            source: TEXT_NODE_ID,
            source_port: "bad/port",
            target: MEDIA_NODE_ID,
            target_port: "text",
          },
        ],
        shots: [],
      },
    ],
  ])("rejects a strict malformed graph response: %s", async (_case, snapshot) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(graphResponse(snapshot))));

    await expect(getGraph(PROJECT_ID)).rejects.toMatchObject({ status: 502, retryable: true });
  });

  it("uses generated response shapes for project list results", async () => {
    const project = {
      id: PROJECT_ID,
      name: "Project",
      graph_document_id: "graph-id",
      active_revision_id: "revision-1",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(project, 201))
      .mockResolvedValueOnce(response([project]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(createProject("Project")).resolves.toEqual(project);
    await expect(listProjects()).resolves.toEqual([project]);

    const createInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(createInit.body))).toEqual({ name: "Project" });
  });

  it.each([null, [], { message: 42 }, { code: null }])(
    "falls back to a status message for malformed error JSON: %j",
    async (body) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(body, 422)));

      const error = await createProject("Project").catch((reason: unknown) => reason);

      expect(error).toBeInstanceOf(ApiRequestError);
      expect(error).not.toBeInstanceOf(TypeError);
      expect((error as ApiRequestError).message).toBe("API request failed (422)");
    },
  );

  it("falls back to a status message for a non-JSON error response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(invalidJsonResponse(502)));

    const error = await getProject(PROJECT_ID).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).status).toBe(502);
    expect((error as ApiRequestError).message).toBe("API request failed (502)");
    expect((error as ApiRequestError).retryable).toBe(true);
  });

  it("rejects malformed successful project, list, graph, and viewport responses", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({}))
      .mockResolvedValueOnce(response({}))
      .mockResolvedValueOnce(response({ id: PROJECT_ID }))
      .mockResolvedValueOnce(
        response({
          revision_id: "revision-id",
          graph_document_id: "graph-id",
          sequence: 1,
          snapshot_hash: "hash",
          snapshot: { schema_version: 1, nodes: [{}], edges: [] },
          viewport: { x: 0, y: 0, zoom: 1 },
        }),
      )
      .mockResolvedValueOnce(response({}));
    vi.stubGlobal("fetch", fetchMock);

    const errors = await Promise.all([
      createProject("Project").catch((error: unknown) => error),
      listProjects().catch((error: unknown) => error),
      getProject(PROJECT_ID).catch((error: unknown) => error),
      getGraph(PROJECT_ID).catch((error: unknown) => error),
      saveViewport(PROJECT_ID, { x: 0, y: 0, zoom: 1 }).catch(
        (error: unknown) => error,
      ),
    ]);

    for (const error of errors) {
      expect(error).toBeInstanceOf(ApiRequestError);
      expect((error as ApiRequestError).status).toBe(502);
      expect((error as ApiRequestError).retryable).toBe(true);
      expect((error as ApiRequestError).message).toContain("Invalid API response");
    }
  });

  it("rejects non-JSON success at the generated response boundary", async () => {
    const malformedSuccess = {
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new SyntaxError("invalid json")),
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(malformedSuccess));

    const error = await getProject(PROJECT_ID).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).status).toBe(502);
    expect((error as ApiRequestError).message).toContain("Invalid API response");
  });
});

describe("resource catalog API helpers", () => {
  const resourceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const revisionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const resource = {
    id: resourceId,
    origin_project_id: PROJECT_ID,
    kind: "STYLE" as const,
    name: "电影暖调",
    created_at: "2026-08-28T00:00:00Z",
    archived_at: null,
    latest_revision: {
      id: revisionId,
      sequence: 1,
      payload_sha256: "a".repeat(64),
      created_at: "2026-08-28T00:00:00Z",
      resource_id: resourceId,
      schema_version: 1 as const,
      payload: { kind: "STYLE" as const, summary: "暖色电影质感" },
      tags: [{ display_tag: "电影", normalized_tag: "电影" }],
      artifacts: [],
      created_by_actor: "local-user",
    },
  };

  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    history.replaceState({}, "", "/");
    sessionStorage.setItem("ai-canvas-session", "session-token");
  });

  it("uses repeated tag filters and validates resource and update responses", async () => {
    const update = {
      binding_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      binding_key: "style.primary",
      scope_kind: "PROJECT" as const,
      scope_id: null,
      resource_id: resourceId,
      resource_kind: "STYLE" as const,
      pinned_revision_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      latest_revision_id: revisionId,
      pinned_sequence: 1,
      latest_sequence: 2,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response([resource]))
      .mockResolvedValueOnce(response([update]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listResources({ kind: "STYLE", tags: ["电影", "夜景"], includeArchived: true }),
    ).resolves.toEqual([resource]);
    await expect(listProjectResourceUpdates(PROJECT_ID)).resolves.toEqual([update]);

    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.searchParams.getAll("tags")).toEqual(["电影", "夜景"]);
    expect(url.searchParams.get("include_archived")).toBe("true");
  });

  it("preserves the typed current revision on a resource revision conflict", async () => {
    const currentRevisionId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(
          {
            code: "resource_revision_conflict",
            resource_id: resourceId,
            current_revision_id: currentRevisionId,
          },
          409,
        ),
      ),
    );

    const error = await createResourceRevision(PROJECT_ID, resourceId, {
      base_revision_id: revisionId,
      payload: { kind: "STYLE", summary: "仍保留的编辑" },
      tags: [],
      artifacts: [],
    }).catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ResourceRevisionConflictError);
    expect(error).toMatchObject({ resourceId, currentRevisionId, retryable: false });
  });

  it("serializes a create request without widening the resource contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(resource, 201));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createResource(PROJECT_ID, {
        name: resource.name,
        payload: resource.latest_revision.payload,
        tags: ["电影"],
        artifacts: [],
      }),
    ).resolves.toEqual(resource);
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual({
      name: resource.name,
      payload: resource.latest_revision.payload,
      tags: ["电影"],
      artifacts: [],
    });
  });
});

describe("artifact import and preview helpers", () => {
  const imageRevision = {
    revision_id: IMAGE_REVISION_ID,
    artifact_id: ARTIFACT_ID,
    sequence: 1,
    media_kind: "image" as const,
    media_type: "image/png",
    byte_size: 3,
    display_filename: "safe.png",
    metadata: { kind: "image" as const, width: 2, height: 1 },
    source_kind: "import",
    source_project_id: PROJECT_ID,
    origin_project_id: PROJECT_ID,
    created_at: "2026-07-21T01:02:03Z",
  };

  const completedSession = {
    import_id: IMPORT_ID,
    source_project_id: PROJECT_ID,
    client_import_id: CLIENT_IMPORT_ID,
    replace_revision_id: null,
    requested_media_kind: "image" as const,
    original_filename: "unsafe/../safe.png",
    declared_content_type: "image/png",
    expected_byte_size: 3,
    state: "COMPLETED" as const,
    received_byte_size: 3,
    failure_code: null,
    created_at: "2026-07-21T01:02:03Z",
    updated_at: "2026-07-21T01:02:04Z",
    artifact_revision: imageRevision,
  };

  const createdSession = {
    ...completedSession,
    declared_content_type: null,
    state: "CREATED" as const,
    received_byte_size: 0,
    artifact_revision: null,
  };

  const associationAnchorRevisionId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const associationGraphRevisionId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const videoRevision = {
    ...imageRevision,
    revision_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    artifact_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
    media_kind: "video" as const,
    media_type: "video/mp4",
    display_filename: "safe.mp4",
    metadata: {
      kind: "video" as const,
      width: 2,
      height: 1,
      duration_seconds: 1,
      frames_per_second: 24,
    },
  };
  const associationItem = {
    graph_revision_id: associationGraphRevisionId,
    graph_revision_sequence: 7,
    event_ordinal: 0,
    occurred_at: "2026-07-30T01:02:03Z",
    action: "associated" as const,
    reference_index: null,
    artifact_revision: imageRevision,
  };
  const associationHistory = (
    responseOverride: Record<string, unknown> = {},
    itemOverride: Record<string, unknown> = {},
  ) => ({
    project_id: PROJECT_ID,
    shot_id: SHOT_ID,
    slot: "image" as const,
    anchor_graph_revision_id: associationAnchorRevisionId,
    anchor_graph_sequence: 7,
    items: [{ ...associationItem, ...itemOverride }],
    next_cursor: null,
    ...responseOverride,
  });

  const importSessionWireKeys = [
    "import_id",
    "source_project_id",
    "client_import_id",
    "replace_revision_id",
    "requested_media_kind",
    "original_filename",
    "declared_content_type",
    "expected_byte_size",
    "state",
    "received_byte_size",
    "failure_code",
    "created_at",
    "updated_at",
    "artifact_revision",
  ] as const;

  const missingSequenceRevision: Record<string, unknown> = { ...imageRevision };
  delete missingSequenceRevision.sequence;
  const invalidArtifactRevisionHistories = [
    ["missing sequence", [missingSequenceRevision]],
    ["non-numeric sequence", [{ ...imageRevision, sequence: "bad" }]],
    ["sparse revision history", new Array<unknown>(1)],
  ] as const;

  beforeEach(() => {
    sessionStorage.clear();
    sessionStorage.setItem("ai-canvas-session", "session-token");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    history.replaceState({}, "", "/");
  });

  it("creates an import with the exact declaration and accepts a completed replay", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(completedSession));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await expect(
      createArtifactImport(
        PROJECT_ID,
        {
          clientImportId: CLIENT_IMPORT_ID,
          filename: "unsafe/../safe.png",
          expectedByteSize: 3,
          requestedMediaKind: "image",
          declaredContentType: "image/png",
          replaceRevisionId: null,
        },
        controller.signal,
      ),
    ).resolves.toEqual(completedSession);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe(`/api/v1/projects/${PROJECT_ID}/artifact-imports`);
    expect(init.signal).toBe(controller.signal);
    expect(JSON.parse(String(init.body))).toEqual({
      client_import_id: CLIENT_IMPORT_ID,
      filename: "unsafe/../safe.png",
      expected_byte_size: 3,
      requested_media_kind: "image",
      declared_content_type: "image/png",
      replace_revision_id: null,
    });
  });

  it("normalizes a declared content-type hint before sending and comparing the replay", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(completedSession));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createArtifactImport(PROJECT_ID, {
        clientImportId: CLIENT_IMPORT_ID,
        filename: "unsafe/../safe.png",
        expectedByteSize: 3,
        requestedMediaKind: "image",
        declaredContentType: "  image/png  ",
        replaceRevisionId: null,
      }),
    ).resolves.toEqual(completedSession);

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).declared_content_type).toBe(
      "image/png",
    );
  });

  it("counts declaration and echoed-session filename limits by Unicode code point", async () => {
    const filename = `${"😀".repeat(508)}.png`;
    const payload = {
      ...createdSession,
      original_filename: filename,
      declared_content_type: "image/png",
    };
    const fetchMock = vi.fn().mockResolvedValue(response(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      createArtifactImport(PROJECT_ID, {
        clientImportId: CLIENT_IMPORT_ID,
        filename,
        expectedByteSize: 3,
        requestedMediaKind: "image",
        declaredContentType: "image/png",
        replaceRevisionId: null,
      }),
    ).resolves.toEqual(payload);
    await expect(getArtifactImport(PROJECT_ID, IMPORT_ID)).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["filename", { filename: "x".repeat(513) }],
    ["Unicode filename", { filename: `${"😀".repeat(509)}.png` }],
    ["declared content type", { declaredContentType: "x".repeat(256) }],
  ])("rejects invalid local artifact %s with a typed declaration error", async (_case, override) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const error = await createArtifactImport(PROJECT_ID, {
      clientImportId: CLIENT_IMPORT_ID,
      filename: "safe.png",
      expectedByteSize: 3,
      requestedMediaKind: "image",
      declaredContentType: "image/png",
      replaceRevisionId: null,
      ...override,
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(Error);
    expect(error).toBeInstanceOf(ArtifactDeclarationValidationError);
    expect((error as Error).name).toBe("ArtifactDeclarationValidationError");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uploads the raw Blob with bearer and explicit octet-stream then validates the revision", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(imageRevision));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["png"], "safe.png", { type: "image/png" });

    await expect(uploadArtifactContent(PROJECT_ID, IMPORT_ID, file)).resolves.toEqual(
      imageRevision,
    );

    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe(
      `/api/v1/projects/${PROJECT_ID}/artifact-imports/${IMPORT_ID}/content`,
    );
    expect(init.method).toBe("PUT");
    expect(init.body).toBe(file);
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("Content-Type")).toBe("application/octet-stream");
  });

  it("discards the exact owner-scoped import result through an authenticated JSON DELETE", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({
        outcome: "discarded",
        content_cleanup: "removed",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await expect(
      discardArtifactImportResult(
        {
          projectId: PROJECT_ID,
          importId: IMPORT_ID,
          clientImportId: CLIENT_IMPORT_ID,
          expectedRevisionId: IMAGE_REVISION_ID,
        },
        controller.signal,
      ),
    ).resolves.toEqual({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe(
      `/api/v1/projects/${PROJECT_ID}/artifact-imports/${IMPORT_ID}/result`,
    );
    expect(init.method).toBe("DELETE");
    expect(init.signal).toBe(controller.signal);
    expect(JSON.parse(String(init.body))).toEqual({
      client_import_id: CLIENT_IMPORT_ID,
      expected_revision_id: IMAGE_REVISION_ID,
    });
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer session-token");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("preserves a 202 cleanup-pending result as an unfinished discard", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        {
          outcome: "discarded",
          content_cleanup: "pending",
        },
        202,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      discardArtifactImportResult({
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId: CLIENT_IMPORT_ID,
        expectedRevisionId: null,
      }),
    ).resolves.toEqual({
      httpStatus: 202,
      outcome: "discarded",
      contentCleanup: "pending",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      client_import_id: CLIENT_IMPORT_ID,
      expected_revision_id: null,
    });
  });

  it.each([
    [404, { code: "artifact_import_not_found", import_id: IMPORT_ID }, ApiRequestError],
    [
      409,
      {
        code: "artifact_import_conflict",
        import_id: IMPORT_ID,
        reason: "result_referenced",
      },
      ArtifactImportConflictError,
    ],
    [503, { code: "service_unavailable" }, ApiRequestError],
  ] as const)(
    "never treats discard HTTP %s as idempotent success",
    async (status, body, errorType) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(body, status)));

      await expect(
        discardArtifactImportResult({
          projectId: PROJECT_ID,
          importId: IMPORT_ID,
          clientImportId: CLIENT_IMPORT_ID,
          expectedRevisionId: IMAGE_REVISION_ID,
        }),
      ).rejects.toBeInstanceOf(errorType);
    },
  );

  it.each([
    ["missing field", { outcome: "discarded" }, 200],
    [
      "extra field",
      {
        outcome: "discarded",
        content_cleanup: "removed",
        local_path: "D:/secret.png",
      },
      200,
    ],
    [
      "unknown cleanup",
      { outcome: "discarded", content_cleanup: "unknown" },
      200,
    ],
    [
      "pending on 200",
      { outcome: "discarded", content_cleanup: "pending" },
      200,
    ],
    [
      "completed cleanup on 202",
      { outcome: "discarded", content_cleanup: "removed" },
      202,
    ],
  ])(
    "rejects a malformed discard success: %s",
    async (_label, body, status) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(body, status)));

      await expect(
        discardArtifactImportResult({
          projectId: PROJECT_ID,
          importId: IMPORT_ID,
          clientImportId: CLIENT_IMPORT_ID,
          expectedRevisionId: IMAGE_REVISION_ID,
        }),
      ).rejects.toMatchObject({ status: 502, retryable: true });
    },
  );

  it.each([
    ["project id", { projectId: "../escape" }],
    ["import id", { importId: `${IMPORT_ID}/content` }],
    ["client import id", { clientImportId: "not-a-uuid" }],
    ["revision id", { expectedRevisionId: "not-a-uuid" }],
  ])("rejects an unsafe discard %s before fetch", async (_label, override) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      discardArtifactImportResult({
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId: CLIENT_IMPORT_ID,
        expectedRevisionId: IMAGE_REVISION_ID,
        ...override,
      }),
    ).rejects.toBeInstanceOf(TypeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fetches session, summary, and content through UUID-only authenticated paths", async () => {
    const raw = new Blob(["png"], { type: "image/png" });
    const contentResponse = {
      ok: true,
      status: 200,
      blob: vi.fn().mockResolvedValue(raw),
    } as unknown as Response;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(completedSession))
      .mockResolvedValueOnce(response(imageRevision))
      .mockResolvedValueOnce(contentResponse);
    vi.stubGlobal("fetch", fetchMock);

    await expect(getArtifactImport(PROJECT_ID, IMPORT_ID)).resolves.toEqual(completedSession);
    await expect(getArtifactRevision(IMAGE_REVISION_ID)).resolves.toEqual(imageRevision);
    await expect(getArtifactRevisionContent(IMAGE_REVISION_ID)).resolves.toBe(raw);

    expect(fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)).toEqual([
      `/api/v1/projects/${PROJECT_ID}/artifact-imports/${IMPORT_ID}`,
      `/api/v1/artifact-revisions/${IMAGE_REVISION_ID}`,
      `/api/v1/artifact-revisions/${IMAGE_REVISION_ID}/content`,
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer session-token");
    }
    expect(fetchMock.mock.calls.every(([url]) => !String(url).includes("session-token"))).toBe(
      true,
    );
  });

  it("maps structured import conflict and validation failures without losing reason", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response(
          {
            code: "artifact_import_conflict",
            import_id: IMPORT_ID,
            reason: "upload_in_progress",
          },
          409,
        ),
      )
      .mockResolvedValueOnce(
        response(
          {
            code: "artifact_import_failed",
            import_id: IMPORT_ID,
            failure_code: "probe_timeout",
          },
          422,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const conflict = await createArtifactImport(PROJECT_ID, {
      clientImportId: CLIENT_IMPORT_ID,
      filename: "safe.png",
      expectedByteSize: 3,
      requestedMediaKind: "image",
      declaredContentType: "image/png",
      replaceRevisionId: null,
    }).catch((error: unknown) => error);
    const failed = await uploadArtifactContent(
      PROJECT_ID,
      IMPORT_ID,
      new Blob(["png"]),
    ).catch((error: unknown) => error);

    expect(conflict).toBeInstanceOf(ArtifactImportConflictError);
    expect(conflict).toMatchObject({
      status: 409,
      importId: IMPORT_ID,
      reason: "upload_in_progress",
      retryable: true,
    });
    expect(failed).toBeInstanceOf(ArtifactImportFailedError);
    expect(failed).toMatchObject({
      status: 422,
      importId: IMPORT_ID,
      failureCode: "probe_timeout",
      retryable: true,
    });
  });

  it.each([
    ["declaration_mismatch", false],
    ["content_replay_mismatch", false],
    ["upload_in_progress", true],
    ["invalid_replacement", false],
    ["invalid_state", false],
    ["discard_precondition_failed", false],
    ["result_referenced", false],
  ] as const)("classifies conflict reason %s without suggesting a destructive retry", (reason, retryable) => {
    expect(new ArtifactImportConflictError(IMPORT_ID, reason)).toMatchObject({
      reason,
      retryable,
    });
  });

  it.each([
    ["upload_interrupted", true],
    ["probe_timeout", true],
    ["probe_unavailable", true],
    ["size_mismatch", false],
    ["corrupt_media", false],
    ["unsupported_media", false],
    ["incompatible_declaration", false],
  ] as const)("classifies artifact failure code %s", (failureCode, retryable) => {
    expect(new ArtifactImportFailedError(IMPORT_ID, failureCode)).toMatchObject({
      failureCode,
      retryable,
    });
  });

  it("rejects an upload response scoped to a different source project", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          ...imageRevision,
          source_project_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        }),
      ),
    );

    await expect(
      uploadArtifactContent(PROJECT_ID, IMPORT_ID, new Blob(["png"])),
    ).rejects.toMatchObject({ status: 502, retryable: true });
  });

  it.each([
    ["wrong completed byte size", { byte_size: 4 }],
    ["wrong source kind", { source_kind: "external" }],
  ])("rejects an upload response with %s", async (_case, override) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response({ ...imageRevision, ...override })),
    );

    await expect(
      uploadArtifactContent(PROJECT_ID, IMPORT_ID, new Blob(["png"])),
    ).rejects.toMatchObject({ status: 502, retryable: true });
  });

  it("lists a validated artifact revision history through an authenticated GET", async () => {
    const revisions = [
      imageRevision,
      {
        ...imageRevision,
        revision_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        sequence: 2,
      },
    ];
    const fetchMock = vi.fn().mockResolvedValue(response(revisions));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await expect(listArtifactRevisions(ARTIFACT_ID, controller.signal)).resolves.toEqual(revisions);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe(`/api/v1/artifacts/${ARTIFACT_ID}/revisions`);
    expect(init.method).toBe("GET");
    expect(init.signal).toBe(controller.signal);
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("lists a slot association history through the bound authenticated GET", async () => {
    const payload = associationHistory({ next_cursor: "opaque+/cursor?=value" });
    const fetchMock = vi.fn().mockResolvedValue(response(payload));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await expect(
      listShotSlotAssociations(
        PROJECT_ID,
        SHOT_ID,
        "image",
        associationAnchorRevisionId,
        "previous+/cursor?=value",
        17,
        controller.signal,
      ),
    ).resolves.toEqual(payload);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.pathname).toBe(
      `/api/v1/projects/${PROJECT_ID}/shots/${SHOT_ID}/slots/image/associations`,
    );
    expect(url.searchParams.get("anchor_revision_id")).toBe(associationAnchorRevisionId);
    expect(url.searchParams.get("cursor")).toBe("previous+/cursor?=value");
    expect(url.searchParams.get("limit")).toBe("17");
    expect(init).toMatchObject({ method: "GET", signal: controller.signal });
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer session-token");
  });

  it("accepts an empty association page with an opaque continuation cursor", async () => {
    const payload = associationHistory({ items: [], next_cursor: "still-more" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(
      listShotSlotAssociations(
        PROJECT_ID,
        SHOT_ID,
        "image",
        associationAnchorRevisionId,
      ),
    ).resolves.toEqual(payload);
  });

  it.each([
    ["extra response field", associationHistory({ local_path: "D:/secret.png" })],
    [
      "missing next cursor",
      (() => {
        const payload: Record<string, unknown> = associationHistory();
        delete payload.next_cursor;
        return payload;
      })(),
    ],
    ["wrong project echo", associationHistory({ project_id: ARTIFACT_ID })],
    ["wrong shot echo", associationHistory({ shot_id: ARTIFACT_ID })],
    ["wrong slot echo", associationHistory({ slot: "video" })],
    ["wrong anchor echo", associationHistory({ anchor_graph_revision_id: ARTIFACT_ID })],
    ["negative anchor sequence", associationHistory({ anchor_graph_sequence: -1 })],
    ["fractional anchor sequence", associationHistory({ anchor_graph_sequence: 1.5 })],
    ["empty continuation cursor", associationHistory({ next_cursor: "" })],
    ["oversized continuation cursor", associationHistory({ next_cursor: "x".repeat(2_049) })],
    ["non-string continuation cursor", associationHistory({ next_cursor: 1 })],
    [
      "too many items for requested limit",
      associationHistory({ items: [associationItem, { ...associationItem, event_ordinal: 1 }] }),
    ],
    ["extra item field", associationHistory({}, { snapshot_hash: "secret" })],
    [
      "missing artifact payload",
      (() => {
        const item: Record<string, unknown> = { ...associationItem };
        delete item.artifact_revision;
        return associationHistory({ items: [item] });
      })(),
    ],
    ["invalid graph revision UUID", associationHistory({}, { graph_revision_id: "bad" })],
    ["negative graph sequence", associationHistory({}, { graph_revision_sequence: -1 })],
    ["fractional graph sequence", associationHistory({}, { graph_revision_sequence: 1.5 })],
    ["negative event ordinal", associationHistory({}, { event_ordinal: -1 })],
    ["fractional event ordinal", associationHistory({}, { event_ordinal: 0.5 })],
    ["oversized event ordinal", associationHistory({}, { event_ordinal: 128 })],
    ["invalid event date", associationHistory({}, { occurred_at: "2026-07-30" })],
    ["unknown action", associationHistory({}, { action: "restored" })],
    ["image removal", associationHistory({}, { action: "removed" })],
    ["image reference index", associationHistory({}, { reference_index: 0 })],
    ["associated without artifact", associationHistory({}, { artifact_revision: null })],
    [
      "cleared with artifact",
      associationHistory({}, { action: "cleared", reference_index: null }),
    ],
    [
      "cleared with reference index",
      associationHistory({}, { action: "cleared", reference_index: 0, artifact_revision: null }),
    ],
    [
      "video carrying image artifact",
      associationHistory({ slot: "video" }, { artifact_revision: imageRevision }),
    ],
    [
      "image carrying video artifact",
      associationHistory({}, { artifact_revision: videoRevision }),
    ],
    [
      "reference without index",
      associationHistory({ slot: "reference" }, { reference_index: null }),
    ],
    [
      "reference with negative index",
      associationHistory({ slot: "reference" }, { reference_index: -1 }),
    ],
    [
      "reference clear",
      associationHistory(
        { slot: "reference" },
        { action: "cleared", reference_index: null, artifact_revision: null },
      ),
    ],
    [
      "reference removed without artifact",
      associationHistory(
        { slot: "reference" },
        { action: "removed", reference_index: 0, artifact_revision: null },
      ),
    ],
  ])("rejects a malformed association history: %s", async (_case, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(
      listShotSlotAssociations(
        PROJECT_ID,
        SHOT_ID,
        payload.slot === "reference" || payload.slot === "video" ? payload.slot : "image",
        associationAnchorRevisionId,
        undefined,
        _case === "too many items for requested limit" ? 1 : 20,
      ),
    ).rejects.toMatchObject({
      status: 502,
      message: "Invalid API response from local service",
      retryable: true,
    });
  });

  it.each([
    ["project", "unsafe/project", SHOT_ID, "image", associationAnchorRevisionId, undefined, 20],
    ["shot", PROJECT_ID, "unsafe/shot", "image", associationAnchorRevisionId, undefined, 20],
    ["slot", PROJECT_ID, SHOT_ID, "audio", associationAnchorRevisionId, undefined, 20],
    ["anchor", PROJECT_ID, SHOT_ID, "image", "bad-anchor", undefined, 20],
    ["empty cursor", PROJECT_ID, SHOT_ID, "image", associationAnchorRevisionId, "", 20],
    [
      "oversized cursor",
      PROJECT_ID,
      SHOT_ID,
      "image",
      associationAnchorRevisionId,
      "x".repeat(2_049),
      20,
    ],
    ["zero limit", PROJECT_ID, SHOT_ID, "image", associationAnchorRevisionId, undefined, 0],
    ["large limit", PROJECT_ID, SHOT_ID, "image", associationAnchorRevisionId, undefined, 51],
    ["fractional limit", PROJECT_ID, SHOT_ID, "image", associationAnchorRevisionId, undefined, 1.5],
  ] as const)(
    "rejects an unsafe association request %s before fetch",
    async (_case, projectId, shotId, slot, anchorId, cursor, limit) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      await expect(
        listShotSlotAssociations(
          projectId,
          shotId,
          slot as "image",
          anchorId,
          cursor,
          limit,
        ),
      ).rejects.toBeInstanceOf(TypeError);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("accepts a dense artifact revision history larger than 5,000 items", async () => {
    const revisions = Array.from({ length: 5_001 }, (_, index) => ({
      ...imageRevision,
      revision_id: `cccccccc-cccc-4ccc-8ccc-${(index + 1).toString(16).padStart(12, "0")}`,
      sequence: index + 1,
    }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(revisions)));

    await expect(listArtifactRevisions(ARTIFACT_ID)).resolves.toEqual(revisions);
  });

  it.each(invalidArtifactRevisionHistories)(
    "rejects an invalid artifact revision history: %s",
    async (_case, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(listArtifactRevisions(ARTIFACT_ID)).rejects.toMatchObject({
      status: 502,
      message: "Invalid API response from local service",
      retryable: true,
    });
    },
  );

  it("rejects an artifact revision history that contains another artifact's revision", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response([
          imageRevision,
          { ...imageRevision, artifact_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" },
        ]),
      ),
    );

    await expect(listArtifactRevisions(ARTIFACT_ID)).rejects.toMatchObject({
      status: 502,
      message: "Invalid API response from local service",
      retryable: true,
    });
  });

  it.each([
    ["unknown id", { ...imageRevision, revision_id: "not-a-uuid" }],
    ["mismatched metadata", { ...imageRevision, metadata: { ...imageRevision.metadata, kind: "video" } }],
    ["non-finite metadata", { ...imageRevision, metadata: { ...imageRevision.metadata, width: Infinity } }],
    ["kind and MIME mismatch", { ...imageRevision, media_type: "video/mp4" }],
    ["unsupported MIME", { ...imageRevision, media_type: "image/gif" }],
    ["extra private field", { ...imageRevision, local_path: "D:/secret.png" }],
  ])("rejects a malformed successful artifact revision: %s", async (_case, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getArtifactRevision(IMAGE_REVISION_ID)).rejects.toMatchObject({
      status: 502,
      retryable: true,
    });
  });

  it.each([
    "0",
    "2026",
    "2026-07-21",
    "2026-07-21T01:02:03",
    "2026-07-21 01:02:03Z",
    "0000-01-01T00:00:00Z",
    "2025-02-29T00:00:00Z",
    "2024-02-30T00:00:00Z",
    "2026-13-01T00:00:00Z",
    "2026-07-21T24:00:00Z",
    "2026-07-21T23:60:00Z",
    "2026-07-21T23:59:60Z",
    "2026-07-21T23:59:59.Z",
    "2026-07-21T23:59:59+24:00",
    "2026-07-21T23:59:59+00:60",
  ])("rejects a non-RFC3339 artifact timestamp: %s", async (createdAt) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response({ ...imageRevision, created_at: createdAt })),
    );

    await expect(getArtifactRevision(IMAGE_REVISION_ID)).rejects.toMatchObject({
      status: 502,
      retryable: true,
    });
  });

  it.each([
    "2026-07-21T01:02:03Z",
    "2026-07-21T01:02:03.123456Z",
    "2026-07-21T01:02:03+00:00",
    "2024-02-29T23:59:59.1-14:00",
  ])("accepts a complete valid RFC3339 artifact timestamp: %s", async (createdAt) => {
    const payload = { ...imageRevision, created_at: createdAt };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getArtifactRevision(IMAGE_REVISION_ID)).resolves.toEqual(payload);
  });

  it("rejects a non-RFC3339 import-session updated timestamp", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response({ ...createdSession, updated_at: "2026-07-21" })),
    );

    await expect(getArtifactImport(PROJECT_ID, IMPORT_ID)).rejects.toMatchObject({
      status: 502,
      retryable: true,
    });
  });

  it.each([
    "../relative/camera.png",
    "/absolute/path/camera.png",
    String.raw`C:\absolute\camera.png`,
    String.raw`\\server\share\camera.png`,
    "photo.png:payload.exe",
    "bad\0name\u202egnp.exe.png",
    'bad<>"|?*.png',
    "CON",
    "nul.txt",
    "... ",
    "cafe\u0301.png",
    "camera.png. ",
    "x".repeat(256),
  ])("rejects an artifact revision with a non-sanitized display filename: %j", async (displayFilename) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({ ...imageRevision, display_filename: displayFilename }),
      ),
    );

    await expect(getArtifactRevision(IMAGE_REVISION_ID)).rejects.toMatchObject({
      status: 502,
      retryable: true,
    });
  });

  it.each(["camera.png", "photo.png_payload.exe", "_CON", "upload", "caf\u00e9.png", "😀".repeat(255)])(
    "accepts a safe sanitized display filename: %j",
    async (displayFilename) => {
      const payload = { ...imageRevision, display_filename: displayFilename };
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

      await expect(getArtifactRevision(IMAGE_REVISION_ID)).resolves.toEqual(payload);
    },
  );

  it.each(importSessionWireKeys)(
    "rejects an import session missing required wire field %s",
    async (missingKey) => {
      const payload: Record<string, unknown> = { ...createdSession };
      delete payload[missingKey];
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

      await expect(getArtifactImport(PROJECT_ID, IMPORT_ID)).rejects.toMatchObject({
        status: 502,
        retryable: true,
      });
    },
  );

  it("accepts explicitly present null values for every nullable import-session field", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(createdSession)));

    await expect(getArtifactImport(PROJECT_ID, IMPORT_ID)).resolves.toEqual(createdSession);
  });

  it.each([
    ["completed without revision", { ...completedSession, artifact_revision: null }],
    ["failure code on completed", { ...completedSession, failure_code: "probe_timeout" }],
    [
      "final code in retryable phase",
      {
        ...completedSession,
        state: "FAILED_RETRYABLE",
        received_byte_size: 0,
        failure_code: "corrupt_media",
        artifact_revision: null,
      },
    ],
    [
      "retryable code in final phase",
      {
        ...completedSession,
        state: "FAILED_FINAL",
        received_byte_size: 0,
        failure_code: "probe_timeout",
        artifact_revision: null,
      },
    ],
    ["wrong client id", { ...completedSession, client_import_id: "not-a-uuid" }],
    ["extra field", { ...completedSession, bearer: "secret" }],
  ])("rejects a malformed successful import session: %s", async (_case, payload) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getArtifactImport(PROJECT_ID, IMPORT_ID)).rejects.toMatchObject({
      status: 502,
      retryable: true,
    });
  });

  it.each([
    [new File(["x"], "still.PNG", { type: "" }), "image"],
    [new File(["x"], "clip.bin", { type: "video/webm" }), "video"],
    [new File(["x"], "clip.mp4", { type: "application/octet-stream" }), "video"],
    [new File(["x"], "clip.mov", { type: "video/quicktime" }), "video"],
    [new File(["x"], "clip.mkv", { type: "video/x-matroska" }), "video"],
    [new File(["x"], "unknown.bin", { type: "application/octet-stream" }), null],
  ] as const)("uses browser file fields only as a media-kind hint", (file, expected) => {
    expect(inferRequestedMediaKind(file)).toBe(expected);
  });

  it.each(["..", "not-a-uuid", `${IMPORT_ID}/content`, `${IMPORT_ID}?x=1`])(
    "rejects an unsafe artifact UUID path segment %s before fetch",
    async (unsafeId) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      await expect(getArtifactRevision(unsafeId)).rejects.toThrow("UUID path segment");
      await expect(listArtifactRevisions(unsafeId)).rejects.toThrow("UUID path segment");
      await expect(getArtifactImport(PROJECT_ID, unsafeId)).rejects.toThrow(
        "UUID path segment",
      );
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
});
