import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PromptBatchPanel } from "./PromptBatchPanel";

const api = vi.hoisted(() => ({
  requestPromptProfileExtraction: vi.fn(),
  approvePromptProfileExtraction: vi.fn(),
  executePromptProfileExtraction: vi.fn(),
  materializePromptProfileExtraction: vi.fn(),
  preflightPromptBatch: vi.fn(),
  requestPromptBatch: vi.fn(),
  getPromptBatch: vi.fn(),
  approvePromptBatch: vi.fn(),
  executePromptBatchItem: vi.fn(),
  decidePromptBatchItem: vi.fn(),
  finalizePromptBatch: vi.fn(),
  acceptAllSucceededPromptBatch: vi.fn(),
  approvePromptBatchRetry: vi.fn(),
  retryFailedPromptBatch: vi.fn(),
}));

vi.mock("../../api/client", () => api);

const projectId = "11111111-1111-4111-8111-111111111111";
const promptNodeId = "22222222-2222-4222-8222-222222222222";

function extractionResponse() {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    project_id: projectId,
    source_node_id: promptNodeId,
    source_document_sha256: "a".repeat(64),
    state: "PENDING_APPROVAL",
    output: null,
    output_sha256: null,
    compiled_profile: null,
    materialized_resource_revision_id: null,
    failure_code: null,
    created_at: "2026-09-12T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
    run_request: {
      id: "44444444-4444-4444-8444-444444444444",
      project_id: projectId,
      correlation_id: "55555555-5555-4555-8555-555555555555",
      operation_kind: "PROMPT_PROFILE_EXTRACTION",
      model_profile_revision_id: "66666666-6666-4666-8666-666666666666",
      branch_id: "77777777-7777-4777-8777-777777777777",
      branch_head_version: 1,
      selection_version: 1,
      scope_sha256: "b".repeat(64),
      request_sha256: "c".repeat(64),
      egress_plan_sha256: "d".repeat(64),
      state: "PENDING_APPROVAL",
      egress_plan: {
        mode: "MODEL_PROVIDER",
        endpoint: "http://127.0.0.1:8765/v1/chat/completions",
        locality: "LOCAL_LOOPBACK",
        endpoint_identity_sha256: "e".repeat(64),
        external_data_classes: ["USER_INSTRUCTION", "PROMPT_EXAMPLE_DOCUMENT"],
        task_count: 1,
        gpu_count: 0,
        media_execution: false,
        cost_mode: "HARD",
        currency: "CNY",
        cost_bound_minor: 0,
        data_residency: "LOCAL_DEVICE",
        retention_policy: "NO_RETENTION_DECLARED",
        max_retries: 0,
        request_sha256: "c".repeat(64),
        plan_sha256: "d".repeat(64),
        preview: {
          model_id: "prompt-editor-v1",
          contexts: [
            { category: "USER_INSTRUCTION", content: "只提炼组织与格式规则", characters: 11 },
            { category: "PROMPT_EXAMPLE_DOCUMENT", content: { rendered_text: "示例提示词" }, characters: 5 },
          ],
          original_characters: 5,
          sent_characters: 5,
          truncated: false,
        },
      },
    },
  };
}

describe("PromptBatchPanel informed approval", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.requestPromptProfileExtraction.mockResolvedValue(extractionResponse());
  });

  it("shows sealed egress evidence before approval and clears it on context change", async () => {
    const props = {
      branchContext: {
        branchId: "77777777-7777-4777-8777-777777777777",
        branchHeadRevisionId: "88888888-8888-4888-8888-888888888888",
        branchHeadVersion: 1,
        selectionVersion: 1,
      },
      nodes: [{ id: promptNodeId, data: { kind: "prompt" } }] as never,
      shots: [],
      onGraphChangeSet: vi.fn(),
      projectId,
    };
    const { rerender } = render(<PromptBatchPanel {...props} />);
    fireEvent.change(screen.getByLabelText("示例 PromptNode"), {
      target: { value: promptNodeId },
    });
    fireEvent.click(screen.getByRole("button", { name: "请求提炼预览" }));

    expect(await screen.findByRole("heading", { name: "外发审批证据" })).toBeTruthy();
    expect(screen.getByText("http://127.0.0.1:8765/v1/chat/completions")).toBeTruthy();
    expect(screen.getByText(/prompt-editor-v1/)).toBeTruthy();
    expect(screen.getByText(/LOCAL_DEVICE/)).toBeTruthy();
    expect(screen.getByText(/NO_RETENTION_DECLARED/)).toBeTruthy();
    expect(screen.getByText(/GPU 0/)).toBeTruthy();
    expect(screen.getByText("只提炼组织与格式规则")).toBeTruthy();
    expect(screen.getByRole("button", { name: "批准准确提炼请求" })).toBeTruthy();

    rerender(
      <PromptBatchPanel
        {...props}
        projectId="99999999-9999-4999-8999-999999999999"
      />,
    );
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "外发审批证据" })).toBeNull(),
    );
  });
});
