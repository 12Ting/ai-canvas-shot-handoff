import { createHash, randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

type BranchWorkspace = {
  branches: Array<{
    id: string;
    name: string;
    head: { graph_revision_id: string; version: number };
    is_selected: boolean;
  }>;
  selection: { branch_id: string; graph_revision_id: string; version: number };
};

type GraphResponse = {
  revision_id: string;
  snapshot: Record<string, unknown>;
};

type ChangeSet = {
  id: string;
  changeset_sha256: string;
  state: string;
  created_branch_id: string | null;
};

type ConfigRevision = {
  id: string;
};

type Extraction = {
  materialized_resource_revision_id: string | null;
};

type Batch = {
  id: string;
  phase: string;
  graph_change_set: ChangeSet | null;
};

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, stable(item)]),
    );
  }
  return value;
}

function sha256(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(stable(value))).digest("hex");
}

async function apiJson<T>(
  page: Page,
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  return page.evaluate(
    async ({ requestPath, requestMethod, requestBody }) => {
      const token = sessionStorage.getItem("ai-canvas-session");
      if (!token) throw new Error("AI Canvas session is not established");
      const response = await fetch(requestPath, {
        method: requestMethod,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(requestBody === undefined ? {} : { "Content-Type": "application/json" }),
        },
        ...(requestBody === undefined ? {} : { body: JSON.stringify(requestBody) }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          `${requestMethod} ${requestPath} failed (${response.status}): ${JSON.stringify(payload)}`,
        );
      }
      return payload;
    },
    { requestPath: path, requestMethod: method, requestBody: body },
  );
}

async function applySnapshot(
  page: Page,
  projectId: string,
  snapshot: Record<string, unknown>,
): Promise<ChangeSet> {
  const workspace = await apiJson<BranchWorkspace>(page, `/api/v1/projects/${projectId}/branches`);
  const viewed = workspace.branches.find((branch) => branch.id === workspace.selection.branch_id);
  if (!viewed) throw new Error("selected branch is missing");
  const proposal = await apiJson<ChangeSet>(
    page,
    `/api/v1/projects/${projectId}/graph-change-sets`,
    "POST",
    {
      kind: "graph_change_set",
      schema_version: 1,
      base_graph_revision_id: viewed.head.graph_revision_id,
      client_mutation_id: randomUUID(),
      operations: [{ op: "replace_snapshot", snapshot }],
      application_target: {
        mode: "DIRECT",
        branch_id: viewed.id,
        expected_branch_head_revision_id: viewed.head.graph_revision_id,
        expected_branch_head_version: viewed.head.version,
        expected_selection_version: workspace.selection.version,
      },
    },
  );
  await apiJson(
    page,
    `/api/v1/projects/${projectId}/graph-change-sets/${proposal.id}/approval-decisions`,
    "POST",
    { decision: "APPROVED", expected_changeset_sha256: proposal.changeset_sha256 },
  );
  return apiJson(
    page,
    `/api/v1/projects/${projectId}/graph-change-sets/${proposal.id}/apply`,
    "POST",
    { expected_changeset_sha256: proposal.changeset_sha256 },
  );
}

async function activatePromptEditor(page: Page, projectId: string): Promise<void> {
  const config = await apiJson<ConfigRevision>(page, "/api/v1/control/config-revision");
  const revision = {
    role: "PROMPT_EDITOR",
    model_id: "c4-loopback-prompt-editor",
    model_version: "1",
    endpoint_url: "http://127.0.0.1:8011/v1/chat/completions",
    locality: "LOCAL_LOOPBACK",
    data_residency: "LOCAL_DEVICE",
    allowed_content_categories: [
      "USER_INSTRUCTION",
      "PROMPT_EXAMPLE_DOCUMENT",
      "PROMPT_TARGET_DOCUMENT",
      "PROMPT_PROFILE_RULES",
      "PRESERVE_CONSTRAINTS",
    ],
    retention_policy: "NO_RETENTION_DECLARED",
    retention_note: "C4 local loopback witness",
    tool_permissions: ["PROMPT_PROFILE_DRAFT", "PROMPT_BATCH_ITEM_DRAFT"],
    cost_mode: "HARD",
    currency: "CNY",
    cost_bound_minor: 0,
    max_output_tokens: 2048,
    timeout_seconds: 15,
    credential_kind: "NONE",
    fallback_model_profile_revision_id: null,
  };
  const proposal = await apiJson<ChangeSet>(page, "/api/v1/control/config-change-sets", "POST", {
    kind: "config_change_set",
    schema_version: 1,
    audit_project_id: projectId,
    base_config_revision_id: config.id,
    operations: [{
      op: "declare_model_profile_revision",
      provider_id: randomUUID(),
      provider: {
        provider_key: "c4-loopback",
        display_name: "C4 Loopback",
        adapter_kind: "OPENAI_COMPATIBLE_CHAT_JSON_V1",
      },
      model_profile_id: randomUUID(),
      profile_key: "prompt-editor-default",
      model_profile_revision_id: randomUUID(),
      revision,
      declaration_sha256: sha256(revision),
      activate: true,
    }],
  });
  await apiJson(
    page,
    `/api/v1/control/config-change-sets/${proposal.id}/approval-decisions`,
    "POST",
    { decision: "APPROVED", expected_changeset_sha256: proposal.changeset_sha256 },
  );
  await apiJson(
    page,
    `/api/v1/control/config-change-sets/${proposal.id}/apply`,
    "POST",
    { expected_changeset_sha256: proposal.changeset_sha256 },
  );
}

function eightShotSnapshot() {
  const shotIds = Array.from({ length: 8 }, () => randomUUID());
  const promptIds = Array.from({ length: 8 }, () => randomUUID());
  const shotNodeIds = Array.from({ length: 8 }, () => randomUUID());
  const nodes: Array<Record<string, unknown>> = [];
  const edges: Array<Record<string, unknown>> = [];
  for (let index = 0; index < 8; index += 1) {
    const subject = `角色${index + 1}`;
    const editable = `基础镜头提示 ${index + 1}`;
    const subjectBlockId = randomUUID();
    const editableBlockId = randomUUID();
    nodes.push({
      id: promptIds[index],
      kind: "prompt",
      position: { x: index * 260, y: 80 },
      data: {
        document: {
          schema_version: 1,
          blocks: [
            { id: subjectBlockId, kind: "SUBJECT", text: subject, locked: true },
            { id: editableBlockId, kind: "FREEFORM", text: editable, locked: false },
          ],
          rendered_text: `${subject}\n${editable}`,
          spans: [
            { block_id: subjectBlockId, start_utf16: 0, end_utf16: subject.length },
            {
              block_id: editableBlockId,
              start_utf16: subject.length + 1,
              end_utf16: subject.length + 1 + editable.length,
            },
          ],
        },
      },
    });
    nodes.push({
      id: shotNodeIds[index],
      kind: "shot",
      position: { x: index * 260, y: 360 },
      data: { shot_id: shotIds[index] },
    });
    edges.push({
      id: randomUUID(),
      source: promptIds[index],
      source_port: "prompt_document",
      target: shotNodeIds[index],
      target_port: "prompt_document",
    });
  }
  return {
    shotIds,
    promptIds,
    snapshot: {
      schema_version: 3,
      nodes,
      edges,
      shots: shotIds.map((id, order) => ({
        id,
        order,
        image_revision_id: null,
        video_revision_id: null,
        reference_revision_ids: [],
      })),
      resource_bindings: [],
    },
  };
}

async function openC4(page: Page): Promise<void> {
  const summary = page.getByText("C4 PromptProfile 与批次", { exact: true });
  const details = summary.locator("..");
  if (!(await details.getAttribute("open"))) await summary.click();
  await expect(page.getByRole("region", { name: "C4 Prompt 批次中心" })).toBeVisible();
}

async function ensureBranchPanelOpen(page: Page): Promise<void> {
  const trigger = page.locator(".branch-panel").getByRole("button", {
    name: /^(?:查看\/正式：.+|查看：.+｜正式：.+)$/,
  });
  if ((await trigger.getAttribute("aria-expanded")) !== "true") await trigger.click();
  await expect(page.getByRole("region", { name: "图谱分支" })).toBeVisible();
}

test.setTimeout(240_000);

test("C4 extracts a certified profile and reviews an eight-shot retryable batch", async ({
  modelWitness,
  page,
}) => {
  await page.goto("/");
  const projectName = `c4-batch-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await expect(page).toHaveURL(/\?project=[0-9a-f-]+$/i);
  const projectId = new URL(page.url()).searchParams.get("project");
  if (!projectId) throw new Error("created project URL has no project id");

  await activatePromptEditor(page, projectId);
  const fixture = eightShotSnapshot();
  await applySnapshot(page, projectId, fixture.snapshot);
  await page.reload();
  await expect(page.getByRole("heading", { name: projectName })).toBeVisible();
  await openC4(page);

  await page.getByLabel("示例 PromptNode").selectOption(fixture.promptIds[0]);
  await page.getByRole("button", { name: "请求提炼预览" }).click();
  await expect(page.getByText(/提炼状态 PENDING_APPROVAL/)).toBeVisible();
  expect(modelWitness.calls).toBe(0);
  await page.getByRole("button", { name: "批准准确提炼请求" }).click();
  await expect(page.getByText(/提炼状态 PENDING_EXECUTION/)).toBeVisible();
  expect(modelWitness.calls).toBe(0);
  await page.getByRole("button", { name: "执行一次提炼" }).click();
  await expect(page.getByText(/提炼状态 SUCCEEDED/)).toBeVisible();
  expect(modelWitness.schemaCalls.prompt_profile_draft_v1).toBe(1);
  const materializedResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    /\/prompt-profile-extractions\/[^/]+\/materialize$/.test(new URL(response.url()).pathname) &&
    response.status() === 200,
  );
  await page.getByRole("button", { name: "确认并保存不可变资源 revision" }).click();
  const extraction = (await (await materializedResponse).json()) as Extraction;
  expect(extraction.materialized_resource_revision_id).toBeTruthy();
  if (!extraction.materialized_resource_revision_id) {
    throw new Error("materialized profile revision is missing");
  }

  const graph = await apiJson<GraphResponse>(page, `/api/v1/projects/${projectId}/graph`);
  const boundSnapshot = {
    ...graph.snapshot,
    resource_bindings: [
      {
        id: randomUUID(),
        resource_kind: "PROMPT",
        binding_key: "visual-profile",
        scope_kind: "PROJECT",
        scope_id: null,
        resource_revision_id: extraction.materialized_resource_revision_id,
      },
      {
        id: randomUUID(),
        resource_kind: "PROMPT",
        binding_key: "visual-profile",
        scope_kind: "SHOT",
        scope_id: fixture.shotIds[0],
        resource_revision_id: extraction.materialized_resource_revision_id,
      },
    ],
  };
  await applySnapshot(page, projectId, boundSnapshot);
  await page.reload();
  await openC4(page);

  await page.getByRole("button", { name: "预检" }).click();
  await expect(page.getByRole("status").filter({ hasText: "预检：8 项可执行" })).toBeVisible();

  await page.getByRole("button", { name: "创建批次" }).click();
  await expect(page.getByText(/状态 PENDING_APPROVAL · 8 项/)).toBeVisible();
  await page.getByRole("button", { name: "拒绝（零发送）" }).last().click();
  await expect(page.getByText(/状态 REJECTED · 8 项/)).toBeVisible();
  expect(modelWitness.schemaCalls.prompt_batch_item_draft_v1 ?? 0).toBe(0);

  const batchResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    /\/prompt-batches$/.test(new URL(response.url()).pathname) &&
    response.status() === 201,
  );
  await page.getByRole("button", { name: "创建批次" }).click();
  const createdBatch = (await (await batchResponse).json()) as Batch;
  await page.getByRole("button", { name: "批准准确批次" }).click();
  await expect(page.getByText(/状态 PENDING_EXECUTION · 8 项/)).toBeVisible();
  expect(modelWitness.schemaCalls.prompt_batch_item_draft_v1 ?? 0).toBe(0);
  await page.getByRole("button", { name: "执行未发送项（并发 4）" }).click();
  await expect.poll(() => modelWitness.schemaCalls.prompt_batch_item_draft_v1, {
    timeout: 60_000,
  }).toBe(8);
  await expect(page.getByText(/状态 REVIEW_PENDING · 8 项/)).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".prompt-batch-panel li").filter({ hasText: "FAILED" })).toHaveCount(1);
  await expect(page.locator(".prompt-batch-panel li").filter({ hasText: "SUCCEEDED" })).toHaveCount(7);

  await page.getByRole("button", { name: "接受全部成功项" }).click();
  let reviewed = page.locator(".prompt-batch-panel li").filter({ hasText: "SUCCEEDED" }).first();
  await reviewed.getByRole("button", { name: "拒绝" }).click();
  reviewed = page.locator(".prompt-batch-panel li").filter({ hasText: "SUCCEEDED" }).first();
  await reviewed.getByRole("button", { name: "接受" }).click();
  await page.getByRole("button", { name: "只重试失败项" }).click();
  await expect(page.getByText(/状态 RETRY_APPROVAL_REQUIRED · 8 项/)).toBeVisible();
  await page.getByRole("button", { name: "批准失败子集重试" }).click();
  await page.getByRole("button", { name: "执行未发送项（并发 4）" }).click();
  await expect.poll(() => modelWitness.schemaCalls.prompt_batch_item_draft_v1, {
    timeout: 30_000,
  }).toBe(9);
  await expect(page.getByText(/状态 REVIEW_PENDING · 8 项/)).toBeVisible({ timeout: 15_000 });
  expect(Object.values(modelWitness.shotCalls).sort()).toEqual([1, 1, 1, 1, 1, 1, 1, 2]);

  await page.getByRole("button", { name: "接受全部成功项" }).click();
  const finalizeResponse = page.waitForResponse((response) =>
    response.request().method() === "POST" &&
    /\/prompt-batches\/[^/]+\/finalize$/.test(new URL(response.url()).pathname),
  );
  await page.getByRole("button", { name: "汇总已接受项并进入 GraphChangeSet 复核" }).click();
  const finalizeHttpResponse = await finalizeResponse;
  const finalizeBody = await finalizeHttpResponse.text();
  expect(finalizeHttpResponse.status(), finalizeBody).toBe(200);
  const finalized = JSON.parse(finalizeBody) as Batch;
  expect(finalized.phase).toBe("FINALIZED");
  expect(finalized.graph_change_set?.state).toBe("PENDING_APPROVAL");
  const review = page.getByRole("dialog", { name: "Agent 图谱提案审阅" });
  await expect(review).toContainText("将创建实验分支“C4 提示词候选”");
  await expect(review).toContainText("提示词文档");
  await review.getByRole("button", { name: "确认应用变更" }).click();
  const branchTrigger = page.getByRole("button", { name: "查看：C4 提示词候选｜正式：主线" });
  await expect(branchTrigger).toBeVisible();

  let workspace = await apiJson<BranchWorkspace>(page, `/api/v1/projects/${projectId}/branches`);
  const main = workspace.branches.find((branch) => branch.name === "主线");
  const candidate = workspace.branches.find((branch) => branch.name === "C4 提示词候选");
  expect(main?.is_selected).toBe(true);
  expect(candidate?.is_selected).toBe(false);
  expect(candidate).toBeTruthy();

  await branchTrigger.click();
  await page.getByRole("button", { name: "提升为当前选择" }).click();
  await page.getByRole("dialog", { name: "提升分支为正式选择" })
    .getByRole("button", { name: "提升为正式选择" }).click();
  await expect(page.getByRole("button", { name: "查看/正式：C4 提示词候选" })).toBeVisible();
  workspace = await apiJson<BranchWorkspace>(page, `/api/v1/projects/${projectId}/branches`);
  expect(workspace.selection.branch_id).toBe(candidate?.id);

  await ensureBranchPanelOpen(page);
  await page.getByRole("button", { name: "撤销上次提升" }).click();
  await page.getByRole("dialog", { name: "撤销上次提升" })
    .getByRole("button", { name: "确认撤销提升" }).click();
  await expect(page.getByRole("button", { name: "查看：C4 提示词候选｜正式：主线" })).toBeVisible();
  workspace = await apiJson<BranchWorkspace>(page, `/api/v1/projects/${projectId}/branches`);
  expect(workspace.selection.branch_id).toBe(main?.id);

  await page.reload();
  await openC4(page);
  await page.getByLabel("恢复批次 ID").fill(createdBatch.id);
  await page.getByRole("button", { name: "恢复" }).click();
  await expect(page.getByText(/状态 FINALIZED · 8 项/)).toBeVisible();
  await expect(page.getByRole("button", { name: "接受", exact: true }).first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "拒绝", exact: true }).first()).toBeDisabled();
  expect(modelWitness.calls).toBe(10);
});
