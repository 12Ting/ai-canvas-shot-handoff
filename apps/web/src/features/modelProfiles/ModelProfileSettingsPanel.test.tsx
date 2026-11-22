import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelProfileSettingsPanel } from "./ModelProfileSettingsPanel";

const api = vi.hoisted(() => ({
  applyConfigChangeSet: vi.fn(), approveConfigChangeSet: vi.fn(), getActiveControlConfig: vi.fn(),
  listModelProfileRevisions: vi.fn(), listModelProfiles: vi.fn(), proposeModelProfileConfigChangeSet: vi.fn(),
}));
vi.mock("../../api/client", () => api);

const projectId = "11111111-1111-4111-8111-111111111111";
const config = { id: "22222222-2222-4222-8222-222222222222", sequence: 1, dry_run_max_tasks: 1, active_canvas_agent_profile_revision_id: null, config_sha256: "a".repeat(64), created_at: "2026-09-03T00:00:00Z" };
const pending = { id: "33333333-3333-4333-8333-333333333333", audit_project_id: projectId, correlation_id: "44444444-4444-4444-8444-444444444444", base_config_revision_id: config.id, changeset_sha256: "b".repeat(64), operations: [], risk_summary: { classes: ["CONTROL_CONFIG_WRITE", "MODEL_EGRESS"] }, state: "PENDING_APPROVAL", created_at: "2026-09-03T00:00:00Z" };

describe("ModelProfileSettingsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getActiveControlConfig.mockResolvedValue(config);
    api.listModelProfiles.mockResolvedValue([]);
    api.proposeModelProfileConfigChangeSet.mockResolvedValue(pending);
    api.approveConfigChangeSet.mockResolvedValue({ ...pending, state: "REJECTED" });
    vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValue("55555555-5555-4555-8555-555555555555"), subtle: { digest: vi.fn().mockResolvedValue(new Uint8Array(32).buffer) } });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("exposes the full non-secret declaration and only mutates through ConfigChangeSet review", async () => {
    render(<ModelProfileSettingsPanel projectId={projectId} />);
    await screen.findByText("活动版本：未配置");
    expect(screen.queryByLabelText(/key|密钥|secret/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "审阅模型配置" }));
    const dialog = await screen.findByRole("dialog", { name: "模型配置 ConfigChangeSet 审阅" });
    expect(dialog.textContent).toContain("LOCAL_LOOPBACK / LOCAL_DEVICE");
    expect(dialog.textContent).toContain("没有密钥输入");
    expect(api.proposeModelProfileConfigChangeSet).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(api.approveConfigChangeSet).toHaveBeenCalledWith(pending.id, pending.changeset_sha256, "REJECTED"));
    expect(api.applyConfigChangeSet).not.toHaveBeenCalled();
  });
});
