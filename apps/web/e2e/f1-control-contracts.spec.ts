import { expect, test } from "./fixtures";

test("persists reviewed graph, config, and no-egress dry-run chains across reload", async ({
  allowConsumedRunAuthorization,
  page,
}) => {
  await page.goto("/");
  const projectName = `f1-control-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();

  await page.getByRole("button", { name: "添加便签" }).click();
  await page.getByLabel("便签内容").fill("必须经人工审阅后才应用");
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存");

  await page.getByLabel("dry-run 最大任务数").fill("2");
  await page.getByRole("button", { name: "审阅控制配置" }).click();
  await expect(
    page.getByRole("dialog", { name: "控制配置变更审阅" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认应用控制配置" }).click();
  await expect(page.getByRole("status").filter({ hasText: "控制配置已应用" })).toBeVisible();

  await page.getByLabel("dry-run 任务数").fill("1");
  await page.getByRole("button", { name: "审阅无外发 dry-run" }).click();
  await expect(
    page.getByRole("dialog", { name: "无外发 dry-run 审阅" }),
  ).toContainText("无外发、无费用、无 GPU、不产生媒体");
  const dryRunResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/v1\/projects\/[^/]+\/run-requests\/[^/]+\/dry-run$/.test(
        new URL(response.url()).pathname,
      ) &&
      response.status() === 200,
  );
  await page.getByRole("button", { name: "确认并执行 dry-run" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Dry-run 已完成" })).toBeVisible();

  const completedDryRun = await dryRunResponse;
  const repeatPath = new URL(completedDryRun.url()).pathname;
  allowConsumedRunAuthorization(completedDryRun.url());
  const repeatedDryRunResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === repeatPath &&
      response.status() === 409,
  );
  await page.getByRole("button", { name: "验证同一授权不可重复执行" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "已验证：同一授权再次执行被拒绝。" }),
  ).toBeVisible();

  const repeatedDryRun = await repeatedDryRunResponse;
  expect(repeatedDryRun.status()).toBe(409);

  const trace = page.getByLabel("操作与审批记录");
  await trace.getByRole("button", { name: "刷新" }).click();
  await expect(trace).toContainText("graph_change_set.applied");
  await expect(trace).toContainText("config_change_set.applied");
  await expect(trace).toContainText("run.dry_run_succeeded");

  await page.reload();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  const restoredTrace = page.getByLabel("操作与审批记录");
  await restoredTrace.getByRole("button", { name: "刷新" }).click();
  await expect(restoredTrace).toContainText("graph_change_set.applied");
  await expect(restoredTrace).toContainText("config_change_set.applied");
  await expect(restoredTrace).toContainText("run.dry_run_succeeded");
});
