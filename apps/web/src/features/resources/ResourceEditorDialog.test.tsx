import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResourceRevisionConflictError } from "../../api/client";
import { ResourceEditorDialog } from "./ResourceEditorDialog";

const api = vi.hoisted(() => ({
  createResource: vi.fn(),
  createResourceRevision: vi.fn(),
  getResource: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  ...api,
}));

const projectId = "11111111-1111-4111-8111-111111111111";
const resourceId = "22222222-2222-4222-8222-222222222222";
const revisionId = "33333333-3333-4333-8333-333333333333";
const currentRevisionId = "44444444-4444-4444-8444-444444444444";

const resource = {
  id: resourceId,
  origin_project_id: projectId,
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
    payload: {
      kind: "STYLE" as const,
      summary: "暖色电影质感",
      palette: ["琥珀", "深绿"],
      lighting: ["柔和实景光"],
      materials: ["旧木"],
      camera_language: ["缓慢推近"],
      negative_constraints: ["避免霓虹"],
    },
    tags: [{ display_tag: "电影", normalized_tag: "电影" }],
    artifacts: [],
    created_by_actor: "local-user",
  },
  revision_history: [
    {
      id: revisionId,
      sequence: 1,
      payload_sha256: "a".repeat(64),
      created_at: "2026-08-28T00:00:00Z",
    },
  ],
};

describe("ResourceEditorDialog", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it("preserves every typed payload field when editing one value", async () => {
    api.createResourceRevision.mockResolvedValue({
      ...resource.latest_revision,
      id: currentRevisionId,
      sequence: 2,
    });
    render(
      <ResourceEditorDialog
        onClose={() => undefined}
        onSaved={() => undefined}
        projectId={projectId}
        resource={resource}
        trigger={null}
      />,
    );

    fireEvent.change(screen.getByLabelText("风格摘要"), {
      target: { value: "暖色电影质感 v2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建新版本" }));

    await waitFor(() => expect(api.createResourceRevision).toHaveBeenCalledOnce());
    expect(api.createResourceRevision.mock.calls[0][2]).toEqual({
      base_revision_id: revisionId,
      payload: {
        ...resource.latest_revision.payload,
        summary: "暖色电影质感 v2",
      },
      tags: ["电影"],
      artifacts: [],
    });
  });

  it("keeps input on conflict and reloads only the base until a manual resubmit", async () => {
    api.createResourceRevision
      .mockRejectedValueOnce(
        new ResourceRevisionConflictError(resourceId, currentRevisionId, {
          code: "resource_revision_conflict",
          resource_id: resourceId,
          current_revision_id: currentRevisionId,
        }),
      )
      .mockResolvedValueOnce({
        ...resource.latest_revision,
        id: "55555555-5555-4555-8555-555555555555",
        sequence: 3,
      });
    api.getResource.mockResolvedValue({
      ...resource,
      latest_revision: { ...resource.latest_revision, id: currentRevisionId, sequence: 2 },
    });
    render(
      <ResourceEditorDialog
        onClose={() => undefined}
        onSaved={() => undefined}
        projectId={projectId}
        resource={resource}
        trigger={null}
      />,
    );
    const summary = screen.getByLabelText("风格摘要");
    fireEvent.change(summary, { target: { value: "我的未保存版本" } });
    fireEvent.click(screen.getByRole("button", { name: "创建新版本" }));

    expect(await screen.findByText(/你的输入已保留/)).toBeTruthy();
    expect(summary).toHaveProperty("value", "我的未保存版本");
    expect(api.createResourceRevision).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /重新载入最新基线/ }));
    await screen.findByText(/编辑内容仍保留/);
    expect(summary).toHaveProperty("value", "我的未保存版本");
    expect(api.createResourceRevision).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "创建新版本" }));
    await waitFor(() => expect(api.createResourceRevision).toHaveBeenCalledTimes(2));
    expect(api.createResourceRevision.mock.calls[1][2]).toEqual(
      expect.objectContaining({
        base_revision_id: currentRevisionId,
        payload: expect.objectContaining({ summary: "我的未保存版本" }),
      }),
    );
  });

  it("pins explicitly entered artifact revisions when creating a media resource", async () => {
    const artifactRevisionId = "77777777-7777-4777-8777-777777777777";
    api.createResource.mockResolvedValue({ id: resourceId });

    render(
      <ResourceEditorDialog
        initialKind="MEDIA"
        onClose={() => undefined}
        onSaved={() => undefined}
        projectId={projectId}
        trigger={null}
      />,
    );

    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "参考画面" },
    });
    fireEvent.change(screen.getByLabelText("使用说明"), {
      target: { value: "保持构图与主色调" },
    });
    fireEvent.change(screen.getByLabelText("ArtifactRevision 固定"), {
      target: { value: `reference, 0, ${artifactRevisionId}` },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建资源" }));

    await waitFor(() => expect(api.createResource).toHaveBeenCalledOnce());
    expect(api.createResource.mock.calls[0][1]).toEqual({
      name: "参考画面",
      payload: {
        kind: "MEDIA",
        usage_notes: "保持构图与主色调",
        expected_media_kind: null,
      },
      tags: [],
      artifacts: [
        {
          slot: "reference",
          position: 0,
          artifact_revision_id: artifactRevisionId,
        },
      ],
    });
  });
});
