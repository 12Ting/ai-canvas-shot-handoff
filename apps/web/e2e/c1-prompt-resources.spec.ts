import { expect, test, type Locator, type Page } from "@playwright/test";

const promptText = "原文🙂\n第二行";
test.setTimeout(120_000);

async function apiJson<T>(page: Page, path: string, init: RequestInit = {}): Promise<T> {
  return page.evaluate(
    async ({ requestPath, requestInit }) => {
      const token = sessionStorage.getItem("ai-canvas-session");
      if (!token) throw new Error("AI Canvas session is not established");
      const headers = new Headers(requestInit.headers);
      headers.set("Authorization", `Bearer ${token}`);
      const response = await fetch(requestPath, { ...requestInit, headers });
      const body = await response.json();
      if (!response.ok) throw new Error(`${requestPath} failed (${response.status}): ${JSON.stringify(body)}`);
      return body;
    },
    { requestPath: path, requestInit: init },
  );
}

async function seedV2Project(page: Page, name: string) {
  const project = await apiJson<{
    id: string;
    active_revision_id: string;
  }>(page, "/api/v1/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const promptNodeId = crypto.randomUUID();
  const shotNodeId = crypto.randomUUID();
  const shotId = crypto.randomUUID();
  const proposal = await apiJson<{
    id: string;
    changeset_sha256: string;
  }>(page, `/api/v1/projects/${project.id}/graph-change-sets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: "graph_change_set",
      schema_version: 1,
      base_graph_revision_id: project.active_revision_id,
      client_mutation_id: crypto.randomUUID(),
      operations: [
        {
          op: "replace_snapshot",
          snapshot: {
            schema_version: 2,
            nodes: [
              {
                id: promptNodeId,
                kind: "prompt",
                position: { x: 120, y: 100 },
                data: { text: promptText },
              },
              {
                id: shotNodeId,
                kind: "shot",
                position: { x: 420, y: 100 },
                data: { shot_id: shotId },
              },
            ],
            edges: [],
            shots: [
              {
                id: shotId,
                order: 0,
                image_revision_id: null,
                video_revision_id: null,
                reference_revision_ids: [],
              },
            ],
          },
        },
      ],
    }),
  });
  await apiJson(page, `/api/v1/projects/${project.id}/graph-change-sets/${proposal.id}/approval-decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      decision: "APPROVED",
      expected_changeset_sha256: proposal.changeset_sha256,
    }),
  });
  await apiJson(page, `/api/v1/projects/${project.id}/graph-change-sets/${proposal.id}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_changeset_sha256: proposal.changeset_sha256 }),
  });
  return { projectId: project.id, promptNodeId, shotId };
}

async function createResource(
  page: Page,
  kindLabel: "风格" | "角色" | "提示词" | "媒体",
  name: string,
  primaryLabel: string,
  primary: string,
  secondaryLabel?: string,
  secondary?: string,
): Promise<void> {
  await page.getByRole("tab", { name: kindLabel }).click();
  await page.getByRole("button", { name: `新建${kindLabel}资源` }).click();
  const editor = page.getByRole("dialog", { name: "新建资源" });
  await editor.getByLabel("名称").fill(name);
  await editor.getByLabel(primaryLabel).fill(primary);
  if (secondaryLabel && secondary !== undefined) {
    await editor.getByLabel(secondaryLabel).fill(secondary);
  }
  await editor.getByRole("button", { name: "创建资源" }).click();
  await expect(page.getByRole("article").filter({ hasText: name })).toBeVisible();
}

async function bindResource(
  page: Page,
  kindLabel: "风格" | "角色" | "提示词" | "媒体",
  name: string,
  scope: "项目" | "分镜" | "节点",
  bindingKey: string,
): Promise<void> {
  await page.getByRole("tab", { name: kindLabel }).click();
  const card = page.getByRole("article").filter({ hasText: name });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "绑定" }).click();
  const dialog = page.getByRole("dialog", { name: `绑定资源 · ${name}` });
  await dialog.getByLabel("作用域").selectOption({ label: scope });
  if (scope !== "项目") {
    await expect(dialog.getByLabel("作用目标")).not.toHaveValue("");
  }
  await dialog.getByLabel("绑定键").fill(bindingKey);
  await dialog.getByRole("button", { name: "保存绑定" }).click();
  await expect(dialog).toBeHidden();
}

async function closeCatalog(page: Page): Promise<void> {
  await page.getByRole("button", { name: "关闭资源目录" }).click();
  await expect(page.getByRole("dialog", { name: "资源目录" })).toBeHidden();
}

async function resourceCard(page: Page, name: string): Promise<Locator> {
  const card = page.getByRole("article").filter({ hasText: name });
  await expect(card).toBeVisible();
  return card;
}

test("C1 preserves V2 prompt text, exact pins, upgrades, and zero-execution boundaries", async ({
  page,
}) => {
  const unexpectedNetwork: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.hostname !== "127.0.0.1") unexpectedNetwork.push(request.url());
  });
  const sessionToken = process.env.AI_CANVAS_E2E_SESSION_TOKEN_INTERNAL;
  if (sessionToken) {
    await page.addInitScript((token) => sessionStorage.setItem("ai-canvas-session", token), sessionToken);
  }

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "最近项目" })).toBeVisible();
  const projectName = `c1-resources-${Date.now()}`;
  const { projectId } = await seedV2Project(page, projectName);
  await page.goto(`/?project=${projectId}`);
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();

  await page.getByRole("button", { name: "编辑结构化提示词" }).click();
  await expect(page.getByLabel("提示词块 1 内容")).toHaveValue(promptText);
  await expect(page.getByLabel("渲染预览")).toHaveText(promptText);
  await page.getByRole("button", { name: "关闭提示词编辑器" }).click();

  await page.getByRole("button", { name: "打开资源目录" }).click();
  await createResource(page, "风格", "C1 暖调", "风格摘要", "暖色电影质感");
  await createResource(page, "角色", "C1 女主", "角色规范名", "Gwen", "身份锚点", "短黑发与蓝眼睛");
  await createResource(
    page,
    "提示词",
    "C1 提示规则",
    "格式规则（每行一项）",
    "one paragraph",
    "内容顺序（类型名，逗号分隔）",
    "SUBJECT, SCENE_ACTION",
  );
  await createResource(page, "媒体", "C1 构图参考", "使用说明", "仅用于构图参考");

  await bindResource(page, "风格", "C1 暖调", "项目", "style.primary");
  await bindResource(page, "角色", "C1 女主", "分镜", "character.primary");
  await bindResource(page, "提示词", "C1 提示规则", "节点", "prompt.rules");
  await bindResource(page, "媒体", "C1 构图参考", "节点", "media.reference");
  const catalogText = await page.getByRole("dialog", { name: "资源目录" }).innerText();
  expect(catalogText).not.toMatch(/GPU|运行媒体|生成媒体/);
  await closeCatalog(page);

  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  const firstReview = page.getByRole("dialog", { name: "图谱变更审阅" });
  await expect(firstReview).toContainText("资源绑定 +4 · -0 · 更新 0");
  await firstReview.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存");

  const firstGraph = await apiJson<{ revision_id: string; snapshot: { resource_bindings: Array<{ binding_key: string; resource_revision_id: string }> } }>(
    page,
    `/api/v1/projects/${projectId}/graph`,
  );
  const firstStylePin = firstGraph.snapshot.resource_bindings.find((binding) => binding.binding_key === "style.primary");
  expect(firstStylePin).toBeTruthy();

  await page.getByRole("button", { name: "打开资源目录" }).click();
  await page.getByRole("tab", { name: "风格" }).click();
  const styleCard = await resourceCard(page, "C1 暖调");
  await styleCard.getByRole("button", { name: "新版本" }).click();
  const editor = page.getByRole("dialog", { name: "创建新版本 · C1 暖调" });
  await editor.getByLabel("风格摘要").fill("暖色电影质感，增加柔和实景光");
  await editor.getByRole("button", { name: "创建新版本" }).click();
  await expect(page.getByRole("article").filter({ hasText: "C1 暖调" })).toContainText("v2");
  await expect(page.getByLabel("可用资源更新")).toContainText("v1 → v2");

  const detail = await apiJson<Array<{ latest_revision: { id: string } }>>(
    page,
    "/api/v1/resources?kind=STYLE&query=C1%20%E6%9A%96%E8%B0%83&include_archived=true&limit=50",
  );
  const styleRevisionTwo = detail[0]?.latest_revision.id;
  expect(styleRevisionTwo).toBeTruthy();
  const beforeUpgrade = await apiJson<typeof firstGraph>(page, `/api/v1/projects/${projectId}/graph`);
  expect(beforeUpgrade.revision_id).toBe(firstGraph.revision_id);
  expect(
    beforeUpgrade.snapshot.resource_bindings.find((binding) => binding.binding_key === "style.primary")?.resource_revision_id,
  ).toBe(firstStylePin?.resource_revision_id);

  await page.getByRole("button", { name: "升级此绑定" }).click();
  await closeCatalog(page);
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  const updateReview = page.getByRole("dialog", { name: "图谱变更审阅" });
  await expect(updateReview).toContainText("资源绑定 +0 · -0 · 更新 1");
  await expect(updateReview).toContainText("style.primary");
  await updateReview.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存");

  const upgradedGraph = await apiJson<typeof firstGraph>(page, `/api/v1/projects/${projectId}/graph`);
  expect(upgradedGraph.revision_id).not.toBe(firstGraph.revision_id);
  expect(
    upgradedGraph.snapshot.resource_bindings.find((binding) => binding.binding_key === "style.primary")?.resource_revision_id,
  ).toBe(styleRevisionTwo);

  await page.reload();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  await page.getByRole("button", { name: "编辑结构化提示词" }).click();
  await expect(page.getByLabel("提示词块 1 内容")).toHaveValue(promptText);
  await page.getByRole("button", { name: "关闭提示词编辑器" }).click();
  const restoredGraph = await apiJson<typeof firstGraph>(page, `/api/v1/projects/${projectId}/graph`);
  expect(restoredGraph).toEqual(upgradedGraph);
  expect(unexpectedNetwork).toEqual([]);
});
