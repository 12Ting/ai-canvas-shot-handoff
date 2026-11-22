import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Route } from "@playwright/test";
import { expect, test } from "./fixtures";

const here = path.dirname(fileURLToPath(import.meta.url));
const png = Buffer.from(
  fs
    .readFileSync(
      path.resolve(here, "../../api/tests/fixtures/tiny_png.b64"),
      "ascii",
    )
    .trim(),
  "base64",
);
const IMPORT_IDS = [
  "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
] as const;

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

type Declaration = {
  client_import_id: string;
  filename: string;
  expected_byte_size: number;
  requested_media_kind: "image";
  declared_content_type: string | null;
  replace_revision_id: null;
};

function importSession(
  projectId: string,
  importId: string,
  declaration: Declaration,
  state: "CREATED" | "FAILED_FINAL",
) {
  return {
    import_id: importId,
    source_project_id: projectId,
    client_import_id: declaration.client_import_id,
    replace_revision_id: null,
    requested_media_kind: "image",
    original_filename: declaration.filename,
    declared_content_type: declaration.declared_content_type,
    expected_byte_size: declaration.expected_byte_size,
    state,
    received_byte_size: 0,
    failure_code: state === "FAILED_FINAL" ? "corrupt_media" : null,
    created_at: "2026-07-24T00:00:00Z",
    updated_at: "2026-07-24T00:00:01Z",
    artifact_revision: null,
  };
}

async function fulfillJson(
  route: Route,
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
) {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers,
    body: JSON.stringify(body),
  });
}

test("keeps the reservation and recovery choices until exact result deletion is confirmed", async ({
  page,
  allowArtifactImportResultNetworkFailure,
}) => {
  await page.goto("/");
  await page.getByLabel("项目名称").fill(`discard-e2e-${Date.now()}`);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("project"))
    .not.toBeNull();
  const projectId = new URL(page.url()).searchParams.get("project");
  if (!projectId) throw new Error("project query parameter is missing");

  const origin = new URL(page.url()).origin;
  const imports = new Map<
    string,
    { importId: string; declaration: Declaration }
  >();
  const collectionUrl =
    `${origin}/api/v1/projects/${projectId}/artifact-imports`;
  const postStarted = [deferred<void>(), deferred<void>()];
  const allowPost = [deferred<void>(), deferred<void>()];
  let postIndex = 0;

  await page.route(collectionUrl, async (route) => {
    expect(route.request().method()).toBe("POST");
    const index = postIndex++;
    const importId = IMPORT_IDS[index];
    if (!importId) throw new Error("unexpected third import");
    const declaration = route.request().postDataJSON() as Declaration;
    expect(declaration).toMatchObject({
      expected_byte_size: png.byteLength,
      requested_media_kind: "image",
      declared_content_type: "image/png",
      replace_revision_id: null,
    });
    expect(declaration.client_import_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f-]{27}$/i,
    );
    imports.set(importId, { importId, declaration });
    postStarted[index].resolve();
    await allowPost[index].promise;
    await fulfillJson(
      route,
      importSession(projectId, importId, declaration, "CREATED"),
    );
  });

  for (const importId of IMPORT_IDS) {
    const sessionUrl = `${collectionUrl}/${importId}`;
    await page.route(`${sessionUrl}/content`, async (route) => {
      expect(route.request().method()).toBe("PUT");
      expect(route.request().postDataBuffer()?.byteLength).toBe(png.byteLength);
      await fulfillJson(route, {});
    });
    await page.route(sessionUrl, async (route) => {
      expect(route.request().method()).toBe("GET");
      const current = imports.get(importId);
      if (!current) throw new Error("GET observed before exact POST");
      await fulfillJson(
        route,
        importSession(
          projectId,
          importId,
          current.declaration,
          "FAILED_FINAL",
        ),
      );
    });
  }

  const firstDeleteStarted = deferred<void>();
  const allowFirstDelete = deferred<void>();
  const deleteBodies: unknown[] = [];
  let deleteAttempt = 0;
  const firstResultUrl = `${collectionUrl}/${IMPORT_IDS[0]}/result`;
  await page.route(firstResultUrl, async (route) => {
    expect(route.request().method()).toBe("DELETE");
    deleteAttempt += 1;
    const body: unknown = route.request().postDataJSON();
    deleteBodies.push(body);
    expect(body).toEqual({
      client_import_id: imports.get(IMPORT_IDS[0])?.declaration
        .client_import_id,
      expected_revision_id: null,
    });
    if (deleteAttempt === 1) {
      firstDeleteStarted.resolve();
      await allowFirstDelete.promise;
      await fulfillJson(
        route,
        { outcome: "discarded", content_cleanup: "pending" },
        202,
      );
      return;
    }
    if (deleteAttempt === 2) {
      allowArtifactImportResultNetworkFailure(route.request().url());
      await route.abort("internetdisconnected");
      return;
    }
    if (deleteAttempt === 3) {
      await fulfillJson(
        route,
        {
          code: "artifact_import_conflict",
          import_id: IMPORT_IDS[0],
          reason: "result_referenced",
        },
        409,
        { "X-E2E-Expected-Discard-Conflict": "true" },
      );
      return;
    }
    await fulfillJson(route, {
      outcome: "already_discarded",
      content_cleanup: "not_present",
    });
  });

  const picker = page.getByLabel("选择多个本地媒体");
  await picker.setInputFiles({
    name: "first.png",
    mimeType: "image/png",
    buffer: png,
  });
  await postStarted[0].promise;
  const firstReservation = page
    .locator(".media-ingress-reservation")
    .filter({ hasText: "first.png" });
  await expect(firstReservation).toBeVisible();
  const firstPosition = await firstReservation.boundingBox();
  expect(firstPosition).not.toBeNull();
  allowPost[0].resolve();

  const failedRow = page.getByRole("listitem", {
    name: "失败项 first.png",
  });
  await expect(failedRow).toBeVisible();
  await failedRow
    .getByRole("button", {
      name: "删除本地素材并忽略 first.png",
    })
    .click();
  await firstDeleteStarted.promise;

  const deletingRow = page.getByRole("listitem", {
    name: "删除本地素材项 first.png",
  });
  await expect(deletingRow).toContainText(
    "正在删除本地素材，请勿关闭工作台",
  );
  await expect(
    deletingRow.getByRole("button", { name: /重新选择|重试/ }),
  ).toHaveCount(0);
  const deletingBatch = deletingRow.locator("xpath=ancestor::article[1]");
  await expect(
    deletingBatch.getByRole("button", { name: /关闭批次/ }),
  ).toHaveCount(0);

  await picker.setInputFiles({
    name: "second.png",
    mimeType: "image/png",
    buffer: png,
  });
  await postStarted[1].promise;
  const secondReservation = page
    .locator(".media-ingress-reservation")
    .filter({ hasText: "second.png" });
  await expect(secondReservation).toBeVisible();
  const secondPosition = await secondReservation.boundingBox();
  expect(secondPosition).not.toBeNull();
  expect(secondPosition).not.toEqual(firstPosition);
  allowPost[1].resolve();
  await expect(
    page.getByRole("listitem", { name: "失败项 second.png" }),
  ).toBeVisible();

  allowFirstDelete.resolve();
  const retry = deletingRow.getByRole("button", {
    name: "重试删除本地素材 first.png",
  });
  await expect(deletingRow).toContainText("本地素材清理待完成");
  await retry.click();
  await expect(deletingRow).toContainText("无法连接本地服务");
  await retry.click();
  await expect(deletingRow).toContainText(
    "仍被项目历史或其他结果引用",
  );
  await retry.click();

  await expect(deletingRow).toHaveCount(0);
  expect(deleteAttempt).toBe(4);
  expect(deleteBodies).toHaveLength(4);
  expect(new Set(deleteBodies.map((body) => JSON.stringify(body))).size).toBe(
    1,
  );
});
