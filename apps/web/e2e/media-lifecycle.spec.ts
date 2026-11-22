import { expect, test } from "./fixtures";

test("opens the B4 storage and two-step GC review without moving content before confirmation", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("项目名称").fill(`b4-lifecycle-${Date.now()}`);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await page.getByRole("button", { name: "打开媒体生命周期" }).click();
  await expect(page.getByRole("complementary", { name: "媒体生命周期" })).toBeVisible();
  await expect(page.getByRole("region", { name: "工作区存储" })).toBeVisible();

  await page.getByRole("button", { name: "审阅可清理内容" }).click();
  const dialog = page.getByRole("dialog", { name: "审阅清理计划" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("确认前不会移动任何正式素材。确认后先隔离 7 天，期间可以恢复。")).toBeVisible();
  await expect(dialog.getByText("当前没有可清理的正式内容。")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认移入隔离区" })).toBeDisabled();
});
