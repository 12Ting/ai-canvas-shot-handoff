import { describe, expect, it } from "vitest";

import type { BranchWorkspaceResponse } from "../../api/client";
import { deriveBranchViewState } from "./branchViewState";

const MAIN_ID = "11111111-1111-4111-8111-111111111111";
const EXPERIMENT_ID = "22222222-2222-4222-8222-222222222222";
const DOCUMENT_ID = "33333333-3333-4333-8333-333333333333";
const MAIN_REVISION_ID = "44444444-4444-4444-8444-444444444444";
const EXPERIMENT_REVISION_ID = "55555555-5555-4555-8555-555555555555";

function workspace(): BranchWorkspaceResponse {
  return {
    project_id: "66666666-6666-4666-8666-666666666666",
    graph_document_id: DOCUMENT_ID,
    branches: [
      {
        id: MAIN_ID,
        graph_document_id: DOCUMENT_ID,
        name: "主线",
        kind: "MAIN",
        forked_from_branch_id: null,
        forked_from_revision_id: MAIN_REVISION_ID,
        head: {
          graph_revision_id: MAIN_REVISION_ID,
          version: 4,
          graph_revision_sha256: "a".repeat(64),
        },
        created_at: "2026-08-30T00:00:00Z",
        is_selected: true,
      },
      {
        id: EXPERIMENT_ID,
        graph_document_id: DOCUMENT_ID,
        name: "探索",
        kind: "EXPERIMENT",
        forked_from_branch_id: MAIN_ID,
        forked_from_revision_id: MAIN_REVISION_ID,
        head: {
          graph_revision_id: EXPERIMENT_REVISION_ID,
          version: 2,
          graph_revision_sha256: "b".repeat(64),
        },
        created_at: "2026-08-30T00:01:00Z",
        is_selected: false,
      },
    ],
    selection: {
      graph_document_id: DOCUMENT_ID,
      branch_id: MAIN_ID,
      graph_revision_id: MAIN_REVISION_ID,
      version: 7,
    },
    latest_promotion: null,
    can_undo_latest_promotion: false,
  };
}

describe("deriveBranchViewState", () => {
  it("keeps the formal selection separate from the viewed experiment", () => {
    expect(deriveBranchViewState(workspace(), EXPERIMENT_ID)).toMatchObject({
      selectedBranchId: MAIN_ID,
      selectionVersion: 7,
      viewedBranchId: EXPERIMENT_ID,
      viewedBranchHeadRevisionId: EXPERIMENT_REVISION_ID,
      viewedBranchHeadVersion: 2,
      isViewingUnpromotedBranch: true,
    });
  });

  it("fails closed when the requested viewed branch is absent", () => {
    expect(() =>
      deriveBranchViewState(
        workspace(),
        "77777777-7777-4777-8777-777777777777",
      ),
    ).toThrow("Viewed branch is absent from the workspace");
  });
});
