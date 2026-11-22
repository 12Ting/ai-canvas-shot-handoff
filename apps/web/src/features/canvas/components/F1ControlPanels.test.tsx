import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ControlConfigPanel } from "./ControlConfigPanel";
import { NoEgressDryRunPanel } from "./NoEgressDryRunPanel";
import { OperationTracePanel } from "./OperationTracePanel";

const api = vi.hoisted(() => ({
  ApiRequestError: class ApiRequestError extends Error {
    readonly status: number;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  acknowledgeExperienceEventCursor: vi.fn(),
  applyConfigChangeSet: vi.fn(),
  approveConfigChangeSet: vi.fn(),
  approveNoEgressDryRun: vi.fn(),
  executeNoEgressDryRun: vi.fn(),
  getActiveControlConfig: vi.fn(),
  proposeConfigChangeSet: vi.fn(),
  readExperienceEventCursor: vi.fn(),
  requestNoEgressDryRun: vi.fn(),
}));

vi.mock("../../../api/client", () => api);

const projectId = "11111111-1111-4111-8111-111111111111";
const config = {
  id: "22222222-2222-4222-8222-222222222222",
  sequence: 1,
  dry_run_max_tasks: 1,
  config_sha256: "a".repeat(64),
  created_at: "2026-08-27T00:00:00Z",
};

describe("F1 control panels", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  afterEach(cleanup);

  it("keeps control configuration in its own explicit review and apply path", async () => {
    const proposal = {
      id: "33333333-3333-4333-8333-333333333333",
      audit_project_id: projectId,
      correlation_id: "44444444-4444-4444-8444-444444444444",
      base_config_revision_id: config.id,
      changeset_sha256: "b".repeat(64),
      state: "PENDING_APPROVAL",
      created_at: "2026-08-27T00:00:00Z",
    };
    api.getActiveControlConfig.mockResolvedValueOnce(config).mockResolvedValueOnce({
      ...config,
      id: "55555555-5555-4555-8555-555555555555",
      sequence: 2,
      dry_run_max_tasks: 3,
    });
    api.proposeConfigChangeSet.mockResolvedValue(proposal);
    api.approveConfigChangeSet.mockResolvedValue({ ...proposal, state: "APPROVED" });
    api.applyConfigChangeSet.mockResolvedValue({ ...proposal, state: "APPLIED" });

    render(<ControlConfigPanel projectId={projectId} />);
    await screen.findByText("dry-run 最大任务数：1");
    fireEvent.change(screen.getByLabelText("dry-run 最大任务数"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "审阅控制配置" }));

    expect(await screen.findByRole("dialog", { name: "控制配置变更审阅" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认应用控制配置" }));

    await screen.findByText("控制配置已应用");
    expect(api.proposeConfigChangeSet).toHaveBeenCalledWith(projectId, {
      baseConfigRevisionId: config.id,
      dryRunMaxTasks: 3,
    });
    expect(api.approveConfigChangeSet).toHaveBeenCalledWith(
      proposal.id,
      proposal.changeset_sha256,
    );
  });

  it("shows the no-egress plan before consuming its one-time authorization", async () => {
    const request = {
      id: "66666666-6666-4666-8666-666666666666",
      project_id: projectId,
      correlation_id: "77777777-7777-4777-8777-777777777777",
      request_sha256: "c".repeat(64),
      egress_plan_sha256: "d".repeat(64),
      egress_plan: {
        mode: "NONE",
        endpoint: null,
        external_data_classes: [],
        task_count: 1,
        cost_bound: 0,
        gpu_count: 0,
        request_sha256: "c".repeat(64),
        plan_sha256: "d".repeat(64),
      },
      state: "PENDING_APPROVAL",
    };
    api.requestNoEgressDryRun.mockResolvedValue(request);
    api.approveNoEgressDryRun.mockResolvedValue({
      id: "88888888-8888-4888-8888-888888888888",
      state: "APPROVED",
      expires_at: "2026-08-27T00:05:00Z",
    });
    api.executeNoEgressDryRun
      .mockResolvedValueOnce({
        id: "99999999-9999-4999-8999-999999999999",
        run_request_id: request.id,
        terminal_state: "DRY_RUN_SUCCEEDED",
        created_at: "2026-08-27T00:00:01Z",
      })
      .mockRejectedValueOnce(new api.ApiRequestError(409, "run_authorization_consumed"));

    render(<NoEgressDryRunPanel projectId={projectId} />);
    fireEvent.click(screen.getByRole("button", { name: "审阅无外发 dry-run" }));

    const dialog = await screen.findByRole("dialog", { name: "无外发 dry-run 审阅" });
    expect(dialog.textContent).toContain("无外发、无费用、无 GPU、不产生媒体");
    expect(dialog.textContent).toContain(`计划哈希：${"d".repeat(12)}`);
    expect(dialog.textContent).toContain(`请求哈希：${"c".repeat(12)}`);
    fireEvent.click(screen.getByRole("button", { name: "确认并执行 dry-run" }));

    await screen.findByText("Dry-run 已完成");
    expect(api.approveNoEgressDryRun).toHaveBeenCalledWith(
      projectId,
      request.id,
      request.request_sha256,
    );
    expect(api.executeNoEgressDryRun).toHaveBeenCalledWith(projectId, request.id);

    fireEvent.click(screen.getByRole("button", { name: "验证同一授权不可重复执行" }));

    await screen.findByText("已验证：同一授权再次执行被拒绝。");
    expect(api.executeNoEgressDryRun).toHaveBeenNthCalledWith(2, projectId, request.id);
  });

  it("records rejected config and run reviews and restores their triggers", async () => {
    const proposal = {
      id: "33333333-3333-4333-8333-333333333333",
      audit_project_id: projectId,
      correlation_id: "44444444-4444-4444-8444-444444444444",
      base_config_revision_id: config.id,
      changeset_sha256: "b".repeat(64),
      state: "PENDING_APPROVAL",
      created_at: "2026-08-27T00:00:00Z",
    };
    const request = {
      id: "66666666-6666-4666-8666-666666666666",
      project_id: projectId,
      correlation_id: "77777777-7777-4777-8777-777777777777",
      request_sha256: "c".repeat(64),
      egress_plan_sha256: "d".repeat(64),
      egress_plan: {
        mode: "NONE",
        endpoint: null,
        external_data_classes: [],
        task_count: 1,
        cost_bound: 0,
        gpu_count: 0,
        request_sha256: "c".repeat(64),
        plan_sha256: "d".repeat(64),
      },
      state: "PENDING_APPROVAL",
    };
    api.getActiveControlConfig.mockResolvedValue(config);
    api.proposeConfigChangeSet.mockResolvedValue(proposal);
    api.approveConfigChangeSet.mockResolvedValue({ ...proposal, state: "REJECTED" });
    api.requestNoEgressDryRun.mockResolvedValue(request);
    api.approveNoEgressDryRun.mockResolvedValue({ ...request, state: "REJECTED" });

    const { unmount } = render(<ControlConfigPanel projectId={projectId} />);
    await screen.findByText("dry-run 最大任务数：1");
    const configTrigger = screen.getByRole("button", { name: "审阅控制配置" });
    fireEvent.click(configTrigger);
    await screen.findByRole("dialog", { name: "控制配置变更审阅" });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() =>
      expect(api.approveConfigChangeSet).toHaveBeenCalledWith(
        proposal.id,
        proposal.changeset_sha256,
        "REJECTED",
      ),
    );
    await waitFor(() => expect(document.activeElement).toBe(configTrigger));
    unmount();

    render(<NoEgressDryRunPanel projectId={projectId} />);
    const runTrigger = screen.getByRole("button", { name: "审阅无外发 dry-run" });
    fireEvent.click(runTrigger);
    await screen.findByRole("dialog", { name: "无外发 dry-run 审阅" });
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() =>
      expect(api.approveNoEgressDryRun).toHaveBeenCalledWith(
        projectId,
        request.id,
        request.request_sha256,
        "REJECTED",
      ),
    );
    await waitFor(() => expect(document.activeElement).toBe(runTrigger));
  });

  it("reads and explicitly acknowledges the persistent event cursor", async () => {
    const page = {
      events: [
        {
          event_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          stream_position: 1,
          event_type: "graph_change_set.applied",
          correlation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        },
      ],
      next_after: 1,
      high_watermark: 1,
    };
    api.readExperienceEventCursor.mockResolvedValue(page);
    api.acknowledgeExperienceEventCursor.mockResolvedValue({
      consumer_key: "f1-web",
      last_ack_stream_position: 1,
    });

    render(<OperationTracePanel projectId={projectId} />);
    await screen.findByText("graph_change_set.applied");
    fireEvent.click(screen.getByRole("button", { name: "确认已读" }));

    await screen.findByText("已确认读取到当前位置");
    expect(api.acknowledgeExperienceEventCursor).toHaveBeenCalledWith(projectId, "f1-web", 1);
  });
});
