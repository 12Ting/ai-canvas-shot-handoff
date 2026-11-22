import type { BranchWorkspaceResponse } from "../../api/client";

export type CanvasBranchContext = Readonly<{
  branches: BranchWorkspaceResponse["branches"];
  selectedBranchId: string;
  selectionVersion: number;
  viewedBranchId: string;
  viewedBranchHeadRevisionId: string;
  viewedBranchHeadVersion: number;
  isViewingUnpromotedBranch: boolean;
}>;
