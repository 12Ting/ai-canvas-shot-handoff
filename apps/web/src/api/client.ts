import type { components } from "./generated-schema";

type SessionResponse = components["schemas"]["SessionResponse"];
type SessionStatusResponse = components["schemas"]["SessionStatusResponse"];
export type SessionMode = "session" | "development-auto-session";
export type ProjectResponse = components["schemas"]["ProjectResponse"] & { handoff_enabled?: boolean };
type GeneratedGraphResponse = components["schemas"]["GraphRevisionResponse"];
type GeneratedGraphSnapshotV1 = components["schemas"]["GraphSnapshotV1"];
type GeneratedGraphSnapshotV2 = components["schemas"]["GraphSnapshotV2"];
type GeneratedGraphSnapshotV3 = components["schemas"]["GraphSnapshotV3"];
export type ArtifactImportSession = components["schemas"]["ArtifactImportSessionResponse"];
export type ArtifactRevision = components["schemas"]["ArtifactRevisionResponse"];
export type LargeUploadSession = components["schemas"]["LargeUploadSessionResponse"];
export type ExternalRootGrant = components["schemas"]["ExternalRootGrantResponse"];
export type ExternalReference = components["schemas"]["ExternalReferenceResponse"];
export type ManagedCopy = components["schemas"]["ManagedCopyResponse"];
export type RenditionJob = components["schemas"]["RenditionJobResponse"];
export type StorageSummary = components["schemas"]["StorageSummaryResponse"];
export type GcPlan = components["schemas"]["GcPlanResponse"];
export type QuarantineEntry = components["schemas"]["QuarantineEntryResponse"];
export type ShotSlotAssociationHistoryItem =
  components["schemas"]["ShotSlotAssociationHistoryItem"];
export type ShotSlotAssociationHistoryResponse =
  components["schemas"]["ShotSlotAssociationHistoryResponse"];
export type ShotSlot = ShotSlotAssociationHistoryResponse["slot"];
type GeneratedDiscardArtifactImportResultRequest =
  components["schemas"]["DiscardArtifactImportResultRequest"];
type GeneratedDiscardArtifactImportResultResponse =
  components["schemas"]["DiscardArtifactImportResultResponse"];
export type ArtifactImportFailureCode =
  components["schemas"]["ArtifactImportFailureCode"];
export type ArtifactImportConflictReason =
  components["schemas"]["ArtifactImportConflictReason"];
export type MediaKind = "image" | "video";
export type ArtifactImportDeclaration = {
  clientImportId: string;
  filename: string;
  expectedByteSize: number;
  requestedMediaKind: MediaKind;
  declaredContentType: string | null;
  replaceRevisionId: string | null;
};
export type LargeUploadDeclaration = {
  /**
   * One client operation id is shared by the ingress queue and both upload
   * protocols.  The wire contract calls this `client_upload_id` for B4.
   */
  clientImportId: string;
  filename: string;
  expectedByteSize: number;
  requestedMediaKind: MediaKind;
  declaredContentType: string | null;
  replaceRevisionId: string | null;
};
export type LargeUploadPartReceipt = Readonly<{
  uploadId: string;
  partNumber: number;
  receivedByteSize: number;
}>;
export type DiscardArtifactImportResultInput = Readonly<{
  projectId: string;
  importId: string;
  clientImportId: string;
  expectedRevisionId: string | null;
}>;
export type DiscardArtifactImportResult = Readonly<{
  httpStatus: 200 | 202;
  outcome: GeneratedDiscardArtifactImportResultResponse["outcome"];
  contentCleanup: GeneratedDiscardArtifactImportResultResponse["content_cleanup"];
}>;
export type GraphSnapshotV1Body = Omit<GeneratedGraphSnapshotV1, "schema_version"> & {
  schema_version: 1;
};
export type GraphSnapshotV2Body = Omit<GeneratedGraphSnapshotV2, "schema_version"> & {
  schema_version: 2;
};
export type GraphSnapshotV3Body = Omit<GeneratedGraphSnapshotV3, "schema_version"> & {
  schema_version: 3;
};
export type GraphSnapshotBody = GraphSnapshotV1Body | GraphSnapshotV2Body | GraphSnapshotV3Body;
export type GraphResponse = Omit<GeneratedGraphResponse, "snapshot"> & {
  snapshot: GraphSnapshotBody;
};
export type GraphChangeSetResponse = components["schemas"]["GraphChangeSetResponse"];
export type BranchWorkspaceResponse = components["schemas"]["BranchWorkspaceResponse"];
export type BranchGraphResponse = components["schemas"]["BranchGraphResponse"];
export type BranchApplicationTarget =
  | components["schemas"]["DirectApplicationTarget"]
  | components["schemas"]["NewBranchApplicationTarget"];
export type PromotionResponse = components["schemas"]["PromotionResponse"];
export type ControlConfigRevisionResponse = components["schemas"]["ControlConfigRevisionResponse"];
export type ConfigChangeSetResponse = components["schemas"]["ConfigChangeSetResponse"];
export type ModelProfileResponse = components["schemas"]["ModelProfileResponse"];
export type ModelProfileRevisionResponse = components["schemas"]["ModelProfileRevisionResponse"];
export type ModelProfileRevisionInput = components["schemas"]["ModelProfileRevisionInput"];
export type AgentProposalResponse = components["schemas"]["AgentProposalResponse"];
export type CreateAgentProposalRequest = components["schemas"]["CreateAgentProposalRequest"];
export type PromptBatchResponse = components["schemas"]["PromptBatchResponse"];
export type PromptBatchPreflightResponse = components["schemas"]["PromptBatchPreflightResponse"];
export type CreatePromptBatchRequest = components["schemas"]["CreatePromptBatchRequest"];
export type PromptBatchItemDecisionRequest = components["schemas"]["PromptBatchItemDecisionRequest"];
export type PromptBatchRetryRequest = components["schemas"]["PromptBatchRetryRequest"];
export type PromptProfileExtractionResponse = components["schemas"]["PromptProfileExtractionResponse"];
export type CreatePromptProfileExtractionRequest = components["schemas"]["CreatePromptProfileExtractionRequest"];
export type MaterializePromptProfileRequest = components["schemas"]["MaterializePromptProfileRequest"];
export type RunRequestResponse = components["schemas"]["RunRequestResponse"];
export type EgressPlanResponse = components["schemas"]["EgressPlanResponse"];
export type RunAuthorizationResponse = components["schemas"]["RunAuthorizationResponse"];
export type DryRunReceiptResponse = components["schemas"]["DryRunReceiptResponse"];
export type ExperienceEventPage = components["schemas"]["ExperienceEventPage"];
export type ExperienceEventResponse = components["schemas"]["ExperienceEventResponse"];
export type EventCursorResponse = components["schemas"]["EventCursorResponse"];
export type ResourceResponse = components["schemas"]["ResourceResponse"];
export type ResourceDetailResponse = components["schemas"]["ResourceDetailResponse"];
export type ResourceRevisionResponse = components["schemas"]["ResourceRevisionResponse"];
export type ResourceUpdateItem = components["schemas"]["ResourceUpdateItem"];
export type CreateResourceRequestBody = components["schemas"]["CreateResourceRequest"];
export type CreateResourceRevisionRequestBody =
  components["schemas"]["CreateResourceRevisionRequest"];
type ViewportState = components["schemas"]["ViewportState"];
type GraphNodeBody = components["schemas"]["GraphNode"];
type GraphNodeV2Body = NonNullable<GeneratedGraphSnapshotV2["nodes"]>[number];
type GraphNodeV3Body = NonNullable<GeneratedGraphSnapshotV3["nodes"]>[number];
type GraphEdgeBody = components["schemas"]["GraphEdge"];
type ShotBody = components["schemas"]["Shot"];
type PromptDocumentBody = components["schemas"]["PromptDocumentV1"];
type ResourceBindingBody = components["schemas"]["ResourceBinding"];
type PromptBlockBody = components["schemas"]["PromptBlock"];
type PromptSpanBody = components["schemas"]["PromptSpan"];
type BootstrapFragment =
  | { present: false; token: null }
  | { present: true; token: string };
type SessionChannelMessage =
  | { type: "session-rotated"; token: string }
  | { type: "request"; requestId: string }
  | { type: "response"; requestId: string; token: string };
type AttestedSession = { token: string; mode: SessionMode };
type SessionAttestation = {
  epoch: number;
  result: Promise<AttestedSession>;
  supersede(replacement: SessionAttestation | null): void;
};
type SessionCandidate = (
  attestation: SessionAttestation,
) => Promise<AttestedSession>;

const STORAGE_KEY = "ai-canvas-session";
const CHANNEL_NAME = "ai-canvas-session-handoff";
let bootstrapInFlight: Promise<SessionMode> | null = null;
let sessionChannel: BroadcastChannel | null | undefined;
let attestationEpoch = 0;
let currentAttestation: SessionAttestation | null = null;

export class ApiRequestError extends Error {
  readonly status: number;
  readonly retryable: boolean;

  constructor(
    status: number,
    message = `API request failed (${status})`,
    retryable = status === 408 || status === 429 || status >= 500,
  ) {
    super(message);
    this.status = status;
    this.retryable = retryable;
  }
}

export class GraphChangeSetConflictError extends ApiRequestError {
  readonly currentRevisionId: string;

  constructor(currentRevisionId: string) {
    super(409, "graph_base_revision_stale", false);
    this.name = "GraphChangeSetConflictError";
    this.currentRevisionId = currentRevisionId;
  }
}

export class C2ApiError extends ApiRequestError {
  readonly code: string;
  readonly problem: Readonly<Record<string, unknown>>;

  constructor(status: number, code: string, problem: Record<string, unknown>) {
    super(status, code, false);
    this.name = "C2ApiError";
    this.code = code;
    this.problem = problem;
  }
}

export class BranchHeadStaleError extends C2ApiError {
  readonly branchId: string;
  readonly currentRevisionId: string;
  readonly currentVersion: number;

  constructor(
    branchId: string,
    currentRevisionId: string,
    currentVersion: number,
    problem: Record<string, unknown>,
  ) {
    super(409, "branch_head_stale", problem);
    this.name = "BranchHeadStaleError";
    this.branchId = branchId;
    this.currentRevisionId = currentRevisionId;
    this.currentVersion = currentVersion;
  }
}

export class SelectionPointerStaleError extends C2ApiError {
  readonly currentBranchId: string;
  readonly currentRevisionId: string;
  readonly currentVersion: number;

  constructor(
    currentBranchId: string,
    currentRevisionId: string,
    currentVersion: number,
    problem: Record<string, unknown>,
  ) {
    super(409, "selection_pointer_stale", problem);
    this.name = "SelectionPointerStaleError";
    this.currentBranchId = currentBranchId;
    this.currentRevisionId = currentRevisionId;
    this.currentVersion = currentVersion;
  }
}

export class SessionExpiredError extends ApiRequestError {
  constructor() {
    super(401, "本地会话已失效，请从 AI Canvas 启动器重新打开。", false);
  }
}

export class ArtifactImportConflictError extends ApiRequestError {
  readonly importId: string;
  readonly reason: ArtifactImportConflictReason;

  constructor(importId: string, reason: ArtifactImportConflictReason) {
    super(
      409,
      `Artifact import conflict: ${reason}`,
      reason === "upload_in_progress",
    );
    this.importId = importId;
    this.reason = reason;
  }
}

const retryableArtifactFailureCodes: ReadonlySet<ArtifactImportFailureCode> = new Set([
  "upload_interrupted",
  "probe_timeout",
  "probe_unavailable",
]);

export class ArtifactImportFailedError extends ApiRequestError {
  readonly importId: string;
  readonly failureCode: ArtifactImportFailureCode;

  constructor(importId: string, failureCode: ArtifactImportFailureCode) {
    super(
      422,
      `Artifact import failed: ${failureCode}`,
      retryableArtifactFailureCodes.has(failureCode),
    );
    this.importId = importId;
    this.failureCode = failureCode;
  }
}

export class ArtifactDeclarationValidationError extends TypeError {
  constructor(message: string) {
    super(message);
    this.name = "ArtifactDeclarationValidationError";
  }
}

export function getSessionToken(): string | null {
  return sessionStorage.getItem(STORAGE_KEY);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isSessionResponse(value: unknown): value is SessionResponse {
  return isRecord(value) && isNonEmptyString(value.access_token);
}

function isSessionMode(value: unknown): value is SessionMode {
  return value === "session" || value === "development-auto-session";
}

function isSessionStatusResponse(value: unknown): value is SessionStatusResponse {
  return isRecord(value) && isSessionMode(value.mode);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function readPlainDataRecord(
  value: unknown,
  required: readonly string[],
  optional: readonly string[] = [],
): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null) return null;
  let prototype: object | null;
  try {
    if (Array.isArray(value)) return null;
    prototype = Object.getPrototypeOf(value);
  } catch {
    return null;
  }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const allowed = new Set([...required, ...optional]);
  let keys: PropertyKey[];
  try {
    keys = Reflect.ownKeys(value);
  } catch {
    return null;
  }
  if (
    keys.some((key) => typeof key !== "string" || !allowed.has(key)) ||
    required.some((key) => !keys.includes(key))
  ) {
    return null;
  }
  const fields: Record<string, unknown> = {};
  for (const key of keys) {
    if (typeof key !== "string") return null;
    let descriptor: PropertyDescriptor | undefined;
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
    } catch {
      return null;
    }
    if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) return null;
    fields[key] = descriptor.value;
  }
  return fields;
}

function readDenseArray(value: unknown, maxLength: number): unknown[] | null {
  let prototype: object | null;
  try {
    if (!Array.isArray(value)) return null;
    prototype = Object.getPrototypeOf(value);
  } catch {
    return null;
  }
  if (prototype !== Array.prototype) return null;
  let keys: PropertyKey[];
  let lengthDescriptor: PropertyDescriptor | undefined;
  try {
    keys = Reflect.ownKeys(value);
    lengthDescriptor = Object.getOwnPropertyDescriptor(value, "length");
  } catch {
    return null;
  }
  if (
    !lengthDescriptor ||
    !("value" in lengthDescriptor) ||
    !Number.isSafeInteger(lengthDescriptor.value) ||
    lengthDescriptor.value < 0 ||
    lengthDescriptor.value > maxLength ||
    keys.length !== lengthDescriptor.value + 1
  ) {
    return null;
  }
  const items = new Array<unknown>(lengthDescriptor.value);
  for (let index = 0; index < lengthDescriptor.value; index += 1) {
    const key = `${index}`;
    let descriptor: PropertyDescriptor | undefined;
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
    } catch {
      return null;
    }
    if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) return null;
    items[index] = descriptor.value;
  }
  if (!keys.includes("length")) return null;
  return items;
}

function parseDenseArray<T>(
  value: unknown,
  maxLength: number,
  parseItem: (item: unknown) => T | null,
): T[] | null {
  const items = readDenseArray(value, maxLength);
  if (!items) return null;
  const parsed: T[] = [];
  for (const item of items) {
    const parsedItem = parseItem(item);
    if (parsedItem === null) return null;
    parsed.push(parsedItem);
  }
  return parsed;
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const portNamePattern = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/;

function isUuid(value: unknown): value is string {
  return typeof value === "string" && uuidPattern.test(value);
}

function parseGraphChangeSetConflict(value: unknown): string | null {
  const fields = readPlainDataRecord(value, ["code", "current_revision_id"]);
  return fields?.code === "graph_base_revision_stale" && isUuid(fields.current_revision_id)
    ? fields.current_revision_id
    : null;
}

function isProjectResponse(value: unknown): value is ProjectResponse {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) &&
    isNonEmptyString(value.name) &&
    isNonEmptyString(value.graph_document_id) &&
    isNonEmptyString(value.active_revision_id)
  );
}

function isProjectResponseList(value: unknown): value is ProjectResponse[] {
  return Array.isArray(value) && value.every(isProjectResponse);
}

const resourceKinds = new Set(["STYLE", "CHARACTER", "PROMPT", "MEDIA"] as const);
const promptResourceBlockKinds = new Set([
  "SUBJECT",
  "SCENE_ACTION",
  "COMPOSITION_CAMERA",
  "LIGHT_COLOR_MATERIAL_STYLE",
  "NEGATIVE_CONSTRAINTS",
  "MOTION",
  "PROVIDER_EXTENSION",
  "FREEFORM",
] as const);

function isStringList(value: unknown, maximumLength = 64): value is string[] {
  const values = readDenseArray(value, maximumLength);
  return values !== null && values.every((item) => isBoundedString(item, 1_000));
}

function isOptionalStringList(value: unknown): value is string[] | undefined {
  return value === undefined || isStringList(value);
}

function isResourcePayload(value: unknown): boolean {
  if (!isRecord(value)) return false;
  switch (value.kind) {
    case "STYLE": {
      const fields = readPlainDataRecord(
        value,
        ["kind", "summary"],
        ["palette", "lighting", "materials", "camera_language", "negative_constraints"],
      );
      return Boolean(
        fields &&
          isBoundedString(fields.summary, 10_000) &&
          isOptionalStringList(fields.palette) &&
          isOptionalStringList(fields.lighting) &&
          isOptionalStringList(fields.materials) &&
          isOptionalStringList(fields.camera_language) &&
          isOptionalStringList(fields.negative_constraints),
      );
    }
    case "CHARACTER": {
      const fields = readPlainDataRecord(
        value,
        ["kind", "canonical_name", "identity_anchor"],
        ["locked_traits", "wardrobe_notes"],
      );
      return Boolean(
        fields &&
          isBoundedString(fields.canonical_name, 10_000) &&
          isBoundedString(fields.identity_anchor, 10_000) &&
          isOptionalStringList(fields.locked_traits) &&
          isOptionalStringList(fields.wardrobe_notes),
      );
    }
    case "PROMPT": {
      const fields = readPlainDataRecord(
        value,
        ["kind", "content_order", "detail_density", "format_rules"],
        [
          "camera_rules",
          "positive_rules",
          "negative_rules",
          "applicability",
          "source_example_sha256",
        ],
      );
      const order = fields ? readDenseArray(fields.content_order, 8) : null;
      return Boolean(
        fields &&
          order &&
          order.length > 0 &&
          order.every(
            (item) =>
              typeof item === "string" && promptResourceBlockKinds.has(item as never),
          ) &&
          new Set(order).size === order.length &&
          (fields.detail_density === "LOW" ||
            fields.detail_density === "MEDIUM" ||
            fields.detail_density === "HIGH") &&
          isStringList(fields.format_rules) &&
          fields.format_rules.length > 0 &&
          isOptionalStringList(fields.camera_rules) &&
          isOptionalStringList(fields.positive_rules) &&
          isOptionalStringList(fields.negative_rules) &&
          isOptionalStringList(fields.applicability) &&
          (fields.source_example_sha256 === undefined ||
            fields.source_example_sha256 === null ||
            isSha256(fields.source_example_sha256)),
      );
    }
    case "MEDIA": {
      const fields = readPlainDataRecord(
        value,
        ["kind", "usage_notes"],
        ["expected_media_kind"],
      );
      return Boolean(
        fields &&
          isBoundedString(fields.usage_notes, 10_000) &&
          (fields.expected_media_kind === undefined ||
            fields.expected_media_kind === null ||
            fields.expected_media_kind === "image" ||
            fields.expected_media_kind === "video"),
      );
    }
    default:
      return false;
  }
}

function isNormalizedResourceTag(value: unknown): boolean {
  const fields = readPlainDataRecord(value, ["display_tag", "normalized_tag"]);
  return Boolean(
    fields &&
      isBoundedString(fields.display_tag, 100) &&
      isBoundedString(fields.normalized_tag, 100),
  );
}

function isResourceArtifactReference(value: unknown): boolean {
  const fields = readPlainDataRecord(value, ["slot", "position", "artifact_revision_id"]);
  return Boolean(
    fields &&
      typeof fields.slot === "string" &&
      /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/.test(fields.slot) &&
      isNonNegativeSafeInteger(fields.position) &&
      isUuid(fields.artifact_revision_id),
  );
}

function isResourceRevisionSummary(value: unknown): boolean {
  const fields = readPlainDataRecord(value, ["id", "sequence", "payload_sha256", "created_at"]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isPositiveSafeInteger(fields.sequence) &&
      isSha256(fields.payload_sha256) &&
      isDateTimeString(fields.created_at),
  );
}

function isResourceRevisionResponse(value: unknown): value is ResourceRevisionResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "sequence",
    "payload_sha256",
    "created_at",
    "resource_id",
    "schema_version",
    "payload",
    "tags",
    "artifacts",
    "created_by_actor",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isPositiveSafeInteger(fields.sequence) &&
      isSha256(fields.payload_sha256) &&
      isDateTimeString(fields.created_at) &&
      isUuid(fields.resource_id) &&
      fields.schema_version === 1 &&
      isResourcePayload(fields.payload) &&
      parseDenseArray(fields.tags, 64, (tag) => (isNormalizedResourceTag(tag) ? tag : null)) !==
        null &&
      parseDenseArray(fields.artifacts, 64, (artifact) =>
        isResourceArtifactReference(artifact) ? artifact : null,
      ) !== null &&
      isBoundedString(fields.created_by_actor, 128),
  );
}

function isResourceResponse(value: unknown): value is ResourceResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "origin_project_id",
    "kind",
    "name",
    "created_at",
    "archived_at",
    "latest_revision",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isUuid(fields.origin_project_id) &&
      typeof fields.kind === "string" &&
      resourceKinds.has(fields.kind as never) &&
      isBoundedString(fields.name, 200) &&
      isDateTimeString(fields.created_at) &&
      (fields.archived_at === null || isDateTimeString(fields.archived_at)) &&
      isResourceRevisionResponse(fields.latest_revision) &&
      fields.latest_revision.resource_id === fields.id &&
      fields.latest_revision.payload.kind === fields.kind,
  );
}

function isResourceResponseList(value: unknown): value is ResourceResponse[] {
  return parseDenseArray(value, 100, (item) => (isResourceResponse(item) ? item : null)) !== null;
}

function isResourceDetailResponse(value: unknown): value is ResourceDetailResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "origin_project_id",
    "kind",
    "name",
    "created_at",
    "archived_at",
    "latest_revision",
    "revision_history",
  ]);
  if (!fields) return false;
  const response = {
    id: fields.id,
    origin_project_id: fields.origin_project_id,
    kind: fields.kind,
    name: fields.name,
    created_at: fields.created_at,
    archived_at: fields.archived_at,
    latest_revision: fields.latest_revision,
  };
  return (
    isResourceResponse(response) &&
    parseDenseArray(fields.revision_history, 10_000, (item) =>
      isResourceRevisionSummary(item) ? item : null,
    ) !== null
  );
}

function isResourceUpdateItem(value: unknown): value is ResourceUpdateItem {
  const fields = readPlainDataRecord(value, [
    "binding_id",
    "binding_key",
    "scope_kind",
    "scope_id",
    "resource_id",
    "resource_kind",
    "pinned_revision_id",
    "latest_revision_id",
    "pinned_sequence",
    "latest_sequence",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.binding_id) &&
      isBoundedString(fields.binding_key, 64) &&
      (fields.scope_kind === "PROJECT" ||
        fields.scope_kind === "SHOT" ||
        fields.scope_kind === "NODE") &&
      (fields.scope_id === null || isUuid(fields.scope_id)) &&
      (fields.scope_kind === "PROJECT" ? fields.scope_id === null : fields.scope_id !== null) &&
      isUuid(fields.resource_id) &&
      typeof fields.resource_kind === "string" &&
      resourceKinds.has(fields.resource_kind as never) &&
      isUuid(fields.pinned_revision_id) &&
      isUuid(fields.latest_revision_id) &&
      isPositiveSafeInteger(fields.pinned_sequence) &&
      isPositiveSafeInteger(fields.latest_sequence) &&
      fields.latest_sequence > fields.pinned_sequence,
  );
}

function isResourceUpdateItemList(value: unknown): value is ResourceUpdateItem[] {
  return parseDenseArray(value, 10_000, (item) =>
    isResourceUpdateItem(item) ? item : null,
  ) !== null;
}

const artifactImportStates = new Set([
  "CREATED",
  "RECEIVING",
  "VALIDATING",
  "COMPLETED",
  "FAILED_RETRYABLE",
  "FAILED_FINAL",
] as const);
const artifactFailureCodes: ReadonlySet<string> = new Set([
  "upload_interrupted",
  "probe_timeout",
  "probe_unavailable",
  "size_mismatch",
  "corrupt_media",
  "unsupported_media",
  "incompatible_declaration",
]);
const artifactConflictReasons: ReadonlySet<string> = new Set([
  "declaration_mismatch",
  "content_replay_mismatch",
  "upload_in_progress",
  "invalid_replacement",
  "invalid_state",
  "discard_precondition_failed",
  "result_referenced",
]);

function isBoundedString(value: unknown, maximum: number): value is string {
  if (typeof value !== "string") return false;
  const codePointLength = Array.from(value).length;
  return codePointLength > 0 && codePointLength <= maximum;
}

function isDateTimeString(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match =
    /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?(?:Z|[+-]([0-9]{2}):([0-9]{2}))$/.exec(
      value,
    );
  if (!match) return false;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = Number(match[7] ?? 0);
  const offsetMinute = Number(match[8] ?? 0);
  if (year === 0 || month < 1 || month > 12) return false;

  const isLeapYear =
    year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [
    31,
    isLeapYear ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ];
  const maximumDay = daysInMonth[month - 1];

  return (
    maximumDay !== undefined &&
    day >= 1 &&
    day <= maximumDay &&
    hour <= 23 &&
    minute <= 59 &&
    second <= 59 &&
    offsetHour <= 23 &&
    offsetMinute <= 59
  );
}

function isPositiveSafeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) > 0;
}

function isNonNegativeSafeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function isArtifactMetadata(
  value: unknown,
  expectedKind: MediaKind,
): value is ArtifactRevision["metadata"] {
  if (expectedKind === "image") {
    const fields = readPlainDataRecord(value, ["kind", "width", "height"]);
    return Boolean(
      fields &&
        fields.kind === "image" &&
        isPositiveSafeInteger(fields.width) &&
        isPositiveSafeInteger(fields.height),
    );
  }
  const fields = readPlainDataRecord(value, [
    "kind",
    "width",
    "height",
    "duration_seconds",
    "frames_per_second",
  ]);
  return Boolean(
    fields &&
      fields.kind === "video" &&
      isPositiveSafeInteger(fields.width) &&
      isPositiveSafeInteger(fields.height) &&
      isFiniteNumber(fields.duration_seconds) &&
      fields.duration_seconds > 0 &&
      isFiniteNumber(fields.frames_per_second) &&
      fields.frames_per_second > 0,
  );
}

function isArtifactRevision(value: unknown): value is ArtifactRevision {
  const fields = readPlainDataRecord(value, [
    "revision_id",
    "artifact_id",
    "sequence",
    "media_kind",
    "media_type",
    "byte_size",
    "display_filename",
    "metadata",
    "source_kind",
    "source_project_id",
    "origin_project_id",
    "created_at",
  ]);
  if (
    !fields ||
    !isUuid(fields.revision_id) ||
    !isUuid(fields.artifact_id) ||
    !isPositiveSafeInteger(fields.sequence) ||
    (fields.media_kind !== "image" && fields.media_kind !== "video") ||
    !isArtifactMediaType(fields.media_type, fields.media_kind) ||
    !isPositiveSafeInteger(fields.byte_size) ||
    fields.byte_size > 64 * 1024 * 1024 ||
    !isSanitizedDisplayFilename(fields.display_filename) ||
    !isBoundedString(fields.source_kind, 64) ||
    !isUuid(fields.source_project_id) ||
    !isUuid(fields.origin_project_id) ||
    !isDateTimeString(fields.created_at)
  ) {
    return false;
  }
  return isArtifactMetadata(fields.metadata, fields.media_kind);
}

function isArtifactRevisionList(value: unknown): value is ArtifactRevision[] {
  const revisions = readDenseArray(value, Number.MAX_SAFE_INTEGER);
  return revisions !== null && revisions.every(isArtifactRevision);
}

function isShotSlotAssociationHistoryItem(
  value: unknown,
  slot: ShotSlot,
): value is ShotSlotAssociationHistoryItem {
  const fields = readPlainDataRecord(value, [
    "graph_revision_id",
    "graph_revision_sequence",
    "event_ordinal",
    "occurred_at",
    "action",
    "reference_index",
    "artifact_revision",
  ]);
  if (
    !fields ||
    !isUuid(fields.graph_revision_id) ||
    !isNonNegativeSafeInteger(fields.graph_revision_sequence) ||
    !isNonNegativeSafeInteger(fields.event_ordinal) ||
    fields.event_ordinal > 127 ||
    !isDateTimeString(fields.occurred_at) ||
    (fields.action !== "associated" &&
      fields.action !== "cleared" &&
      fields.action !== "removed")
  ) {
    return false;
  }

  const referenceIndex = fields.reference_index;
  if (
    referenceIndex !== null &&
    (!isNonNegativeSafeInteger(referenceIndex) || referenceIndex > 63)
  ) {
    return false;
  }
  let revision: ArtifactRevision | null;
  if (fields.artifact_revision === null) {
    revision = null;
  } else {
    if (!isArtifactRevision(fields.artifact_revision)) return false;
    revision = fields.artifact_revision;
  }

  if (slot === "reference") {
    return (
      (fields.action === "associated" || fields.action === "removed") &&
      referenceIndex !== null &&
      revision !== null &&
      revision.media_kind === "image"
    );
  }
  if (
    fields.action === "removed" ||
    referenceIndex !== null ||
    (fields.action === "cleared") !== (revision === null)
  ) {
    return false;
  }
  return revision === null || revision.media_kind === slot;
}

function isShotSlotAssociationHistoryResponse(
  value: unknown,
  projectId: string,
  shotId: string,
  slot: ShotSlot,
  anchorRevisionId: string,
  limit: number,
): value is ShotSlotAssociationHistoryResponse {
  const fields = readPlainDataRecord(value, [
    "project_id",
    "shot_id",
    "slot",
    "anchor_graph_revision_id",
    "anchor_graph_sequence",
    "items",
    "next_cursor",
  ]);
  if (
    !fields ||
    !isUuid(fields.project_id) ||
    fields.project_id !== projectId ||
    !isUuid(fields.shot_id) ||
    fields.shot_id !== shotId ||
    fields.slot !== slot ||
    !isUuid(fields.anchor_graph_revision_id) ||
    fields.anchor_graph_revision_id !== anchorRevisionId ||
    !isNonNegativeSafeInteger(fields.anchor_graph_sequence) ||
    (fields.next_cursor !== null && !isBoundedString(fields.next_cursor, 2_048))
  ) {
    return false;
  }
  const items = readDenseArray(fields.items, limit);
  if (!items) return false;
  const eventKeys = new Set<string>();
  for (const item of items) {
    if (!isShotSlotAssociationHistoryItem(item, slot)) return false;
    if (item.graph_revision_sequence > fields.anchor_graph_sequence) return false;
    const eventKey = `${item.graph_revision_id}:${item.event_ordinal}`;
    if (eventKeys.has(eventKey)) return false;
    eventKeys.add(eventKey);
  }
  return true;
}

function isArtifactMediaType(value: unknown, mediaKind: MediaKind): value is string {
  if (typeof value !== "string") return false;
  return mediaKind === "image"
    ? imageMimeHints.has(value)
    : videoMimeHints.has(value);
}

const windowsReservedBasenamePattern = /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i;
const unsafeWindowsFilenameCharacterPattern = /[<>:"/\\|?*]/;
const controlOrFormatCharacterPattern = /[\p{Cc}\p{Cf}]/u;

function isSanitizedDisplayFilename(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.normalize("NFC") !== value ||
    Array.from(value).length === 0 ||
    Array.from(value).length > 255 ||
    unsafeWindowsFilenameCharacterPattern.test(value) ||
    controlOrFormatCharacterPattern.test(value) ||
    /^[ .]|[ .]$/.test(value)
  ) {
    return false;
  }
  const basename = value.split(".", 1)[0].replace(/[ .]+$/, "");
  return !windowsReservedBasenamePattern.test(basename);
}

function isNullableUuid(value: unknown): value is string | null {
  return value === null || isUuid(value);
}

function isNullableBoundedString(
  value: unknown,
  maximum: number,
): value is string | null {
  return value === null || isBoundedString(value, maximum);
}

function isArtifactImportSession(value: unknown): value is ArtifactImportSession {
  const fields = readPlainDataRecord(
    value,
    [
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
    ],
  );
  if (
    !fields ||
    !isUuid(fields.import_id) ||
    !isUuid(fields.source_project_id) ||
    !isUuid(fields.client_import_id) ||
    !isNullableUuid(fields.replace_revision_id) ||
    (fields.requested_media_kind !== "image" && fields.requested_media_kind !== "video") ||
    !isBoundedString(fields.original_filename, 512) ||
    !isNullableBoundedString(fields.declared_content_type, 255) ||
    !isPositiveSafeInteger(fields.expected_byte_size) ||
    fields.expected_byte_size > 64 * 1024 * 1024 ||
    typeof fields.state !== "string" ||
    !artifactImportStates.has(fields.state as never) ||
    !Number.isSafeInteger(fields.received_byte_size) ||
    (fields.received_byte_size as number) < 0 ||
    (fields.received_byte_size as number) > fields.expected_byte_size ||
    !isDateTimeString(fields.created_at) ||
    !isDateTimeString(fields.updated_at)
  ) {
    return false;
  }
  const failureCode = fields.failure_code;
  if (
    failureCode !== null &&
    (typeof failureCode !== "string" || !artifactFailureCodes.has(failureCode))
  ) {
    return false;
  }
  const failed = fields.state === "FAILED_RETRYABLE" || fields.state === "FAILED_FINAL";
  if (failed !== (failureCode !== null)) return false;
  if (
    (fields.state === "FAILED_RETRYABLE" &&
      !retryableArtifactFailureCodes.has(failureCode as ArtifactImportFailureCode)) ||
    (fields.state === "FAILED_FINAL" &&
      retryableArtifactFailureCodes.has(failureCode as ArtifactImportFailureCode))
  ) {
    return false;
  }

  const revision = fields.artifact_revision;
  const completed = fields.state === "COMPLETED";
  if (completed !== (revision !== null)) return false;
  if (!completed) return true;
  return (
    isArtifactRevision(revision) &&
    revision.media_kind === fields.requested_media_kind &&
    revision.source_project_id === fields.source_project_id &&
    revision.source_kind === "import" &&
    revision.byte_size === fields.expected_byte_size &&
    fields.received_byte_size === fields.expected_byte_size
  );
}

function isDiscardArtifactImportResultResponse(
  value: unknown,
): value is GeneratedDiscardArtifactImportResultResponse {
  const fields = readPlainDataRecord(value, ["outcome", "content_cleanup"]);
  return Boolean(
    fields &&
      (fields.outcome === "discarded" ||
        fields.outcome === "already_discarded") &&
      (fields.content_cleanup === "removed" ||
        fields.content_cleanup === "not_present" ||
        fields.content_cleanup === "retained_shared" ||
        fields.content_cleanup === "not_applicable" ||
        fields.content_cleanup === "pending"),
  );
}

function parsePoint(value: unknown): GraphNodeBody["position"] | null {
  const fields = readPlainDataRecord(value, ["x", "y"]);
  if (!fields || !isFiniteNumber(fields.x) || !isFiniteNumber(fields.y)) return null;
  return { x: fields.x, y: fields.y };
}

function parseNodeTextData(value: unknown): GraphNodeBody["data"] | null {
  const fields = readPlainDataRecord(value, ["text"]);
  if (!fields || typeof fields.text !== "string" || fields.text.length > 100_000) {
    return null;
  }
  return { text: fields.text };
}

function parseGraphNodeV1(value: unknown): GraphNodeBody | null {
  const fields = readPlainDataRecord(value, ["id", "kind", "position", "data"]);
  if (
    !fields ||
    !isUuid(fields.id) ||
    (fields.kind !== "text_source" && fields.kind !== "prompt" && fields.kind !== "note")
  ) {
    return null;
  }
  const position = parsePoint(fields.position);
  const data = parseNodeTextData(fields.data);
  if (!position || !data) return null;
  return { id: fields.id, kind: fields.kind, position, data };
}

function parseMediaInputData(
  value: unknown,
): Extract<GraphNodeV2Body, { kind: "media_input" }>["data"] | null {
  const fields = readPlainDataRecord(value, [], ["artifact_revision_id", "media_kind"]);
  if (!fields) return null;
  const rawRevision = fields.artifact_revision_id;
  let revision: string | null;
  if (rawRevision === undefined || rawRevision === null) revision = null;
  else if (isUuid(rawRevision)) revision = rawRevision;
  else return null;
  const rawMediaKind = fields.media_kind;
  let mediaKind: "image" | "video" | null;
  if (rawMediaKind === undefined || rawMediaKind === null) mediaKind = null;
  else if (rawMediaKind === "image" || rawMediaKind === "video") mediaKind = rawMediaKind;
  else return null;
  if ((revision === null) !== (mediaKind === null)) {
    return null;
  }
  return { artifact_revision_id: revision, media_kind: mediaKind };
}

function parseShotNodeData(
  value: unknown,
): Extract<GraphNodeV2Body, { kind: "shot" }>["data"] | null {
  const fields = readPlainDataRecord(value, ["shot_id"]);
  if (!fields || !isUuid(fields.shot_id)) return null;
  return { shot_id: fields.shot_id };
}

function parseGraphNodeV2(value: unknown): GraphNodeV2Body | null {
  const fields = readPlainDataRecord(value, ["id", "kind", "position", "data"]);
  if (!fields || !isUuid(fields.id)) return null;
  const position = parsePoint(fields.position);
  if (!position) return null;
  switch (fields.kind) {
    case "text_source":
    case "prompt":
    case "note": {
      const data = parseNodeTextData(fields.data);
      return data ? { id: fields.id, kind: fields.kind, position, data } : null;
    }
    case "media_input": {
      const data = parseMediaInputData(fields.data);
      return data ? { id: fields.id, kind: fields.kind, position, data } : null;
    }
    case "shot": {
      const data = parseShotNodeData(fields.data);
      return data ? { id: fields.id, kind: fields.kind, position, data } : null;
    }
    default:
      return null;
  }
}

export class ResourceApiError extends ApiRequestError {
  readonly code: string;
  readonly problem: Readonly<Record<string, unknown>>;

  constructor(status: number, code: string, problem: Record<string, unknown>) {
    super(status, code, false);
    this.name = "ResourceApiError";
    this.code = code;
    this.problem = problem;
  }
}

export class ResourceRevisionConflictError extends ResourceApiError {
  readonly resourceId: string;
  readonly currentRevisionId: string | null;

  constructor(resourceId: string, currentRevisionId: string | null, problem: Record<string, unknown>) {
    super(409, "resource_revision_conflict", problem);
    this.name = "ResourceRevisionConflictError";
    this.resourceId = resourceId;
    this.currentRevisionId = currentRevisionId;
  }
}

const promptBlockKinds = new Set([
  "SUBJECT",
  "SCENE_ACTION",
  "COMPOSITION_CAMERA",
  "LIGHT_COLOR_MATERIAL_STYLE",
  "NEGATIVE_CONSTRAINTS",
  "MOTION",
  "PROVIDER_EXTENSION",
  "FREEFORM",
]);

function parsePromptBlock(value: unknown): PromptBlockBody | null {
  const fields = readPlainDataRecord(value, ["id", "kind", "text", "locked"]);
  if (
    !fields ||
    !isUuid(fields.id) ||
    typeof fields.kind !== "string" ||
    !promptBlockKinds.has(fields.kind) ||
    typeof fields.text !== "string" ||
    fields.text.length > 100_000 ||
    typeof fields.locked !== "boolean"
  ) {
    return null;
  }
  return {
    id: fields.id,
    kind: fields.kind as PromptBlockBody["kind"],
    text: fields.text,
    locked: fields.locked,
  };
}

function parsePromptSpan(value: unknown): PromptSpanBody | null {
  const fields = readPlainDataRecord(value, ["block_id", "start_utf16", "end_utf16"]);
  if (
    !fields ||
    !isUuid(fields.block_id) ||
    !Number.isSafeInteger(fields.start_utf16) ||
    !Number.isSafeInteger(fields.end_utf16) ||
    (fields.start_utf16 as number) < 0 ||
    (fields.end_utf16 as number) < (fields.start_utf16 as number)
  ) {
    return null;
  }
  return {
    block_id: fields.block_id,
    start_utf16: fields.start_utf16 as number,
    end_utf16: fields.end_utf16 as number,
  };
}

function parsePromptDocument(value: unknown): PromptDocumentBody | null {
  const fields = readPlainDataRecord(value, ["schema_version", "rendered_text"], ["blocks", "spans"]);
  if (!fields || fields.schema_version !== 1 || typeof fields.rendered_text !== "string") {
    return null;
  }
  const blocks = parseDenseArray(fields.blocks ?? [], 5_000, parsePromptBlock);
  const spans = parseDenseArray(fields.spans ?? [], 5_000, parsePromptSpan);
  if (!blocks || !spans || blocks.length !== spans.length) return null;
  const blockIds = blocks.map((block) => block.id);
  if (new Set(blockIds).size !== blockIds.length) return null;
  let cursor = 0;
  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index];
    const span = spans[index];
    if (
      span.block_id !== block.id ||
      span.start_utf16 !== cursor ||
      span.end_utf16 !== cursor + block.text.length
    ) {
      return null;
    }
    cursor = span.end_utf16 + (index < blocks.length - 1 ? 1 : 0);
  }
  if (blocks.map((block) => block.text).join("\n") !== fields.rendered_text) return null;
  return { schema_version: 1, blocks, rendered_text: fields.rendered_text, spans };
}

function parseGraphNodeV3(value: unknown): GraphNodeV3Body | null {
  const fields = readPlainDataRecord(value, ["id", "kind", "position", "data"]);
  if (!fields || !isUuid(fields.id)) return null;
  const position = parsePoint(fields.position);
  if (!position) return null;
  if (fields.kind === "prompt") {
    const dataFields = readPlainDataRecord(fields.data, ["document"]);
    const document = dataFields ? parsePromptDocument(dataFields.document) : null;
    return document ? { id: fields.id, kind: "prompt", position, data: { document } } : null;
  }
  return parseGraphNodeV2(value) as GraphNodeV3Body | null;
}

function parseResourceBinding(value: unknown): ResourceBindingBody | null {
  const fields = readPlainDataRecord(value, [
    "id",
    "resource_kind",
    "binding_key",
    "scope_kind",
    "scope_id",
    "resource_revision_id",
  ]);
  if (
    !fields ||
    !isUuid(fields.id) ||
    (fields.resource_kind !== "STYLE" &&
      fields.resource_kind !== "CHARACTER" &&
      fields.resource_kind !== "PROMPT" &&
      fields.resource_kind !== "MEDIA") ||
    typeof fields.binding_key !== "string" ||
    fields.binding_key.length < 1 ||
    fields.binding_key.length > 64 ||
    !portNamePattern.test(fields.binding_key) ||
    (fields.scope_kind !== "PROJECT" &&
      fields.scope_kind !== "SHOT" &&
      fields.scope_kind !== "NODE") ||
    !isUuid(fields.resource_revision_id)
  ) {
    return null;
  }
  const scopeId = fields.scope_id;
  if (
    (fields.scope_kind === "PROJECT" && scopeId !== null) ||
    (fields.scope_kind !== "PROJECT" && !isUuid(scopeId))
  ) {
    return null;
  }
  return {
    id: fields.id,
    resource_kind: fields.resource_kind,
    binding_key: fields.binding_key,
    scope_kind: fields.scope_kind,
    scope_id: scopeId as string | null,
    resource_revision_id: fields.resource_revision_id,
  };
}

function parseGraphEdge(value: unknown): GraphEdgeBody | null {
  const fields = readPlainDataRecord(
    value,
    ["id", "source", "source_port", "target", "target_port"],
  );
  if (
    !fields ||
    !isUuid(fields.id) ||
    !isUuid(fields.source) ||
    typeof fields.source_port !== "string" ||
    fields.source_port.length < 1 ||
    fields.source_port.length > 64 ||
    !portNamePattern.test(fields.source_port) ||
    !isUuid(fields.target) ||
    typeof fields.target_port !== "string" ||
    fields.target_port.length < 1 ||
    fields.target_port.length > 64 ||
    !portNamePattern.test(fields.target_port)
  ) {
    return null;
  }
  return {
    id: fields.id,
    source: fields.source,
    source_port: fields.source_port,
    target: fields.target,
    target_port: fields.target_port,
  };
}

type ValidatedNode = GraphNodeBody | GraphNodeV2Body | GraphNodeV3Body;

const graphPorts: Record<string, { source: readonly string[]; target: readonly string[] }> = {
  text_source: { source: ["text"], target: [] },
  prompt: { source: ["prompt_document"], target: ["text"] },
  note: { source: [], target: [] },
  media_input: { source: [], target: [] },
  shot: { source: [], target: ["prompt_document"] },
};

function hasValidGraphRelations(nodes: ValidatedNode[], edges: GraphEdgeBody[]): boolean {
  const nodeIds = nodes.map((node) => node.id);
  const edgeIds = edges.map((edge) => edge.id);
  if (new Set(nodeIds).size !== nodeIds.length || new Set(edgeIds).size !== edgeIds.length) {
    return false;
  }
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map(nodeIds.map((id) => [id, new Set<string>()]));
  const indegree = new Map(nodeIds.map((id) => [id, 0]));
  for (const edge of edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target || edge.source === edge.target) return false;
    const sourcePorts = graphPorts[source.kind];
    const targetPorts = graphPorts[target.kind];
    if (
      !sourcePorts?.source.includes(edge.source_port) ||
      !targetPorts?.target.includes(edge.target_port)
    ) {
      return false;
    }
    const targets = adjacency.get(edge.source)!;
    if (!targets.has(edge.target)) {
      targets.add(edge.target);
      indegree.set(edge.target, indegree.get(edge.target)! + 1);
    }
  }
  const queue = [...indegree.entries()]
    .filter(([, degree]) => degree === 0)
    .map(([id]) => id);
  let visited = 0;
  while (queue.length > 0) {
    const current = queue.shift()!;
    visited += 1;
    for (const target of adjacency.get(current)!) {
      const degree = indegree.get(target)! - 1;
      indegree.set(target, degree);
      if (degree === 0) queue.push(target);
    }
  }
  return visited === nodes.length;
}

function parseShot(value: unknown): ShotBody | null {
  const fields = readPlainDataRecord(
    value,
    ["id", "order"],
    ["image_revision_id", "video_revision_id", "reference_revision_ids"],
  );
  if (
    !fields ||
    !isUuid(fields.id) ||
    !Number.isSafeInteger(fields.order) ||
    (fields.order as number) < 0
  ) {
    return null;
  }
  const rawImageRevision = fields.image_revision_id;
  let imageRevision: string | null;
  if (rawImageRevision === undefined || rawImageRevision === null) imageRevision = null;
  else if (isUuid(rawImageRevision)) imageRevision = rawImageRevision;
  else return null;
  const rawVideoRevision = fields.video_revision_id;
  let videoRevision: string | null;
  if (rawVideoRevision === undefined || rawVideoRevision === null) videoRevision = null;
  else if (isUuid(rawVideoRevision)) videoRevision = rawVideoRevision;
  else return null;
  const references = fields.reference_revision_ids === undefined
    ? []
    : parseDenseArray(fields.reference_revision_ids, 64, (reference) =>
        isUuid(reference) ? reference : null,
      );
  if (
    references === null ||
    new Set(references).size !== references.length
  ) {
    return null;
  }
  return {
    id: fields.id,
    order: fields.order as number,
    image_revision_id: imageRevision,
    video_revision_id: videoRevision,
    reference_revision_ids: references,
  };
}

function hasValidShotRelations(nodes: ValidatedNode[], shots: ShotBody[]): boolean {
  const shotIds = shots.map((shot) => shot.id);
  const shotOrders = shots.map((shot) => shot.order);
  if (
    new Set(shotIds).size !== shotIds.length ||
    new Set(shotOrders).size !== shotOrders.length ||
    shots.reduce((total, shot) => total + (shot.reference_revision_ids?.length ?? 0), 0) >
      10_000
  ) {
    return false;
  }
  const nodeShotIds = nodes
    .filter((node) => node.kind === "shot")
    .map((node) => node.data.shot_id);
  return (
    nodeShotIds.length === shotIds.length &&
    new Set(nodeShotIds).size === nodeShotIds.length &&
    nodeShotIds.every((id) => shotIds.includes(id))
  );
}

function hasValidResourceBindings(
  nodes: GraphNodeV3Body[],
  shots: ShotBody[],
  bindings: ResourceBindingBody[],
): boolean {
  const ids = bindings.map((binding) => binding.id);
  const keys = bindings.map(
    (binding) => `${binding.scope_kind}:${binding.scope_id ?? ""}:${binding.binding_key}`,
  );
  if (new Set(ids).size !== ids.length || new Set(keys).size !== keys.length) return false;
  const nodeIds = new Set(nodes.map((node) => node.id));
  const shotIds = new Set(shots.map((shot) => shot.id));
  return bindings.every(
    (binding) =>
      binding.scope_kind === "PROJECT" ||
      (binding.scope_kind === "SHOT" && shotIds.has(binding.scope_id!)) ||
      (binding.scope_kind === "NODE" && nodeIds.has(binding.scope_id!)),
  );
}

function isStructuredCloneableGraph(value: unknown): boolean {
  try {
    structuredClone(value);
    return true;
  } catch {
    return false;
  }
}

function parseGraphSnapshotValue(value: unknown): GraphSnapshotBody | null {
  const fields = readPlainDataRecord(
    value,
    ["schema_version"],
    ["nodes", "edges", "shots", "resource_bindings"],
  );
  if (!fields) return null;
  const rawNodes = fields.nodes === undefined ? [] : fields.nodes;
  const rawEdges = fields.edges === undefined ? [] : fields.edges;
  if (fields.schema_version === 1) {
    if (
      Object.prototype.hasOwnProperty.call(fields, "shots") ||
      Object.prototype.hasOwnProperty.call(fields, "resource_bindings")
    ) return null;
    const nodes = parseDenseArray(rawNodes, 5_000, parseGraphNodeV1);
    const edges = parseDenseArray(rawEdges, 10_000, parseGraphEdge);
    if (!nodes || !edges || !hasValidGraphRelations(nodes, edges)) return null;
    return isStructuredCloneableGraph(value) ? { schema_version: 1, nodes, edges } : null;
  }
  if (fields.schema_version === 2) {
    if (Object.prototype.hasOwnProperty.call(fields, "resource_bindings")) return null;
    const rawShots = fields.shots === undefined ? [] : fields.shots;
    const nodes = parseDenseArray(rawNodes, 5_000, parseGraphNodeV2);
    const edges = parseDenseArray(rawEdges, 10_000, parseGraphEdge);
    const shots = parseDenseArray(rawShots, 5_000, parseShot);
    if (
      !nodes ||
      !edges ||
      !shots ||
      !hasValidGraphRelations(nodes, edges) ||
      !hasValidShotRelations(nodes, shots)
    ) {
      return null;
    }
    return isStructuredCloneableGraph(value)
      ? { schema_version: 2, nodes, edges, shots }
      : null;
  }
  if (fields.schema_version === 3) {
    const nodes = parseDenseArray(rawNodes, 5_000, parseGraphNodeV3);
    const edges = parseDenseArray(rawEdges, 10_000, parseGraphEdge);
    const shots = parseDenseArray(fields.shots ?? [], 5_000, parseShot);
    const bindings = parseDenseArray(
      fields.resource_bindings ?? [],
      10_000,
      parseResourceBinding,
    );
    if (
      !nodes ||
      !edges ||
      !shots ||
      !bindings ||
      !hasValidGraphRelations(nodes, edges) ||
      !hasValidShotRelations(nodes, shots) ||
      !hasValidResourceBindings(nodes, shots, bindings)
    ) {
      return null;
    }
    return isStructuredCloneableGraph(value)
      ? {
          schema_version: 3,
          nodes,
          edges,
          shots,
          resource_bindings: bindings,
        }
      : null;
  }
  return null;
}

function isGraphSnapshot(value: unknown): value is GraphSnapshotBody {
  return parseGraphSnapshotValue(value) !== null;
}

export function parseGraphSnapshot(value: unknown): GraphSnapshotBody {
  const snapshot = parseGraphSnapshotValue(value);
  if (!snapshot) throw new TypeError("Invalid graph snapshot");
  return snapshot;
}

function isViewportState(value: unknown): value is ViewportState {
  const fields = readPlainDataRecord(value, ["x", "y", "zoom"]);
  return Boolean(
    fields &&
      isFiniteNumber(fields.x) &&
      isFiniteNumber(fields.y) &&
      isFiniteNumber(fields.zoom) &&
      fields.zoom >= 0.1 &&
      fields.zoom <= 4,
  );
}

function isGraphResponse(value: unknown): value is GraphResponse {
  const fields = readPlainDataRecord(value, [
    "revision_id",
    "graph_document_id",
    "sequence",
    "snapshot_hash",
    "snapshot",
    "viewport",
  ]);
  return (
    fields !== null &&
    isNonEmptyString(fields.revision_id) &&
    isNonEmptyString(fields.graph_document_id) &&
    Number.isInteger(fields.sequence) &&
    isNonEmptyString(fields.snapshot_hash) &&
    isGraphSnapshot(fields.snapshot) &&
    isViewportState(fields.viewport)
  );
}

function isPositiveVersion(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 1;
}

function isNonNegativeCount(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isMaterializationSummary(value: unknown): boolean {
  const fields = readPlainDataRecord(value, ["unmaterialized", "current", "stale"]);
  return Boolean(
    fields &&
      isNonNegativeCount(fields.unmaterialized) &&
      isNonNegativeCount(fields.current) &&
      isNonNegativeCount(fields.stale),
  );
}

function isSelectionPointer(value: unknown, graphDocumentId?: string): boolean {
  const fields = readPlainDataRecord(value, [
    "graph_document_id",
    "branch_id",
    "graph_revision_id",
    "version",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.graph_document_id) &&
      (graphDocumentId === undefined || fields.graph_document_id === graphDocumentId) &&
      isUuid(fields.branch_id) &&
      isUuid(fields.graph_revision_id) &&
      isPositiveVersion(fields.version),
  );
}

function isBranchHead(value: unknown): boolean {
  const fields = readPlainDataRecord(value, [
    "graph_revision_id",
    "version",
    "graph_revision_sha256",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.graph_revision_id) &&
      isPositiveVersion(fields.version) &&
      (fields.graph_revision_sha256 === null || isSha256(fields.graph_revision_sha256)),
  );
}

function isBranchSummary(value: unknown, graphDocumentId: string): boolean {
  const fields = readPlainDataRecord(value, [
    "id",
    "graph_document_id",
    "name",
    "kind",
    "forked_from_branch_id",
    "forked_from_revision_id",
    "head",
    "created_at",
    "is_selected",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      fields.graph_document_id === graphDocumentId &&
      typeof fields.name === "string" &&
      Array.from(fields.name).length >= 1 &&
      Array.from(fields.name).length <= 100 &&
      (fields.kind === "MAIN" || fields.kind === "EXPERIMENT") &&
      isNullableUuid(fields.forked_from_branch_id) &&
      isUuid(fields.forked_from_revision_id) &&
      isBranchHead(fields.head) &&
      isDateTimeString(fields.created_at) &&
      typeof fields.is_selected === "boolean",
  );
}

function isApplicationTarget(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (value.mode === "DIRECT") {
    const fields = readPlainDataRecord(value, [
      "mode",
      "branch_id",
      "expected_branch_head_revision_id",
      "expected_branch_head_version",
      "expected_selection_version",
    ]);
    return Boolean(
      fields &&
        fields.mode === "DIRECT" &&
        isUuid(fields.branch_id) &&
        isUuid(fields.expected_branch_head_revision_id) &&
        isPositiveVersion(fields.expected_branch_head_version) &&
        isPositiveVersion(fields.expected_selection_version),
    );
  }
  if (value.mode === "NEW_BRANCH") {
    const fields = readPlainDataRecord(value, [
      "mode",
      "base_branch_id",
      "branch_name",
      "expected_branch_head_revision_id",
      "expected_branch_head_version",
      "expected_selection_version",
    ]);
    return Boolean(
      fields &&
        fields.mode === "NEW_BRANCH" &&
        isUuid(fields.base_branch_id) &&
        typeof fields.branch_name === "string" &&
        Array.from(fields.branch_name).length >= 1 &&
        Array.from(fields.branch_name).length <= 100 &&
        isUuid(fields.expected_branch_head_revision_id) &&
        isPositiveVersion(fields.expected_branch_head_version) &&
        isPositiveVersion(fields.expected_selection_version),
    );
  }
  return false;
}

const impactKinds = new Set(["DIRECT", "DOWNSTREAM", "REMOVED"]);
const materializationStates = new Set(["UNMATERIALIZED", "CURRENT", "STALE", "ABSENT"]);
const impactReasons = new Set([
  "NODE_ADDED",
  "NODE_REMOVED",
  "NODE_TYPE_CHANGED",
  "NODE_DATA_CHANGED",
  "PROMPT_DOCUMENT_CHANGED",
  "INCOMING_EDGES_CHANGED",
  "RESOURCE_BINDING_CHANGED",
  "DOWNSTREAM_OF_CHANGE",
]);

function isNodeImpactItem(value: unknown): boolean {
  const fields = readPlainDataRecord(value, [
    "node_id",
    "impact_kind",
    "reasons",
    "old_state",
    "projected_state",
  ]);
  const reasons = fields ? readDenseArray(fields.reasons, impactReasons.size) : null;
  return Boolean(
    fields &&
      isUuid(fields.node_id) &&
      impactKinds.has(String(fields.impact_kind)) &&
      reasons &&
      reasons.every((reason) => typeof reason === "string" && impactReasons.has(reason)) &&
      new Set(reasons).size === reasons.length &&
      materializationStates.has(String(fields.old_state)) &&
      materializationStates.has(String(fields.projected_state)),
  );
}

function isStaleImpact(value: unknown): boolean {
  const fields = readPlainDataRecord(value, [
    "items",
    "summary",
    "impact_sha256",
    "confirmation_required",
  ]);
  const items = fields ? readDenseArray(fields.items, 5_000) : null;
  return Boolean(
    fields &&
      items &&
      items.every(isNodeImpactItem) &&
      isMaterializationSummary(fields.summary) &&
      isSha256(fields.impact_sha256) &&
      typeof fields.confirmation_required === "boolean",
  );
}

function isGraphChangeSetResponse(
  value: unknown,
  expectedProjectId?: string,
): value is GraphChangeSetResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "project_id",
    "correlation_id",
    "base_graph_revision_id",
    "changeset_sha256",
    "snapshot_hash",
    "diff",
    "risk_summary",
    "state",
    "applied_graph_revision_id",
    "application_target",
    "impact",
    "created_branch_id",
    "applied_branch_id",
    "created_at",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isUuid(fields.project_id) &&
      (expectedProjectId === undefined || fields.project_id === expectedProjectId) &&
      isUuid(fields.correlation_id) &&
      isUuid(fields.base_graph_revision_id) &&
      typeof fields.changeset_sha256 === "string" &&
      /^[0-9a-f]{64}$/i.test(fields.changeset_sha256) &&
      typeof fields.snapshot_hash === "string" &&
      /^[0-9a-f]{64}$/i.test(fields.snapshot_hash) &&
      isRecord(fields.diff) &&
      isRecord(fields.risk_summary) &&
      (fields.state === "PENDING_APPROVAL" ||
        fields.state === "APPROVED" ||
        fields.state === "APPLIED" ||
        fields.state === "REJECTED") &&
      (fields.applied_graph_revision_id === null ||
        isUuid(fields.applied_graph_revision_id)) &&
      isApplicationTarget(fields.application_target) &&
      isStaleImpact(fields.impact) &&
      isNullableUuid(fields.created_branch_id) &&
      isNullableUuid(fields.applied_branch_id) &&
      isDateTimeString(fields.created_at),
  );
}

function isBranchWorkspaceResponse(
  value: unknown,
  expectedProjectId?: string,
): value is BranchWorkspaceResponse {
  const fields = readPlainDataRecord(value, [
    "project_id",
    "graph_document_id",
    "branches",
    "selection",
    "latest_promotion",
    "can_undo_latest_promotion",
  ]);
  if (
    !fields ||
    !isUuid(fields.project_id) ||
    (expectedProjectId !== undefined && fields.project_id !== expectedProjectId) ||
    !isUuid(fields.graph_document_id) ||
    !isSelectionPointer(fields.selection, fields.graph_document_id) ||
    typeof fields.can_undo_latest_promotion !== "boolean"
  ) {
    return false;
  }
  const branches = readDenseArray(fields.branches, 5_000);
  if (!branches || branches.length === 0) return false;
  const selection = fields.selection as Record<string, unknown>;
  if (!branches.every((branch) => isBranchSummary(branch, fields.graph_document_id as string))) {
    return false;
  }
  const ids = branches.map((branch) => (branch as Record<string, unknown>).id);
  if (new Set(ids).size !== ids.length) return false;
  const selected = branches.filter(
    (branch) => (branch as Record<string, unknown>).is_selected === true,
  );
  const selectedBranch = branches.find(
    (branch) => (branch as Record<string, unknown>).id === selection.branch_id,
  ) as Record<string, unknown> | undefined;
  const selectedHead = selectedBranch?.head as Record<string, unknown> | undefined;
  return Boolean(
    selected.length === 1 &&
      selected[0] === selectedBranch &&
      selectedHead?.graph_revision_id === selection.graph_revision_id &&
      (fields.latest_promotion === null ||
        isPromotionResponse(fields.latest_promotion, fields.project_id as string)),
  );
}

function isBranchGraphResponse(value: unknown, expectedBranchId?: string): value is BranchGraphResponse {
  const fields = readPlainDataRecord(value, [
    "branch",
    "selection",
    "revision",
    "materialization",
  ]);
  if (!fields || !isRecord(fields.branch) || !isUuid(fields.branch.graph_document_id)) {
    return false;
  }
  const graphDocumentId = fields.branch.graph_document_id;
  return Boolean(
    isBranchSummary(fields.branch, graphDocumentId) &&
      (expectedBranchId === undefined || fields.branch.id === expectedBranchId) &&
      isSelectionPointer(fields.selection, graphDocumentId) &&
      isGraphResponse(fields.revision) &&
      isRecord(fields.revision) &&
      isUuid(fields.revision.revision_id) &&
      fields.revision.graph_document_id === graphDocumentId &&
      isRecord(fields.branch.head) &&
      fields.branch.head.graph_revision_id === fields.revision.revision_id &&
      isSha256(fields.revision.snapshot_hash) &&
      isMaterializationSummary(fields.materialization),
  );
}

function isPromotionImpact(value: unknown): boolean {
  const fields = readPlainDataRecord(value, [
    "source_branch_id",
    "source_graph_revision_id",
    "source_graph_revision_sha256",
    "target_branch_id",
    "target_graph_revision_id",
    "target_graph_revision_sha256",
    "materialization",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.source_branch_id) &&
      isUuid(fields.source_graph_revision_id) &&
      isSha256(fields.source_graph_revision_sha256) &&
      isUuid(fields.target_branch_id) &&
      isUuid(fields.target_graph_revision_id) &&
      isSha256(fields.target_graph_revision_sha256) &&
      isMaterializationSummary(fields.materialization),
  );
}

function isPromotionResponse(value: unknown, expectedProjectId?: string): value is PromotionResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "project_id",
    "correlation_id",
    "client_mutation_id",
    "kind",
    "source_branch_id",
    "source_graph_revision_id",
    "source_branch_head_version",
    "target_branch_id",
    "target_graph_revision_id",
    "target_branch_head_version",
    "expected_selection_version",
    "undo_of_promotion_id",
    "impact",
    "impact_sha256",
    "promotion_sha256",
    "risk_summary",
    "state",
    "approval_id",
    "created_at",
    "applied_at",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isUuid(fields.project_id) &&
      (expectedProjectId === undefined || fields.project_id === expectedProjectId) &&
      isUuid(fields.correlation_id) &&
      isUuid(fields.client_mutation_id) &&
      (fields.kind === "PROMOTE" || fields.kind === "UNDO_PROMOTION") &&
      isUuid(fields.source_branch_id) &&
      isUuid(fields.source_graph_revision_id) &&
      isPositiveVersion(fields.source_branch_head_version) &&
      isUuid(fields.target_branch_id) &&
      isUuid(fields.target_graph_revision_id) &&
      isPositiveVersion(fields.target_branch_head_version) &&
      isPositiveVersion(fields.expected_selection_version) &&
      isNullableUuid(fields.undo_of_promotion_id) &&
      isPromotionImpact(fields.impact) &&
      isSha256(fields.impact_sha256) &&
      isSha256(fields.promotion_sha256) &&
      isRecord(fields.risk_summary) &&
      (fields.state === "PENDING_APPROVAL" ||
        fields.state === "APPROVED" ||
        fields.state === "APPLIED" ||
        fields.state === "REJECTED" ||
        fields.state === "STALE") &&
      isNullableUuid(fields.approval_id) &&
      isDateTimeString(fields.created_at) &&
      (fields.applied_at === null || isDateTimeString(fields.applied_at)),
  );
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/i.test(value);
}

function isControlConfigRevisionResponse(value: unknown): value is ControlConfigRevisionResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "sequence",
    "dry_run_max_tasks",
    "config_sha256",
    "created_at",
  ], ["active_canvas_agent_profile_revision_id", "active_prompt_editor_profile_revision_id"]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      Number.isInteger(fields.sequence) &&
      (fields.sequence as number) >= 1 &&
      Number.isInteger(fields.dry_run_max_tasks) &&
      (fields.dry_run_max_tasks as number) >= 1 &&
      (fields.dry_run_max_tasks as number) <= 100 &&
      (fields.active_canvas_agent_profile_revision_id === undefined ||
        fields.active_canvas_agent_profile_revision_id === null ||
        isUuid(fields.active_canvas_agent_profile_revision_id)) &&
      (fields.active_prompt_editor_profile_revision_id === undefined ||
        fields.active_prompt_editor_profile_revision_id === null ||
        isUuid(fields.active_prompt_editor_profile_revision_id)) &&
      isSha256(fields.config_sha256) &&
      isDateTimeString(fields.created_at),
  );
}

function isConfigChangeSetResponse(value: unknown): value is ConfigChangeSetResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "audit_project_id",
    "correlation_id",
    "base_config_revision_id",
    "changeset_sha256",
    "operations",
    "risk_summary",
    "state",
    "created_at",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isUuid(fields.audit_project_id) &&
      isUuid(fields.correlation_id) &&
      isUuid(fields.base_config_revision_id) &&
      isSha256(fields.changeset_sha256) &&
      readDenseArray(fields.operations, 32) !== null &&
      isRecord(fields.risk_summary) &&
      (fields.state === "PENDING_APPROVAL" ||
        fields.state === "APPROVED" ||
        fields.state === "APPLIED" ||
        fields.state === "REJECTED") &&
      isDateTimeString(fields.created_at),
  );
}

export class C3ApiError extends ApiRequestError {
  readonly code: string;
  readonly problem: Readonly<Record<string, unknown>>;

  constructor(status: number, code: string, problem: Record<string, unknown>) {
    super(status, code, false);
    this.name = "C3ApiError";
    this.code = code;
    this.problem = problem;
  }
}

function isModelProfileResponse(value: unknown): value is ModelProfileResponse {
  const fields = readPlainDataRecord(value, [
    "id", "profile_key", "role", "active_revision_id", "created_at",
  ]);
  return Boolean(fields && isUuid(fields.id) && isBoundedString(fields.profile_key, 200) &&
    (fields.role === "CANVAS_AGENT" || fields.role === "PROMPT_EDITOR") &&
    isNullableUuid(fields.active_revision_id) &&
    isDateTimeString(fields.created_at));
}

function isModelProfileRevisionResponse(value: unknown): value is ModelProfileRevisionResponse {
  const fields = readPlainDataRecord(value, [
    "id", "sequence", "provider_id", "declaration", "declaration_sha256",
    "credential_available", "created_at",
  ]);
  return Boolean(fields && isUuid(fields.id) && isPositiveVersion(fields.sequence) &&
    isUuid(fields.provider_id) && isRecord(fields.declaration) &&
    isSha256(fields.declaration_sha256) && typeof fields.credential_available === "boolean" &&
    isDateTimeString(fields.created_at));
}

const agentContentCategories = new Set([
  "USER_INSTRUCTION", "SELECTED_NODE_TEXT", "SELECTED_TEXT",
  "ADJACENT_NODE_TEXT", "RESOURCE_TEXT_METADATA",
  "PROMPT_EXAMPLE_DOCUMENT", "PROMPT_TARGET_DOCUMENT", "PROMPT_PROFILE_RULES",
  "PRESERVE_CONSTRAINTS",
]);

function isAgentReviewContext(value: unknown): boolean {
  const compact = readPlainDataRecord(value, ["category", "content"], ["characters"]);
  const fields = compact ?? readPlainDataRecord(value, [
    "category", "content", "original_characters", "sent_characters", "truncated",
  ]);
  if (!fields || typeof fields.category !== "string" ||
      !agentContentCategories.has(fields.category) || fields.content === undefined) return false;
  if (compact) {
    if (compact.characters !== undefined && !isNonNegativeSafeInteger(compact.characters)) return false;
  } else {
    const { original_characters: original, sent_characters: sent, truncated } = fields;
    if (!isNonNegativeSafeInteger(original) || !isNonNegativeSafeInteger(sent) || sent > original ||
        truncated !== (sent < original) || typeof fields.content !== "string" ||
        Array.from(fields.content).length !== sent) return false;
  }
  try {
    return JSON.stringify(fields.content).length <= 1_000_000;
  } catch {
    return false;
  }
}

function isAgentEgressPlan(value: unknown): boolean {
  const fields = readPlainDataRecord(value, [
    "mode", "endpoint", "locality", "endpoint_identity_sha256", "external_data_classes",
    "task_count", "gpu_count", "media_execution", "cost_mode", "currency", "cost_bound_minor", "data_residency",
    "retention_policy", "max_retries", "request_sha256", "plan_sha256", "preview",
  ]);
  const classes = fields ? readDenseArray(fields.external_data_classes, 9) : null;
  const preview = fields && isRecord(fields.preview) ? fields.preview : null;
  const contexts = preview ? readDenseArray(preview.contexts, 9) : null;
  return Boolean(fields && fields.mode === "MODEL_PROVIDER" && isBoundedString(fields.endpoint, 2_048) &&
    (fields.locality === "LOCAL_LOOPBACK" || fields.locality === "REMOTE_HTTPS") &&
    isSha256(fields.endpoint_identity_sha256) && classes &&
    classes.every((item) => typeof item === "string" && agentContentCategories.has(item)) &&
    new Set(classes).size === classes.length && fields.task_count === 1 &&
    fields.gpu_count === 0 && fields.media_execution === false &&
    (fields.cost_mode === "HARD" || fields.cost_mode === "ESTIMATED" || fields.cost_mode === "UNKNOWN") &&
    (fields.currency === null || isBoundedString(fields.currency, 8)) &&
    (fields.cost_bound_minor === null || (Number.isInteger(fields.cost_bound_minor) && (fields.cost_bound_minor as number) >= 0)) &&
    ["LOCAL_DEVICE", "CN", "US", "EU", "UNKNOWN"].includes(String(fields.data_residency)) &&
    ["NO_RETENTION_DECLARED", "PROVIDER_DECLARED", "UNKNOWN"].includes(String(fields.retention_policy)) &&
    fields.max_retries === 0 && isSha256(fields.request_sha256) && isSha256(fields.plan_sha256) &&
    preview && contexts && contexts.every(isAgentReviewContext) &&
    new Set(contexts.map((item) => (item as Record<string, unknown>).category)).size === contexts.length &&
    contexts.every((item) => classes.includes((item as Record<string, unknown>).category)));
}

function isAgentRunRequest(
  value: unknown,
  projectId: string,
  expectedOperation?: "AGENT_PROPOSAL" | "PROMPT_PROFILE_EXTRACTION" | "PROMPT_BATCH_ITEM",
): boolean {
  const fields = readPlainDataRecord(value, [
    "id", "project_id", "correlation_id", "operation_kind", "model_profile_revision_id",
    "branch_id", "branch_head_version", "selection_version", "scope_sha256", "request_sha256",
    "egress_plan_sha256", "egress_plan", "state",
  ]);
  return Boolean(fields && isUuid(fields.id) && fields.project_id === projectId &&
    isUuid(fields.correlation_id) &&
    ["AGENT_PROPOSAL", "PROMPT_PROFILE_EXTRACTION", "PROMPT_BATCH_ITEM"].includes(String(fields.operation_kind)) &&
    (expectedOperation === undefined || fields.operation_kind === expectedOperation) &&
    isUuid(fields.model_profile_revision_id) && isUuid(fields.branch_id) &&
    isPositiveVersion(fields.branch_head_version) && isPositiveVersion(fields.selection_version) &&
    isSha256(fields.scope_sha256) && isSha256(fields.request_sha256) &&
    isSha256(fields.egress_plan_sha256) && isAgentEgressPlan(fields.egress_plan) &&
    ["PENDING_APPROVAL", "APPROVED", "REJECTED", "TERMINAL"].includes(String(fields.state)));
}

function isAgentOutput(value: unknown): boolean {
  if (value === null) return true;
  const kind = isRecord(value) ? value.kind : null;
  if (kind === "graph_change_set_draft") {
    const fields = readPlainDataRecord(value, ["kind", "schema_version", "operation", "summary"]);
    const operation = fields ? readPlainDataRecord(fields.operation, ["op", "node_id", "replacement_text"]) : null;
    return Boolean(fields && fields.schema_version === 1 && operation && operation.op === "replace_text" &&
      isUuid(operation.node_id) && typeof operation.replacement_text === "string" &&
      isBoundedString(fields.summary, 500));
  }
  if (kind === "run_request_draft") {
    const fields = readPlainDataRecord(value, ["kind", "schema_version", "operation_kind", "target_node_ids", "task_count", "reason"]);
    const targets = fields ? readDenseArray(fields.target_node_ids, 1) : null;
    return Boolean(fields && fields.schema_version === 1 && fields.operation_kind === "DRY_RUN" &&
      targets?.length === 1 && targets.every(isUuid) && fields.task_count === 1 &&
      isBoundedString(fields.reason, 500));
  }
  return false;
}

function isAgentProposalResponse(value: unknown, projectId: string): value is AgentProposalResponse {
  const fields = readPlainDataRecord(value, [
    "id", "project_id", "run_request", "state", "output", "graph_change_set",
    "failure_code", "created_at", "updated_at",
  ]);
  return Boolean(fields && isUuid(fields.id) && fields.project_id === projectId &&
    isAgentRunRequest(fields.run_request, projectId, "AGENT_PROPOSAL") &&
    ["PENDING_APPROVAL", "PENDING_EXECUTION", "DISPATCHING", "SUCCEEDED", "FAILED", "UNKNOWN_TERMINAL", "REJECTED"].includes(String(fields.state)) &&
    isAgentOutput(fields.output) &&
    (fields.graph_change_set === null || isGraphChangeSetResponse(fields.graph_change_set, projectId)) &&
    (fields.failure_code === null || isBoundedString(fields.failure_code, 200)) &&
    isDateTimeString(fields.created_at) && isDateTimeString(fields.updated_at));
}

function isEgressPlanResponse(value: unknown): value is EgressPlanResponse {
  const fields = readPlainDataRecord(value, [
    "mode",
    "endpoint",
    "external_data_classes",
    "task_count",
    "cost_bound",
    "gpu_count",
    "request_sha256",
    "plan_sha256",
  ]);
  return Boolean(
    fields &&
      fields.mode === "NONE" &&
      fields.endpoint === null &&
      Array.isArray(fields.external_data_classes) &&
      fields.external_data_classes.length === 0 &&
      Number.isInteger(fields.task_count) &&
      (fields.task_count as number) >= 1 &&
      (fields.task_count as number) <= 100 &&
      fields.cost_bound === 0 &&
      fields.gpu_count === 0 &&
      isSha256(fields.request_sha256) &&
      isSha256(fields.plan_sha256),
  );
}

function isRunRequestResponse(value: unknown): value is RunRequestResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "project_id",
    "correlation_id",
    "request_sha256",
    "egress_plan_sha256",
    "egress_plan",
    "state",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isUuid(fields.project_id) &&
      isUuid(fields.correlation_id) &&
      isSha256(fields.request_sha256) &&
      isSha256(fields.egress_plan_sha256) &&
      isEgressPlanResponse(fields.egress_plan) &&
      (fields.state === "PENDING_APPROVAL" ||
        fields.state === "APPROVED" ||
        fields.state === "REJECTED" ||
        fields.state === "TERMINAL"),
  );
}

function isRunAuthorizationResponse(value: unknown): value is RunAuthorizationResponse {
  const fields = readPlainDataRecord(value, ["id", "state", "expires_at"]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      (fields.state === "APPROVED" ||
        fields.state === "CONSUMED" ||
        fields.state === "EXPIRED" ||
        fields.state === "INVALIDATED") &&
      isDateTimeString(fields.expires_at),
  );
}

function isDryRunReceiptResponse(value: unknown): value is DryRunReceiptResponse {
  const fields = readPlainDataRecord(value, [
    "id",
    "run_request_id",
    "terminal_state",
    "created_at",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.id) &&
      isUuid(fields.run_request_id) &&
      (fields.terminal_state === "DRY_RUN_SUCCEEDED" ||
        fields.terminal_state === "DRY_RUN_VALIDATION_FAILED") &&
      isDateTimeString(fields.created_at),
  );
}

function isExperienceEventResponse(value: unknown): value is ExperienceEventResponse {
  const fields = readPlainDataRecord(value, [
    "event_id",
    "stream_position",
    "occurred_at",
    "project_id",
    "correlation_id",
    "causation_event_id",
    "actor_kind",
    "actor_id",
    "subject_type",
    "subject_id",
    "event_type",
    "schema_version",
    "payload",
    "payload_sha256",
    "envelope_sha256",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.event_id) &&
      Number.isInteger(fields.stream_position) &&
      (fields.stream_position as number) >= 1 &&
      isDateTimeString(fields.occurred_at) &&
      isUuid(fields.project_id) &&
      isUuid(fields.correlation_id) &&
      (fields.causation_event_id === null || isUuid(fields.causation_event_id)) &&
      (fields.actor_kind === "HUMAN" ||
        fields.actor_kind === "AI_CANVAS" ||
        fields.actor_kind === "SYSTEM") &&
      isNonEmptyString(fields.actor_id) &&
      isNonEmptyString(fields.subject_type) &&
      isUuid(fields.subject_id) &&
      typeof fields.event_type === "string" &&
      fields.schema_version === 1 &&
      isRecord(fields.payload) &&
      isSha256(fields.payload_sha256) &&
      isSha256(fields.envelope_sha256),
  );
}

function isExperienceEventPage(value: unknown): value is ExperienceEventPage {
  const fields = readPlainDataRecord(value, ["events", "next_after", "high_watermark"]);
  return Boolean(
    fields &&
      Array.isArray(fields.events) &&
      fields.events.every(isExperienceEventResponse) &&
      Number.isInteger(fields.next_after) &&
      (fields.next_after as number) >= 0 &&
      Number.isInteger(fields.high_watermark) &&
      (fields.high_watermark as number) >= 0,
  );
}

function isEventCursorResponse(value: unknown): value is EventCursorResponse {
  const fields = readPlainDataRecord(value, ["consumer_key", "last_ack_stream_position"]);
  return Boolean(
    fields &&
      isNonEmptyString(fields.consumer_key) &&
      Number.isInteger(fields.last_ack_stream_position) &&
      (fields.last_ack_stream_position as number) >= 0,
  );
}

function parseSessionChannelMessage(value: unknown): SessionChannelMessage | null {
  if (!isRecord(value) || typeof value.type !== "string") return null;
  if (value.type === "session-rotated" && isNonEmptyString(value.token)) {
    return { type: value.type, token: value.token };
  }
  if (value.type === "request" && isNonEmptyString(value.requestId)) {
    return { type: value.type, requestId: value.requestId };
  }
  if (
    value.type === "response"
    && isNonEmptyString(value.requestId)
    && isNonEmptyString(value.token)
  ) {
    return { type: value.type, requestId: value.requestId, token: value.token };
  }
  return null;
}

function cancelCurrentAttestation(): void {
  attestationEpoch += 1;
  const attestation = currentAttestation;
  currentAttestation = null;
  attestation?.supersede(null);
}

function closeSessionChannel(): void {
  cancelCurrentAttestation();
  sessionChannel?.close();
  sessionChannel = undefined;
}

function handlePageHide(): void {
  closeSessionChannel();
}

function getSessionChannel(): BroadcastChannel | null {
  if (sessionChannel !== undefined) return sessionChannel;
  if (typeof BroadcastChannel === "undefined") {
    sessionChannel = null;
    return null;
  }
  sessionChannel = new BroadcastChannel(CHANNEL_NAME);
  sessionChannel.addEventListener("message", (event: MessageEvent) => {
    const message = parseSessionChannelMessage(event.data as unknown);
    if (!message) return;
    if (message.type === "session-rotated") {
      const rotationAttestation = startSessionAttestation(
        (attestation) => attestToken(message.token, attestation),
        false,
      );
      void rotationAttestation.result.catch(() => {
        // An untrusted peer token must not disturb the currently committed session.
      });
      return;
    }
    const token = getSessionToken();
    if (message.type === "request" && token) {
      sessionChannel?.postMessage({ type: "response", requestId: message.requestId, token });
    }
  });
  return sessionChannel;
}

function requestTokenFromOpenTab(
  attestation: SessionAttestation,
): Promise<string | null> {
  assertCurrentAttestation(attestation);
  const channel = getSessionChannel();
  if (!channel) return Promise.resolve(null);
  const requestId = crypto.randomUUID();
  return new Promise((resolve) => {
    const finish = (token: string | null) => {
      clearTimeout(timeout);
      channel.removeEventListener("message", onMessage);
      resolve(token);
    };
    const onMessage = (event: MessageEvent) => {
      const message = parseSessionChannelMessage(event.data as unknown);
      if (message?.type === "response" && message.requestId === requestId) {
        finish(message.token);
      }
    };
    const timeout = setTimeout(() => finish(null), 1500);
    channel.addEventListener("message", onMessage);
    channel.postMessage({ type: "request", requestId });
  });
}

function takeBootstrapFragment(): BootstrapFragment {
  const params = new URLSearchParams(location.hash.slice(1));
  if (!params.has("bootstrap")) return { present: false, token: null };
  const bootstrap = params.get("bootstrap") ?? "";
  params.delete("bootstrap");
  const remainingFragment = params.toString();
  history.replaceState(
    history.state,
    "",
    `${location.pathname}${location.search}${remainingFragment ? `#${remainingFragment}` : ""}`,
  );
  return { present: true, token: bootstrap };
}

class SessionStatusHttpError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Local session status request failed (${status}).`);
    this.status = status;
  }
}

class SessionAttestationCancelledError extends Error {
  constructor() {
    super("Local session attestation cancelled.");
  }
}

function assertCurrentAttestation(attestation: SessionAttestation): void {
  if (currentAttestation?.epoch !== attestation.epoch) {
    throw new SessionAttestationCancelledError();
  }
}

async function verifySessionStatus(
  token: string,
  attestation: SessionAttestation,
): Promise<SessionMode> {
  const response = await fetch("/api/v1/session/status", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
    redirect: "error",
  });
  assertCurrentAttestation(attestation);
  if (response.status !== 200) throw new SessionStatusHttpError(response.status);
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    assertCurrentAttestation(attestation);
    throw new Error("Invalid local session status response.");
  }
  assertCurrentAttestation(attestation);
  if (!isSessionStatusResponse(payload)) {
    throw new Error("Invalid local session status response.");
  }
  return payload.mode;
}

function commitSession(token: string, broadcast = true): void {
  sessionStorage.setItem(STORAGE_KEY, token);
  if (broadcast) {
    getSessionChannel()?.postMessage({ type: "session-rotated", token });
  }
}

function followReplacement(
  attestation: SessionAttestation,
  replacement: SessionAttestation | null,
): Promise<AttestedSession> {
  if (!replacement || replacement.epoch <= attestation.epoch) {
    throw new SessionAttestationCancelledError();
  }
  return replacement.result;
}

async function settleSessionAttestation(
  attestation: SessionAttestation,
  candidate: SessionCandidate,
  broadcast: boolean,
  replacementSignal: Promise<SessionAttestation | null>,
): Promise<AttestedSession> {
  let candidateResult: Promise<AttestedSession>;
  try {
    candidateResult = candidate(attestation);
  } catch (error) {
    candidateResult = Promise.reject(error);
  }
  const completedCandidate = candidateResult.then(
    (session) => ({ kind: "completed" as const, session }),
    (error: unknown) => ({ kind: "failed" as const, error }),
  );
  const replacement = replacementSignal.then((next) => ({
    kind: "superseded" as const,
    next,
  }));
  const outcome = await Promise.race([completedCandidate, replacement]);

  if (outcome.kind === "superseded") {
    return followReplacement(attestation, outcome.next);
  }
  if (currentAttestation?.epoch !== attestation.epoch) {
    return followReplacement(attestation, await replacementSignal);
  }
  if (outcome.kind === "failed") throw outcome.error;

  commitSession(outcome.session.token, broadcast);
  return outcome.session;
}

function startSessionAttestation(
  candidate: SessionCandidate,
  broadcast = true,
): SessionAttestation {
  const epoch = ++attestationEpoch;
  let replacementSettled = false;
  let resolveReplacement!: (replacement: SessionAttestation | null) => void;
  const replacementSignal = new Promise<SessionAttestation | null>((resolve) => {
    resolveReplacement = resolve;
  });
  let resolveResult!: (session: AttestedSession) => void;
  let rejectResult!: (error: unknown) => void;
  const result = new Promise<AttestedSession>((resolve, reject) => {
    resolveResult = resolve;
    rejectResult = reject;
  });
  const attestation: SessionAttestation = {
    epoch,
    result,
    supersede(replacement) {
      if (replacementSettled) return;
      replacementSettled = true;
      resolveReplacement(replacement);
    },
  };

  const previous = currentAttestation;
  currentAttestation = attestation;
  previous?.supersede(attestation);
  void settleSessionAttestation(
    attestation,
    candidate,
    broadcast,
    replacementSignal,
  ).then(resolveResult, rejectResult);
  return attestation;
}

async function attestToken(
  token: string,
  attestation: SessionAttestation,
): Promise<AttestedSession> {
  const mode = await verifySessionStatus(token, attestation);
  assertCurrentAttestation(attestation);
  return { token, mode };
}

async function requestDevelopmentSession(
  attestation: SessionAttestation,
): Promise<string | null> {
  const response = await fetch("/api/v1/session/development", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    redirect: "error",
  });
  assertCurrentAttestation(attestation);
  if (response.status === 200) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      assertCurrentAttestation(attestation);
      throw new Error("Invalid local session response.");
    }
    assertCurrentAttestation(attestation);
    if (!isSessionResponse(payload)) {
      throw new Error("Invalid local session response.");
    }
    return payload.access_token;
  }
  if (response.status === 403) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      assertCurrentAttestation(attestation);
      throw new Error("Local development session request failed (403).");
    }
    assertCurrentAttestation(attestation);
    if (isRecord(payload) && payload.code === "development_session_disabled") {
      return null;
    }
    const code = isRecord(payload) && isNonEmptyString(payload.code)
      ? payload.code
      : "unknown_error";
    throw new Error(`Local development session was rejected: ${code}.`);
  }
  throw new Error(`Local development session request failed (${response.status}).`);
}

async function performBootstrap(
  fragment: BootstrapFragment,
  attestation: SessionAttestation,
): Promise<AttestedSession> {
  assertCurrentAttestation(attestation);
  getSessionChannel();
  if (fragment.present) {
    sessionStorage.removeItem(STORAGE_KEY);
    if (!isNonEmptyString(fragment.token)) {
      throw new Error("Missing local bootstrap token. Relaunch AI Canvas.");
    }
    const response = await fetch("/api/v1/session/exchange", {
      method: "POST",
      headers: { "X-Canvas-Bootstrap": fragment.token, "Content-Type": "application/json" },
      body: "{}",
    });
    assertCurrentAttestation(attestation);
    if (!response.ok) throw new Error("Local bootstrap token was rejected.");
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      assertCurrentAttestation(attestation);
      throw new Error("Invalid local session response.");
    }
    assertCurrentAttestation(attestation);
    if (!isSessionResponse(payload)) throw new Error("Invalid local session response.");
    return attestToken(payload.access_token, attestation);
  }

  const storedToken = getSessionToken();
  if (storedToken) {
    sessionStorage.removeItem(STORAGE_KEY);
    try {
      return await attestToken(storedToken, attestation);
    } catch (error) {
      assertCurrentAttestation(attestation);
      if (
        !(error instanceof SessionStatusHttpError)
        || error.status !== 401
      ) {
        throw error;
      }
    }
  }

  const developmentToken = await requestDevelopmentSession(attestation);
  assertCurrentAttestation(attestation);
  if (developmentToken) return attestToken(developmentToken, attestation);

  assertCurrentAttestation(attestation);
  const peerToken = await requestTokenFromOpenTab(attestation);
  assertCurrentAttestation(attestation);
  if (!peerToken) throw new Error("Missing local bootstrap token. Relaunch AI Canvas.");
  return attestToken(peerToken, attestation);
}

export function bootstrapSession(): Promise<SessionMode> {
  const fragment = takeBootstrapFragment();
  if (bootstrapInFlight) return bootstrapInFlight;
  const attestation = startSessionAttestation(
    (current) => performBootstrap(fragment, current),
  );
  bootstrapInFlight = attestation.result
    .then((session) => session.mode)
    .finally(() => {
      bootstrapInFlight = null;
    });
  return bootstrapInFlight;
}

function resolveApiUrl(path: string): URL {
  const url = new URL(path, location.href);
  if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) {
    throw new TypeError("API requests require a same-origin /api/ URL.");
  }
  return url;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = resolveApiUrl(path);
  const token = getSessionToken();
  if (!token) throw new SessionExpiredError();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, { ...init, headers, redirect: "error" });
  if (response.status === 401) {
    sessionStorage.removeItem(STORAGE_KEY);
    throw new SessionExpiredError();
  }
  return response;
}

async function requireJson<T>(
  response: Response,
  validate: (value: unknown) => value is T,
): Promise<T> {
  if (!response.ok) {
    let message = `API request failed (${response.status})`;
    try {
      const problem: unknown = await response.json();
      if (isRecord(problem)) {
        if (isNonEmptyString(problem.message)) message = problem.message;
        else if (isNonEmptyString(problem.code)) message = problem.code;
      }
    } catch {
      // Keep the status-based fallback for non-JSON server and proxy failures.
    }
    throw new ApiRequestError(response.status, message);
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiRequestError(502, "Invalid API response from local service", true);
  }
  if (!validate(payload)) {
    throw new ApiRequestError(502, "Invalid API response from local service", true);
  }
  return payload;
}

const c2ProblemCodes = new Set([
  "graph_branch_not_found",
  "promotion_not_found",
  "graph_branch_name_conflict",
  "branch_head_stale",
  "selection_pointer_stale",
  "stale_impact_confirmation_required",
  "promotion_stale",
  "promotion_undo_stale",
  "graph_changeset_undo_stale",
  "graph_changeset_undo_unsupported",
]);

async function throwC2ResponseError(response: Response): Promise<never> {
  let problem: unknown;
  try {
    problem = await response.json();
  } catch {
    throw new ApiRequestError(response.status);
  }
  const currentRevisionId = parseGraphChangeSetConflict(problem);
  if (response.status === 409 && currentRevisionId !== null) {
    throw new GraphChangeSetConflictError(currentRevisionId);
  }
  if (!isRecord(problem) || !isNonEmptyString(problem.code)) {
    throw new ApiRequestError(response.status);
  }
  const plainProblem = { ...problem };
  if (
    response.status === 409 &&
    problem.code === "branch_head_stale" &&
    isUuid(problem.branch_id) &&
    isUuid(problem.current_revision_id) &&
    isPositiveVersion(problem.current_version)
  ) {
    throw new BranchHeadStaleError(
      problem.branch_id,
      problem.current_revision_id,
      problem.current_version,
      plainProblem,
    );
  }
  if (
    response.status === 409 &&
    problem.code === "selection_pointer_stale" &&
    isUuid(problem.current_branch_id) &&
    isUuid(problem.current_revision_id) &&
    isPositiveVersion(problem.current_version)
  ) {
    throw new SelectionPointerStaleError(
      problem.current_branch_id,
      problem.current_revision_id,
      problem.current_version,
      plainProblem,
    );
  }
  if (c2ProblemCodes.has(problem.code)) {
    throw new C2ApiError(response.status, problem.code, plainProblem);
  }
  throw new ApiRequestError(
    response.status,
    isNonEmptyString(problem.message) ? problem.message : problem.code,
  );
}

async function requireC2Json<T>(
  response: Response,
  validate: (value: unknown) => value is T,
): Promise<T> {
  if (!response.ok) return throwC2ResponseError(response);
  return requireJson(response, validate);
}

async function requireGraphChangeSetJson(
  response: Response,
  expectedProjectId: string,
): Promise<GraphChangeSetResponse> {
  return requireC2Json(
    response,
    (value): value is GraphChangeSetResponse =>
      isGraphChangeSetResponse(value, expectedProjectId),
  );
}

async function throwResourceResponseError(response: Response): Promise<never> {
  let problem: unknown;
  try {
    problem = await response.json();
  } catch {
    throw new ApiRequestError(response.status);
  }
  if (!isRecord(problem)) throw new ApiRequestError(response.status);
  const code = isNonEmptyString(problem.code)
    ? problem.code
    : `resource_request_failed_${response.status}`;
  const resourceId = problem.resource_id;
  const currentRevisionId = problem.current_revision_id;
  if (
    response.status === 409 &&
    code === "resource_revision_conflict" &&
    isUuid(resourceId)
  ) {
    if (currentRevisionId !== null && !isUuid(currentRevisionId)) {
      throw new ResourceApiError(response.status, code, { ...problem });
    }
    throw new ResourceRevisionConflictError(
      resourceId,
      currentRevisionId as string | null,
      { ...problem },
    );
  }
  throw new ResourceApiError(response.status, code, { ...problem });
}

async function requireResourceJson<T>(
  response: Response,
  validate: (value: unknown) => value is T,
): Promise<T> {
  if (!response.ok) return throwResourceResponseError(response);
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiRequestError(502, "Invalid API response from local service", true);
  }
  if (!validate(payload)) {
    throw new ApiRequestError(502, "Invalid API response from local service", true);
  }
  return payload;
}

function parseArtifactConflict(value: unknown): {
  importId: string;
  reason: ArtifactImportConflictReason;
} | null {
  const fields = readPlainDataRecord(value, ["code", "import_id", "reason"]);
  if (
    !fields ||
    fields.code !== "artifact_import_conflict" ||
    !isUuid(fields.import_id) ||
    typeof fields.reason !== "string" ||
    !artifactConflictReasons.has(fields.reason)
  ) {
    return null;
  }
  return {
    importId: fields.import_id,
    reason: fields.reason as ArtifactImportConflictReason,
  };
}

function parseArtifactFailure(value: unknown): {
  importId: string;
  failureCode: ArtifactImportFailureCode;
} | null {
  const fields = readPlainDataRecord(value, ["code", "import_id", "failure_code"]);
  if (
    !fields ||
    fields.code !== "artifact_import_failed" ||
    !isUuid(fields.import_id) ||
    typeof fields.failure_code !== "string" ||
    !artifactFailureCodes.has(fields.failure_code)
  ) {
    return null;
  }
  return {
    importId: fields.import_id,
    failureCode: fields.failure_code as ArtifactImportFailureCode,
  };
}

async function throwArtifactResponseError(response: Response): Promise<never> {
  let problem: unknown;
  try {
    problem = await response.json();
  } catch {
    throw new ApiRequestError(response.status);
  }
  if (response.status === 409) {
    const conflict = parseArtifactConflict(problem);
    if (conflict) {
      throw new ArtifactImportConflictError(conflict.importId, conflict.reason);
    }
  }
  if (response.status === 422) {
    const failure = parseArtifactFailure(problem);
    if (failure) {
      throw new ArtifactImportFailedError(failure.importId, failure.failureCode);
    }
  }
  let message = `API request failed (${response.status})`;
  if (isRecord(problem)) {
    if (isNonEmptyString(problem.message)) message = problem.message;
    else if (isNonEmptyString(problem.code)) message = problem.code;
  }
  throw new ApiRequestError(response.status, message);
}

async function requireArtifactJson<T>(
  response: Response,
  validate: (value: unknown) => value is T,
): Promise<T> {
  if (!response.ok) return throwArtifactResponseError(response);
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiRequestError(502, "Invalid API response from local service", true);
  }
  if (!validate(payload)) {
    throw new ApiRequestError(502, "Invalid API response from local service", true);
  }
  return payload;
}

function projectPath(projectId: string): string {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(projectId)) {
    throw new TypeError("Project id must be a single path segment");
  }
  return `/api/v1/projects/${encodeURIComponent(projectId)}`;
}

function uuidPathSegment(value: string): string {
  if (!isUuid(value)) throw new TypeError("Artifact id must be a UUID path segment");
  return encodeURIComponent(value);
}

function artifactImportPath(projectId: string, importId?: string): string {
  const base = `${projectPath(projectId)}/artifact-imports`;
  return importId === undefined ? base : `${base}/${uuidPathSegment(importId)}`;
}

const imageMimeHints = new Set(["image/jpeg", "image/png", "image/webp"]);
const videoMimeHints = new Set([
  "video/mp4",
  "video/webm",
  "video/quicktime",
  "video/x-matroska",
]);
const imageExtensionHint = /\.(?:jpe?g|png|webp)$/i;
const videoExtensionHint = /\.(?:mp4|webm|mov|mkv)$/i;

export function inferRequestedMediaKind(file: Pick<File, "name" | "type">): MediaKind | null {
  const declaredType = file.type.trim().toLowerCase();
  if (imageMimeHints.has(declaredType)) return "image";
  if (videoMimeHints.has(declaredType)) return "video";
  if (imageExtensionHint.test(file.name)) return "image";
  if (videoExtensionHint.test(file.name)) return "video";
  return null;
}

export async function createProject(name: string): Promise<ProjectResponse> {
  return requireJson<ProjectResponse>(
    await apiFetch("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
    isProjectResponse,
  );
}

export async function getProject(projectId: string): Promise<ProjectResponse> {
  return requireJson<ProjectResponse>(
    await apiFetch(projectPath(projectId)),
    isProjectResponse,
  );
}

export async function listProjects(): Promise<ProjectResponse[]> {
  return requireJson<ProjectResponse[]>(
    await apiFetch("/api/v1/projects"),
    isProjectResponseList,
  );
}

export async function createResource(
  projectId: string,
  input: CreateResourceRequestBody,
  signal?: AbortSignal,
): Promise<ResourceResponse> {
  return requireResourceJson<ResourceResponse>(
    await apiFetch(`${projectPath(projectId)}/resources`, {
      method: "POST",
      signal,
      body: JSON.stringify(input),
    }),
    isResourceResponse,
  );
}

export async function listResources(input: {
  kind?: ResourceResponse["kind"];
  query?: string;
  tags?: readonly string[];
  includeArchived?: boolean;
  limit?: number;
  signal?: AbortSignal;
} = {}): Promise<ResourceResponse[]> {
  const parameters = new URLSearchParams();
  if (input.kind) parameters.set("kind", input.kind);
  if (input.query?.trim()) parameters.set("query", input.query.trim());
  for (const tag of input.tags ?? []) {
    if (tag.trim()) parameters.append("tags", tag.trim());
  }
  if (input.includeArchived) parameters.set("include_archived", "true");
  parameters.set("limit", String(input.limit ?? 50));
  return requireResourceJson<ResourceResponse[]>(
    await apiFetch(`/api/v1/resources?${parameters.toString()}`, { signal: input.signal }),
    isResourceResponseList,
  );
}

export async function getResource(
  resourceId: string,
  signal?: AbortSignal,
): Promise<ResourceDetailResponse> {
  return requireResourceJson<ResourceDetailResponse>(
    await apiFetch(`/api/v1/resources/${uuidPathSegment(resourceId)}`, { signal }),
    isResourceDetailResponse,
  );
}

export async function createResourceRevision(
  projectId: string,
  resourceId: string,
  input: CreateResourceRevisionRequestBody,
  signal?: AbortSignal,
): Promise<ResourceRevisionResponse> {
  return requireResourceJson<ResourceRevisionResponse>(
    await apiFetch(
      `${projectPath(projectId)}/resources/${uuidPathSegment(resourceId)}/revisions`,
      {
        method: "POST",
        signal,
        body: JSON.stringify(input),
      },
    ),
    isResourceRevisionResponse,
  );
}

export async function archiveResource(
  projectId: string,
  resourceId: string,
  signal?: AbortSignal,
): Promise<ResourceResponse> {
  return requireResourceJson<ResourceResponse>(
    await apiFetch(`${projectPath(projectId)}/resources/${uuidPathSegment(resourceId)}/archive`, {
      method: "POST",
      signal,
      body: "{}",
    }),
    isResourceResponse,
  );
}

export async function listProjectResourceUpdates(
  projectId: string,
  signal?: AbortSignal,
): Promise<ResourceUpdateItem[]> {
  return requireResourceJson<ResourceUpdateItem[]>(
    await apiFetch(`${projectPath(projectId)}/resource-updates`, { signal }),
    isResourceUpdateItemList,
  );
}

function validateArtifactDeclaration(input: ArtifactImportDeclaration): void {
  if (!isUuid(input.clientImportId)) {
    throw new ArtifactDeclarationValidationError("Client import id must be a UUID");
  }
  if (!isBoundedString(input.filename, 512)) {
    throw new ArtifactDeclarationValidationError(
      "Artifact filename must contain 1 to 512 characters",
    );
  }
  if (
    !isPositiveSafeInteger(input.expectedByteSize) ||
    input.expectedByteSize > 64 * 1024 * 1024
  ) {
    throw new ArtifactDeclarationValidationError(
      "Artifact byte size is outside the B1 import limit",
    );
  }
  if (input.requestedMediaKind !== "image" && input.requestedMediaKind !== "video") {
    throw new ArtifactDeclarationValidationError(
      "Artifact media kind must be image or video",
    );
  }
  if (
    input.declaredContentType !== null &&
    !isBoundedString(input.declaredContentType, 255)
  ) {
    throw new ArtifactDeclarationValidationError("Declared content type is invalid");
  }
  if (input.replaceRevisionId !== null && !isUuid(input.replaceRevisionId)) {
    throw new ArtifactDeclarationValidationError(
      "Replacement revision id must be a UUID",
    );
  }
}

export class TranscodeConfirmationRequiredError extends ApiRequestError {
  readonly confirmationToken: string;
  readonly profile: "preview_1080p" | "preview_720p";
  readonly width: number;
  readonly height: number;
  readonly estimatedOutputBytes: number;

  constructor(input: {
    confirmationToken: string;
    profile: "preview_1080p" | "preview_720p";
    width: number;
    height: number;
    estimatedOutputBytes: number;
  }) {
    super(409, "转码需要明确确认。", false);
    this.confirmationToken = input.confirmationToken;
    this.profile = input.profile;
    this.width = input.width;
    this.height = input.height;
    this.estimatedOutputBytes = input.estimatedOutputBytes;
  }
}

function validateLargeUploadDeclaration(input: LargeUploadDeclaration): void {
  if (!isUuid(input.clientImportId)) {
    throw new ArtifactDeclarationValidationError("Large upload client id must be a UUID");
  }
  if (!isBoundedString(input.filename, 512)) {
    throw new ArtifactDeclarationValidationError("Large upload filename must contain 1 to 512 characters");
  }
  if (
    !isPositiveSafeInteger(input.expectedByteSize) ||
    input.expectedByteSize > 10 * 1024 * 1024 * 1024
  ) {
    throw new ArtifactDeclarationValidationError("Large upload byte size is outside the B4 import limit");
  }
  if (input.requestedMediaKind !== "image" && input.requestedMediaKind !== "video") {
    throw new ArtifactDeclarationValidationError("Large upload media kind must be image or video");
  }
  if (
    input.declaredContentType !== null &&
    !isBoundedString(input.declaredContentType, 255)
  ) {
    throw new ArtifactDeclarationValidationError("Large upload content type is invalid");
  }
  if (input.replaceRevisionId !== null && !isUuid(input.replaceRevisionId)) {
    throw new ArtifactDeclarationValidationError("Large upload replacement revision id must be a UUID");
  }
}

function invalidArtifactResponse(): ApiRequestError {
  return new ApiRequestError(502, "Invalid API response from local service", true);
}

export async function createArtifactImport(
  projectId: string,
  input: ArtifactImportDeclaration,
  signal?: AbortSignal,
): Promise<ArtifactImportSession> {
  const normalizedInput: ArtifactImportDeclaration = {
    ...input,
    declaredContentType: input.declaredContentType?.trim() ?? null,
  };
  validateArtifactDeclaration(normalizedInput);
  const session = await requireArtifactJson<ArtifactImportSession>(
    await apiFetch(artifactImportPath(projectId), {
      method: "POST",
      signal,
      body: JSON.stringify({
        client_import_id: normalizedInput.clientImportId,
        filename: normalizedInput.filename,
        expected_byte_size: normalizedInput.expectedByteSize,
        requested_media_kind: normalizedInput.requestedMediaKind,
        declared_content_type: normalizedInput.declaredContentType,
        replace_revision_id: normalizedInput.replaceRevisionId,
      }),
    }),
    isArtifactImportSession,
  );
  if (
    session.source_project_id !== projectId ||
    session.client_import_id !== normalizedInput.clientImportId ||
    session.original_filename !== normalizedInput.filename ||
    session.expected_byte_size !== normalizedInput.expectedByteSize ||
    session.requested_media_kind !== normalizedInput.requestedMediaKind ||
    (session.declared_content_type ?? null) !== normalizedInput.declaredContentType ||
    (session.replace_revision_id ?? null) !== normalizedInput.replaceRevisionId
  ) {
    throw invalidArtifactResponse();
  }
  return session;
}

export async function getArtifactImport(
  projectId: string,
  importId: string,
  signal?: AbortSignal,
): Promise<ArtifactImportSession> {
  const session = await requireArtifactJson<ArtifactImportSession>(
    await apiFetch(artifactImportPath(projectId, importId), { signal }),
    isArtifactImportSession,
  );
  if (session.source_project_id !== projectId || session.import_id !== importId) {
    throw invalidArtifactResponse();
  }
  return session;
}

export async function discardArtifactImportResult(
  input: DiscardArtifactImportResultInput,
  signal?: AbortSignal,
): Promise<DiscardArtifactImportResult> {
  if (!isUuid(input.clientImportId)) {
    throw new TypeError("Client import id must be a UUID");
  }
  if (
    input.expectedRevisionId !== null &&
    !isUuid(input.expectedRevisionId)
  ) {
    throw new TypeError("Expected revision id must be a UUID or null");
  }
  const body: GeneratedDiscardArtifactImportResultRequest = {
    client_import_id: input.clientImportId,
    expected_revision_id: input.expectedRevisionId,
  };
  const response = await apiFetch(
    `${artifactImportPath(input.projectId, input.importId)}/result`,
    {
      method: "DELETE",
      signal,
      body: JSON.stringify(body),
    },
  );
  if (response.status !== 200 && response.status !== 202) {
    if (!response.ok) return throwArtifactResponseError(response);
    throw invalidArtifactResponse();
  }
  const payload =
    await requireArtifactJson<GeneratedDiscardArtifactImportResultResponse>(
      response,
      isDiscardArtifactImportResultResponse,
    );
  const pending = payload.content_cleanup === "pending";
  if ((response.status === 202) !== pending) throw invalidArtifactResponse();
  return Object.freeze({
    httpStatus: response.status,
    outcome: payload.outcome,
    contentCleanup: payload.content_cleanup,
  });
}

export async function uploadArtifactContent(
  projectId: string,
  importId: string,
  content: Blob,
  signal?: AbortSignal,
): Promise<ArtifactRevision> {
  if (!(content instanceof Blob)) throw new TypeError("Artifact content must be a Blob");
  const revision = await requireArtifactJson<ArtifactRevision>(
    await apiFetch(`${artifactImportPath(projectId, importId)}/content`, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: content,
      signal,
    }),
    isArtifactRevision,
  );
  if (
    revision.source_project_id !== projectId ||
    revision.source_kind !== "import" ||
    revision.byte_size !== content.size
  ) {
    throw invalidArtifactResponse();
  }
  return revision;
}

function isLargeUploadSession(value: unknown): value is LargeUploadSession {
  const fields = readPlainDataRecord(value, [
    "upload_id",
    "source_project_id",
    "client_upload_id",
    "requested_media_kind",
    "original_filename",
    "expected_byte_size",
    "received_byte_size",
    "part_size",
    "state",
    "failure_code",
    "created_at",
    "updated_at",
    "expires_at",
    "artifact_revision",
  ]);
  const states = new Set([
    "CREATED",
    "RECEIVING",
    "FINALIZING",
    "COMPLETED",
    "ABORTED",
    "EXPIRED",
    "FAILED",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.upload_id) &&
      isUuid(fields.source_project_id) &&
      isUuid(fields.client_upload_id) &&
      (fields.requested_media_kind === "image" || fields.requested_media_kind === "video") &&
      isBoundedString(fields.original_filename, 512) &&
      isPositiveSafeInteger(fields.expected_byte_size) &&
      isNonNegativeSafeInteger(fields.received_byte_size) &&
      fields.received_byte_size <= fields.expected_byte_size &&
      fields.part_size === 8 * 1024 * 1024 &&
      typeof fields.state === "string" &&
      states.has(fields.state) &&
      (fields.failure_code === null || isNonEmptyString(fields.failure_code)) &&
      isDateTimeString(fields.created_at) &&
      isDateTimeString(fields.updated_at) &&
      isDateTimeString(fields.expires_at) &&
      (fields.artifact_revision === null ||
        (isRecord(fields.artifact_revision) && isUuid(fields.artifact_revision.revision_id))),
  );
}

function isLargeUploadPartReceipt(value: unknown, uploadId: string, partNumber: number): boolean {
  const fields = readPlainDataRecord(value, ["upload_id", "part_number", "received_byte_size"]);
  return Boolean(
    fields &&
      fields.upload_id === uploadId &&
      fields.part_number === partNumber &&
      isNonNegativeSafeInteger(fields.received_byte_size),
  );
}

function isExternalRootGrant(value: unknown): value is ExternalRootGrant {
  const fields = readPlainDataRecord(value, ["grant_id", "display_name"]);
  return Boolean(fields && isUuid(fields.grant_id) && isBoundedString(fields.display_name, 512));
}

function isExternalReference(value: unknown): value is ExternalReference {
  const fields = readPlainDataRecord(value, [
    "revision_id",
    "artifact_id",
    "health",
    "storage_kind",
    "snapshot_id",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.revision_id) &&
      isUuid(fields.artifact_id) &&
      isUuid(fields.snapshot_id) &&
      ["AVAILABLE", "UNAVAILABLE", "DRIFTED", "QUARANTINED", "PURGED"].includes(
        String(fields.health),
      ) &&
      fields.storage_kind === "EXTERNAL_REFERENCE",
  );
}

function isManagedCopy(value: unknown): value is ManagedCopy {
  const fields = readPlainDataRecord(value, ["revision_id", "artifact_id", "storage_kind"]);
  return Boolean(
    fields &&
      isUuid(fields.revision_id) &&
      isUuid(fields.artifact_id) &&
      fields.storage_kind === "MANAGED_BLOB",
  );
}

function isRenditionJob(value: unknown): value is RenditionJob {
  const fields = readPlainDataRecord(value, [
    "job_id",
    "rendition_id",
    "profile",
    "width",
    "height",
    "frames_per_second",
    "estimated_output_bytes",
    "reused",
  ]);
  return Boolean(
    fields &&
      isUuid(fields.job_id) &&
      isUuid(fields.rendition_id) &&
      (fields.profile === "preview_1080p" || fields.profile === "preview_720p") &&
      isPositiveSafeInteger(fields.width) &&
      isPositiveSafeInteger(fields.height) &&
      isFiniteNumber(fields.frames_per_second) &&
      fields.frames_per_second > 0 &&
      fields.frames_per_second <= 30 &&
      isPositiveSafeInteger(fields.estimated_output_bytes) &&
      typeof fields.reused === "boolean",
  );
}

function isStorageSummary(value: unknown): value is StorageSummary {
  const fields = readPlainDataRecord(value, [
    "workspace_quota_bytes",
    "managed_blob_bytes",
    "staging_bytes",
    "quarantine_bytes",
    "active_reservation_bytes",
    "physical_free_bytes",
  ]);
  return Boolean(
    fields &&
      isPositiveSafeInteger(fields.workspace_quota_bytes) &&
      [
        fields.managed_blob_bytes,
        fields.staging_bytes,
        fields.quarantine_bytes,
        fields.active_reservation_bytes,
        fields.physical_free_bytes,
      ].every(isNonNegativeSafeInteger),
  );
}

function isGcPlan(value: unknown): value is GcPlan {
  const fields = readPlainDataRecord(value, ["plan_id", "summary_sha256", "entries"]);
  const entries = fields ? parseDenseArray(fields.entries, 10_000, (entry) => {
    const row = readPlainDataRecord(entry, [
      "entry_id",
      "target_id",
      "reason",
      "logical_reclaim_bytes",
      "physical_reclaim_bytes",
    ]);
    return row &&
      isUuid(row.entry_id) &&
      isUuid(row.target_id) &&
      isNonEmptyString(row.reason) &&
      isNonNegativeSafeInteger(row.logical_reclaim_bytes) &&
      isNonNegativeSafeInteger(row.physical_reclaim_bytes)
      ? row
      : null;
  }) : null;
  return Boolean(
    fields &&
      isUuid(fields.plan_id) &&
      typeof fields.summary_sha256 === "string" &&
      /^[0-9a-f]{64}$/i.test(fields.summary_sha256) &&
      entries !== null,
  );
}

function isQuarantineEntry(value: unknown): value is QuarantineEntry {
  const fields = readPlainDataRecord(value, ["quarantine_id", "target_id"]);
  return Boolean(fields && isUuid(fields.quarantine_id) && isUuid(fields.target_id));
}

function isTranscodeConfirmation(value: unknown): value is {
  confirmation_token: string;
  profile: "preview_1080p" | "preview_720p";
  width: number;
  height: number;
  estimated_output_bytes: number;
} {
  const fields = readPlainDataRecord(value, [
    "code",
    "confirmation_token",
    "profile",
    "width",
    "height",
    "estimated_output_bytes",
  ]);
  return Boolean(
    fields &&
      fields.code === "transcode_confirmation_required" &&
      typeof fields.confirmation_token === "string" &&
      /^[0-9a-f]{64}$/i.test(fields.confirmation_token) &&
      (fields.profile === "preview_1080p" || fields.profile === "preview_720p") &&
      isPositiveSafeInteger(fields.width) &&
      isPositiveSafeInteger(fields.height) &&
      isPositiveSafeInteger(fields.estimated_output_bytes),
  );
}

export async function createLargeUpload(
  projectId: string,
  input: LargeUploadDeclaration,
  signal?: AbortSignal,
): Promise<LargeUploadSession> {
  const normalized = { ...input, declaredContentType: input.declaredContentType?.trim() ?? null };
  validateLargeUploadDeclaration(normalized);
  const session = await requireJson<LargeUploadSession>(
    await apiFetch(`${projectPath(projectId)}/large-uploads`, {
      method: "POST",
      signal,
      body: JSON.stringify({
        client_upload_id: normalized.clientImportId,
        filename: normalized.filename,
        expected_byte_size: normalized.expectedByteSize,
        requested_media_kind: normalized.requestedMediaKind,
        declared_content_type: normalized.declaredContentType,
        replace_revision_id: normalized.replaceRevisionId,
      }),
    }),
    isLargeUploadSession,
  );
  if (session.source_project_id !== projectId || session.client_upload_id !== normalized.clientImportId) {
    throw invalidArtifactResponse();
  }
  return session;
}

export async function putLargeUploadPart(
  uploadId: string,
  partNumber: number,
  content: Blob,
  contentRange: string,
  contentSha256: string,
  signal?: AbortSignal,
): Promise<LargeUploadPartReceipt> {
  const encodedUploadId = uuidPathSegment(uploadId);
  if (!Number.isSafeInteger(partNumber) || partNumber < 0) {
    throw new TypeError("Large upload part number must be a non-negative integer");
  }
  if (!(content instanceof Blob) || content.size < 1) {
    throw new TypeError("Large upload part content must be a non-empty Blob");
  }
  if (!/^bytes \d+-\d+\/\d+$/.test(contentRange) || !/^[0-9a-f]{64}$/.test(contentSha256)) {
    throw new TypeError("Large upload part range or SHA-256 is invalid");
  }
  const payload = await requireJson<Record<string, unknown>>(
    await apiFetch(`/api/v1/large-uploads/${encodedUploadId}/parts/${partNumber}`, {
      method: "PUT",
      signal,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Range": contentRange,
        "X-Content-SHA256": contentSha256,
      },
      body: content,
    }),
    (value): value is Record<string, unknown> => isLargeUploadPartReceipt(value, uploadId, partNumber),
  );
  return Object.freeze({
    uploadId,
    partNumber,
    receivedByteSize: payload.received_byte_size as number,
  });
}

export async function completeLargeUpload(
  uploadId: string,
  signal?: AbortSignal,
): Promise<LargeUploadSession> {
  const encodedUploadId = uuidPathSegment(uploadId);
  const session = await requireJson<LargeUploadSession>(
    await apiFetch(`/api/v1/large-uploads/${encodedUploadId}/complete`, {
      method: "POST",
      signal,
      body: "{}",
    }),
    isLargeUploadSession,
  );
  if (session.upload_id !== uploadId || session.state !== "COMPLETED") {
    throw invalidArtifactResponse();
  }
  return session;
}

export async function getLargeUpload(
  uploadId: string,
  signal?: AbortSignal,
): Promise<LargeUploadSession> {
  const encodedUploadId = uuidPathSegment(uploadId);
  const session = await requireJson<LargeUploadSession>(
    await apiFetch(`/api/v1/large-uploads/${encodedUploadId}`, { signal }),
    isLargeUploadSession,
  );
  if (session.upload_id !== uploadId) throw invalidArtifactResponse();
  return session;
}

export async function abortLargeUpload(
  uploadId: string,
  signal?: AbortSignal,
): Promise<LargeUploadSession> {
  const encodedUploadId = uuidPathSegment(uploadId);
  const session = await requireJson<LargeUploadSession>(
    await apiFetch(`/api/v1/large-uploads/${encodedUploadId}`, {
      method: "DELETE",
      signal,
      body: "{}",
    }),
    isLargeUploadSession,
  );
  if (session.upload_id !== uploadId) throw invalidArtifactResponse();
  return session;
}

export async function getArtifactRevision(
  revisionId: string,
  signal?: AbortSignal,
): Promise<ArtifactRevision> {
  const revision = await requireArtifactJson<ArtifactRevision>(
    await apiFetch(`/api/v1/artifact-revisions/${uuidPathSegment(revisionId)}`, { signal }),
    isArtifactRevision,
  );
  if (revision.revision_id !== revisionId) throw invalidArtifactResponse();
  return revision;
}

export async function listArtifactRevisions(
  artifactId: string,
  signal?: AbortSignal,
): Promise<ArtifactRevision[]> {
  const revisions = await requireArtifactJson<ArtifactRevision[]>(
    await apiFetch(`/api/v1/artifacts/${uuidPathSegment(artifactId)}/revisions`, {
      method: "GET",
      signal,
    }),
    isArtifactRevisionList,
  );
  if (revisions.some((revision) => revision.artifact_id !== artifactId)) {
    throw invalidArtifactResponse();
  }
  return revisions;
}

export async function listShotSlotAssociations(
  projectId: string,
  shotId: string,
  slot: ShotSlot,
  anchorRevisionId: string,
  cursor?: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<ShotSlotAssociationHistoryResponse> {
  const encodedShotId = uuidPathSegment(shotId);
  uuidPathSegment(anchorRevisionId);
  if (slot !== "image" && slot !== "video" && slot !== "reference") {
    throw new TypeError("Shot slot must be image, video, or reference");
  }
  if (cursor !== undefined && !isBoundedString(cursor, 2_048)) {
    throw new TypeError("Association cursor must contain 1 to 2048 characters");
  }
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 50) {
    throw new TypeError("Association history limit must be an integer from 1 to 50");
  }
  const query = new URLSearchParams({
    anchor_revision_id: anchorRevisionId,
    limit: String(limit),
  });
  if (cursor !== undefined) query.set("cursor", cursor);
  return requireJson<ShotSlotAssociationHistoryResponse>(
    await apiFetch(
      `${projectPath(projectId)}/shots/${encodedShotId}/slots/${encodeURIComponent(slot)}/associations?${query.toString()}`,
      { method: "GET", signal },
    ),
    (value): value is ShotSlotAssociationHistoryResponse =>
      isShotSlotAssociationHistoryResponse(
        value,
        projectId,
        shotId,
        slot,
        anchorRevisionId,
        limit,
      ),
  );
}

export async function getArtifactRevisionContent(
  revisionId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const response = await apiFetch(
    `/api/v1/artifact-revisions/${uuidPathSegment(revisionId)}/content`,
    { signal },
  );
  if (!response.ok) return throwArtifactResponseError(response);
  let content: Blob;
  try {
    content = await response.blob();
  } catch {
    throw invalidArtifactResponse();
  }
  if (!(content instanceof Blob)) throw invalidArtifactResponse();
  return content;
}

function isMediaSessionResponse(value: unknown, revisionId: string): boolean {
  const fields = readPlainDataRecord(value, [
    "revision_id",
    "idle_expires_at",
    "absolute_expires_at",
  ]);
  return Boolean(
    fields &&
      fields.revision_id === revisionId &&
      isDateTimeString(fields.idle_expires_at) &&
      isDateTimeString(fields.absolute_expires_at),
  );
}

export async function createMediaStreamUrl(revisionId: string): Promise<string> {
  const encodedRevisionId = uuidPathSegment(revisionId);
  await requireJson(
    await apiFetch("/api/v1/media-sessions", {
      method: "POST",
      body: JSON.stringify({ revision_id: revisionId }),
      credentials: "include",
    }),
    (value): value is Record<string, unknown> => isMediaSessionResponse(value, revisionId),
  );
  return `/api/v1/media/revisions/${encodedRevisionId}`;
}

export async function grantExternalRoot(signal?: AbortSignal): Promise<ExternalRootGrant> {
  return requireJson<ExternalRootGrant>(
    await apiFetch("/api/v1/external-root-grants/select", {
      method: "POST",
      signal,
      body: "{}",
    }),
    isExternalRootGrant,
  );
}

export async function createExternalReference(
  grantId: string,
  input: { projectId: string; requestedMediaKind: MediaKind },
  signal?: AbortSignal,
): Promise<ExternalReference> {
  const encodedGrantId = uuidPathSegment(grantId);
  const projectId = uuidPathSegment(input.projectId);
  if (input.requestedMediaKind !== "image" && input.requestedMediaKind !== "video") {
    throw new TypeError("External reference media kind must be image or video");
  }
  return requireJson<ExternalReference>(
    await apiFetch(`/api/v1/external-root-grants/${encodedGrantId}/references/select`, {
      method: "POST",
      signal,
      body: JSON.stringify({ project_id: projectId, requested_media_kind: input.requestedMediaKind }),
    }),
    isExternalReference,
  );
}

export async function reconfirmExternalReference(
  revisionId: string,
  grantId: string,
  signal?: AbortSignal,
): Promise<ExternalReference> {
  const encodedRevisionId = uuidPathSegment(revisionId);
  const encodedGrantId = uuidPathSegment(grantId);
  return requireJson<ExternalReference>(
    await apiFetch(`/api/v1/artifact-revisions/${encodedRevisionId}/external-reconfirm`, {
      method: "POST",
      signal,
      body: JSON.stringify({ grant_id: encodedGrantId }),
    }),
    isExternalReference,
  );
}

export async function verifyExternalReference(
  revisionId: string,
  signal?: AbortSignal,
): Promise<ExternalReference> {
  const encodedRevisionId = uuidPathSegment(revisionId);
  return requireJson<ExternalReference>(
    await apiFetch(`/api/v1/artifact-revisions/${encodedRevisionId}/external-verify`, {
      method: "POST",
      signal,
      body: "{}",
    }),
    isExternalReference,
  );
}

export async function importExternalCopy(
  revisionId: string,
  signal?: AbortSignal,
): Promise<ManagedCopy> {
  return requireJson<ManagedCopy>(
    await apiFetch(
      `/api/v1/artifact-revisions/${uuidPathSegment(revisionId)}/external-import-copy`,
      { method: "POST", signal, body: "{}" },
    ),
    isManagedCopy,
  );
}

export async function requestRendition(
  revisionId: string,
  profile: "preview_1080p" | "preview_720p",
  confirmationToken: string | null = null,
  signal?: AbortSignal,
): Promise<RenditionJob> {
  const encodedRevisionId = uuidPathSegment(revisionId);
  if (profile !== "preview_1080p" && profile !== "preview_720p") {
    throw new TypeError("Unsupported rendition profile");
  }
  if (confirmationToken !== null && !/^[0-9a-f]{64}$/i.test(confirmationToken)) {
    throw new TypeError("Transcode confirmation token is invalid");
  }
  const response = await apiFetch(`/api/v1/artifact-revisions/${encodedRevisionId}/renditions`, {
    method: "POST",
    signal,
    body: JSON.stringify({ profile, confirmation_token: confirmationToken }),
  });
  if (response.status === 409) {
    let problem: unknown;
    try {
      problem = await response.json();
    } catch {
      throw new ApiRequestError(409);
    }
    if (isTranscodeConfirmation(problem)) {
      throw new TranscodeConfirmationRequiredError({
        confirmationToken: problem.confirmation_token,
        profile: problem.profile,
        width: problem.width,
        height: problem.height,
        estimatedOutputBytes: problem.estimated_output_bytes,
      });
    }
    throw new ApiRequestError(409, "Transcode request conflicted", false);
  }
  return requireJson<RenditionJob>(response, isRenditionJob);
}

export async function storageSummary(signal?: AbortSignal): Promise<StorageSummary> {
  return requireJson<StorageSummary>(
    await apiFetch("/api/v1/storage/summary", { signal }),
    isStorageSummary,
  );
}

export async function createGcPlan(signal?: AbortSignal): Promise<GcPlan> {
  return requireJson<GcPlan>(
    await apiFetch("/api/v1/gc-plans", { method: "POST", signal, body: "{}" }),
    isGcPlan,
  );
}

export async function confirmGcPlan(
  planId: string,
  summarySha256: string,
  signal?: AbortSignal,
): Promise<QuarantineEntry[]> {
  const encodedPlanId = uuidPathSegment(planId);
  if (!/^[0-9a-f]{64}$/i.test(summarySha256)) {
    throw new TypeError("GC plan summary SHA-256 is invalid");
  }
  return requireJson<QuarantineEntry[]>(
    await apiFetch(`/api/v1/gc-plans/${encodedPlanId}/confirm`, {
      method: "POST",
      signal,
      body: JSON.stringify({ summary_sha256: summarySha256 }),
    }),
    (value): value is QuarantineEntry[] =>
      parseDenseArray(value, 10_000, (entry) => (isQuarantineEntry(entry) ? entry : null)) !== null,
  );
}

export async function restoreQuarantine(
  quarantineId: string,
  signal?: AbortSignal,
): Promise<boolean> {
  const encodedQuarantineId = uuidPathSegment(quarantineId);
  return requireJson<{ restored: boolean }>(
    await apiFetch(`/api/v1/quarantine/${encodedQuarantineId}/restore`, {
      method: "POST",
      signal,
      body: "{}",
    }),
    (value): value is { restored: boolean } => {
      const fields = readPlainDataRecord(value, ["restored"]);
      return Boolean(fields && typeof fields.restored === "boolean");
    },
  ).then((result) => result.restored);
}

export async function getGraph(projectId: string): Promise<GraphResponse> {
  return requireJson<GraphResponse>(
    await apiFetch(`${projectPath(projectId)}/graph`),
    isGraphResponse,
  );
}

export async function proposeGraphChangeSet(
  projectId: string,
  input: {
    baseRevisionId: string;
    clientMutationId: string;
    snapshot: GraphSnapshotBody;
    applicationTarget?: BranchApplicationTarget;
  },
): Promise<GraphChangeSetResponse> {
  uuidPathSegment(input.baseRevisionId);
  uuidPathSegment(input.clientMutationId);
  if (!isGraphSnapshot(input.snapshot)) throw new TypeError("Invalid graph snapshot");
  return requireGraphChangeSetJson(
    await apiFetch(`${projectPath(projectId)}/graph-change-sets`, {
      method: "POST",
      body: JSON.stringify({
        kind: "graph_change_set",
        schema_version: 1,
        base_graph_revision_id: input.baseRevisionId,
        client_mutation_id: input.clientMutationId,
        operations: [{ op: "replace_snapshot", snapshot: input.snapshot }],
        ...(input.applicationTarget
          ? { application_target: input.applicationTarget }
          : {}),
      }),
    }),
    projectId,
  );
}

export async function approveGraphChangeSet(
  projectId: string,
  changeSetId: string,
  expectedChangesetSha256: string,
  decision: "APPROVED" | "REJECTED" = "APPROVED",
  confirmedStaleImpactSha256?: string,
): Promise<GraphChangeSetResponse> {
  if (!/^[0-9a-f]{64}$/i.test(expectedChangesetSha256)) {
    throw new TypeError("Graph change-set hash is invalid");
  }
  return requireGraphChangeSetJson(
    await apiFetch(
      `${projectPath(projectId)}/graph-change-sets/${uuidPathSegment(changeSetId)}/approval-decisions`,
      {
        method: "POST",
        body: JSON.stringify({
          decision,
          expected_changeset_sha256: expectedChangesetSha256,
          ...(confirmedStaleImpactSha256
            ? { confirmed_stale_impact_sha256: confirmedStaleImpactSha256 }
            : {}),
        }),
      },
    ),
    projectId,
  );
}

export async function listBranches(projectId: string): Promise<BranchWorkspaceResponse> {
  return requireC2Json(
    await apiFetch(`${projectPath(projectId)}/branches`),
    (value): value is BranchWorkspaceResponse =>
      isBranchWorkspaceResponse(value, projectId),
  );
}

export async function getBranchGraph(
  projectId: string,
  branchId: string,
): Promise<BranchGraphResponse> {
  return requireC2Json(
    await apiFetch(`${projectPath(projectId)}/branches/${uuidPathSegment(branchId)}/graph`),
    (value): value is BranchGraphResponse => isBranchGraphResponse(value, branchId),
  );
}

export async function proposePromotion(
  projectId: string,
  input: components["schemas"]["PromotionRequest"],
): Promise<PromotionResponse> {
  return requireC2Json(
    await apiFetch(`${projectPath(projectId)}/promotions`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
    (value): value is PromotionResponse => isPromotionResponse(value, projectId),
  );
}

export async function decidePromotion(
  projectId: string,
  promotionId: string,
  expectedHash: string,
  decision: "APPROVED" | "REJECTED" = "APPROVED",
): Promise<PromotionResponse> {
  return requireC2Json(
    await apiFetch(`${projectPath(projectId)}/promotions/${uuidPathSegment(promotionId)}/approval-decisions`, {
      method: "POST",
      body: JSON.stringify({ decision, expected_promotion_sha256: expectedHash }),
    }),
    (value): value is PromotionResponse => isPromotionResponse(value, projectId),
  );
}

export async function applyPromotion(
  projectId: string,
  promotionId: string,
  expectedHash: string,
): Promise<PromotionResponse> {
  return requireC2Json(
    await apiFetch(`${projectPath(projectId)}/promotions/${uuidPathSegment(promotionId)}/apply`, {
      method: "POST",
      body: JSON.stringify({ decision: "APPROVED", expected_promotion_sha256: expectedHash }),
    }),
    (value): value is PromotionResponse => isPromotionResponse(value, projectId),
  );
}

export async function proposePromotionUndo(
  projectId: string,
  promotionId: string,
): Promise<PromotionResponse> {
  return requireC2Json(
    await apiFetch(`${projectPath(projectId)}/promotions/${uuidPathSegment(promotionId)}/undo-proposals`, {
      method: "POST",
      body: JSON.stringify({ client_mutation_id: crypto.randomUUID() }),
    }),
    (value): value is PromotionResponse => isPromotionResponse(value, projectId),
  );
}

export async function proposeGraphChangeSetUndo(
  projectId: string,
  changeSetId: string,
  clientMutationId: string = crypto.randomUUID(),
): Promise<GraphChangeSetResponse> {
  uuidPathSegment(clientMutationId);
  return requireGraphChangeSetJson(
    await apiFetch(
      `${projectPath(projectId)}/graph-change-sets/${uuidPathSegment(changeSetId)}/undo-proposals`,
      {
        method: "POST",
        body: JSON.stringify({ client_mutation_id: clientMutationId }),
      },
    ),
    projectId,
  );
}

export async function applyGraphChangeSet(
  projectId: string,
  changeSetId: string,
  expectedChangesetSha256: string,
): Promise<GraphChangeSetResponse> {
  if (!/^[0-9a-f]{64}$/i.test(expectedChangesetSha256)) {
    throw new TypeError("Graph change-set hash is invalid");
  }
  return requireGraphChangeSetJson(
    await apiFetch(
      `${projectPath(projectId)}/graph-change-sets/${uuidPathSegment(changeSetId)}/apply`,
      {
        method: "POST",
        body: JSON.stringify({ expected_changeset_sha256: expectedChangesetSha256 }),
      },
    ),
    projectId,
  );
}

export async function getActiveControlConfig(): Promise<ControlConfigRevisionResponse> {
  return requireJson<ControlConfigRevisionResponse>(
    await apiFetch("/api/v1/control/config-revision"),
    isControlConfigRevisionResponse,
  );
}

const c3ProblemCodes = new Set([
  "agent_proposal_not_found", "agent_proposal_state_conflict", "model_profile_unavailable",
  "model_credential_unavailable", "model_endpoint_policy_invalid", "agent_scope_invalid",
  "approval_subject_stale", "approval_subject_integrity_mismatch", "run_authorization_consumed",
  "run_authorization_expired", "run_authorization_invalidated",
]);

async function requireC3Json<T>(
  response: Response,
  validate: (value: unknown) => value is T,
): Promise<T> {
  if (!response.ok) {
    let problem: unknown;
    try { problem = await response.json(); } catch { throw new ApiRequestError(response.status); }
    if (isRecord(problem) && isNonEmptyString(problem.code) && c3ProblemCodes.has(problem.code)) {
      throw new C3ApiError(response.status, problem.code, { ...problem });
    }
    throw new ApiRequestError(response.status, isRecord(problem) && isNonEmptyString(problem.code) ? problem.code : undefined);
  }
  return requireJson(response, validate);
}

export async function listModelProfiles(): Promise<ModelProfileResponse[]> {
  const body = await requireJson<{ profiles: ModelProfileResponse[] }>(
    await apiFetch("/api/v1/control/model-profiles"),
    (value): value is { profiles: ModelProfileResponse[] } => {
      const fields = readPlainDataRecord(value, ["profiles"]);
      const profiles = fields ? readDenseArray(fields.profiles, 100) : null;
      return Boolean(profiles && profiles.every(isModelProfileResponse));
    },
  );
  return body.profiles;
}

export async function listModelProfileRevisions(
  profileId: string,
): Promise<ModelProfileRevisionResponse[]> {
  const expectedProfileId = uuidPathSegment(profileId);
  const body = await requireJson<{ profile_id: string; revisions: ModelProfileRevisionResponse[] }>(
    await apiFetch(`/api/v1/control/model-profiles/${expectedProfileId}/revisions`),
    (value): value is { profile_id: string; revisions: ModelProfileRevisionResponse[] } => {
      const fields = readPlainDataRecord(value, ["profile_id", "revisions"]);
      const revisions = fields ? readDenseArray(fields.revisions, 1_000) : null;
      return Boolean(fields && fields.profile_id === expectedProfileId && revisions &&
        revisions.every(isModelProfileRevisionResponse));
    },
  );
  return body.revisions;
}

export async function proposeModelProfileConfigChangeSet(
  auditProjectId: string,
  baseConfigRevisionId: string,
  operation: Readonly<{
    provider_id: string;
    provider: { provider_key: string; display_name: string; adapter_kind: "OPENAI_COMPATIBLE_CHAT_JSON_V1" };
    model_profile_id: string;
    profile_key: string;
    model_profile_revision_id: string;
    revision: ModelProfileRevisionInput;
    declaration_sha256: string;
  }>,
): Promise<ConfigChangeSetResponse> {
  uuidPathSegment(auditProjectId);
  uuidPathSegment(baseConfigRevisionId);
  uuidPathSegment(operation.provider_id);
  uuidPathSegment(operation.model_profile_id);
  uuidPathSegment(operation.model_profile_revision_id);
  if (!isBoundedString(operation.profile_key, 64) || !isSha256(operation.declaration_sha256)) {
    throw new TypeError("Model profile declaration is invalid");
  }
  return requireJson<ConfigChangeSetResponse>(
    await apiFetch("/api/v1/control/config-change-sets", {
      method: "POST",
      body: JSON.stringify({
        kind: "config_change_set",
        schema_version: 1,
        audit_project_id: auditProjectId,
        base_config_revision_id: baseConfigRevisionId,
        operations: [{ op: "declare_model_profile_revision", ...operation, activate: true }],
      }),
    }),
    isConfigChangeSetResponse,
  );
}

export async function proposeConfigChangeSet(
  auditProjectId: string,
  input: { baseConfigRevisionId: string; dryRunMaxTasks: number },
): Promise<ConfigChangeSetResponse> {
  uuidPathSegment(auditProjectId);
  uuidPathSegment(input.baseConfigRevisionId);
  if (!Number.isInteger(input.dryRunMaxTasks) || input.dryRunMaxTasks < 1 || input.dryRunMaxTasks > 100) {
    throw new TypeError("dryRunMaxTasks must be an integer from 1 to 100");
  }
  return requireJson<ConfigChangeSetResponse>(
    await apiFetch("/api/v1/control/config-change-sets", {
      method: "POST",
      body: JSON.stringify({
        kind: "config_change_set",
        schema_version: 1,
        audit_project_id: auditProjectId,
        base_config_revision_id: input.baseConfigRevisionId,
        operations: [
          { op: "replace", path: "/dry_run_max_tasks", value: input.dryRunMaxTasks },
        ],
      }),
    }),
    isConfigChangeSetResponse,
  );
}

export async function approveConfigChangeSet(
  changeSetId: string,
  expectedChangesetSha256: string,
  decision: "APPROVED" | "REJECTED" = "APPROVED",
): Promise<ConfigChangeSetResponse> {
  if (!isSha256(expectedChangesetSha256)) throw new TypeError("Config change-set hash is invalid");
  return requireJson<ConfigChangeSetResponse>(
    await apiFetch(
      `/api/v1/control/config-change-sets/${uuidPathSegment(changeSetId)}/approval-decisions`,
      {
        method: "POST",
        body: JSON.stringify({
          decision,
          expected_changeset_sha256: expectedChangesetSha256,
        }),
      },
    ),
    isConfigChangeSetResponse,
  );
}

export async function applyConfigChangeSet(
  changeSetId: string,
  expectedChangesetSha256: string,
): Promise<ConfigChangeSetResponse> {
  if (!isSha256(expectedChangesetSha256)) throw new TypeError("Config change-set hash is invalid");
  return requireJson<ConfigChangeSetResponse>(
    await apiFetch(
      `/api/v1/control/config-change-sets/${uuidPathSegment(changeSetId)}/apply`,
      {
        method: "POST",
        body: JSON.stringify({ expected_changeset_sha256: expectedChangesetSha256 }),
      },
    ),
    isConfigChangeSetResponse,
  );
}

export async function requestNoEgressDryRun(
  projectId: string,
  taskCount: number,
): Promise<RunRequestResponse> {
  if (!Number.isInteger(taskCount) || taskCount < 1 || taskCount > 100) {
    throw new TypeError("taskCount must be an integer from 1 to 100");
  }
  return requireJson<RunRequestResponse>(
    await apiFetch(`${projectPath(projectId)}/run-requests`, {
      method: "POST",
      body: JSON.stringify({
        operation_kind: "DRY_RUN",
        task_count: taskCount,
        endpoint: null,
        external_data_classes: [],
        cost_bound: 0,
        gpu_count: 0,
        real_media: false,
        max_retries: 0,
      }),
    }),
    isRunRequestResponse,
  );
}

export function approveNoEgressDryRun(
  projectId: string,
  requestId: string,
  expectedRequestSha256: string,
  decision?: "APPROVED",
): Promise<RunAuthorizationResponse>;
export function approveNoEgressDryRun(
  projectId: string,
  requestId: string,
  expectedRequestSha256: string,
  decision: "REJECTED",
): Promise<RunRequestResponse>;
export async function approveNoEgressDryRun(
  projectId: string,
  requestId: string,
  expectedRequestSha256: string,
  decision: "APPROVED" | "REJECTED" = "APPROVED",
): Promise<RunAuthorizationResponse | RunRequestResponse> {
  if (!isSha256(expectedRequestSha256)) throw new TypeError("Run request hash is invalid");
  const response = await apiFetch(
    `${projectPath(projectId)}/run-requests/${uuidPathSegment(requestId)}/approval-decisions`,
    {
      method: "POST",
      body: JSON.stringify({
        decision,
        expected_request_sha256: expectedRequestSha256,
      }),
    },
  );
  return decision === "REJECTED"
    ? requireJson<RunRequestResponse>(response, isRunRequestResponse)
    : requireJson<RunAuthorizationResponse>(response, isRunAuthorizationResponse);
}

export async function executeNoEgressDryRun(
  projectId: string,
  requestId: string,
): Promise<DryRunReceiptResponse> {
  return requireJson<DryRunReceiptResponse>(
    await apiFetch(
      `${projectPath(projectId)}/run-requests/${uuidPathSegment(requestId)}/dry-run`,
      { method: "POST", body: "{}" },
    ),
    isDryRunReceiptResponse,
  );
}

export async function requestAgentProposal(
  projectId: string,
  input: CreateAgentProposalRequest,
): Promise<AgentProposalResponse> {
  return requireC3Json<AgentProposalResponse>(
    await apiFetch(`${projectPath(projectId)}/agent-proposals`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
    (value): value is AgentProposalResponse => isAgentProposalResponse(value, projectId),
  );
}

export async function getAgentProposal(
  projectId: string,
  proposalId: string,
): Promise<AgentProposalResponse> {
  return requireC3Json<AgentProposalResponse>(
    await apiFetch(`${projectPath(projectId)}/agent-proposals/${uuidPathSegment(proposalId)}`),
    (value): value is AgentProposalResponse => isAgentProposalResponse(value, projectId),
  );
}

export async function approveAgentProposal(
  projectId: string,
  proposalId: string,
  expectedRequestSha256: string,
  decision: "APPROVED" | "REJECTED" = "APPROVED",
): Promise<AgentProposalResponse> {
  if (!isSha256(expectedRequestSha256)) throw new TypeError("Agent request hash is invalid");
  return requireC3Json<AgentProposalResponse>(
    await apiFetch(`${projectPath(projectId)}/agent-proposals/${uuidPathSegment(proposalId)}/approval-decisions`, {
      method: "POST",
      body: JSON.stringify({ decision, expected_request_sha256: expectedRequestSha256 }),
    }),
    (value): value is AgentProposalResponse => isAgentProposalResponse(value, projectId),
  );
}

export async function executeAgentProposal(
  projectId: string,
  proposalId: string,
): Promise<AgentProposalResponse> {
  return requireC3Json<AgentProposalResponse>(
    await apiFetch(`${projectPath(projectId)}/agent-proposals/${uuidPathSegment(proposalId)}/execute`, {
      method: "POST",
      body: JSON.stringify({ kind: "execute_agent_proposal" }),
    }),
    (value): value is AgentProposalResponse => isAgentProposalResponse(value, projectId),
  );
}

function isPromptManifestEntry(value: unknown): boolean {
  const fields = readPlainDataRecord(value, ["block_id", "kind", "locked", "index", "text_sha256"]);
  return Boolean(fields && isUuid(fields.block_id) && typeof fields.kind === "string" &&
    promptBlockKinds.has(fields.kind) && typeof fields.locked === "boolean" &&
    Number.isSafeInteger(fields.index) && (fields.index as number) >= 0 &&
    isSha256(fields.text_sha256));
}

function isPromptBatchDecision(value: unknown): boolean {
  const fields = readPlainDataRecord(value, [
    "id", "attempt_id", "decision", "decision_sequence", "expected_output_sha256",
    "actor_id", "created_at",
  ]);
  return Boolean(fields && isUuid(fields.id) && isUuid(fields.attempt_id) &&
    (fields.decision === "ACCEPTED" || fields.decision === "REJECTED") &&
    isPositiveVersion(fields.decision_sequence) && isSha256(fields.expected_output_sha256) &&
    isBoundedString(fields.actor_id, 128) && isDateTimeString(fields.created_at));
}

function isPromptBatchAttempt(value: unknown, projectId: string): boolean {
  const fields = readPlainDataRecord(value, [
    "id", "attempt_number", "parent_attempt_id", "run_request", "state", "output",
    "output_sha256", "compiled_document_sha256", "compiled_document", "failure_code",
    "created_at", "updated_at",
  ]);
  return Boolean(fields && isUuid(fields.id) && isPositiveVersion(fields.attempt_number) &&
    isNullableUuid(fields.parent_attempt_id) &&
    isAgentRunRequest(fields.run_request, projectId, "PROMPT_BATCH_ITEM") &&
    ["PENDING_APPROVAL", "PENDING_EXECUTION", "DISPATCHING", "SUCCEEDED", "FAILED",
      "UNKNOWN_TERMINAL", "REJECTED"].includes(String(fields.state)) &&
    (fields.output === null || isRecord(fields.output)) &&
    (fields.output_sha256 === null || isSha256(fields.output_sha256)) &&
    (fields.compiled_document_sha256 === null || isSha256(fields.compiled_document_sha256)) &&
    (fields.compiled_document === null || parsePromptDocument(fields.compiled_document) !== null) &&
    (fields.failure_code === null || isBoundedString(fields.failure_code, 200)) &&
    isDateTimeString(fields.created_at) && isDateTimeString(fields.updated_at));
}

function isPromptBatchItem(value: unknown, projectId: string): boolean {
  const fields = readPlainDataRecord(value, [
    "id", "shot_id", "shot_node_id", "prompt_node_id", "shot_order",
    "base_document_sha256", "base_document", "protected_manifest_sha256", "protected_manifest",
    "editable_manifest_sha256", "editable_manifest", "prompt_profile_resource_revision_id",
    "prompt_profile_revision_sha256", "prompt_profile_scope_kind", "prompt_profile_scope_id",
    "decision_version", "current_attempt", "latest_decision", "attempts", "decisions",
  ]);
  const protectedManifest = fields ? readDenseArray(fields.protected_manifest, 5_000) : null;
  const editableManifest = fields ? readDenseArray(fields.editable_manifest, 5_000) : null;
  const attempts = fields ? readDenseArray(fields.attempts, 100) : null;
  const decisions = fields ? readDenseArray(fields.decisions, 10_000) : null;
  if (!(fields && isUuid(fields.id) && isUuid(fields.shot_id) && isUuid(fields.shot_node_id) &&
    isUuid(fields.prompt_node_id) && Number.isSafeInteger(fields.shot_order) &&
    (fields.shot_order as number) >= 0 && isSha256(fields.base_document_sha256) &&
    parsePromptDocument(fields.base_document) !== null && isSha256(fields.protected_manifest_sha256) &&
    protectedManifest && protectedManifest.every(isPromptManifestEntry) &&
    isSha256(fields.editable_manifest_sha256) && editableManifest && editableManifest.every(isPromptManifestEntry) &&
    editableManifest.length > 0 && isUuid(fields.prompt_profile_resource_revision_id) &&
    isSha256(fields.prompt_profile_revision_sha256) &&
    ["PROJECT", "SHOT", "NODE"].includes(String(fields.prompt_profile_scope_kind)) &&
    isNullableUuid(fields.prompt_profile_scope_id) && Number.isSafeInteger(fields.decision_version) &&
    (fields.decision_version as number) >= 0 && attempts && attempts.every((item) => isPromptBatchAttempt(item, projectId)) &&
    decisions && decisions.every(isPromptBatchDecision) &&
    (fields.current_attempt === null || isPromptBatchAttempt(fields.current_attempt, projectId)) &&
    (fields.latest_decision === null || isPromptBatchDecision(fields.latest_decision)))) return false;
  const currentId = fields.current_attempt === null ? null : (fields.current_attempt as Record<string, unknown>).id;
  const latestId = fields.latest_decision === null ? null : (fields.latest_decision as Record<string, unknown>).id;
  return (currentId === null || attempts.some((item) => (item as Record<string, unknown>).id === currentId)) &&
    (latestId === null || (decisions.length > 0 && (decisions[decisions.length - 1] as Record<string, unknown>).id === latestId)) &&
    fields.decision_version === decisions.length;
}

function isPromptBatchResponse(value: unknown, projectId: string): value is PromptBatchResponse {
  const fields = readPlainDataRecord(value, [
    "id", "project_id", "correlation_id", "selection_mode", "binding_key",
    "base_graph_revision_id", "base_graph_revision_sha256", "branch_id",
    "expected_branch_head_version", "expected_selection_version", "application_target",
    "model_profile_revision_id", "config_revision_id", "item_set_sha256",
    "request_set_sha256", "retry_request_set_sha256", "version", "phase", "items",
    "graph_change_set", "final_decision_set_sha256", "failure_code", "created_at", "updated_at",
  ]);
  const items = fields ? readDenseArray(fields.items, 100) : null;
  return Boolean(fields && isUuid(fields.id) && fields.project_id === projectId &&
    isUuid(fields.correlation_id) && ["SELECTED", "ALL"].includes(String(fields.selection_mode)) &&
    isBoundedString(fields.binding_key, 64) && isUuid(fields.base_graph_revision_id) &&
    isSha256(fields.base_graph_revision_sha256) && isUuid(fields.branch_id) &&
    isPositiveVersion(fields.expected_branch_head_version) &&
    isPositiveVersion(fields.expected_selection_version) && isRecord(fields.application_target) &&
    isUuid(fields.model_profile_revision_id) && isUuid(fields.config_revision_id) &&
    isSha256(fields.item_set_sha256) && isSha256(fields.request_set_sha256) &&
    (fields.retry_request_set_sha256 === null || isSha256(fields.retry_request_set_sha256)) &&
    isPositiveVersion(fields.version) && ["PENDING_APPROVAL", "PENDING_EXECUTION", "IN_PROGRESS",
      "REVIEW_PENDING", "RETRY_APPROVAL_REQUIRED", "FINALIZED", "REJECTED"].includes(String(fields.phase)) &&
    items && items.length > 0 && items.every((item) => isPromptBatchItem(item, projectId)) &&
    (fields.graph_change_set === null || isGraphChangeSetResponse(fields.graph_change_set, projectId)) &&
    (fields.final_decision_set_sha256 === null || isSha256(fields.final_decision_set_sha256)) &&
    (fields.failure_code === null || isBoundedString(fields.failure_code, 200)) &&
    isDateTimeString(fields.created_at) && isDateTimeString(fields.updated_at));
}

function isPromptBatchPreflightResponse(value: unknown): value is PromptBatchPreflightResponse {
  const fields = readPlainDataRecord(value, ["eligible", "items", "blockers"]);
  const items = fields ? readDenseArray(fields.items, 100) : null;
  const blockers = fields ? readDenseArray(fields.blockers, 100) : null;
  return Boolean(fields && typeof fields.eligible === "boolean" && items && blockers &&
    items.every((item) => {
      const row = readPlainDataRecord(item, ["shot_id", "shot_node_id", "prompt_node_id", "shot_order"]);
      return Boolean(row && isUuid(row.shot_id) && isUuid(row.shot_node_id) && isUuid(row.prompt_node_id) &&
        Number.isSafeInteger(row.shot_order) && (row.shot_order as number) >= 0);
    }) && blockers.every((item) => {
      const row = readPlainDataRecord(item, ["shot_id", "code"]);
      return Boolean(row && isUuid(row.shot_id) && isBoundedString(row.code, 200));
    }));
}

export async function preflightPromptBatch(projectId: string,
  input: CreatePromptBatchRequest): Promise<PromptBatchPreflightResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/preflight`, {
    method: "POST", body: JSON.stringify(input),
  }), isPromptBatchPreflightResponse);
}

export async function requestPromptBatch(projectId: string,
  input: CreatePromptBatchRequest): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches`, {
    method: "POST", body: JSON.stringify(input),
  }), (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function getPromptBatch(projectId: string, batchId: string): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}`),
    (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function approvePromptBatch(projectId: string, batchId: string,
  expectedRequestSetSha256: string, decision: "APPROVED" | "REJECTED" = "APPROVED"):
  Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}/approval`, {
    method: "POST", body: JSON.stringify({ decision, expected_request_set_sha256: expectedRequestSetSha256 }),
  }), (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function executePromptBatchItem(projectId: string, batchId: string,
  itemId: string, attemptId: string, requestSha256: string): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}` +
    `/items/${uuidPathSegment(itemId)}/attempts/${uuidPathSegment(attemptId)}/execute`, {
    method: "POST", body: JSON.stringify({ expected_attempt_id: attemptId, expected_request_sha256: requestSha256 }),
  }), (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function decidePromptBatchItem(projectId: string, batchId: string, itemId: string,
  input: PromptBatchItemDecisionRequest): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}` +
    `/items/${uuidPathSegment(itemId)}/decisions`, { method: "POST", body: JSON.stringify(input) }),
    (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function finalizePromptBatch(projectId: string, batchId: string,
  expectedBatchVersion: number, clientMutationId: string): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}/finalize`, {
    method: "POST", body: JSON.stringify({ expected_batch_version: expectedBatchVersion,
      client_mutation_id: clientMutationId }),
  }), (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function acceptAllSucceededPromptBatch(projectId: string, batchId: string,
  expectedBatchVersion: number): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}/decisions:accept-all-succeeded`, {
    method: "POST", body: JSON.stringify({ expected_batch_version: expectedBatchVersion,
      client_mutation_id: crypto.randomUUID() }),
  }), (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function retryFailedPromptBatch(projectId: string, batchId: string,
  input: PromptBatchRetryRequest): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}/retries`, {
    method: "POST", body: JSON.stringify(input),
  }), (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

export async function approvePromptBatchRetry(projectId: string, batchId: string,
  expectedRequestSetSha256: string): Promise<PromptBatchResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-batches/${uuidPathSegment(batchId)}/retry-approval`, {
    method: "POST", body: JSON.stringify({ decision: "APPROVED",
      expected_request_set_sha256: expectedRequestSetSha256 }),
  }), (value): value is PromptBatchResponse => isPromptBatchResponse(value, projectId));
}

function isPromptProfileExtractionResponse(value: unknown, projectId: string):
  value is PromptProfileExtractionResponse {
  const fields = readPlainDataRecord(value, [
    "id", "project_id", "run_request", "source_node_id", "source_document_sha256",
    "state", "output", "output_sha256", "compiled_profile",
    "materialized_resource_revision_id", "failure_code", "created_at", "updated_at",
  ]);
  return Boolean(fields && isUuid(fields.id) && fields.project_id === projectId &&
    isAgentRunRequest(fields.run_request, projectId, "PROMPT_PROFILE_EXTRACTION") &&
    isUuid(fields.source_node_id) && isSha256(fields.source_document_sha256) &&
    ["PENDING_APPROVAL", "PENDING_EXECUTION", "DISPATCHING", "SUCCEEDED", "FAILED",
      "UNKNOWN_TERMINAL", "REJECTED", "DRAFT_REJECTED", "MATERIALIZED"].includes(String(fields.state)) &&
    (fields.output === null || isRecord(fields.output)) &&
    (fields.output_sha256 === null || isSha256(fields.output_sha256)) &&
    (fields.compiled_profile === null || isRecord(fields.compiled_profile)) &&
    isNullableUuid(fields.materialized_resource_revision_id) &&
    (fields.failure_code === null || isBoundedString(fields.failure_code, 200)) &&
    isDateTimeString(fields.created_at) && isDateTimeString(fields.updated_at));
}

export async function requestPromptProfileExtraction(projectId: string,
  input: CreatePromptProfileExtractionRequest): Promise<PromptProfileExtractionResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-profile-extractions`, {
    method: "POST", body: JSON.stringify(input),
  }), (value): value is PromptProfileExtractionResponse =>
    isPromptProfileExtractionResponse(value, projectId));
}

export async function getPromptProfileExtraction(projectId: string, extractionId: string):
  Promise<PromptProfileExtractionResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-profile-extractions/${uuidPathSegment(extractionId)}`),
    (value): value is PromptProfileExtractionResponse => isPromptProfileExtractionResponse(value, projectId));
}

export async function approvePromptProfileExtraction(projectId: string, extractionId: string,
  expectedRequestSha256: string, decision: "APPROVED" | "REJECTED" = "APPROVED"):
  Promise<PromptProfileExtractionResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-profile-extractions/${uuidPathSegment(extractionId)}/approval-decisions`, {
    method: "POST", body: JSON.stringify({ decision, expected_request_sha256: expectedRequestSha256 }),
  }), (value): value is PromptProfileExtractionResponse => isPromptProfileExtractionResponse(value, projectId));
}

export async function executePromptProfileExtraction(projectId: string, extractionId: string):
  Promise<PromptProfileExtractionResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-profile-extractions/${uuidPathSegment(extractionId)}/execute`, {
    method: "POST", body: JSON.stringify({ kind: "execute_prompt_profile_extraction" }),
  }), (value): value is PromptProfileExtractionResponse => isPromptProfileExtractionResponse(value, projectId));
}

export async function materializePromptProfileExtraction(projectId: string, extractionId: string,
  input: MaterializePromptProfileRequest): Promise<PromptProfileExtractionResponse> {
  return requireC3Json(await apiFetch(`${projectPath(projectId)}/prompt-profile-extractions/${uuidPathSegment(extractionId)}/materialize`, {
    method: "POST", body: JSON.stringify(input),
  }), (value): value is PromptProfileExtractionResponse => isPromptProfileExtractionResponse(value, projectId));
}

function boundedEventQueryValue(value: number, fallback: number, maximum: number): number {
  if (!Number.isInteger(value) || value < 0 || value > maximum) return fallback;
  return value;
}

function eventConsumerPathSegment(consumerKey: string): string {
  if (
    !isNonEmptyString(consumerKey) ||
    consumerKey.length > 128 ||
    consumerKey.includes("/") ||
    consumerKey === "." ||
    consumerKey === ".."
  ) {
    throw new TypeError("Event consumer key is invalid");
  }
  return encodeURIComponent(consumerKey);
}

export async function readExperienceEventCursor(
  projectId: string,
  consumerKey: string,
  limit = 50,
): Promise<ExperienceEventPage> {
  const consumerPath = eventConsumerPathSegment(consumerKey);
  const query = new URLSearchParams({
    limit: String(boundedEventQueryValue(limit, 50, 100)),
  });
  return requireJson<ExperienceEventPage>(
    await apiFetch(
      `${projectPath(projectId)}/experience-event-cursors/${consumerPath}/events?${query}`,
    ),
    isExperienceEventPage,
  );
}

export async function acknowledgeExperienceEventCursor(
  projectId: string,
  consumerKey: string,
  streamPosition: number,
): Promise<EventCursorResponse> {
  const consumerPath = eventConsumerPathSegment(consumerKey);
  if (!Number.isInteger(streamPosition) || streamPosition < 0) {
    throw new TypeError("Event stream position is invalid");
  }
  return requireJson<EventCursorResponse>(
    await apiFetch(
      `${projectPath(projectId)}/experience-event-cursors/${consumerPath}`,
      {
        method: "PUT",
        body: JSON.stringify({ ack_stream_position: streamPosition }),
      },
    ),
    isEventCursorResponse,
  );
}

export async function saveViewport(
  projectId: string,
  viewport: ViewportState,
): Promise<void> {
  await requireJson<ViewportState>(
    await apiFetch(`${projectPath(projectId)}/view-state`, {
      method: "PUT",
      body: JSON.stringify(viewport),
    }),
    isViewportState,
  );
}

function handlePageShow(event: PageTransitionEvent): void {
  if (event.persisted && isNonEmptyString(getSessionToken())) {
    getSessionChannel();
  }
}

function disposeSessionChannelLifecycle(): void {
  if (typeof window !== "undefined") {
    window.removeEventListener("pagehide", handlePageHide);
    window.removeEventListener("pageshow", handlePageShow);
  }
  closeSessionChannel();
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", handlePageHide);
  window.addEventListener("pageshow", handlePageShow);
}

if (import.meta.hot) {
  import.meta.hot.dispose(disposeSessionChannelLifecycle);
}
