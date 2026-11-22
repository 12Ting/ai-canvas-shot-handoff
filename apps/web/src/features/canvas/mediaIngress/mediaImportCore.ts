import {
  abortLargeUpload,
  ArtifactImportConflictError,
  ArtifactImportFailedError,
  createArtifactImport,
  createLargeUpload,
  discardArtifactImportResult,
  completeLargeUpload,
  getArtifactImport,
  getLargeUpload,
  putLargeUploadPart,
  uploadArtifactContent,
  type ArtifactImportDeclaration,
  type DiscardArtifactImportResult,
  type ArtifactImportSession,
  type ArtifactRevision,
  type LargeUploadDeclaration,
  type LargeUploadSession,
} from "../../../api/client";

const B1_MAX_BYTES = 64 * 1024 * 1024;
const LARGE_UPLOAD_PART_BYTES = 8 * 1024 * 1024;

export type MediaImportNetworkPhase =
  "initializing" | "receiving" | "validating";

export type LargeUploadProgressUpdate = Readonly<{
  /** Bytes acknowledged by the server, never optimistic client-side bytes. */
  receivedByteSize: number;
  totalByteSize: number;
}>;

export type CancelReconciliation =
  | Readonly<{ kind: "completed"; revision: ArtifactRevision }>
  | Readonly<{
      kind: "cancelled";
      serverState: "CREATED" | "ABORTED" | "EXPIRED" | "FAILED";
      reason:
        | "content_not_dispatched"
        | "aborted"
        | "expired"
        | "failed";
    }>
  | Readonly<{
      kind: "cancelled";
      serverState: "FAILED_RETRYABLE";
      reason: "failed_retryable";
    }>
  | Readonly<{
      kind: "cancelled";
      serverState: "FAILED_FINAL";
      reason: "failed_final";
    }>
  | Readonly<{
      kind: "pending";
      serverState: "CREATED" | "RECEIVING" | "VALIDATING" | "FINALIZING";
    }>
  | Readonly<{
      kind: "unconfirmed";
      reason: "deadline_exceeded" | "poll_budget_exhausted" | "observer_failed";
      errorMessage: string;
    }>;

export type CreateMediaImportAttemptInput = Readonly<{
  operationId: string;
  projectId: string;
  declaration: Readonly<ArtifactImportDeclaration | LargeUploadDeclaration>;
  file: File;
}>;
export type DiscardMediaImportInput = Readonly<{
  projectId: string;
  importId: string;
  clientImportId: string;
  expectedRevisionId: string | null;
  isLargeUpload?: boolean;
  signal: AbortSignal;
}>;

export interface MediaImportAttempt {
  readonly operationId: string;
  readonly declaration: Readonly<ArtifactImportDeclaration | LargeUploadDeclaration>;
  getImportId(): string | null;
  wasCreateDispatched(): boolean;
  wasContentPutDispatched(): boolean;
  recoverImportId(
    input: Readonly<{ signal: AbortSignal }>,
  ): Promise<string>;
  execute(
    input: Readonly<{
      signal: AbortSignal;
      onPhase(phase: MediaImportNetworkPhase): void;
      onProgress?(progress: LargeUploadProgressUpdate): void;
    }>,
  ): Promise<ArtifactRevision>;
  reconcileCancellation(
    input: Readonly<{
      signal: AbortSignal;
      maxPolls: number;
      deadlineMs: number;
    }>,
  ): Promise<CancelReconciliation>;
  releaseFile(): void;
}

export interface MediaImportCore {
  createAttempt(input: CreateMediaImportAttemptInput): MediaImportAttempt;
  discardImport(input: DiscardMediaImportInput): Promise<DiscardArtifactImportResult>;
}

const POLL_MILLISECONDS = 250;

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, POLL_MILLISECONDS);
    if (signal.aborted) onAbort();
    else signal.addEventListener("abort", onAbort, { once: true });
  });
}

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "media import observer failed";
}

class Attempt implements MediaImportAttempt {
  readonly operationId: string;
  readonly declaration: Readonly<ArtifactImportDeclaration>;
  private readonly projectId: string;
  private file: File | null;
  private importId: string | null = null;
  private createDispatched = false;
  private contentPutDispatched = false;
  private contentPutRetryAllowed = false;

  constructor(
    input: CreateMediaImportAttemptInput & {
      declaration: Readonly<ArtifactImportDeclaration>;
    },
  ) {
    this.operationId = input.operationId;
    this.projectId = input.projectId;
    this.declaration = Object.freeze({ ...input.declaration });
    this.file = input.file;
  }

  getImportId(): string | null {
    return this.importId;
  }
  wasCreateDispatched(): boolean {
    return this.createDispatched;
  }
  wasContentPutDispatched(): boolean {
    return this.contentPutDispatched;
  }
  releaseFile(): void {
    this.file = null;
  }

  private matches(session: ArtifactImportSession): boolean {
    return (
      session.source_project_id === this.projectId &&
      session.client_import_id === this.declaration.clientImportId &&
      (this.importId === null || session.import_id === this.importId) &&
      session.original_filename === this.declaration.filename &&
      session.expected_byte_size === this.declaration.expectedByteSize &&
      session.requested_media_kind === this.declaration.requestedMediaKind &&
      (session.declared_content_type ?? null) ===
        this.declaration.declaredContentType &&
      (session.replace_revision_id ?? null) ===
        this.declaration.replaceRevisionId
    );
  }

  private completed(revision: ArtifactRevision): ArtifactRevision {
    if (
      revision.media_kind !== this.declaration.requestedMediaKind ||
      revision.source_project_id !== this.projectId ||
      revision.source_kind !== "import" ||
      revision.byte_size !== this.declaration.expectedByteSize
    ) {
      throw new Error(
        "completed import revision does not match the active declaration",
      );
    }
    return revision;
  }

  async execute({
    signal,
    onPhase,
    onProgress: _onProgress,
  }: Readonly<{
    signal: AbortSignal;
    onPhase(phase: MediaImportNetworkPhase): void;
    onProgress?(progress: LargeUploadProgressUpdate): void;
  }>): Promise<ArtifactRevision> {
    if (!this.file) throw new Error("media import file has been released");
    onPhase("initializing");
    this.createDispatched = true;
    try {
      return await this.advance(
        await createArtifactImport(this.projectId, this.declaration, signal),
        signal,
        onPhase,
        this.contentPutDispatched ? "retry" : "initial",
      );
    } catch (error) {
      if (
        error instanceof ArtifactImportConflictError &&
        error.reason === "upload_in_progress"
      ) {
        if (this.importId !== null && this.importId !== error.importId)
          throw new Error(
            "upload_in_progress import id does not match the active attempt",
          );
        this.importId = error.importId;
        return this.advance(
          await getArtifactImport(this.projectId, error.importId, signal),
          signal,
          onPhase,
          "observe",
        );
      }
      throw error;
    }
  }

  async recoverImportId({
    signal,
  }: Readonly<{ signal: AbortSignal }>): Promise<string> {
    this.createDispatched = true;
    try {
      const session = await createArtifactImport(
        this.projectId,
        this.declaration,
        signal,
      );
      if (!this.matches(session)) {
        throw new Error(
          "import session does not match the active declaration",
        );
      }
      this.importId = session.import_id;
      return session.import_id;
    } catch (error) {
      if (
        error instanceof ArtifactImportConflictError &&
        error.reason === "upload_in_progress"
      ) {
        if (this.importId !== null && this.importId !== error.importId) {
          throw new Error(
            "upload_in_progress import id does not match the active attempt",
          );
        }
        this.importId = error.importId;
        return error.importId;
      }
      throw error;
    }
  }

  private async advance(
    session: ArtifactImportSession,
    signal: AbortSignal,
    onPhase: (phase: MediaImportNetworkPhase) => void,
    mode: "initial" | "retry" | "observe",
  ): Promise<ArtifactRevision> {
    let current = session;
    while (true) {
      if (!this.matches(current))
        throw new Error("import session does not match the active declaration");
      this.importId = current.import_id;
      switch (current.state) {
        case "COMPLETED":
          if (!current.artifact_revision)
            throw new Error("completed import is missing its revision");
          return this.completed(current.artifact_revision);
        case "FAILED_FINAL":
          throw new ArtifactImportFailedError(
            current.import_id,
            current.failure_code ?? "incompatible_declaration",
          );
        case "CREATED":
          if (
            mode === "initial" ||
            (mode === "retry" && this.contentPutRetryAllowed)
          ) {
            this.contentPutRetryAllowed = false;
            return this.put(current.import_id, signal, onPhase);
          }
          break;
        case "FAILED_RETRYABLE":
          if (
            mode === "initial" ||
            (mode === "retry" && this.contentPutRetryAllowed)
          ) {
            this.contentPutRetryAllowed = false;
            return this.put(current.import_id, signal, onPhase);
          }
          this.contentPutRetryAllowed = true;
          throw new ArtifactImportFailedError(
            current.import_id,
            current.failure_code ?? "upload_interrupted",
          );
        case "RECEIVING":
          onPhase("receiving");
          break;
        case "VALIDATING":
          onPhase("validating");
          break;
      }
      await waitForPoll(signal);
      current = await getArtifactImport(
        this.projectId,
        current.import_id,
        signal,
      );
    }
  }

  private async put(
    importId: string,
    signal: AbortSignal,
    onPhase: (phase: MediaImportNetworkPhase) => void,
  ): Promise<ArtifactRevision> {
    const file = this.file;
    if (!file) throw new Error("media import file has been released");
    onPhase("receiving");
    this.contentPutDispatched = true;
    const observer = new AbortController();
    const abortObserver = () => observer.abort();
    if (signal.aborted) abortObserver();
    else signal.addEventListener("abort", abortObserver, { once: true });
    void this.observePendingPut(importId, observer.signal, onPhase).catch(
      () => undefined,
    );
    try {
      return this.completed(
        await uploadArtifactContent(this.projectId, importId, file, signal),
      );
    } catch (error) {
      observer.abort();
      if (error instanceof ArtifactImportFailedError) {
        this.contentPutRetryAllowed = error.retryable;
        throw error;
      }
      if (
        error instanceof ArtifactImportConflictError &&
        error.reason === "upload_in_progress"
      ) {
        return this.advance(
          await getArtifactImport(this.projectId, importId, signal),
          signal,
          onPhase,
          "observe",
        );
      }
      if (error instanceof ArtifactImportConflictError) throw error;
      // A transport failure can be ambiguous; observe this exact session but never repeat PUT here.
      try {
        return await this.advance(
          await getArtifactImport(this.projectId, importId, signal),
          signal,
          onPhase,
          "observe",
        );
      } catch (observerError) {
        if (observerError instanceof ArtifactImportFailedError)
          throw observerError;
        throw error;
      }
    } finally {
      signal.removeEventListener("abort", abortObserver);
      observer.abort();
    }
  }

  private async observePendingPut(
    importId: string,
    signal: AbortSignal,
    onPhase: (phase: MediaImportNetworkPhase) => void,
  ): Promise<void> {
    while (!signal.aborted) {
      await waitForPoll(signal);
      const current = await getArtifactImport(this.projectId, importId, signal);
      if (!this.matches(current)) return;
      if (current.state === "RECEIVING") onPhase("receiving");
      if (current.state === "VALIDATING") onPhase("validating");
      if (current.state === "COMPLETED" || current.state === "FAILED_FINAL")
        return;
    }
  }

  async reconcileCancellation({
    signal,
    maxPolls,
    deadlineMs,
  }: Readonly<{
    signal: AbortSignal;
    maxPolls: number;
    deadlineMs: number;
  }>): Promise<CancelReconciliation> {
    if (!this.importId)
      return {
        kind: "unconfirmed",
        reason: "observer_failed",
        errorMessage: "import id is unavailable",
      };
    const observer = new AbortController();
    let active = true;
    let deadlineExceeded = false;
    let rejectBoundary!: (reason: unknown) => void;
    const boundary = new Promise<never>((_resolve, reject) => {
      rejectBoundary = reject;
    });
    const abortObserver = () => {
      if (!active) return;
      observer.abort();
      rejectBoundary(abortError());
    };
    if (signal.aborted) abortObserver();
    else signal.addEventListener("abort", abortObserver, { once: true });
    const deadlineTimer = setTimeout(() => {
      if (!active) return;
      deadlineExceeded = true;
      observer.abort();
      rejectBoundary(
        new Error("cancellation reconciliation deadline exceeded"),
      );
    }, Math.max(0, deadlineMs));
    const bounded = <T>(operation: Promise<T>): Promise<T> =>
      Promise.race([operation, boundary]);
    let requests = 0;
    try {
      while (requests < maxPolls) {
        const current = await bounded(
          getArtifactImport(
            this.projectId,
            this.importId,
            observer.signal,
          ),
        );
        requests += 1;
        if (!this.matches(current))
          return {
            kind: "unconfirmed",
            reason: "observer_failed",
            errorMessage:
              "import session does not match the active declaration",
          };
        if (current.state === "COMPLETED") {
          if (!current.artifact_revision)
            return {
              kind: "unconfirmed",
              reason: "observer_failed",
              errorMessage: "completed import is missing its revision",
            };
          return {
            kind: "completed",
            revision: this.completed(current.artifact_revision),
          };
        }
        if (current.state === "FAILED_FINAL")
          return {
            kind: "cancelled",
            serverState: "FAILED_FINAL",
            reason: "failed_final",
          };
        if (current.state === "FAILED_RETRYABLE")
          return {
            kind: "cancelled",
            serverState: "FAILED_RETRYABLE",
            reason: "failed_retryable",
          };
        if (current.state === "CREATED") {
          if (!this.contentPutDispatched)
            return {
              kind: "cancelled",
              serverState: "CREATED",
              reason: "content_not_dispatched",
            };
        }
        if (requests < maxPolls)
          await bounded(waitForPoll(observer.signal));
      }
      return {
        kind: "unconfirmed",
        reason: "poll_budget_exhausted",
        errorMessage: "cancellation reconciliation poll budget exhausted",
      };
    } catch (error) {
      return deadlineExceeded
        ? {
            kind: "unconfirmed",
            reason: "deadline_exceeded",
            errorMessage: "cancellation reconciliation deadline exceeded",
          }
        : {
            kind: "unconfirmed",
            reason: "observer_failed",
            errorMessage: message(error),
          };
    } finally {
      active = false;
      clearTimeout(deadlineTimer);
      signal.removeEventListener("abort", abortObserver);
      observer.abort();
    }
  }
}

class LargeAttempt implements MediaImportAttempt {
  readonly operationId: string;
  readonly declaration: Readonly<LargeUploadDeclaration>;
  private readonly projectId: string;
  private file: File | null;
  private uploadId: string | null = null;
  private nextPartNumber = 0;
  private createDispatched = false;
  private contentPutDispatched = false;

  constructor(input: CreateMediaImportAttemptInput & { declaration: Readonly<LargeUploadDeclaration> }) {
    this.operationId = input.operationId;
    this.projectId = input.projectId;
    this.declaration = Object.freeze({ ...input.declaration });
    this.file = input.file;
  }

  getImportId(): string | null {
    return this.uploadId;
  }
  wasCreateDispatched(): boolean {
    return this.createDispatched;
  }
  wasContentPutDispatched(): boolean {
    return this.contentPutDispatched;
  }
  releaseFile(): void {
    this.file = null;
  }

  private matches(session: LargeUploadSession): boolean {
    return (
      session.source_project_id === this.projectId &&
      session.client_upload_id === this.declaration.clientImportId &&
      session.original_filename === this.declaration.filename &&
      session.expected_byte_size === this.declaration.expectedByteSize &&
      session.requested_media_kind === this.declaration.requestedMediaKind
    );
  }

  private completed(session: LargeUploadSession): ArtifactRevision {
    const revision = session.artifact_revision;
    if (
      session.state !== "COMPLETED" ||
      !revision ||
      revision.media_kind !== this.declaration.requestedMediaKind ||
      revision.source_project_id !== this.projectId ||
      revision.byte_size !== this.declaration.expectedByteSize
    ) {
      throw new Error("completed large upload does not match the active declaration");
    }
    return revision;
  }

  async recoverImportId({ signal }: Readonly<{ signal: AbortSignal }>): Promise<string> {
    const session = await this.create(signal);
    return session.upload_id;
  }

  async execute({
    signal,
    onPhase,
    onProgress,
  }: Readonly<{
    signal: AbortSignal;
    onPhase(phase: MediaImportNetworkPhase): void;
    onProgress?(progress: LargeUploadProgressUpdate): void;
  }>): Promise<ArtifactRevision> {
    const file = this.file;
    if (!file) throw new Error("media import file has been released");
    onPhase("initializing");
    const session = await this.create(signal);
    if (session.state === "COMPLETED") return this.completed(session);
    if (session.state !== "CREATED" && session.state !== "RECEIVING") {
      throw new Error("large upload session is not ready to receive parts");
    }
    if (session.part_size !== LARGE_UPLOAD_PART_BYTES) {
      throw new Error("large upload session has an unexpected part size");
    }
    onProgress?.({
      receivedByteSize: session.received_byte_size,
      totalByteSize: file.size,
    });
    onPhase("receiving");
    this.nextPartNumber = Math.max(
      this.nextPartNumber,
      Math.floor(session.received_byte_size / LARGE_UPLOAD_PART_BYTES),
    );
    for (
      let start = this.nextPartNumber * LARGE_UPLOAD_PART_BYTES;
      start < file.size;
      start += LARGE_UPLOAD_PART_BYTES
    ) {
      const partNumber = start / LARGE_UPLOAD_PART_BYTES;
      const part = file.slice(start, Math.min(start + LARGE_UPLOAD_PART_BYTES, file.size));
      this.contentPutDispatched = true;
      const receipt = await putLargeUploadPart(
        session.upload_id,
        partNumber,
        part,
        `bytes ${start}-${start + part.size - 1}/${file.size}`,
        await sha256Hex(part),
        signal,
      );
      onProgress?.({
        receivedByteSize: receipt.receivedByteSize,
        totalByteSize: file.size,
      });
      this.nextPartNumber = partNumber + 1;
    }
    onPhase("validating");
    return this.completed(await completeLargeUpload(session.upload_id, signal));
  }

  async reconcileCancellation({
    signal,
    maxPolls,
    deadlineMs,
  }: Readonly<{
    signal: AbortSignal;
    maxPolls: number;
    deadlineMs: number;
  }>): Promise<CancelReconciliation> {
    try {
      // A browser can lose the response after the idempotent create POST has
      // reached the server. Replay that create with the same client import id
      // before declaring cancellation local-only, so its capacity reservation
      // is recovered and explicitly released.
      let uploadId = this.uploadId;
      if (uploadId === null) uploadId = (await this.create(signal)).upload_id;
      const deadline = Date.now() + deadlineMs;
      let polls = 1;
      let session = await abortLargeUpload(uploadId, signal);
      while (true) {
        if (!this.matches(session)) {
          throw new Error("large upload cancellation response does not match the active declaration");
        }
        if (session.state === "ABORTED") {
          return { kind: "cancelled", serverState: "ABORTED", reason: "aborted" };
        }
        if (session.state === "EXPIRED") {
          return { kind: "cancelled", serverState: "EXPIRED", reason: "expired" };
        }
        if (session.state === "FAILED") {
          return { kind: "cancelled", serverState: "FAILED", reason: "failed" };
        }
        if (session.state === "COMPLETED") {
          return { kind: "completed", revision: this.completed(session) };
        }
        if (session.state !== "CREATED" && session.state !== "RECEIVING" && session.state !== "FINALIZING") {
          throw new Error("large upload cancellation state is invalid");
        }
        if (polls >= maxPolls || Date.now() >= deadline) {
          return { kind: "pending", serverState: session.state };
        }
        await waitForPoll(signal);
        polls += 1;
        session = await getLargeUpload(uploadId, signal);
      }
    } catch (error) {
      return {
        kind: "unconfirmed",
        reason: "observer_failed",
        errorMessage: message(error),
      };
    }
  }

  private async create(signal: AbortSignal): Promise<LargeUploadSession> {
    this.createDispatched = true;
    const session = await createLargeUpload(this.projectId, this.declaration, signal);
    if (!this.matches(session)) throw new Error("large upload session does not match the active declaration");
    this.uploadId = session.upload_id;
    return session;
  }
}

async function sha256Hex(content: Blob): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("Web Crypto is unavailable for large upload integrity checks");
  const digest = await subtle.digest("SHA-256", await content.arrayBuffer());
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

export function createMediaImportCore(): MediaImportCore {
  return {
    createAttempt: (input) =>
      input.declaration.expectedByteSize > B1_MAX_BYTES
        ? new LargeAttempt(input as CreateMediaImportAttemptInput & {
            declaration: Readonly<LargeUploadDeclaration>;
          })
        : new Attempt(input as CreateMediaImportAttemptInput & {
            declaration: Readonly<ArtifactImportDeclaration>;
          }),
    discardImport: async ({ isLargeUpload = false, signal, ...input }) => {
      if (!isLargeUpload) return discardArtifactImportResult(input, signal);
      const session = await abortLargeUpload(input.importId, signal);
      if (session.client_upload_id !== input.clientImportId) {
        throw new Error("large upload discard response does not match the active declaration");
      }
      if (session.state === "FINALIZING" || session.state === "CREATED" || session.state === "RECEIVING") {
        throw new Error("large upload cancellation is still pending");
      }
      return {
        httpStatus: 200,
        outcome: "discarded",
        // Completed uploads are logically discarded and remain recoverable via
        // the explicit B4 GC/quarantine flow; unfinished uploads are removed.
        contentCleanup: session.state === "COMPLETED" ? "not_applicable" : "removed",
      };
    },
  };
}
