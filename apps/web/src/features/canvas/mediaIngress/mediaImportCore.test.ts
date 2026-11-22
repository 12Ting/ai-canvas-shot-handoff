import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ArtifactImportConflictError,
  ArtifactImportFailedError,
  type ArtifactImportDeclaration,
  type ArtifactImportSession,
  type ArtifactRevision,
  type LargeUploadDeclaration,
  type LargeUploadSession,
} from "../../../api/client";
import { createMediaImportCore, type MediaImportAttempt } from "./mediaImportCore";

const api = vi.hoisted(() => ({
  abortLargeUpload: vi.fn(),
  createArtifactImport: vi.fn(),
  createLargeUpload: vi.fn(),
  discardArtifactImportResult: vi.fn(),
  completeLargeUpload: vi.fn(),
  getArtifactImport: vi.fn(),
  getLargeUpload: vi.fn(),
  putLargeUploadPart: vi.fn(),
  uploadArtifactContent: vi.fn(),
}));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  ...api,
}));

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const IMPORT_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_ID = "99999999-9999-4999-8999-999999999999";
const declaration: ArtifactImportDeclaration = {
  clientImportId: "33333333-3333-4333-8333-333333333333",
  filename: "clip.png",
  expectedByteSize: 3,
  requestedMediaKind: "image",
  declaredContentType: "image/png",
  replaceRevisionId: "66666666-6666-4666-8666-666666666666",
};
const largeDeclaration: LargeUploadDeclaration = {
  // The ingress controller uses one client operation id for both import paths.
  // Large uploads must accept that public declaration shape as well.
  clientImportId: declaration.clientImportId,
  filename: "large.mov",
  expectedByteSize: 65 * 1024 * 1024,
  requestedMediaKind: "video",
  declaredContentType: "video/quicktime",
  replaceRevisionId: declaration.replaceRevisionId,
};

function revision(overrides: Partial<ArtifactRevision> = {}): ArtifactRevision {
  return {
    revision_id: "44444444-4444-4444-8444-444444444444",
    artifact_id: "55555555-5555-4555-8555-555555555555",
    sequence: 1,
    media_kind: "image",
    media_type: "image/png",
    byte_size: 3,
    display_filename: "clip.png",
    metadata: { kind: "image", width: 1, height: 1 },
    source_kind: "import",
    source_project_id: PROJECT_ID,
    origin_project_id: PROJECT_ID,
    created_at: "2026-07-21T00:00:00Z",
    ...overrides,
  };
}

function session(
  state: ArtifactImportSession["state"],
  overrides: Partial<ArtifactImportSession> = {},
): ArtifactImportSession {
  return {
    import_id: IMPORT_ID,
    source_project_id: PROJECT_ID,
    client_import_id: declaration.clientImportId,
    replace_revision_id: declaration.replaceRevisionId,
    requested_media_kind: "image",
    original_filename: "clip.png",
    declared_content_type: "image/png",
    expected_byte_size: 3,
    state,
    received_byte_size: state === "COMPLETED" ? 3 : 0,
    failure_code: null,
    created_at: "2026-07-21T00:00:00Z",
    updated_at: "2026-07-21T00:00:01Z",
    artifact_revision: state === "COMPLETED" ? revision() : null,
    ...overrides,
  };
}

function largeSession(
  state: LargeUploadSession["state"],
  overrides: Partial<LargeUploadSession> = {},
): LargeUploadSession {
  return {
    upload_id: IMPORT_ID,
    source_project_id: PROJECT_ID,
    client_upload_id: largeDeclaration.clientImportId,
    requested_media_kind: "video",
    original_filename: largeDeclaration.filename,
    expected_byte_size: largeDeclaration.expectedByteSize,
    received_byte_size: state === "COMPLETED" ? largeDeclaration.expectedByteSize : 0,
    part_size: 8 * 1024 * 1024,
    state,
    failure_code: null,
    created_at: "2026-08-18T00:00:00Z",
    updated_at: "2026-08-18T00:00:01Z",
    expires_at: "2026-08-18T01:00:00Z",
    artifact_revision:
      state === "COMPLETED"
        ? revision({
            media_kind: "video",
            media_type: "video/mp4",
            byte_size: largeDeclaration.expectedByteSize,
            metadata: {
              kind: "video",
              width: 1920,
              height: 1080,
              duration_seconds: 1,
              frames_per_second: 24,
            },
          })
        : null,
    ...overrides,
  };
}

function attempt(inputDeclaration = declaration): MediaImportAttempt {
  return createMediaImportCore().createAttempt({
    operationId: "operation-1",
    projectId: PROJECT_ID,
    declaration: inputDeclaration,
    file: new File(["png"], inputDeclaration.filename, { type: "image/png" }),
  });
}

function execute(value: MediaImportAttempt) {
  return value.execute({
    signal: new AbortController().signal,
    onPhase: vi.fn(),
  });
}

const sessionMismatches: ReadonlyArray<
  readonly [string, Partial<ArtifactImportSession>]
> = [
  ["source project", { source_project_id: OTHER_ID }],
  ["client import id", { client_import_id: OTHER_ID }],
  ["filename", { original_filename: "other.png" }],
  ["expected byte size", { expected_byte_size: 4 }],
  ["requested media kind", { requested_media_kind: "video" }],
  ["declared content type", { declared_content_type: "video/mp4" }],
  ["replacement revision", { replace_revision_id: OTHER_ID }],
];

const revisionMismatches: ReadonlyArray<
  readonly [string, Partial<ArtifactRevision>]
> = [
  ["media kind", { media_kind: "video" }],
  ["source project", { source_project_id: OTHER_ID }],
  ["source kind", { source_kind: "external" }],
  ["byte size", { byte_size: 4 }],
];

describe("MediaImportCore", () => {
  afterEach(() => {
    vi.useRealTimers();
    for (const mock of Object.values(api)) mock.mockReset();
  });

  it("forwards an owner-scoped discard without broadening its precondition", async () => {
    const signal = new AbortController().signal;
    api.discardArtifactImportResult.mockResolvedValue({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });

    await expect(
      createMediaImportCore().discardImport({
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId: declaration.clientImportId,
        expectedRevisionId: revision().revision_id,
        signal,
      }),
    ).resolves.toEqual({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });
    expect(api.discardArtifactImportResult).toHaveBeenCalledWith(
      {
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId: declaration.clientImportId,
        expectedRevisionId: revision().revision_id,
      },
      signal,
    );
  });

  it("uses the B4 upload deletion endpoint when discarding a large-upload result", async () => {
    api.abortLargeUpload.mockResolvedValue(largeSession("ABORTED"));

    await expect(
      createMediaImportCore().discardImport({
        projectId: PROJECT_ID,
        importId: IMPORT_ID,
        clientImportId: largeDeclaration.clientImportId,
        expectedRevisionId: null,
        isLargeUpload: true,
        signal: new AbortController().signal,
      }),
    ).resolves.toEqual({
      httpStatus: 200,
      outcome: "discarded",
      contentCleanup: "removed",
    });
    expect(api.abortLargeUpload).toHaveBeenCalledWith(
      IMPORT_ID,
      expect.any(AbortSignal),
    );
    expect(api.discardArtifactImportResult).not.toHaveBeenCalled();
  });

  it("sends a file over 64 MiB through exact 8 MiB session chunks", async () => {
    const core = createMediaImportCore();
    const file = new File(
      [new Uint8Array(largeDeclaration.expectedByteSize)],
      largeDeclaration.filename,
      { type: largeDeclaration.declaredContentType },
    );
    const value = core.createAttempt({
      operationId: "large-operation",
      projectId: PROJECT_ID,
      declaration: largeDeclaration,
      file,
    });
    api.createLargeUpload.mockResolvedValue(largeSession("CREATED"));
    api.putLargeUploadPart.mockImplementation(async (_id, partNumber, content) => ({
      uploadId: IMPORT_ID,
      partNumber,
      receivedByteSize: Math.min((partNumber + 1) * 8 * 1024 * 1024, content.size + partNumber * 8 * 1024 * 1024),
    }));
    api.completeLargeUpload.mockResolvedValue(largeSession("COMPLETED"));
    const onProgress = vi.fn();

    await expect(value.execute({
      signal: new AbortController().signal,
      onPhase: vi.fn(),
      onProgress,
    })).resolves.toMatchObject({
      media_kind: "video",
      byte_size: largeDeclaration.expectedByteSize,
    });

    expect(api.createLargeUpload).toHaveBeenCalledWith(PROJECT_ID, largeDeclaration, expect.any(AbortSignal));
    expect(api.putLargeUploadPart).toHaveBeenCalledTimes(9);
    expect(api.putLargeUploadPart.mock.calls[0][3]).toBe(
      `bytes 0-${8 * 1024 * 1024 - 1}/${largeDeclaration.expectedByteSize}`,
    );
    expect(api.putLargeUploadPart.mock.calls[8][3]).toBe(
      `bytes ${8 * 8 * 1024 * 1024}-${largeDeclaration.expectedByteSize - 1}/${largeDeclaration.expectedByteSize}`,
    );
    expect(api.completeLargeUpload).toHaveBeenCalledWith(IMPORT_ID, expect.any(AbortSignal));
    expect(onProgress).toHaveBeenCalledWith({
      receivedByteSize: largeDeclaration.expectedByteSize,
      totalByteSize: largeDeclaration.expectedByteSize,
    });
  });

  it("cancels an active large-upload session through the B4 endpoint", async () => {
    const value = createMediaImportCore().createAttempt({
      operationId: "large-cancel",
      projectId: PROJECT_ID,
      declaration: largeDeclaration,
      file: new File(["large"], largeDeclaration.filename, {
        type: largeDeclaration.declaredContentType,
      }),
    });
    api.createLargeUpload.mockResolvedValue(largeSession("RECEIVING"));
    await value.recoverImportId({ signal: new AbortController().signal });
    api.abortLargeUpload.mockResolvedValue(largeSession("ABORTED"));

    await expect(
      value.reconcileCancellation({
        signal: new AbortController().signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toEqual({
      kind: "cancelled",
      serverState: "ABORTED",
      reason: "aborted",
    });
    expect(api.abortLargeUpload).toHaveBeenCalledWith(
      IMPORT_ID,
      expect.any(AbortSignal),
    );
    expect(api.getArtifactImport).not.toHaveBeenCalled();
  });

  it("recovers and aborts a large session when the initial create response was lost", async () => {
    const value = createMediaImportCore().createAttempt({
      operationId: "large-create-lost",
      projectId: PROJECT_ID,
      declaration: largeDeclaration,
      file: new File(["large"], largeDeclaration.filename, {
        type: largeDeclaration.declaredContentType,
      }),
    });
    api.createLargeUpload
      .mockRejectedValueOnce(new TypeError("create response lost"))
      .mockResolvedValueOnce(largeSession("CREATED"));

    await expect(execute(value)).rejects.toThrow("create response lost");
    expect(value.wasCreateDispatched()).toBe(true);
    api.abortLargeUpload.mockResolvedValue(largeSession("ABORTED"));

    await expect(
      value.reconcileCancellation({
        signal: new AbortController().signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toMatchObject({ kind: "cancelled", serverState: "ABORTED" });
    expect(api.createLargeUpload).toHaveBeenCalledTimes(2);
    expect(api.abortLargeUpload).toHaveBeenCalledWith(
      IMPORT_ID,
      expect.any(AbortSignal),
    );
  });

  it("replays the immutable declaration to recover an import id after a lost create response", async () => {
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(
      session("FAILED_FINAL", {
        failure_code: "corrupt_media",
      }),
    );
    const signal = new AbortController().signal;

    await expect(value.recoverImportId({ signal })).resolves.toBe(IMPORT_ID);

    expect(api.createArtifactImport).toHaveBeenCalledWith(
      PROJECT_ID,
      declaration,
      signal,
    );
    expect(value.getImportId()).toBe(IMPORT_ID);
  });

  it.each(sessionMismatches)(
    "rejects an initial POST session with mismatched %s",
    async (_label, mismatch) => {
      const value = attempt();
      api.createArtifactImport.mockResolvedValue(session("CREATED", mismatch));

      await expect(execute(value)).rejects.toThrow("active declaration");

      expect(value.getImportId()).toBeNull();
      expect(value.wasContentPutDispatched()).toBe(false);
      expect(api.uploadArtifactContent).not.toHaveBeenCalled();
      expect(api.getArtifactImport).not.toHaveBeenCalled();
    },
  );

  it("accepts the server-assigned initial import id and requires every later GET to keep it", async () => {
    vi.useFakeTimers();
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("RECEIVING"));
    api.getArtifactImport
      .mockResolvedValueOnce(session("VALIDATING"))
      .mockResolvedValueOnce(
        session("COMPLETED", { artifact_revision: revision() }),
      );

    const result = execute(value);
    await vi.advanceTimersByTimeAsync(500);

    await expect(result).resolves.toEqual(revision());
    expect(value.getImportId()).toBe(IMPORT_ID);
    expect(api.getArtifactImport).toHaveBeenNthCalledWith(
      1,
      PROJECT_ID,
      IMPORT_ID,
      expect.any(AbortSignal),
    );
    expect(api.getArtifactImport).toHaveBeenNthCalledWith(
      2,
      PROJECT_ID,
      IMPORT_ID,
      expect.any(AbortSignal),
    );
    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
  });

  it.each([
    ["import id", { import_id: OTHER_ID }],
    ...sessionMismatches,
  ] as ReadonlyArray<readonly [string, Partial<ArtifactImportSession>]>) (
    "rejects a polled GET session with mismatched %s without following it",
    async (_label, mismatch) => {
      vi.useFakeTimers();
      const value = attempt();
      api.createArtifactImport.mockResolvedValue(session("RECEIVING"));
      api.getArtifactImport.mockResolvedValue(
        session("COMPLETED", {
          artifact_revision: revision(),
          ...mismatch,
        }),
      );

      const result = execute(value);
      const rejected = expect(result).rejects.toThrow("active declaration");
      await vi.advanceTimersByTimeAsync(250);

      await rejected;
      expect(value.getImportId()).toBe(IMPORT_ID);
      expect(api.uploadArtifactContent).not.toHaveBeenCalled();
    },
  );

  it("returns an exact init replay revision without dispatching a PUT", async () => {
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("COMPLETED"));

    await expect(execute(value)).resolves.toEqual(revision());

    expect(value.getImportId()).toBe(IMPORT_ID);
    expect(value.wasContentPutDispatched()).toBe(false);
    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
  });

  it.each(revisionMismatches)(
    "rejects an init replay revision with mismatched %s",
    async (_label, mismatch) => {
      api.createArtifactImport.mockResolvedValue(
        session("COMPLETED", {
          artifact_revision: revision(mismatch),
        }),
      );

      await expect(execute(attempt())).rejects.toThrow(
        "completed import revision",
      );
      expect(api.uploadArtifactContent).not.toHaveBeenCalled();
    },
  );

  it.each(revisionMismatches)(
    "rejects a direct PUT revision with mismatched %s",
    async (_label, mismatch) => {
      const value = attempt();
      api.createArtifactImport.mockResolvedValue(session("CREATED"));
      api.uploadArtifactContent.mockResolvedValue(revision(mismatch));

      await expect(execute(value)).rejects.toThrow(
        "completed import revision",
      );

      expect(value.wasContentPutDispatched()).toBe(true);
      expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
    },
  );

  it("reconciles an ambiguous PUT through one exact GET without repeating PUT", async () => {
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("CREATED"));
    api.uploadArtifactContent.mockRejectedValue(new TypeError("connection lost"));
    api.getArtifactImport.mockResolvedValue(
      session("COMPLETED", { artifact_revision: revision() }),
    );

    await expect(execute(value)).resolves.toEqual(revision());

    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
    expect(api.getArtifactImport).toHaveBeenCalledWith(
      PROJECT_ID,
      IMPORT_ID,
      expect.any(AbortSignal),
    );
  });

  it("polls ambiguous PUT state CREATED without ever dispatching a second PUT", async () => {
    vi.useFakeTimers();
    const transport = new TypeError("connection lost");
    api.createArtifactImport.mockResolvedValue(session("CREATED"));
    api.uploadArtifactContent.mockRejectedValueOnce(transport);
    api.getArtifactImport
      .mockResolvedValueOnce(session("CREATED"))
      .mockResolvedValueOnce(
        session("COMPLETED", { artifact_revision: revision() }),
      );

    const result = execute(attempt());
    const completed = expect(result).resolves.toEqual(revision());
    await vi.advanceTimersByTimeAsync(250);

    await completed;
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
    expect(api.getArtifactImport).toHaveBeenCalledTimes(2);
  });

  it("settles stable FAILED_RETRYABLE observation and authorizes only a later explicit retry", async () => {
    const value = attempt();
    const transport = new TypeError("connection lost");
    api.createArtifactImport
      .mockResolvedValueOnce(session("CREATED"))
      .mockResolvedValueOnce(session("FAILED_RETRYABLE"));
    api.uploadArtifactContent
      .mockRejectedValueOnce(transport)
      .mockResolvedValueOnce(revision());
    api.getArtifactImport.mockResolvedValueOnce(
      session("FAILED_RETRYABLE", { failure_code: "upload_interrupted" }),
    );

    await expect(execute(value)).rejects.toMatchObject({
      failureCode: "upload_interrupted",
      retryable: true,
    });
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
    expect(api.getArtifactImport).toHaveBeenCalledTimes(1);

    await expect(execute(value)).resolves.toEqual(revision());
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(2);
  });

  it("observes a retry POST replay after an ambiguous PUT without uploading again", async () => {
    vi.useFakeTimers();
    const value = attempt();
    const transport = new TypeError("connection lost");
    api.createArtifactImport
      .mockResolvedValueOnce(session("CREATED"))
      .mockResolvedValueOnce(session("CREATED"));
    api.uploadArtifactContent.mockRejectedValueOnce(transport);
    api.getArtifactImport
      .mockRejectedValueOnce(new TypeError("observer offline"))
      .mockResolvedValueOnce(
        session("COMPLETED", { artifact_revision: revision() }),
      );

    await expect(execute(value)).rejects.toBe(transport);
    const retried = execute(value);
    await vi.advanceTimersByTimeAsync(250);

    await expect(retried).resolves.toEqual(revision());
    expect(api.createArtifactImport).toHaveBeenCalledTimes(2);
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
  });

  it("uses a retry replay FAILED_RETRYABLE only to authorize the next explicit retry", async () => {
    const value = attempt();
    const transport = new TypeError("connection lost");
    api.createArtifactImport
      .mockResolvedValueOnce(session("CREATED"))
      .mockResolvedValueOnce(session("FAILED_RETRYABLE"))
      .mockResolvedValueOnce(session("FAILED_RETRYABLE"));
    api.uploadArtifactContent
      .mockRejectedValueOnce(transport)
      .mockResolvedValueOnce(revision());
    api.getArtifactImport.mockRejectedValueOnce(
      new TypeError("observer offline"),
    );

    await expect(execute(value)).rejects.toBe(transport);
    await expect(execute(value)).rejects.toMatchObject({
      failureCode: "upload_interrupted",
      retryable: true,
    });
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);

    await expect(execute(value)).resolves.toEqual(revision());
    expect(api.createArtifactImport).toHaveBeenCalledTimes(3);
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["mismatched session", session("COMPLETED", { import_id: OTHER_ID })],
    ["mismatched revision", session("COMPLETED", { artifact_revision: revision({ byte_size: 4 }) })],
  ])("does not accept a %s after ambiguous PUT", async (_label, observed) => {
    const transport = new TypeError("connection lost");
    api.createArtifactImport.mockResolvedValue(session("CREATED"));
    api.uploadArtifactContent.mockRejectedValue(transport);
    api.getArtifactImport.mockResolvedValue(observed);

    await expect(execute(attempt())).rejects.toBe(transport);
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
  });

  it("does not accept observer failure as evidence after ambiguous PUT", async () => {
    const transport = new TypeError("connection lost");
    api.createArtifactImport.mockResolvedValue(session("CREATED"));
    api.uploadArtifactContent.mockRejectedValue(transport);
    api.getArtifactImport.mockRejectedValue(new TypeError("observer offline"));

    await expect(execute(attempt())).rejects.toBe(transport);
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
  });

  it("keeps a pending authoritative PUT alive across advisory failure at the 250ms interval", async () => {
    vi.useFakeTimers();
    let finishPut!: (value: ArtifactRevision) => void;
    api.createArtifactImport.mockResolvedValue(session("CREATED"));
    api.uploadArtifactContent.mockReturnValue(
      new Promise((resolve) => {
        finishPut = resolve;
      }),
    );
    api.getArtifactImport.mockRejectedValue(new TypeError("observer offline"));
    const value = attempt();

    const running = execute(value);
    await vi.advanceTimersByTimeAsync(249);
    expect(api.getArtifactImport).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(api.getArtifactImport).toHaveBeenCalledTimes(1);
    finishPut(revision());

    await expect(running).resolves.toEqual(revision());
    expect(api.uploadArtifactContent).toHaveBeenCalledTimes(1);
  });

  it("reports attempt state before POST, after POST, and after authoritative PUT dispatch", async () => {
    let finishPut!: (value: ArtifactRevision) => void;
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("CREATED"));
    api.uploadArtifactContent.mockReturnValue(
      new Promise((resolve) => {
        finishPut = resolve;
      }),
    );

    expect(value.getImportId()).toBeNull();
    expect(value.wasContentPutDispatched()).toBe(false);
    const running = execute(value);
    await vi.waitFor(() => expect(value.getImportId()).toBe(IMPORT_ID));
    expect(value.wasContentPutDispatched()).toBe(true);
    finishPut(revision());

    await expect(running).resolves.toEqual(revision());
  });

  it("stops consuming the File after releaseFile", async () => {
    const value = attempt();
    value.releaseFile();

    await expect(execute(value)).rejects.toThrow("file has been released");
    expect(api.createArtifactImport).not.toHaveBeenCalled();
  });

  it("copies and freezes declaration identity for the whole attempt", async () => {
    const mutable = { ...declaration };
    const value = attempt(mutable);
    mutable.filename = "mutated.png";
    mutable.expectedByteSize = 99;
    api.createArtifactImport.mockResolvedValue(session("COMPLETED"));

    await expect(execute(value)).resolves.toEqual(revision());

    expect(value.operationId).toBe("operation-1");
    expect(value.declaration).toEqual(declaration);
    expect(Object.isFrozen(value.declaration)).toBe(true);
    expect(api.createArtifactImport.mock.calls[0][1]).toEqual(declaration);
  });

  it("reconciles cancellation with an independent observer signal and the same validators", async () => {
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("COMPLETED"));
    await execute(value);
    api.getArtifactImport.mockResolvedValue(
      session("COMPLETED", { artifact_revision: revision() }),
    );
    const observer = new AbortController();

    await expect(
      value.reconcileCancellation({
        signal: observer.signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toEqual({ kind: "completed", revision: revision() });
    expect(api.getArtifactImport).toHaveBeenLastCalledWith(
      PROJECT_ID,
      IMPORT_ID,
      expect.any(AbortSignal),
    );
    expect(api.getArtifactImport.mock.calls.at(-1)?.[2]).not.toBe(
      observer.signal,
    );

    api.getArtifactImport.mockResolvedValueOnce(
      session("COMPLETED", { artifact_revision: revision({ byte_size: 4 }) }),
    );
    await expect(
      value.reconcileCancellation({
        signal: observer.signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toMatchObject({ kind: "unconfirmed", reason: "observer_failed" });

    api.getArtifactImport.mockResolvedValueOnce(
      session("COMPLETED", { client_import_id: OTHER_ID }),
    );
    await expect(
      value.reconcileCancellation({
        signal: observer.signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toMatchObject({ kind: "unconfirmed", reason: "observer_failed" });
  });

  it.each(revisionMismatches)(
    "rejects a cancellation COMPLETED revision with mismatched %s",
    async (_label, mismatch) => {
      const value = attempt();
      api.createArtifactImport.mockResolvedValue(session("COMPLETED"));
      await execute(value);
      api.getArtifactImport.mockResolvedValue(
        session("COMPLETED", {
          artifact_revision: revision(mismatch),
        }),
      );

      await expect(
        value.reconcileCancellation({
          signal: new AbortController().signal,
          maxPolls: 1,
          deadlineMs: 1_000,
        }),
      ).resolves.toMatchObject({
        kind: "unconfirmed",
        reason: "observer_failed",
      });
    },
  );

  it("aborts a never-settling observer GET at the one total deadline", async () => {
    vi.useFakeTimers();
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("COMPLETED"));
    await execute(value);
    const parent = new AbortController();
    const addListener = vi.spyOn(parent.signal, "addEventListener");
    const removeListener = vi.spyOn(parent.signal, "removeEventListener");
    let observerSignal: AbortSignal | null = null;
    api.getArtifactImport.mockImplementation(
      (_projectId, _importId, signal) => {
        observerSignal = signal;
        return new Promise<ArtifactImportSession>(() => undefined);
      },
    );

    const result = value.reconcileCancellation({
      signal: parent.signal,
      maxPolls: 40,
      deadlineMs: 1_000,
    });
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(result).resolves.toMatchObject({
      kind: "unconfirmed",
      reason: "deadline_exceeded",
    });
    expect(observerSignal).not.toBe(parent.signal);
    expect(observerSignal?.aborted).toBe(true);
    expect(addListener).toHaveBeenCalledTimes(1);
    expect(removeListener).toHaveBeenCalledTimes(1);
  });

  it("returns poll_budget_exhausted after the exact pending observation budget", async () => {
    vi.useFakeTimers();
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("COMPLETED"));
    await execute(value);
    api.getArtifactImport.mockResolvedValue(session("RECEIVING"));

    const result = value.reconcileCancellation({
      signal: new AbortController().signal,
      maxPolls: 3,
      deadlineMs: 10_000,
    });
    await vi.advanceTimersByTimeAsync(500);

    await expect(result).resolves.toMatchObject({
      kind: "unconfirmed",
      reason: "poll_budget_exhausted",
    });
    expect(api.getArtifactImport).toHaveBeenCalledTimes(3);
  });

  it("maps bound CREATED cancellation to content_not_dispatched before any PUT", async () => {
    const value = attempt();
    const controller = new AbortController();
    api.createArtifactImport.mockResolvedValue(session("RECEIVING"));
    const running = value.execute({
      signal: controller.signal,
      onPhase: vi.fn(),
    });
    await vi.waitFor(() => expect(value.getImportId()).toBe(IMPORT_ID));
    expect(value.wasContentPutDispatched()).toBe(false);
    controller.abort();
    await expect(running).rejects.toMatchObject({ name: "AbortError" });
    api.getArtifactImport.mockResolvedValue(session("CREATED"));

    await expect(
      value.reconcileCancellation({
        signal: new AbortController().signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toEqual({
      kind: "cancelled",
      serverState: "CREATED",
      reason: "content_not_dispatched",
    });
  });

  it("keeps CREATED pending during cancellation after an ambiguous PUT was dispatched", async () => {
    const value = attempt();
    const transport = new TypeError("connection lost");
    api.createArtifactImport.mockResolvedValue(session("CREATED"));
    api.uploadArtifactContent.mockRejectedValueOnce(transport);
    api.getArtifactImport
      .mockRejectedValueOnce(new TypeError("observer offline"))
      .mockResolvedValueOnce(session("CREATED"));
    await expect(execute(value)).rejects.toBe(transport);
    expect(value.wasContentPutDispatched()).toBe(true);

    await expect(
      value.reconcileCancellation({
        signal: new AbortController().signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toMatchObject({
      kind: "unconfirmed",
      reason: "poll_budget_exhausted",
    });
  });

  it.each([
    ["RECEIVING", { kind: "unconfirmed", reason: "poll_budget_exhausted" }],
    ["VALIDATING", { kind: "unconfirmed", reason: "poll_budget_exhausted" }],
    ["FAILED_RETRYABLE", { kind: "cancelled", serverState: "FAILED_RETRYABLE", reason: "failed_retryable" }],
    ["FAILED_FINAL", { kind: "cancelled", serverState: "FAILED_FINAL", reason: "failed_final" }],
  ] as const)("maps cancellation observer state %s to its public union", async (state, expected) => {
    const value = attempt();
    api.createArtifactImport.mockResolvedValue(session("COMPLETED"));
    await execute(value);
    api.getArtifactImport.mockResolvedValue(session(state));

    await expect(
      value.reconcileCancellation({
        signal: new AbortController().signal,
        maxPolls: 1,
        deadlineMs: 1_000,
      }),
    ).resolves.toMatchObject(expected);
  });

  it("follows upload_in_progress only through the exact import id", async () => {
    api.createArtifactImport.mockRejectedValue(
      new ArtifactImportConflictError(IMPORT_ID, "upload_in_progress"),
    );
    api.getArtifactImport.mockResolvedValue(
      session("COMPLETED", { artifact_revision: revision() }),
    );

    await expect(execute(attempt())).resolves.toEqual(revision());
    expect(api.uploadArtifactContent).not.toHaveBeenCalled();
    expect(api.getArtifactImport).toHaveBeenCalledWith(
      PROJECT_ID,
      IMPORT_ID,
      expect.any(AbortSignal),
    );
  });

  it("rejects upload_in_progress for a different id after this attempt is already bound", async () => {
    const value = attempt();
    api.createArtifactImport
      .mockResolvedValueOnce(session("CREATED"))
      .mockRejectedValueOnce(
        new ArtifactImportConflictError(OTHER_ID, "upload_in_progress"),
      );
    api.uploadArtifactContent.mockRejectedValueOnce(
      new ArtifactImportFailedError(IMPORT_ID, "upload_interrupted"),
    );
    await expect(execute(value)).rejects.toMatchObject({
      failureCode: "upload_interrupted",
      retryable: true,
    });
    expect(value.getImportId()).toBe(IMPORT_ID);

    await expect(execute(value)).rejects.toThrow("does not match");

    expect(value.getImportId()).toBe(IMPORT_ID);
    expect(api.getArtifactImport).not.toHaveBeenCalledWith(
      PROJECT_ID,
      OTHER_ID,
      expect.any(AbortSignal),
    );
  });
});
