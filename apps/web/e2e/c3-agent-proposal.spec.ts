import { expect, test } from "./fixtures";

test("C3 reviews loopback egress, sends once, and hands a text selection to NEW_BRANCH", async ({
  allowConsumedAgentProposal,
  modelWitness,
  page,
}) => {
  await page.goto("/");
  const projectName = `c3-agent-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await page.getByRole("button", { name: "添加便签" }).click();
  await page.getByLabel("便签内容").fill("前🙂后");
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存");

  await page.getByText("C3 模型与 Agent", { exact: true }).click();

  await page.getByRole("button", { name: "审阅模型配置" }).click();
  const profileReview = page.getByRole("dialog", { name: "模型配置 ConfigChangeSet 审阅" });
  await expect(profileReview).toContainText("http://127.0.0.1:8011/v1/chat/completions");
  await expect(profileReview).toContainText("此界面没有密钥输入");
  await page.getByRole("button", { name: "确认激活" }).click();
  await expect(page.getByText(/模型配置版本已通过受信 ConfigChangeSet 激活/)).toBeVisible();

  await page.getByLabel("指令").fill("只改写选中的 emoji");
  await page.getByText("仅文本选择区").click();
  await page.getByLabel("start UTF-16").fill("1");
  await page.getByLabel("end UTF-16").fill("3");
  await page.getByRole("button", { name: "生成外发计划（不发送）" }).click();
  await expect(page.getByText("完整 EgressPlan")).toBeVisible();
  await expect(page.getByText("LOCAL_LOOPBACK / LOCAL_DEVICE")).toBeVisible();
  expect(modelWitness.calls).toBe(0);
  await page.getByRole("button", { name: "拒绝" }).last().click();
  await expect(page.getByText("已拒绝；模型端点未收到请求。")).toBeVisible();
  expect(modelWitness.calls).toBe(0);

  await page.getByRole("button", { name: "新建提案" }).click();
  await page.getByRole("button", { name: "生成外发计划（不发送）" }).click();
  await page.getByRole("button", { name: "批准但不发送" }).click();
  await expect(page.getByText("已批准；尚未发送。请再次明确执行。")).toBeVisible();
  expect(modelWitness.calls).toBe(0);
  const executeResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" && /\/agent-proposals\/[^/]+\/execute$/.test(new URL(response.url()).pathname) && response.status() === 200,
  );
  await page.getByRole("button", { name: "明确执行一次模型调用" }).click();
  const executed = await executeResponse;
  await expect(page.getByRole("dialog", { name: "Agent 图谱提案审阅" })).toContainText("将创建实验分支");
  expect(modelWitness.calls).toBe(1);

  allowConsumedAgentProposal(executed.url());
  const repeatStatus = await page.evaluate(async (path) => {
    const token = sessionStorage.getItem("ai-canvas-session");
    const response = await fetch(path, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ kind: "execute_agent_proposal" }) });
    return response.status;
  }, new URL(executed.url()).pathname);
  expect(repeatStatus).toBe(409);
  expect(modelWitness.calls).toBe(1);

  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.getByLabel("便签内容")).toHaveValue("前由本地 C3 witness 改写🙂后");
  expect(modelWitness.calls).toBe(1);
  await page.reload();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  await page.getByText("C3 模型与 Agent").click();
  await expect(page.getByText("完整 EgressPlan")).toBeVisible();
  expect(modelWitness.calls).toBe(1);
});
