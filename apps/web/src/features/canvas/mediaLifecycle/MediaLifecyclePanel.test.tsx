import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TranscodeConfirmationRequiredError } from "../../../api/client";
import { MediaLifecyclePanel } from "./MediaLifecyclePanel";

const api = vi.hoisted(() => ({
  createExternalReference: vi.fn(),
  createGcPlan: vi.fn(),
  grantExternalRoot: vi.fn(),
  importExternalCopy: vi.fn(),
  reconfirmExternalReference: vi.fn(),
  requestRendition: vi.fn(),
  storageSummary: vi.fn(),
  confirmGcPlan: vi.fn(),
  restoreQuarantine: vi.fn(),
  verifyExternalReference: vi.fn(),
}));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  ...api,
}));

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const REVISION_ID = "22222222-2222-4222-8222-222222222222";
const GRANT_ID = "33333333-3333-4333-8333-333333333333";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("mounts the open panel at the document body layer", async () => {
  api.storageSummary.mockResolvedValue({
    workspace_quota_bytes: 100 * 1024 ** 3,
    managed_blob_bytes: 0,
    staging_bytes: 0,
    quarantine_bytes: 0,
    active_reservation_bytes: 0,
    physical_free_bytes: 200 * 1024 ** 3,
  });

  const { container } = render(<MediaLifecyclePanel projectId={PROJECT_ID} />);
  fireEvent.click(screen.getByRole("button", { name: "打开媒体生命周期" }));

  const panel = await screen.findByRole("complementary", { name: "媒体生命周期" });
  expect(container.contains(panel)).toBe(false);
  expect(panel.parentElement).toBe(document.body);
});

it("makes external drift recovery, transcode confirmation, storage, and two-step GC reachable", async () => {
  api.storageSummary.mockResolvedValue({
    workspace_quota_bytes: 100 * 1024 ** 3,
    managed_blob_bytes: 3,
    staging_bytes: 0,
    quarantine_bytes: 0,
    active_reservation_bytes: 0,
    physical_free_bytes: 200 * 1024 ** 3,
  });
  api.grantExternalRoot.mockResolvedValue({ grant_id: GRANT_ID, display_name: "视频目录" });
  api.createExternalReference.mockResolvedValue({
    revision_id: REVISION_ID,
    artifact_id: "44444444-4444-4444-8444-444444444444",
    health: "DRIFTED",
    storage_kind: "EXTERNAL_REFERENCE",
    snapshot_id: "55555555-5555-4555-8555-555555555555",
  });
  api.requestRendition
    .mockRejectedValueOnce(
      new TranscodeConfirmationRequiredError({
        confirmationToken: "a".repeat(64),
        profile: "preview_720p",
        width: 1280,
        height: 720,
        estimatedOutputBytes: 4 * 1024 * 1024,
      }),
    )
    .mockResolvedValueOnce({ job_id: "66666666-6666-4666-8666-666666666666" });
  api.createGcPlan.mockResolvedValue({
    plan_id: "77777777-7777-4777-8777-777777777777",
    summary_sha256: "b".repeat(64),
    entries: [{
      entry_id: "88888888-8888-4888-8888-888888888888",
      target_id: REVISION_ID,
      reason: "discarded_unreferenced_revision",
      logical_reclaim_bytes: 3,
      physical_reclaim_bytes: 3,
    }],
  });
  api.confirmGcPlan.mockResolvedValue([]);

  render(<MediaLifecyclePanel projectId={PROJECT_ID} />);
  expect(screen.queryByRole("complementary", { name: "媒体生命周期" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "打开媒体生命周期" }));
  await screen.findByText("工作区存储");
  fireEvent.click(screen.getByRole("button", { name: "授权外部目录" }));
  await screen.findByText("已授权：视频目录");
  fireEvent.click(screen.getByRole("button", { name: "从已授权目录添加视频" }));
  await screen.findByText(/状态：DRIFTED/);
  expect(screen.queryByRole("video")).toBeNull();
  expect(screen.getByRole("button", { name: "检查外部状态" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "重新确认新版本" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "导入工作区副本" })).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "生成 720p 代理" }));
  await screen.findByRole("dialog", { name: "确认生成代理" });
  expect(screen.getByText(/源素材不会改变/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "确认开始转码" }));
  await waitFor(() => expect(api.requestRendition).toHaveBeenCalledTimes(2));

  fireEvent.click(screen.getByRole("button", { name: "审阅可清理内容" }));
  await screen.findByRole("dialog", { name: "审阅清理计划" });
  expect(screen.getByText(/确认前不会移动任何正式素材/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "确认移入隔离区" }));
  await screen.findByText(/已移入隔离区/);
});
