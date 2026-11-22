import type { Edge, Node } from "@xyflow/react";
import type { GraphSnapshotV3Body } from "../../api/client";

type SnapshotNode = NonNullable<GraphSnapshotV3Body["nodes"]>[number];
type SnapshotEdge = NonNullable<GraphSnapshotV3Body["edges"]>[number];
type SnapshotShot = NonNullable<GraphSnapshotV3Body["shots"]>[number];
type SnapshotResourceBinding = NonNullable<GraphSnapshotV3Body["resource_bindings"]>[number];

export type GraphSnapshot = {
  schema_version: 3;
  nodes: SnapshotNode[];
  edges: SnapshotEdge[];
  shots: SnapshotShot[];
  resource_bindings: SnapshotResourceBinding[];
};

export type TextNodeKind = "text_source" | "prompt" | "note";
export type NodeKind = TextNodeKind | "media_input" | "shot";
export type AddableNodeKind = Exclude<NodeKind, "shot">;
export type MediaKind = "image" | "video";

const canvasUuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isCanvasUuid(value: unknown): value is string {
  return typeof value === "string" && canvasUuidPattern.test(value);
}

export type ShotAssociationTarget = Readonly<{
  shotId: string;
  slot: "image" | "video" | "reference";
}>;
export type CanvasRuntimeIdentity = Readonly<{
  projectId: string;
  loadEpoch: number;
}>;
export type ShotRevisionRef = Readonly<{
  revisionId: string;
  mediaKind: MediaKind;
}>;

type TextNodeData<Kind extends TextNodeKind> = {
  kind: Kind;
  text: string;
};

export type PromptBlockKind =
  | "SUBJECT"
  | "SCENE_ACTION"
  | "COMPOSITION_CAMERA"
  | "LIGHT_COLOR_MATERIAL_STYLE"
  | "NEGATIVE_CONSTRAINTS"
  | "MOTION"
  | "PROVIDER_EXTENSION"
  | "FREEFORM";

export type PromptBlock = {
  id: string;
  kind: PromptBlockKind;
  text: string;
  locked: boolean;
};

export type PromptSpan = {
  block_id: string;
  start_utf16: number;
  end_utf16: number;
};

export type PromptDocument = {
  schema_version: 1;
  blocks: PromptBlock[];
  rendered_text: string;
  spans: PromptSpan[];
};

export type PromptNodeData = {
  kind: "prompt";
  document: PromptDocument;
  text?: never;
};

export type MediaInputNodeData = {
  kind: "media_input";
  artifactRevisionId: string | null;
  mediaKind: MediaKind | null;
  text?: never;
};

export type ShotNodeData = {
  kind: "shot";
  shotId: string;
  text?: never;
};

export type CanvasNodeData =
  | TextNodeData<"text_source">
  | PromptNodeData
  | TextNodeData<"note">
  | MediaInputNodeData
  | ShotNodeData;

export type CanvasNode =
  | Node<TextNodeData<"text_source">, "text_source">
  | Node<PromptNodeData, "prompt">
  | Node<TextNodeData<"note">, "note">
  | Node<MediaInputNodeData, "media_input">
  | Node<ShotNodeData, "shot">;

export type CanvasShot = {
  id: string;
  order: number;
  imageRevisionId: string | null;
  videoRevisionId: string | null;
  referenceRevisionIds: string[];
};

export type ResourceKind = "STYLE" | "CHARACTER" | "PROMPT" | "MEDIA";
export type ResourceScopeKind = "PROJECT" | "SHOT" | "NODE";
export type CanvasResourceBinding = {
  id: string;
  resourceKind: ResourceKind;
  bindingKey: string;
  scopeKind: ResourceScopeKind;
  scopeId: string | null;
  resourceRevisionId: string;
};

export type CanvasEdge = Edge;
export type SaveStatus = "clean" | "dirty" | "saving" | "conflict" | "error";
