import { expect, test } from "./fixtures";

test("serves the built canvas with production CSP and working mutations", async ({
  page,
}) => {
  const health = await page.request.get("/api/v1/health");
  expect(health.headers()["x-ai-canvas-instance"]).toBe("release-e2e-child");
  const response = await page.goto("/");
  expect(response?.headers()["content-security-policy"]).toContain("default-src 'self'");

  const projectName = `release-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await page.getByRole("button", { name: "添加便签" }).click();
  await page.getByLabel("便签内容").fill("packaged release smoke");
  await expect(page.locator(".save-status")).toHaveText("有未保存修改", { timeout: 5_000 });
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存", { timeout: 5_000 });

  await page.reload();
  await expect(page.getByLabel("便签内容")).toHaveValue("packaged release smoke");
  await page.getByRole("button", { name: "返回项目列表" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("button", { name: projectName })).toBeVisible();
  await page.getByRole("button", { name: projectName }).click();
  await expect(page.getByLabel("便签内容")).toHaveValue("packaged release smoke");
});

test("rejects protected release data from a clean browser context", async ({
  browser,
  baseURL,
}) => {
  if (!baseURL) throw new Error("Release E2E baseURL is missing");
  const cleanContext = await browser.newContext({ baseURL });
  try {
    expect((await cleanContext.storageState()).origins).toEqual([]);
    const response = await cleanContext.request.get("/api/v1/projects");
    expect(response.status()).toBe(401);
    expect(await response.json()).toEqual({ code: "unauthorized" });
  } finally {
    await cleanContext.close();
  }
});
