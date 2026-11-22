import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BranchWorkspaceResponse,
  PromotionResponse,
} from "../../api/client";
import { BranchPanel } from "./BranchPanel";

const api = vi.hoisted(() => ({
  applyPromotion: vi.fn(),
  decidePromotion: vi.fn(),
  proposePromotion: vi.fn(),
  proposePromotionUndo: vi.fn(),
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
  source_branch_head_version: 1,
  target_branch_id: experimentId,
  target_graph_revision_id: experimentRevisionId,
  target_branch_head_version: 2,
  expected_selection_version: 1,
  undo_of_promotion_id: null,
  impact: {
    source_graph_revision_sha256: "a".repeat(64),
    target_graph_revision_sha256: "b".repeat(64),
    materialization: { unmaterialized: 0, current: 0, stale: 0 },
  },
  impact_sha256: "c".repeat(64),
  promotion_sha256: "d".repeat(64),
  risk_summary: { classes: ["SELECTION_PROMOTION"] },
  state: "PENDING_APPROVAL",
  approval_id: null,
  created_at: "2026-08-30T00:00:00Z",
  applied_at: null,
};

function workspace(
  selectedId: string = mainId,
  latestPromotion: PromotionResponse | null = null,
): BranchWorkspaceResponse {
  return {
    project_id: projectId,
    graph_document_id: "99999999-9999-4999-8999-999999999999",
    branches: [
      {
        id: mainId,
        graph_document_id: "99999999-9999-4999-8999-999999999999",
        name: "主线",
        kind: "MAIN",
        forked_from_branch_id: null,
        forked_from_revision_id: mainRevisionId,
        head: {
          graph_revision_id: mainRevisionId,
          version: 1,
          graph_revision_sha256: "a".repeat(64),
        },
        created_at: "2026-08-30T00:00:00Z",
        is_selected: selectedId === mainId,
      },
      {
        id: experimentId,
        graph_document_id: "99999999-9999-4999-8999-999999999999",
        name: "探索 A",
        kind: "EXPERIMENT",
        forked_from_branch_id: mainId,
        forked_from_revision_id: mainRevisionId,
        head: {
          graph_revision_id: experimentRevisionId,
          version: 2,
          graph_revision_sha256: "b".repeat(64),
        },
        created_at: "2026-08-30T00:01:00Z",
        is_selected: selectedId === experimentId,
      },
    ],
    selection: {
      graph_document_id: "99999999-9999-4999-8999-999999999999",
      branch_id: selectedId,
      graph_revision_id:
        selectedId === mainId ? mainRevisionId : experimentRevisionId,
      version: latestPromotion ? 2 : 1,
    },
    latest_promotion: latestPromotion,
    can_undo_latest_promotion: latestPromotion !== null,
  };
}

describe("BranchPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.proposePromotion.mockResolvedValue(proposal);
    api.decidePromotion.mockResolvedValue({ ...proposal, state: "APPROVED" });
    api.applyPromotion.mockResolvedValue({ ...proposal, state: "APPLIED" });
  });

  afterEach(cleanup);

  it("distinguishes viewed and selected branches and requires a separate promotion review", async () => {
    const onSelectionChanged = vi.fn();
    render(
      <BranchPanel
        onOpen={vi.fn()}
        onSelectionChanged={onSelectionChanged}
        projectId={projectId}
        viewedBranchId={experimentId}
        workspace={workspace()}
      />,
    );

    const panelTrigger = screen.getByRole("button", {
      name: "查看：探索 A｜正式：主线",
    });
    fireEvent.click(panelTrigger);
    expect(screen.getByRole("region", { name: "图谱分支" }).parentElement).toBe(
      document.body,
    );
    expect(screen.getByText(/正在编辑未提升分支/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /主线 正式选择/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /探索 A 实验分支 · 正在查看/ })).toBeTruthy();
    expect(screen.getByText(`头 ${mainRevisionId.slice(0, 12)} · v1`)).toBeTruthy();
    expect(
      screen.getByText(`头 ${experimentRevisionId.slice(0, 12)} · v2`),
    ).toBeTruthy();
    expect(screen.getByText(`派生自 主线 · ${mainRevisionId.slice(0, 12)}`)).toBeTruthy();

    const promotionTrigger = screen.getByRole("button", {
      name: "提升为当前选择",
    });
    fireEvent.click(promotionTrigger);

    await screen.findByRole("dialog", { name: "提升分支为正式选择" });
    expect(api.proposePromotion).toHaveBeenCalledTimes(1);
    expect(api.decidePromotion).not.toHaveBeenCalled();
    expect(api.applyPromotion).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "提升为正式选择" }));
    await waitFor(() => expect(onSelectionChanged).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.activeElement).toBe(promotionTrigger));
  });

  it("creates a reverse proposal before undoing the latest promotion", async () => {
    const applied = {
      ...proposal,
      state: "APPLIED" as const,
      approval_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      applied_at: "2026-08-30T00:02:00Z",
    };
    const reverse = {
      ...proposal,
      id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      kind: "UNDO_PROMOTION" as const,
      source_branch_id: experimentId,
      source_graph_revision_id: experimentRevisionId,
      target_branch_id: mainId,
      target_graph_revision_id: mainRevisionId,
      undo_of_promotion_id: proposal.id,
    };
    api.proposePromotionUndo.mockResolvedValue(reverse);
    render(
      <BranchPanel
        onOpen={vi.fn()}
        onSelectionChanged={vi.fn()}
        projectId={projectId}
        viewedBranchId={experimentId}
        workspace={workspace(experimentId, applied)}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "查看/正式：探索 A" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "撤销上次提升" }));

    await screen.findByRole("dialog", { name: "撤销上次提升" });
    expect(api.proposePromotionUndo).toHaveBeenCalledWith(projectId, proposal.id);
    expect(api.decidePromotion).not.toHaveBeenCalled();
  });
});
