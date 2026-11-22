import type { BrowserContext, Page, Response } from "@playwright/test";
import { expect, test } from "./fixtures";

type Project = {
  id: string;
  active_revision_id: string;
};

type Branch = {
  id: string;
  name: string;
  forked_from_revision_id: string;
  head: { graph_revision_id: string; version: number };
  is_selected: boolean;
};

type BranchWorkspace = {
  branches: Branch[];
  selection: {
    branch_id: string;
    graph_revision_id: string;
    version: number;
  };
};

type GraphChangeSet = {
  id: string;
  applied_graph_revision_id: string;
  applied_branch_id: string | null;
  created_branch_id: string | null;
};

type BranchGraph = {
  branch: Branch;
  revision: {
    revision_id: string;
    snapshot: {
      nodes: Array<{ kind: string; data: Record<string, unknown> }>;
    };
  };
};

const graphChangeApplyPath =
  /\/api\/v1\/projects\/[^/]+\/graph-change-sets\/[^/]+\/apply$/;

test.setTimeout(180_000);

async function apiJson<T>(page: Page, path: string): Promise<T> {
  return page.evaluate(async (requestPath) => {
    const token = sessionStorage.getItem("ai-canvas-session");
    if (!token) throw new Error("AI Canvas session is not established");
    const response = await fetch(requestPath, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(`${requestPath} failed (${response.status}): ${JSON.stringify(body)}`);
    }
    return body;
  }, path);
}

async function workspace(page: Page, projectId: string): Promise<BranchWorkspace> {
  return apiJson(page, `/api/v1/projects/${projectId}/branches`);
}

async function ensureBranchPanelOpen(page: Page): Promise<void> {
  const trigger = page.locator(".branch-panel").getByRole("button", {
    name: /^(?:查看\/正式：.+|查看：.+｜正式：.+)$/,
  });
  if ((await trigger.getAttribute("aria-expanded")) !== "true") {
    await trigger.click();
  }
  await expect(page.getByRole("region", { name: "图谱分支" })).toBeVisible();
}

async function applyDraft(page: Page): Promise<GraphChangeSet> {
  const response = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "POST" &&
      graphChangeApplyPath.test(new URL(candidate.url()).pathname) &&
      candidate.status() === 200,
  );
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  const review = page.getByRole("dialog", { name: "图谱变更审阅" });
  await expect(review).toContainText("不会运行媒体、GPU、付费或外发操作");
  await review.getByRole("button", { name: "确认应用变更" }).click();
  const result = (await (await response).json()) as GraphChangeSet;
  await expect(page.locator(".save-status")).toHaveText("已保存");
  return result;
}

async function undoLastDirectApply(page: Page): Promise<GraphChangeSet> {
  await page.getByRole("button", { name: "撤销刚才的直接应用" }).click();
  const review = page.getByRole("dialog", { name: "撤销直接应用审阅" });
  await expect(review).toContainText("将创建一个新的图谱版本");
  const response = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "POST" &&
      graphChangeApplyPath.test(new URL(candidate.url()).pathname) &&
      candidate.status() === 200,
  );
  await review.getByRole("button", { name: "确认创建撤销版本" }).click();
  const result = (await (await response).json()) as GraphChangeSet;
  await expect(page.getByRole("status").filter({ hasText: "已创建撤销版本" })).toBeVisible();
  return result;
}

async function openBranch(page: Page, name: string): Promise<void> {
  await ensureBranchPanelOpen(page);
  const button = page.getByRole("button", {
    name: new RegExp(`^${name} (?:正式选择|实验分支)`),
  });
  if ((await button.getAttribute("aria-current")) !== "page") {
    await button.click();
  }
  await expect(
    page.getByRole("button", {
      name: new RegExp(`^${name} (?:正式选择|实验分支) · 正在查看$`),
    }),
  ).toBeVisible();
}

async function installNetworkBoundary(context: BrowserContext): Promise<{
  external: string[];
  execution: string[];
}> {
  const external: string[] = [];
  const execution: string[] = [];
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname !== "127.0.0.1") {
      external.push(request.url());
      await route.abort();
      return;
    }
    if (
      request.method() !== "GET" &&
      /\/(?:run-requests|artifact-imports|media\/imports|attempts|node-runs)(?:\/|$)/.test(
        url.pathname,
      )
    ) {
      execution.push(`${request.method()} ${url.pathname}`);
    }
    await route.continue();
  });
  return { external, execution };
}

async function appliedResponse(response: Promise<Response>): Promise<GraphChangeSet> {
  return (await (await response).json()) as GraphChangeSet;
}

test("C2 keeps branch exploration explicit, append-only, conflict-safe, and local", async ({
  allowGraphRevisionConflict,
  context,
  page,
}) => {
  const network = await installNetworkBoundary(context);
  await page.goto("/");
  const projectName = `c2-branches-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  await expect(page).toHaveURL(/\?project=[0-9a-f-]+$/i);
  await expect(
    page.getByRole("button", { name: "查看/正式：主线" }),
  ).toBeVisible();

  const projectId = new URL(page.url()).searchParams.get("project");
  expect(projectId).toBeTruthy();
  if (!projectId) throw new Error("created project URL has no project id");

  await ensureBranchPanelOpen(page);
  await expect(
    page.getByRole("button", { name: "主线 正式选择 · 正在查看" }),
  ).toBeVisible();
  const initialWorkspace = await workspace(page, projectId);
  expect(initialWorkspace.branches).toHaveLength(1);
  const main = initialWorkspace.branches[0];
  expect(main?.name).toBe("主线");
  expect(main?.is_selected).toBe(true);
  expect(initialWorkspace.selection.graph_revision_id).toBe(main?.head.graph_revision_id);
  const initialRevisionId = initialWorkspace.selection.graph_revision_id;

  await page.getByRole("button", { name: "添加便签" }).click();
  await page.getByLabel("便签内容").fill("C2 主线直接应用");
  await expect(page.getByLabel("应用方式")).toHaveValue("DIRECT");
  const firstDirect = await applyDraft(page);
  expect(firstDirect.created_branch_id).toBeNull();
  expect(firstDirect.applied_branch_id).toBe(main?.id);
  expect(firstDirect.applied_graph_revision_id).not.toBe(initialRevisionId);
  const afterDirect = await workspace(page, projectId);
  expect(afterDirect.selection.graph_revision_id).toBe(firstDirect.applied_graph_revision_id);
  expect(afterDirect.branches[0]?.forked_from_revision_id).toBe(initialRevisionId);

  await page.getByLabel("便签内容").fill("C2 实验分支起点");
  await page.getByLabel("应用方式").selectOption("NEW_BRANCH");
  await page.getByLabel("新分支名称").fill("探索 C2");
  const newBranchResponse = page.waitForResponse(
    (candidate) =>
      candidate.request().method() === "POST" &&
      graphChangeApplyPath.test(new URL(candidate.url()).pathname) &&
      candidate.status() === 200,
  );
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  const newBranchReview = page.getByRole("dialog", { name: "图谱变更审阅" });
  await expect(newBranchReview).toContainText("将创建实验分支“探索 C2”");
  await expect(newBranchReview).toContainText("正式选择保持不变");
  await newBranchReview.getByRole("button", { name: "确认应用变更" }).click();
  const newBranch = await appliedResponse(newBranchResponse);
  expect(newBranch.created_branch_id).toBeTruthy();
  await expect(page.getByText(/正在编辑未提升分支/)).toBeVisible();

  const afterBranch = await workspace(page, projectId);
  expect(afterBranch.branches).toHaveLength(2);
  expect(afterBranch.selection).toEqual(afterDirect.selection);
  const experiment = afterBranch.branches.find((branch) => branch.name === "探索 C2");
  expect(experiment?.id).toBe(newBranch.created_branch_id);
  expect(experiment?.head.graph_revision_id).toBe(newBranch.applied_graph_revision_id);
  await expect(
    page.getByRole("button", { name: "查看：探索 C2｜正式：主线" }),
  ).toBeVisible();
  await ensureBranchPanelOpen(page);
  await expect(
    page.getByRole("button", { name: "探索 C2 实验分支 · 正在查看" }),
  ).toBeVisible();

  await page.getByLabel("便签内容").fill("C2 未提升分支继续编辑");
  await page.getByLabel("应用方式").selectOption("DIRECT");
  const experimentDirect = await applyDraft(page);
  const afterExperimentDirect = await workspace(page, projectId);
  expect(afterExperimentDirect.selection).toEqual(afterDirect.selection);
  expect(
    afterExperimentDirect.branches.find((branch) => branch.id === experiment?.id)?.head
      .graph_revision_id,
  ).toBe(experimentDirect.applied_graph_revision_id);

  await ensureBranchPanelOpen(page);
  await page.getByRole("button", { name: "提升为当前选择" }).click();
  const promotionReview = page.getByRole("dialog", { name: "提升分支为正式选择" });
  await expect(promotionReview).toContainText("只移动正式选择，不复制或重写图谱版本");
  await promotionReview.getByRole("button", { name: "提升为正式选择" }).click();
  await expect(
    page.getByRole("button", { name: "查看/正式：探索 C2" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "探索 C2 正式选择 · 正在查看" }),
  ).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "查看/正式：探索 C2" }),
  ).toBeVisible();
  await ensureBranchPanelOpen(page);
  await expect(
    page.getByRole("button", { name: "探索 C2 正式选择 · 正在查看" }),
  ).toBeVisible();
  const restoredWorkspace = await workspace(page, projectId);
  const restoredProject = await apiJson<Project>(page, `/api/v1/projects/${projectId}`);
  expect(restoredWorkspace.selection.branch_id).toBe(experiment?.id);
  expect(restoredProject.active_revision_id).toBe(restoredWorkspace.selection.graph_revision_id);

  await page.getByRole("button", { name: "撤销上次提升" }).click();
  const undoPromotion = page.getByRole("dialog", { name: "撤销上次提升" });
  await expect(undoPromotion).toContainText("恢复先前的正式选择，不删除任何分支");
  await undoPromotion.getByRole("button", { name: "确认撤销提升" }).click();
  await expect(page.getByText(/正在编辑未提升分支/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "查看：探索 C2｜正式：主线" }),
  ).toBeVisible();
  const afterPromotionUndo = await workspace(page, projectId);
  expect(afterPromotionUndo.selection.branch_id).toBe(main?.id);
  expect(afterPromotionUndo.branches.map((branch) => branch.id).sort()).toEqual(
    afterExperimentDirect.branches.map((branch) => branch.id).sort(),
  );

  await page.getByLabel("便签内容").fill("C2 可撤销直接应用");
  await expect(page.getByLabel("应用方式")).toHaveValue("DIRECT");
  const undoableDirect = await applyDraft(page);
  const beforeUndoWorkspace = await workspace(page, projectId);
  const undoResult = await undoLastDirectApply(page);
  const afterUndoWorkspace = await workspace(page, projectId);
  expect(undoResult.applied_graph_revision_id).not.toBe(
    undoableDirect.applied_graph_revision_id,
  );
  expect(
    afterUndoWorkspace.branches.find((branch) => branch.id === experiment?.id)?.head.version,
  ).toBe(
    (beforeUndoWorkspace.branches.find((branch) => branch.id === experiment?.id)?.head.version ?? 0) +
      1,
  );

  const secondPage = await context.newPage();
  await secondPage.goto(`/?project=${projectId}`);
  await expect(secondPage.getByRole("heading", { name: projectName })).toBeVisible();
  await openBranch(secondPage, "探索 C2");
  await secondPage.getByLabel("便签内容").fill("C2 第二标签未确认草稿");

  await page.getByLabel("便签内容").fill("C2 第一标签先保存");
  const firstPageAdvance = await applyDraft(page);
  allowGraphRevisionConflict(
    secondPage,
    `${page.url().split("?")[0]}api/v1/projects/${projectId}/graph-change-sets`,
  );
  await secondPage.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(secondPage.locator(".save-status")).toContainText("保存冲突");
  await expect(secondPage.getByRole("alert")).toContainText("你的本地画布仍被保留");
  await expect(secondPage.getByLabel("便签内容")).toHaveValue("C2 第二标签未确认草稿");
  const serverExperiment = await apiJson<BranchGraph>(
    page,
    `/api/v1/projects/${projectId}/branches/${experiment?.id}/graph`,
  );
  expect(serverExperiment.revision.revision_id).toBe(firstPageAdvance.applied_graph_revision_id);
  expect(serverExperiment.revision.snapshot.nodes).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        kind: "note",
        data: expect.objectContaining({ text: "C2 第一标签先保存" }),
      }),
    ]),
  );

  expect(network.external).toEqual([]);
  expect(network.execution).toEqual([]);
});
