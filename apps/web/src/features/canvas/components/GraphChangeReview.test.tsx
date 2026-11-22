import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BranchHeadStaleError,
  GraphChangeSetConflictError,
  SelectionPointerStaleError,
} from "../../../api/client";
import { useCanvasStore } from "../canvasStore";
import { GraphChangeReview } from "./GraphChangeReview";

const api = vi.hoisted(() => ({
  applyGraphChangeSet: vi.fn(),
  approveGraphChangeSet: vi.fn(),
  proposeGraphChangeSet: vi.fn(),
  proposeGraphChangeSetUndo: vi.fn(),
}));

vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  ...api,
}));

const projectId = "11111111-1111-4111-8111-111111111111";
const baseRevisionId = "22222222-2222-4222-8222-222222222222";
const changeSetId = "33333333-3333-4333-8333-333333333333";
const nextRevisionId = "44444444-4444-4444-8444-444444444444";

const proposal = {
  id: changeSetId,
  project_id: projectId,
  correlation_id: "55555555-5555-4555-8555-555555555555",
  base_graph_revision_id: baseRevisionId,
  changeset_sha256: "a".repeat(64),
  snapshot_hash: "b".repeat(64),
  diff: {
    op: "replace_snapshot" as const,
    from_snapshot_hash: "c".repeat(64),
    to_snapshot_hash: "d".repeat(64),
    prompt_documents_changed: [{
      node_id: "88888888-8888-4888-8888-888888888888",
      old_document_sha256: null,
      new_document_sha256: "e".repeat(64),
      old_locked_block_count: 0,
      new_locked_block_count: 1,
    }],
    resource_bindings_added: [{
      binding_id: "77777777-7777-4777-8777-777777777777",
      scope_kind: "PROJECT" as const,
      scope_id: null,
      binding_key: "style.primary",
      resource_kind: "STYLE" as const,
      resource_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    }],
    resource_bindings_removed: [{
      binding_id: "66666666-6666-4666-8666-666666666666",
      scope_kind: "PROJECT" as const,
      scope_id: null,
      binding_key: "style.old",
      resource_kind: "STYLE" as const,
      resource_revision_id: "99999999-9999-4999-8999-999999999999",
    }],
    resource_bindings_updated: [
      {
        binding_id: "55555555-5555-4555-8555-555555555555",
        scope_kind: "PROJECT" as const,
        scope_id: null,
        binding_key: "style.primary",
        old_resource_revision_id: "99999999-9999-4999-8999-999999999999",
        new_resource_revision_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      },
    ],
  },
  risk_summary: { classes: ["GRAPH_WRITE"] },
  state: "PENDING_APPROVAL",
  applied_graph_revision_id: null,
  application_target: {
    mode: "DIRECT" as const,
    branch_id: "99999999-9999-4999-8999-999999999999",
    expected_branch_head_revision_id: baseRevisionId,
    expected_branch_head_version: 1,
    expected_selection_version: 1,
  },
  impact: {
    items: [],
    summary: { unmaterialized: 0, current: 0, stale: 0 },
    impact_sha256: "e".repeat(64),
    confirmation_required: false,
  },
  created_branch_id: null,
  applied_branch_id: null,
  created_at: "2026-08-27T00:00:00Z",
};

describe("GraphChangeReview", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId,
      nodes: [],
      edges: [],
      shots: [],
      revisionId: baseRevisionId,
    });
    useCanvasStore.getState().addNode("note", { x: 120, y: 120 });
    api.proposeGraphChangeSet.mockResolvedValue(proposal);
    api.approveGraphChangeSet.mockResolvedValue({ ...proposal, state: "APPROVED" });
    api.applyGraphChangeSet.mockResolvedValue({
      ...proposal,
      state: "APPLIED",
      applied_graph_revision_id: nextRevisionId,
      applied_branch_id: proposal.application_target.branch_id,
    });
    api.proposeGraphChangeSetUndo.mockResolvedValue({
      ...proposal,
      id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      base_graph_revision_id: nextRevisionId,
      changeset_sha256: "f".repeat(64),
      state: "PENDING_APPROVAL",
    });
  });

  afterEach(cleanup);

  it("requires an explicit review confirmation before applying a new revision", async () => {
    render(<GraphChangeReview projectId={projectId} />);

    fireEvent.click(screen.getByRole("button", { name: "审阅并应用画布变更" }));

    const dialog = await screen.findByRole("dialog", { name: "图谱变更审阅" });
    expect(dialog.textContent).toContain("replace_snapshot");
    expect(dialog.textContent).toContain("提示词文档 1");
    expect(dialog.textContent).toContain("资源绑定 +1 · -1 · 更新 1");
    expect(dialog.textContent).toContain("99999999-9999 → aaaaaaaa-aaaa");
    expect(dialog.textContent).toContain("GRAPH_WRITE");
    expect(api.approveGraphChangeSet).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认应用变更" }));

    await screen.findByText("已应用为新的图谱版本");
    expect(api.approveGraphChangeSet).toHaveBeenCalledWith(
      projectId,
      changeSetId,
      proposal.changeset_sha256,
    );
    expect(api.applyGraphChangeSet).toHaveBeenCalledWith(
      projectId,
      changeSetId,
      proposal.changeset_sha256,
    );
    expect(useCanvasStore.getState().baseRevisionId).toBe(nextRevisionId);
    expect(useCanvasStore.getState().status).toBe("clean");
  });

  it("reviews a server-created Agent proposal through the same NEW_BRANCH apply path", async () => {
    const external = {
      ...proposal,
      state: "PENDING_APPROVAL" as const,
      application_target: {
        mode: "NEW_BRANCH" as const,
        base_branch_id: "99999999-9999-4999-8999-999999999999",
        branch_name: "Agent proposal",
        expected_branch_head_revision_id: baseRevisionId,
        expected_branch_head_version: 1,
        expected_selection_version: 1,
      },
    };
    api.applyGraphChangeSet.mockResolvedValue({
      ...external,
      state: "APPLIED",
      applied_graph_revision_id: nextRevisionId,
      created_branch_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      applied_branch_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    });
    const consumed = vi.fn();
    const applied = vi.fn();
    render(<GraphChangeReview externalProposal={external} onApplied={applied} onExternalConsumed={consumed} projectId={projectId} />);

    const dialog = await screen.findByRole("dialog", { name: "Agent 图谱提案审阅" });
    expect(dialog.textContent).toContain("将创建实验分支“Agent proposal”");
    fireEvent.click(screen.getByRole("button", { name: "确认应用变更" }));
    await waitFor(() => expect(applied).toHaveBeenCalledOnce());
    expect(consumed).toHaveBeenCalledOnce();
    expect(api.approveGraphChangeSet).toHaveBeenCalledWith(projectId, external.id, external.changeset_sha256);
  });

  it("preserves the local draft as a conflict when the proposal base is stale", async () => {
    const currentRevisionId = "66666666-6666-4666-8666-666666666666";
    api.proposeGraphChangeSet.mockRejectedValue(
      new GraphChangeSetConflictError(currentRevisionId),
    );
    render(<GraphChangeReview projectId={projectId} />);

    fireEvent.click(screen.getByRole("button", { name: "审阅并应用画布变更" }));

    await waitFor(() =>
      expect(useCanvasStore.getState()).toMatchObject({
        status: "conflict",
        conflictRevisionId: currentRevisionId,
      }),
    );
    expect(api.approveGraphChangeSet).not.toHaveBeenCalled();
    expect(api.applyGraphChangeSet).not.toHaveBeenCalled();
  });

  it.each([
    new BranchHeadStaleError(
      "77777777-7777-4777-8777-777777777777",
      "66666666-6666-4666-8666-666666666666",
      3,
      { code: "branch_head_stale" },
    ),
    new SelectionPointerStaleError(
      "77777777-7777-4777-8777-777777777777",
      "66666666-6666-4666-8666-666666666666",
      4,
      { code: "selection_pointer_stale" },
    ),
  ])("preserves the draft for a precise C2 CAS conflict: $code", async (error) => {
    api.proposeGraphChangeSet.mockRejectedValue(error);
    render(<GraphChangeReview projectId={projectId} />);

    fireEvent.click(screen.getByRole("button", { name: "审阅并应用画布变更" }));

    await waitFor(() =>
      expect(useCanvasStore.getState()).toMatchObject({
        status: "conflict",
        conflictRevisionId: error.currentRevisionId,
      }),
    );
    expect(api.approveGraphChangeSet).not.toHaveBeenCalled();
    expect(api.applyGraphChangeSet).not.toHaveBeenCalled();
  });

  it("records a rejected decision when the user keeps the local draft", async () => {
    render(<GraphChangeReview projectId={projectId} />);
    const trigger = screen.getByRole("button", { name: "审阅并应用画布变更" });
    fireEvent.click(trigger);
    await screen.findByRole("dialog", { name: "图谱变更审阅" });

    fireEvent.click(screen.getByRole("button", { name: "保留草稿" }));

    await waitFor(() =>
      expect(api.approveGraphChangeSet).toHaveBeenCalledWith(
        projectId,
        changeSetId,
        proposal.changeset_sha256,
        "REJECTED",
      ),
    );
    expect(api.applyGraphChangeSet).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().status).toBe("dirty");
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("binds the viewed branch and exact stale-impact confirmation in a top-level review", async () => {
    const branchId = "99999999-9999-4999-8999-999999999999";
    const impactHash = "f".repeat(64);
    api.proposeGraphChangeSet.mockResolvedValue({
      ...proposal,
      application_target: {
        mode: "DIRECT",
        branch_id: branchId,
        expected_branch_head_revision_id: baseRevisionId,
        expected_branch_head_version: 3,
        expected_selection_version: 4,
      },
      impact: {
        items: [
          {
            node_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            impact_kind: "DIRECT",
            reasons: ["NODE_DATA_CHANGED"],
            old_state: "CURRENT",
            projected_state: "STALE",
          },
        ],
        summary: { unmaterialized: 0, current: 0, stale: 1 },
        impact_sha256: impactHash,
        confirmation_required: true,
      },
      created_branch_id: null,
      applied_branch_id: null,
    });
    render(
      <GraphChangeReview
        branchContext={{
          branchId,
          branchHeadRevisionId: baseRevisionId,
          branchHeadVersion: 3,
          selectionVersion: 4,
        }}
        projectId={projectId}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "审阅并应用画布变更" }));

    const dialog = await screen.findByRole("dialog", { name: "图谱变更审阅" });
    expect(dialog.parentElement).toBe(document.body);
    expect(api.proposeGraphChangeSet).toHaveBeenCalledWith(
      projectId,
      expect.objectContaining({
        applicationTarget: {
          mode: "DIRECT",
          branch_id: branchId,
          expected_branch_head_revision_id: baseRevisionId,
          expected_branch_head_version: 3,
          expected_selection_version: 4,
        },
      }),
    );
    const confirm = screen.getByRole("button", { name: "确认应用变更" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "我确认这些已物化节点需要重新运行",
      }),
    );
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(api.approveGraphChangeSet).toHaveBeenCalledWith(
        projectId,
        changeSetId,
        proposal.changeset_sha256,
        "APPROVED",
        impactHash,
      ),
    );
  });

  it("makes the formal-selection boundary explicit for a new branch", async () => {
    const branchId = "99999999-9999-4999-8999-999999999999";
    api.proposeGraphChangeSet.mockResolvedValue({
      ...proposal,
      application_target: {
        mode: "NEW_BRANCH",
        base_branch_id: branchId,
        branch_name: "探索 A",
        expected_branch_head_revision_id: baseRevisionId,
        expected_branch_head_version: 3,
        expected_selection_version: 4,
      },
      risk_summary: { classes: ["GRAPH_WRITE", "BRANCH_CREATE"] },
    });
    render(
      <GraphChangeReview
        branchContext={{
          branchId,
          branchHeadRevisionId: baseRevisionId,
          branchHeadVersion: 3,
          selectionVersion: 4,
        }}
        projectId={projectId}
      />,
    );

    fireEvent.change(screen.getByLabelText("应用方式"), {
      target: { value: "NEW_BRANCH" },
    });
    fireEvent.change(screen.getByLabelText("新分支名称"), {
      target: { value: "探索 A" },
    });
    fireEvent.click(screen.getByRole("button", { name: "审阅并应用画布变更" }));

    const dialog = await screen.findByRole("dialog", { name: "图谱变更审阅" });
    expect(dialog.textContent).toContain("将创建实验分支“探索 A”");
    expect(dialog.textContent).toContain("正式选择保持不变");
  });

  it("opens a new reviewed GraphChangeSet before undoing the latest direct apply", async () => {
    render(<GraphChangeReview projectId={projectId} />);

    fireEvent.click(screen.getByRole("button", { name: "审阅并应用画布变更" }));
    await screen.findByRole("dialog", { name: "图谱变更审阅" });
    fireEvent.click(screen.getByRole("button", { name: "确认应用变更" }));
    const undoTrigger = await screen.findByRole("button", {
      name: "撤销刚才的直接应用",
    });

    fireEvent.click(undoTrigger);

    await screen.findByRole("dialog", { name: "撤销直接应用审阅" });
    expect(api.proposeGraphChangeSetUndo).toHaveBeenCalledWith(
      projectId,
      changeSetId,
    );
    expect(api.applyGraphChangeSet).toHaveBeenCalledTimes(1);
  });
});
