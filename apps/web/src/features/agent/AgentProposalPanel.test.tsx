import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentProposalPanel } from "./AgentProposalPanel";

const api = vi.hoisted(() => ({ approveAgentProposal: vi.fn(), executeAgentProposal: vi.fn(), getAgentProposal: vi.fn(), requestAgentProposal: vi.fn() }));
vi.mock("../../api/client", () => api);

const projectId = "11111111-1111-4111-8111-111111111111";
const nodeId = "22222222-2222-4222-8222-222222222222";
const proposal = {
  id: "33333333-3333-4333-8333-333333333333", project_id: projectId, state: "PENDING_APPROVAL", output: null, graph_change_set: null, failure_code: null,
  created_at: "2026-09-03T00:00:00Z", updated_at: "2026-09-03T00:00:00Z",
  run_request: { id: "44444444-4444-4444-8444-444444444444", project_id: projectId, correlation_id: "55555555-5555-4555-8555-555555555555", operation_kind: "AGENT_PROPOSAL", model_profile_revision_id: "66666666-6666-4666-8666-666666666666", branch_id: "77777777-7777-4777-8777-777777777777", branch_head_version: 1, selection_version: 1, scope_sha256: "a".repeat(64), request_sha256: "b".repeat(64), egress_plan_sha256: "c".repeat(64), state: "PENDING_APPROVAL",
    egress_plan: { mode: "MODEL_PROVIDER", endpoint: "http://127.0.0.1:8765/v1/chat/completions", locality: "LOCAL_LOOPBACK", endpoint_identity_sha256: "d".repeat(64), external_data_classes: ["USER_INSTRUCTION", "SELECTED_NODE_TEXT"], task_count: 1, cost_mode: "HARD", currency: "CNY", cost_bound_minor: 0, data_residency: "LOCAL_DEVICE", retention_policy: "NO_RETENTION_DECLARED", max_retries: 0, request_sha256: "b".repeat(64), plan_sha256: "c".repeat(64), preview: {} } },
};

describe("AgentProposalPanel", () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); api.requestAgentProposal.mockResolvedValue(proposal); api.approveAgentProposal.mockResolvedValue({ ...proposal, state: "PENDING_EXECUTION", run_request: { ...proposal.run_request, state: "APPROVED" } }); api.executeAgentProposal.mockResolvedValue({ ...proposal, state: "SUCCEEDED", output: { kind: "run_request_draft", schema_version: 1, operation_kind: "DRY_RUN", target_node_ids: [nodeId], task_count: 1, reason: "review" }, graph_change_set: null }); });
  afterEach(cleanup);

  it("shows the complete plan and requires approve-without-send plus a second explicit execute", async () => {
    render(<AgentProposalPanel branchContext={{ branchId: proposal.run_request.branch_id, branchHeadRevisionId: "88888888-8888-4888-8888-888888888888", branchHeadVersion: 1, selectionVersion: 1 }} nodes={[{ id: nodeId, type: "note", position: { x: 0, y: 0 }, data: { kind: "note", text: "hello🙂" } }]} onGraphChangeSet={vi.fn()} projectId={projectId} />);
    fireEvent.change(screen.getByLabelText("指令"), { target: { value: "rewrite" } });
    expect(api.requestAgentProposal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "生成外发计划（不发送）" }));
    await screen.findByText("完整 EgressPlan");
    expect(screen.getByText(/LOCAL_LOOPBACK \/ LOCAL_DEVICE/)).toBeTruthy();
    expect(api.executeAgentProposal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "批准但不发送" }));
    await screen.findByText("已批准；尚未发送。请再次明确执行。");
    expect(api.executeAgentProposal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "明确执行一次模型调用" }));
    await waitFor(() => expect(api.executeAgentProposal).toHaveBeenCalledOnce());
    await screen.findByText("模型生成了不可执行的 RunRequest 草案；未创建执行任务。");
  });
});
