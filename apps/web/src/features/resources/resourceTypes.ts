import type {
  CreateResourceRequestBody,
  CreateResourceRevisionRequestBody,
  ResourceDetailResponse,
  ResourceResponse,
} from "../../api/client";

export type ResourceKind = ResourceResponse["kind"];
export type ResourcePayload = ResourceResponse["latest_revision"]["payload"];
export type ResourceArtifactReference =
  ResourceResponse["latest_revision"]["artifacts"][number];

export const RESOURCE_KINDS: readonly ResourceKind[] = [
  "STYLE",
  "CHARACTER",
  "PROMPT",
  "MEDIA",
];

export const RESOURCE_KIND_LABEL: Readonly<Record<ResourceKind, string>> = {
  STYLE: "风格",
  CHARACTER: "角色",
  PROMPT: "提示词",
  MEDIA: "媒体",
};

export function resourceSummary(resource: ResourceResponse | ResourceDetailResponse): string {
  const payload = resource.latest_revision.payload;
  switch (payload.kind) {
    case "STYLE":
      return payload.summary;
    case "CHARACTER":
      return `${payload.canonical_name} · ${payload.identity_anchor}`;
    case "PROMPT":
      return `${payload.detail_density} · ${payload.content_order.join(" → ")}`;
    case "MEDIA":
      return payload.usage_notes;
  }
}

export function revisionRequest(
  resource: ResourceDetailResponse,
): CreateResourceRevisionRequestBody {
  return {
    base_revision_id: resource.latest_revision.id,
    payload: structuredClone(resource.latest_revision.payload),
    tags: resource.latest_revision.tags.map((tag) => tag.display_tag),
    artifacts: resource.latest_revision.artifacts.map((artifact) => ({ ...artifact })),
  };
}

export function createRequest(
  name: string,
  payload: ResourcePayload,
  tags: string[],
  artifacts: ResourceArtifactReference[],
): CreateResourceRequestBody {
  return { name, payload, tags, artifacts };
}
