import type { BranchWorkspaceResponse } from "../../api/client";
import type { CanvasBranchContext } from "./branchTypes";

export function deriveBranchViewState(
  workspace: BranchWorkspaceResponse,
  viewedBranchId: string,
): CanvasBranchContext {
  const viewed = workspace.branches.find((branch) => branch.id === viewedBranchId);
  if (!viewed) throw new Error("Viewed branch is absent from the workspace");
  const selected = workspace.branches.find(
    (branch) => branch.id === workspace.selection.branch_id,
  );
  if (!selected || selected.head.graph_revision_id !== workspace.selection.graph_revision_id) {
    throw new Error("Selection pointer is inconsistent with the branch workspace");
  }
  return {
    branches: workspace.branches,
    selectedBranchId: selected.id,
    selectionVersion: workspace.selection.version,
    viewedBranchId: viewed.id,
    viewedBranchHeadRevisionId: viewed.head.graph_revision_id,
    viewedBranchHeadVersion: viewed.head.version,
    isViewingUnpromotedBranch: viewed.id !== selected.id,
  };
}
