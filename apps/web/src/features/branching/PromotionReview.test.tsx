import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BranchWorkspaceResponse,
  PromotionResponse,
} from "../../api/client";
import { PromotionReview } from "./PromotionReview";

const api = vi.hoisted(() => ({
  applyPromotion: vi.fn(),
  decidePromotion: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  ...api,
}));

const projectId = "11111111-1111-4111-8111-111111111111";
const mainId = "22222222-2222-4222-8222-222222222222";
const experimentId = "33333333-3333-4333-8333-333333333333";
const mainRevisionId = "44444444-4444-4444-8444-444444444444";
const experimentRevisionId = "55555555-5555-4555-8555-555555555555";

const proposal: PromotionResponse = {
  id: "66666666-6666-4666-8666-666666666666",
  project_id: projectId,
  correlation_id: "77777777-7777-4777-8777-777777777777",
  client_mutation_id: "88888888-8888-4888-8888-888888888888",
  kind: "PROMOTE",
  source_branch_id: mainId,
  source_graph_revision_id: mainRevisionId,
  source_branch_head_version: 2,
  target_branch_id: experimentId,
  target_graph_revision_id: experimentRevisionId,
  target_branch_head_version: 3,
  expected_selection_version: 4,
  undo_of_promotion_id: null,
  impact: {
    source_graph_revision_sha256: "a".repeat(64),
    target_graph_revision_sha256: "b".repeat(64),
    materialization: { unmaterialized: 2, current: 1, stale: 3 },
  },
  impact_sha256: "c".repeat(64),
  promotion_sha256: "d".repeat(64),
  risk_summary: { classes: ["SELECTION_PROMOTION"] },
  state: "PENDING_APPROVAL",
  approval_id: null,
  created_at: "2026-08-30T00:00:00Z",
  applied_at: null,
};

const workspace = {
  project_id: projectId,
  graph_document_id: "99999999-9999-4999-8999-999999999999",
  branches: [
    {
      id: mainId,
      graph_document_id: "99999999-9999-4999-8999-999999999999",
      name: "主线",
      kind: "MAIN" as const,
      forked_from_branch_id: null,
      forked_from_revision_id: mainRevisionId,
      head: {
        graph_revision_id: mainRevisionId,
        version: 2,
        graph_revision_sha256: "a".repeat(64),
      },
      created_at: "2026-08-30T00:00:00Z",
      is_selected: true,
    },
    {
      id: experimentId,
      graph_document_id: "99999999-9999-4999-8999-999999999999",
      name: "探索 A",
      kind: "EXPERIMENT" as const,
      forked_from_branch_id: mainId,
      forked_from_revision_id: mainRevisionId,
      head: {
        graph_revision_id: experimentRevisionId,
        version: 3,
        graph_revision_sha256: "b".repeat(64),
      },
      created_at: "2026-08-30T00:01:00Z",
      is_selected: false,
    },
  ],
  selection: {
    graph_document_id: "99999999-9999-4999-8999-999999999999",
    branch_id: mainId,
    graph_revision_id: mainRevisionId,
    version: 4,
  },
  latest_promotion: null,
  can_undo_latest_promotion: false,
} satisfies BranchWorkspaceResponse;

describe("PromotionReview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.decidePromotion.mockResolvedValue({ ...proposal, state: "APPROVED" });
    api.applyPromotion.mockResolvedValue({
      ...proposal,
      state: "APPLIED",
      approval_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      applied_at: "2026-08-30T00:02:00Z",
    });
  });

  afterEach(cleanup);

  it("renders an exact top-level promotion preview and applies only after confirmation", async () => {
    const onApplied = vi.fn();
    render(
      <PromotionReview
        onApplied={onApplied}
        onClose={vi.fn()}
        projectId={projectId}
        proposal={proposal}
        workspace={workspace}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "提升分支为正式选择" });
    expect(dialog.parentElement).toBe(document.body);
    expect(dialog.textContent).toContain("主线");
    expect(dialog.textContent).toContain("探索 A");
    expect(dialog.textContent).toContain("选择版本 4");
    expect(dialog.textContent).toContain("目标头版本 3");
    expect(dialog.textContent).toContain("未物化 2 · 当前 1 · 陈旧 3");
    expect(api.decidePromotion).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "提升为正式选择" }));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    expect(api.decidePromotion).toHaveBeenCalledWith(
      projectId,
      proposal.id,
      proposal.promotion_sha256,
    );
    expect(api.applyPromotion).toHaveBeenCalledWith(
      projectId,
      proposal.id,
      proposal.promotion_sha256,
    );
  });

  it("rejects with Escape and keeps the pointer unchanged", async () => {
    const onClose = vi.fn();
    render(
      <PromotionReview
        onApplied={vi.fn()}
        onClose={onClose}
        projectId={projectId}
        proposal={proposal}
        workspace={workspace}
      />,
    );

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(api.decidePromotion).toHaveBeenCalledWith(
      projectId,
      proposal.id,
      proposal.promotion_sha256,
      "REJECTED",
    );
    expect(api.applyPromotion).not.toHaveBeenCalled();
  });
});
