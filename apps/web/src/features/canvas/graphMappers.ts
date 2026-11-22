import {
  parseGraphSnapshot,
  type GraphSnapshotBody,
  type GraphSnapshotV2Body,
  type GraphSnapshotV3Body,
} from "../../api/client";
import {
  clonePromptDocument,
  documentFromText,
  validatePromptDocument,
} from "../prompting/promptDocument";
import { isCanvasUuid } from "./canvasTypes";
import type {
  CanvasEdge,
  CanvasNode,
  CanvasResourceBinding,
  CanvasShot,
  GraphSnapshot,
  MediaKind,
  TextNodeKind,
} from "./canvasTypes";

const portNamePattern = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/;

function requirePortName(
  edgeId: string,
  field: "sourceHandle" | "targetHandle",
  value: string | null | undefined,
): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 64 ||
    !portNamePattern.test(value)
  ) {
    throw new Error(
      `Invalid ${field} for edge ${edgeId}: expected a valid 1-64 character port name`,
    );
  }
  return value;
}

function requireFinitePosition(node: CanvasNode): { x: number; y: number } {
  if (!Number.isFinite(node.position.x) || !Number.isFinite(node.position.y)) {
    throw new Error(`Invalid position for node ${node.id}`);
  }
  return { x: node.position.x, y: node.position.y };
}

function requireText(nodeId: string, value: unknown): string {
  if (typeof value !== "string" || value.length > 100_000) {
    throw new Error(`Invalid text for node ${nodeId}`);
  }
  return value;
}

function requireNullableId(field: string, value: unknown): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Invalid ${field}`);
  }
  return value;
}

function requireId(field: string, value: unknown): string {
  const id = requireNullableId(field, value);
  if (id === null) throw new Error(`Invalid ${field}`);
  return id;
}

function requireMediaKind(value: unknown): MediaKind | null {
  if (value === null) return null;
  if (value === "image") return "image";
  if (value === "video") return "video";
  throw new Error("Invalid media kind");
}

function isDenseArray(value: unknown[]): boolean {
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.prototype.hasOwnProperty.call(value, index)) return false;
  }
  return true;
}

function fromTextNode(
  node: {
    id: string;
    kind: TextNodeKind;
    position: { x: number; y: number };
    data: { text: string };
  },
): CanvasNode {
  const kind = node.kind;
  if (kind === "prompt") {
    return {
      id: node.id,
      type: "prompt",
      position: { x: node.position.x, y: node.position.y },
      data: { kind: "prompt", document: documentFromText(node.data.text, node.id) },
    };
  }
  return {
    id: node.id,
    type: kind,
    position: { x: node.position.x, y: node.position.y },
    data: { kind, text: node.data.text },
  } as CanvasNode;
}

function fromV3Node(
  node: NonNullable<GraphSnapshotV3Body["nodes"]>[number],
): CanvasNode {
  if (node.kind !== "prompt") return fromV2Node(node);
  const document = clonePromptDocument({
    schema_version: 1,
    blocks: [...(node.data.document.blocks ?? [])],
    rendered_text: node.data.document.rendered_text,
    spans: [...(node.data.document.spans ?? [])],
  });
  if (!validatePromptDocument(document).ok) {
    throw new Error(`Invalid prompt document for node ${node.id}`);
  }
  return {
    id: node.id,
    type: "prompt",
    position: { x: node.position.x, y: node.position.y },
    data: { kind: "prompt", document },
  };
}

function fromV2Node(
  node: NonNullable<GraphSnapshotV2Body["nodes"]>[number],
): CanvasNode {
  switch (node.kind) {
    case "text_source":
    case "prompt":
    case "note":
      return fromTextNode(node);
    case "media_input":
      return {
        id: node.id,
        type: "media_input",
        position: { x: node.position.x, y: node.position.y },
        data: {
          kind: "media_input",
          artifactRevisionId: node.data.artifact_revision_id ?? null,
          mediaKind: node.data.media_kind ?? null,
        },
      };
    case "shot":
      return {
        id: node.id,
        type: "shot",
        position: { x: node.position.x, y: node.position.y },
        data: { kind: "shot", shotId: node.data.shot_id },
        deletable: false,
      };
  }
}

function fromShot(
  shot: NonNullable<GraphSnapshotV2Body["shots"]>[number],
): CanvasShot {
  return {
    id: shot.id,
    order: shot.order,
    imageRevisionId: shot.image_revision_id ?? null,
    videoRevisionId: shot.video_revision_id ?? null,
    referenceRevisionIds: [...(shot.reference_revision_ids ?? [])],
  };
}

export function fromGraphSnapshot(
  value: GraphSnapshotBody | unknown,
): {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  shots: CanvasShot[];
  resourceBindings: CanvasResourceBinding[];
} {
  const snapshot = parseGraphSnapshot(value);
  const nodes =
    snapshot.schema_version === 1
      ? (snapshot.nodes ?? []).map(fromTextNode)
      : snapshot.schema_version === 2
        ? (snapshot.nodes ?? []).map(fromV2Node)
        : (snapshot.nodes ?? []).map(fromV3Node);
  return {
    nodes,
    edges: (snapshot.edges ?? []).map((edge) => ({
      id: edge.id,
      source: edge.source,
      sourceHandle: edge.source_port,
      target: edge.target,
      targetHandle: edge.target_port,
    })),
    shots:
      snapshot.schema_version === 1 ? [] : (snapshot.shots ?? []).map(fromShot),
    resourceBindings:
      snapshot.schema_version !== 3
        ? []
        : (snapshot.resource_bindings ?? []).map((binding) => ({
            id: binding.id,
            resourceKind: binding.resource_kind,
            bindingKey: binding.binding_key,
            scopeKind: binding.scope_kind,
            scopeId: binding.scope_id,
            resourceRevisionId: binding.resource_revision_id,
          })),
  };
}

function toSnapshotNode(node: CanvasNode): NonNullable<GraphSnapshot["nodes"]>[number] {
  const kind = node.data.kind;
  if (node.type !== kind) {
    throw new Error(`Node ${node.id} type does not match its data kind`);
  }
  const position = requireFinitePosition(node);
  switch (kind) {
    case "text_source":
    case "note":
      return {
        id: node.id,
        kind,
        position,
        data: { text: requireText(node.id, node.data.text) },
      };
    case "prompt": {
      const validation = validatePromptDocument(node.data.document);
      if (!validation.ok) throw new Error(`Invalid prompt document for node ${node.id}`);
      return {
        id: node.id,
        kind,
        position,
        data: { document: clonePromptDocument(node.data.document) },
      };
    }
    case "media_input": {
      const artifactRevisionId = requireNullableId(
        `artifact revision for node ${node.id}`,
        node.data.artifactRevisionId,
      );
      const mediaKind = requireMediaKind(node.data.mediaKind);
      if ((artifactRevisionId === null) !== (mediaKind === null)) {
        throw new Error(`Media node ${node.id} must have both revision and kind or neither`);
      }
      return {
        id: node.id,
        kind,
        position,
        data: {
          artifact_revision_id: artifactRevisionId,
          media_kind: mediaKind,
        },
      };
    }
    case "shot":
      return {
        id: node.id,
        kind,
        position,
        data: { shot_id: requireId(`shot id for node ${node.id}`, node.data.shotId) },
      };
    default:
      throw new Error(`Invalid kind for node ${node.id}`);
  }
}

function toSnapshotResourceBinding(
  binding: CanvasResourceBinding,
): GraphSnapshot["resource_bindings"][number] {
  if (
    !isCanvasUuid(binding.id) ||
    !isCanvasUuid(binding.resourceRevisionId) ||
    (binding.resourceKind !== "STYLE" &&
      binding.resourceKind !== "CHARACTER" &&
      binding.resourceKind !== "PROMPT" &&
      binding.resourceKind !== "MEDIA") ||
    (binding.scopeKind !== "PROJECT" &&
      binding.scopeKind !== "SHOT" &&
      binding.scopeKind !== "NODE") ||
    !portNamePattern.test(binding.bindingKey) ||
    binding.bindingKey.length > 64 ||
    (binding.scopeKind === "PROJECT" && binding.scopeId !== null) ||
    (binding.scopeKind !== "PROJECT" && !isCanvasUuid(binding.scopeId))
  ) {
    throw new Error(`Invalid resource binding ${binding.id}`);
  }
  return {
    id: binding.id,
    resource_kind: binding.resourceKind,
    binding_key: binding.bindingKey,
    scope_kind: binding.scopeKind,
    scope_id: binding.scopeId,
    resource_revision_id: binding.resourceRevisionId,
  };
}

function validateResourceBindingTargets(
  nodes: CanvasNode[],
  shots: CanvasShot[],
  bindings: CanvasResourceBinding[],
): void {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const shotIds = new Set(shots.map((shot) => shot.id));
  const bindingIds = bindings.map((binding) => binding.id);
  const bindingKeys = bindings.map(
    (binding) => `${binding.scopeKind}:${binding.scopeId ?? ""}:${binding.bindingKey}`,
  );
  if (
    new Set(bindingIds).size !== bindingIds.length ||
    new Set(bindingKeys).size !== bindingKeys.length ||
    bindings.some(
      (binding) =>
        (binding.scopeKind === "SHOT" && !shotIds.has(binding.scopeId!)) ||
        (binding.scopeKind === "NODE" && !nodeIds.has(binding.scopeId!)),
    )
  ) {
    throw new Error("Invalid resource binding identity, key, or scope target");
  }
}

function toSnapshotShot(shot: CanvasShot): GraphSnapshot["shots"][number] {
  if (!Number.isSafeInteger(shot.order) || shot.order < 0) {
    throw new Error(`Invalid order for Shot ${shot.id}`);
  }
  if (
    !Array.isArray(shot.referenceRevisionIds) ||
    !isDenseArray(shot.referenceRevisionIds) ||
    shot.referenceRevisionIds.length > 64 ||
    new Set(shot.referenceRevisionIds).size !== shot.referenceRevisionIds.length
  ) {
    throw new Error(`Invalid reference revisions for Shot ${shot.id}`);
  }
  const referenceRevisionIds = shot.referenceRevisionIds.map((revisionId) =>
    requireId(`reference revision for Shot ${shot.id}`, revisionId),
  );
  return {
    id: requireId("Shot id", shot.id),
    order: shot.order,
    image_revision_id: requireNullableId(
      `image revision for Shot ${shot.id}`,
      shot.imageRevisionId,
    ),
    video_revision_id: requireNullableId(
      `video revision for Shot ${shot.id}`,
      shot.videoRevisionId,
    ),
    reference_revision_ids: referenceRevisionIds,
  };
}

function validateShotReferences(nodes: CanvasNode[], shots: CanvasShot[]): void {
  const shotIds = shots.map((shot) => shot.id);
  const shotOrders = shots.map((shot) => shot.order);
  const nodeShotIds: string[] = [];
  for (const node of nodes) {
    if (node.data.kind === "shot") nodeShotIds.push(node.data.shotId);
  }
  if (
    new Set(shotIds).size !== shotIds.length ||
    new Set(shotOrders).size !== shotOrders.length ||
    nodeShotIds.length !== shotIds.length ||
    new Set(nodeShotIds).size !== nodeShotIds.length ||
    nodeShotIds.some((shotId) => !shotIds.includes(shotId))
  ) {
    throw new Error("Every Shot must have exactly one Shot node");
  }
}

export function toGraphSnapshot(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  shots: CanvasShot[],
  resourceBindings: CanvasResourceBinding[] = [],
): GraphSnapshot {
  validateShotReferences(nodes, shots);
  validateResourceBindingTargets(nodes, shots, resourceBindings);
  return {
    schema_version: 3,
    nodes: nodes.map(toSnapshotNode),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      source_port: requirePortName(edge.id, "sourceHandle", edge.sourceHandle),
      target: edge.target,
      target_port: requirePortName(edge.id, "targetHandle", edge.targetHandle),
    })),
    shots: shots.map(toSnapshotShot),
    resource_bindings: [...resourceBindings]
      .sort((left, right) =>
        [left.scopeKind, left.scopeId ?? "", left.bindingKey, left.id]
          .join(":")
          .localeCompare(
            [right.scopeKind, right.scopeId ?? "", right.bindingKey, right.id].join(":"),
          ),
      )
      .map(toSnapshotResourceBinding),
  };
}
