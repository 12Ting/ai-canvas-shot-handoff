import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "../canvas/canvasStore";
import { ResourceBindingDialog } from "./ResourceBindingDialog";

const projectId = "11111111-1111-4111-8111-111111111111";
const resourceId = "22222222-2222-4222-8222-222222222222";
const revisionId = "33333333-3333-4333-8333-333333333333";
const shotId = "44444444-4444-4444-8444-444444444444";
const nodeId = "55555555-5555-4555-8555-555555555555";

const resource = {
  id: resourceId,
  origin_project_id: projectId,
  kind: "STYLE" as const,
  name: "电影暖调",
  created_at: "2026-08-28T00:00:00Z",
  archived_at: null,
  latest_revision: {
    id: revisionId,
    sequence: 2,
    payload_sha256: "a".repeat(64),
    created_at: "2026-08-28T00:00:00Z",
    resource_id: resourceId,
    schema_version: 1 as const,
    payload: { kind: "STYLE" as const, summary: "暖色电影质感" },
    tags: [],
    artifacts: [],
    created_by_actor: "local-user",
  },
  revision_history: [
    {
      id: revisionId,
      sequence: 2,
      payload_sha256: "a".repeat(64),
      created_at: "2026-08-28T00:00:00Z",
    },
  ],
};

describe("ResourceBindingDialog", () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId,
      revisionId: "66666666-6666-4666-8666-666666666666",
      nodes: [
        {
          id: nodeId,
          type: "note",
          position: { x: 0, y: 0 },
          data: { kind: "note", text: "节点" },
        },
        {
          id: "77777777-7777-4777-8777-777777777777",
          type: "shot",
          position: { x: 10, y: 10 },
          data: { kind: "shot", shotId },
        },
      ],
      edges: [],
      shots: [
        {
          id: shotId,
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
    });
  });

  afterEach(cleanup);

  it("pins an exact revision to a Shot scope and restores trigger focus", () => {
    const trigger = document.createElement("button");
    trigger.textContent = "绑定";
    document.body.append(trigger);
    const { unmount } = render(
      <ResourceBindingDialog
        onClose={() => undefined}
        resource={resource}
        trigger={trigger}
      />,
    );

    fireEvent.change(screen.getByLabelText("作用域"), { target: { value: "SHOT" } });
    fireEvent.change(screen.getByLabelText("作用目标"), { target: { value: shotId } });
    fireEvent.change(screen.getByLabelText("绑定键"), { target: { value: "visual_style" } });
    fireEvent.click(screen.getByRole("button", { name: "保存绑定" }));

    expect(useCanvasStore.getState().resourceBindings).toEqual([
      expect.objectContaining({
        resourceKind: "STYLE",
        bindingKey: "visual_style",
        scopeKind: "SHOT",
        scopeId: shotId,
        resourceRevisionId: revisionId,
      }),
    ]);
    unmount();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
});
