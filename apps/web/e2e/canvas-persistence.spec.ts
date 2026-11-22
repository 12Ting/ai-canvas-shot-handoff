import type { Locator, Page } from "@playwright/test";
import { expect, test } from "./fixtures";

async function connect(source: Locator, target: Locator, page: Page): Promise<void> {
  await expect(source).toBeVisible();
  await expect(target).toBeVisible();
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  if (!sourceBox || !targetBox) throw new Error("connection handles are not visible");

  for (const [locator, box] of [
    [source, sourceBox],
    [target, targetBox],
  ] as const) {
    const testId = await locator.getAttribute("data-testid");
    const hitTestId = await page.evaluate(
      ({ x, y }) =>
        document
          .elementFromPoint(x, y)
          ?.closest("[data-testid]")
          ?.getAttribute("data-testid"),
      { x: box.x + box.width / 2, y: box.y + box.height / 2 },
    );
    expect(hitTestId).toBe(testId);
  }

  await page.mouse.move(
    sourceBox.x + sourceBox.width / 2,
    sourceBox.y + sourceBox.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    targetBox.x + targetBox.width / 2,
    targetBox.y + targetBox.height / 2,
    { steps: 8 },
  );
  await page.mouse.up();
}

test("creates, connects, explicitly reviews, applies, and restores a graph", async ({
  page,
}) => {
  const expectedApiPort = process.env.AI_CANVAS_E2E_API_PORT ?? "18765";
  const expectedWebPort = process.env.AI_CANVAS_E2E_WEB_PORT ?? "15173";
  const health = await page.request.get("/api/v1/health");
  expect(new URL(health.url()).port).toBe(expectedWebPort);
  expect(health.headers()["x-ai-canvas-instance"]).toBe("development-e2e-child");
  const directApiHealth = await page.request.get(
    `http://127.0.0.1:${expectedApiPort}/api/v1/health`,
  );
  expect(directApiHealth.headers()["x-ai-canvas-instance"]).toBe(
    "development-e2e-child",
  );
  const webMarker = await page.request.get("/__ai_canvas_instance");
  expect((await webMarker.text()).trim()).toBe("development-e2e-child");

  await page.goto("/");
  expect(new URL(page.url()).port).toBe(expectedWebPort);
  const projectName = `e2e-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await page.getByRole("button", { name: "添加文案节点" }).click();
  await page.getByRole("button", { name: "添加提示词节点" }).click();
  await page.getByLabel("文案内容").fill("雨夜中的末班车");

  await connect(
    page.locator('[data-testid^="source-text-"]'),
    page.locator('[data-testid^="target-text-"]'),
    page,
  );
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存", { timeout: 5_000 });

  await page.reload();
  await expect(page.getByLabel("文案内容")).toHaveValue("雨夜中的末班车");
  await expect(page.getByRole("button", { name: "编辑结构化提示词" })).toHaveCount(1);
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);

  await page.getByLabel("文案内容").fill("雨夜中的末班车，灯熄了");
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存", { timeout: 5_000 });
  await page.reload();
  await expect(page.getByLabel("文案内容")).toHaveValue("雨夜中的末班车，灯熄了");
});
