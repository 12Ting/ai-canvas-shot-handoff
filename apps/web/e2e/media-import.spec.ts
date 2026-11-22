import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { APIResponse, Page, Request } from "@playwright/test";
import { expect, test } from "./fixtures";

const here = path.dirname(fileURLToPath(import.meta.url));

type SnapshotNode = {
  id: string;
  kind: string;
  data: Record<string, unknown>;
};

type GraphResponse = {
  revision_id: string;
  snapshot: {
    schema_version: number;
    nodes: SnapshotNode[];
    edges: unknown[];
    shots: unknown[];
  };
};

type PreviewRequest = {
  owner: Page;
  request: Request;
};

function fixtureBuffer(relativePath: string): Buffer {
  const encoded = fs.readFileSync(path.resolve(here, relativePath), "ascii").trim();
  return Buffer.from(encoded, "base64");
}

const sourcePng = fixtureBuffer("../../api/tests/fixtures/tiny_png.b64");
const sourceWebm = fixtureBuffer("../../api/tests/fixtures/tiny_webm.b64");
const replacementPng = fixtureBuffer("./fixtures/replacement_png.b64");
const importCompletionTimeout = 15_000;

function mediaNodes(graph: GraphResponse): SnapshotNode[] {
  return graph.snapshot.nodes.filter((node) => node.kind === "media_input");
}

function revisionId(node: SnapshotNode): string {
  const value = node.data.artifact_revision_id;
  if (typeof value !== "string") throw new Error(`media node ${node.id} has no revision`);
  return value;
}

function mediaKind(node: SnapshotNode): "image" | "video" {
  const value = node.data.media_kind;
  if (value !== "image" && value !== "video") {
    throw new Error(`media node ${node.id} has no media kind`);
  }
  return value;
}

function assertSnapshotIsPortable(snapshot: unknown): void {
  const serialized = JSON.stringify(snapshot);
  expect(serialized).not.toMatch(/blob:|file:|[a-z]:\\|local_path|objecturl/i);
  expect(serialized).not.toMatch(/"(?:filename|metadata|file|blob)"\s*:/i);
  expect(serialized).not.toMatch(/(?:source|replacement)-(?:storyboard|motion)/i);
  if (
    typeof snapshot === "object" &&
    snapshot !== null &&
    "nodes" in snapshot &&
    Array.isArray(snapshot.nodes)
  ) {
    for (const node of snapshot.nodes as SnapshotNode[]) {
      if (node.kind === "media_input") {
        expect(Object.keys(node.data).sort()).toEqual([
          "artifact_revision_id",
          "media_kind",
        ]);
      }
    }
  }
}

async function authorizedGet(
  page: Page,
  sessionToken: string,
  url: string,
): Promise<APIResponse> {
  return page.request.get(url, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
}

async function readGraph(
  page: Page,
  sessionToken: string,
  projectId: string,
): Promise<GraphResponse> {
  const response = await authorizedGet(
    page,
    sessionToken,
    `/api/v1/projects/${projectId}/graph`,
  );
  expect(response.status()).toBe(200);
  return (await response.json()) as GraphResponse;
}

async function expectImageReady(node: ReturnType<Page["locator"]>, width: number) {
  const image = node.locator("img");
  await expect(image).toBeVisible({ timeout: importCompletionTimeout });
  await expect(image).toHaveJSProperty("naturalWidth", width);
  await expect(image).toHaveJSProperty("complete", true);
  return image;
}

async function expectVideoReady(node: ReturnType<Page["locator"]>) {
  const video = node.locator("video");
  await expect(video).toBeVisible({ timeout: importCompletionTimeout });
  await expect
    .poll(
      () => video.evaluate((element) => {
        const media = element as HTMLVideoElement;
        return {
          readyState: media.readyState,
          videoWidth: media.videoWidth,
          videoHeight: media.videoHeight,
          controls: media.controls,
          muted: media.muted,
          playsInline: media.playsInline,
          autoplay: media.autoplay,
          paused: media.paused,
        };
      }),
      { timeout: importCompletionTimeout },
    )
    .toMatchObject({
      readyState: expect.any(Number),
      videoWidth: 16,
      videoHeight: 16,
      controls: true,
      muted: true,
      playsInline: true,
      autoplay: false,
      paused: true,
    });
  expect(await video.evaluate((element) => (element as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(1);
  return video;
}

async function approveGraphChange(page: Page): Promise<void> {
  await expect(page.locator(".save-status")).toHaveText("有未保存修改", {
    timeout: importCompletionTimeout,
  });
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存", {
    timeout: importCompletionTimeout,
  });
}

test("imports image and video, preserves revisions, and resolves a conflict copy", async ({
  context,
  page,
  sessionToken,
  allowGraphRevisionConflict,
}) => {
  test.setTimeout(60_000);
  const previewRequests: PreviewRequest[] = [];
  const mediaSessionRequests: PreviewRequest[] = [];
  const mediaStreamRequests: PreviewRequest[] = [];
  const previewResponseStatuses = new Map<Request, number>();
  const previewRequestFailures = new Map<Request, string>();
  const browserHttpRequests: Request[] = [];
  const graphMutationSnapshots: unknown[] = [];
  const importDeclarations: Array<Record<string, unknown>> = [];
  context.on("request", (request) => {
    const url = new URL(request.url());
    if (url.protocol === "http:" || url.protocol === "https:") {
      browserHttpRequests.push(request);
    }
    let owner: Page;
    try {
      owner = request.frame().page();
    } catch {
      return;
    }
    if (/^\/api\/v1\/artifact-revisions\/[0-9a-f-]+(?:\/content)?$/i.test(url.pathname)) {
      previewRequests.push({ owner, request });
    }
    if (url.pathname === "/api/v1/media-sessions") {
      mediaSessionRequests.push({ owner, request });
    }
    if (/^\/api\/v1\/media\/revisions\/[0-9a-f-]+$/i.test(url.pathname)) {
      mediaStreamRequests.push({ owner, request });
    }
    if (request.method() !== "POST") return;
    const postData = request.postData();
    if (!postData) return;
    if (/\/graph-change-sets$/i.test(url.pathname)) {
      const body = JSON.parse(postData) as {
        operations?: Array<{ snapshot?: unknown }>;
      };
      graphMutationSnapshots.push(body.operations?.[0]?.snapshot);
    }
    if (/\/artifact-imports$/i.test(url.pathname)) {
      importDeclarations.push(JSON.parse(postData) as Record<string, unknown>);
    }
  });
  context.on("response", (response) => {
    const url = new URL(response.url());
    if (
      /^\/api\/v1\/artifact-revisions\/[0-9a-f-]+(?:\/content)?$/i.test(url.pathname)
      || /^\/api\/v1\/media\/revisions\/[0-9a-f-]+$/i.test(url.pathname)
      || url.pathname === "/api/v1/media-sessions"
    ) {
      previewResponseStatuses.set(response.request(), response.status());
    }
  });
  context.on("requestfailed", (request) => {
    const url = new URL(request.url());
    if (
      /^\/api\/v1\/artifact-revisions\/[0-9a-f-]+(?:\/content)?$/i.test(url.pathname)
      || /^\/api\/v1\/media\/revisions\/[0-9a-f-]+$/i.test(url.pathname)
      || url.pathname === "/api/v1/media-sessions"
    ) {
      previewRequestFailures.set(request, request.failure()?.errorText ?? "unknown failure");
    }
  });

  await page.goto("/");
  const projectName = `media-e2e-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("project"))
    .not.toBeNull();
  const projectId = new URL(page.url()).searchParams.get("project");
  if (!projectId) throw new Error("project query parameter is missing");

  await page.getByRole("button", { name: "添加媒体节点" }).click();
  let nodes = page.locator('article[data-node-kind="media_input"]');
  await expect(nodes).toHaveCount(1);
  const firstImageNode = nodes.nth(0);
  await firstImageNode.locator('input[type="file"]').setInputFiles({
    name: "source-storyboard.png",
    mimeType: "image/png",
    buffer: sourcePng,
  });
  await expect(firstImageNode.getByText("source-storyboard.png", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  await expect(firstImageNode.getByText(`${sourcePng.byteLength} B`, { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  await expect(firstImageNode.getByText("4 × 3", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  const originalImage = await expectImageReady(firstImageNode, 4);
  const originalImageSource = await originalImage.getAttribute("src");
  expect(originalImageSource).toMatch(/^blob:/);

  await page.getByRole("button", { name: "添加媒体节点" }).click();
  nodes = page.locator('article[data-node-kind="media_input"]');
  await expect(nodes).toHaveCount(2);
  const videoNodeBeforeReload = nodes.nth(1);
  await videoNodeBeforeReload.locator('input[type="file"]').setInputFiles({
    name: "source-motion.webm",
    mimeType: "video/webm",
    buffer: sourceWebm,
  });
  await expect(videoNodeBeforeReload.getByText("source-motion.webm", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  await expect(
    videoNodeBeforeReload.getByText(`${sourceWebm.byteLength} B`, { exact: true }),
  ).toBeVisible({ timeout: importCompletionTimeout });
  await expect(videoNodeBeforeReload.getByText("16 × 16", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  await expect(videoNodeBeforeReload.getByText("0.2 秒", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  await expectVideoReady(videoNodeBeforeReload);
  await approveGraphChange(page);

  const initiallySaved = await readGraph(page, sessionToken, projectId);
  assertSnapshotIsPortable(initiallySaved.snapshot);
  const initialMedia = mediaNodes(initiallySaved);
  expect(initialMedia).toHaveLength(2);
  const originalImageRecord = initialMedia.find((node) => mediaKind(node) === "image");
  const videoRecord = initialMedia.find((node) => mediaKind(node) === "video");
  if (!originalImageRecord || !videoRecord) throw new Error("initial media records are missing");
  const originalImageRevision = revisionId(originalImageRecord);
  const videoRevision = revisionId(videoRecord);

  const originalNode = page.locator(`[data-node-id="${originalImageRecord.id}"]`);
  await originalNode.getByRole("button", { name: "复制媒体节点" }).click();
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(3);
  await approveGraphChange(page);

  const afterCopy = await readGraph(page, sessionToken, projectId);
  const copiedImages = mediaNodes(afterCopy).filter((node) => mediaKind(node) === "image");
  expect(copiedImages).toHaveLength(2);
  expect(new Set(copiedImages.map(revisionId))).toEqual(new Set([originalImageRevision]));
  const copiedImageRecord = copiedImages.find((node) => node.id !== originalImageRecord.id);
  if (!copiedImageRecord) throw new Error("copied media node is missing");

  const copiedNode = page.locator(`[data-node-id="${copiedImageRecord.id}"]`);
  const copiedImageBeforeReplacement = await expectImageReady(copiedNode, 4);
  expect(await copiedImageBeforeReplacement.getAttribute("src")).toBe(originalImageSource);
  await copiedNode.locator('input[type="file"]').setInputFiles({
    name: "replacement-storyboard.png",
    mimeType: "image/png",
    buffer: replacementPng,
  });
  await expect(copiedNode.getByText("replacement-storyboard.png", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  await expect(
    copiedNode.getByText(`${replacementPng.byteLength} B`, { exact: true }),
  ).toBeVisible({ timeout: importCompletionTimeout });
  await expect(copiedNode.getByText("2 × 2", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  const replacementImage = await expectImageReady(copiedNode, 2);
  const replacementImageSource = await replacementImage.getAttribute("src");
  expect(replacementImageSource).toMatch(/^blob:/);
  expect(replacementImageSource).not.toBe(originalImageSource);
  await expect(originalNode.getByText("4 × 3", { exact: true })).toBeVisible({ timeout: importCompletionTimeout });
  const unchangedOriginalImage = await expectImageReady(originalNode, 4);
  expect(await unchangedOriginalImage.getAttribute("src")).toBe(originalImageSource);
  await approveGraphChange(page);

  const afterReplacement = await readGraph(page, sessionToken, projectId);
  const replacementRecord = mediaNodes(afterReplacement).find(
    (node) => node.id === copiedImageRecord.id,
  );
  if (!replacementRecord) throw new Error("replacement media node is missing");
  const replacementRevision = revisionId(replacementRecord);
  expect(replacementRevision).not.toBe(originalImageRevision);
  expect(
    importDeclarations.some(
      (declaration) =>
        declaration.replace_revision_id === originalImageRevision &&
        declaration.requested_media_kind === "image",
    ),
  ).toBe(true);

  const originalSummaryBeforeDelete = await authorizedGet(
    page,
    sessionToken,
    `/api/v1/artifact-revisions/${originalImageRevision}`,
  );
  const replacementSummary = await authorizedGet(
    page,
    sessionToken,
    `/api/v1/artifact-revisions/${replacementRevision}`,
  );
  expect(originalSummaryBeforeDelete.status()).toBe(200);
  expect(replacementSummary.status()).toBe(200);
  const originalSummaryBody = (await originalSummaryBeforeDelete.json()) as {
    artifact_id: string;
    sequence: number;
  };
  const replacementSummaryBody = (await replacementSummary.json()) as {
    artifact_id: string;
    sequence: number;
  };
  expect(replacementSummaryBody.artifact_id).toBe(originalSummaryBody.artifact_id);
  expect(replacementSummaryBody.sequence).toBe(originalSummaryBody.sequence + 1);

  await originalNode.getByRole("button", { name: "删除媒体节点" }).click();
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(2);
  await approveGraphChange(page);
  const oldSummary = await authorizedGet(
    page,
    sessionToken,
    `/api/v1/artifact-revisions/${originalImageRevision}`,
  );
  expect(oldSummary.status()).toBe(200);
  const oldContent = await authorizedGet(
    page,
    sessionToken,
    `/api/v1/artifact-revisions/${originalImageRevision}/content`,
  );
  expect(oldContent.status()).toBe(200);
  expect(await oldContent.body()).toEqual(sourcePng);

  const stableMediaIds = new Map([
    [copiedImageRecord.id, replacementRevision],
    [videoRecord.id, videoRevision],
  ]);
  const afterDelete = await readGraph(page, sessionToken, projectId);
  expect(
    new Map(mediaNodes(afterDelete).map((node) => [node.id, revisionId(node)])),
  ).toEqual(stableMediaIds);
  assertSnapshotIsPortable(afterDelete.snapshot);

  await page.reload();
  const reloadedImageNode = page.locator(`[data-node-id="${copiedImageRecord.id}"]`);
  const reloadedVideoNode = page.locator(`[data-node-id="${videoRecord.id}"]`);
  await expectImageReady(reloadedImageNode, 2);
  await expectVideoReady(reloadedVideoNode);
  expect(
    new Map(mediaNodes(await readGraph(page, sessionToken, projectId)).map((node) => [
      node.id,
      revisionId(node),
    ])),
  ).toEqual(stableMediaIds);

  await page.getByRole("button", { name: "返回项目列表" }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("button", { name: projectName }).click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("project"))
    .toBe(projectId);
  await expectImageReady(page.locator(`[data-node-id="${copiedImageRecord.id}"]`), 2);
  await expectVideoReady(page.locator(`[data-node-id="${videoRecord.id}"]`));

  const secondPage = await context.newPage();
  await secondPage.goto("/");
  await secondPage.getByRole("button", { name: projectName }).click();
  await expectImageReady(secondPage.locator(`[data-node-id="${copiedImageRecord.id}"]`), 2);
  await expectVideoReady(secondPage.locator(`[data-node-id="${videoRecord.id}"]`));

  await page.getByRole("button", { name: "添加便签" }).click();
  await page.getByLabel("便签内容").last().fill("服务端版本");
  await approveGraphChange(page);
  allowGraphRevisionConflict(
    secondPage,
    `${new URL(secondPage.url()).origin}/api/v1/projects/${projectId}/graph-change-sets`,
  );
  await secondPage.getByRole("button", { name: "添加文案节点" }).click();
  await secondPage.getByLabel("文案内容").last().fill("保留媒体的冲突副本");
  await secondPage.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(secondPage.getByRole("alert")).toContainText("另一页面已保存了新版本");
  await secondPage.getByRole("button", { name: "保留本地副本" }).click();
  await expect(
    secondPage.getByRole("dialog", { name: "冲突副本变更审阅" }),
  ).toBeVisible();
  expect(new URL(secondPage.url()).searchParams.get("project")).toBe(projectId);
  await secondPage.getByRole("button", { name: "确认创建副本" }).click();
  await expect
    .poll(() => new URL(secondPage.url()).searchParams.get("project"))
    .not.toBe(projectId);
  const copiedProjectId = new URL(secondPage.url()).searchParams.get("project");
  if (!copiedProjectId) throw new Error("conflict-copy project query parameter is missing");

  const copiedProjectGraph = await readGraph(secondPage, sessionToken, copiedProjectId);
  expect(
    new Map(mediaNodes(copiedProjectGraph).map((node) => [node.id, revisionId(node)])),
  ).toEqual(stableMediaIds);
  assertSnapshotIsPortable(copiedProjectGraph.snapshot);

  const previewCountBeforeConflictReload = previewRequests.filter(
    ({ owner }) => owner === secondPage,
  ).length;
  const mediaSessionCountBeforeConflictReload = mediaSessionRequests.filter(
    ({ owner }) => owner === secondPage,
  ).length;
  const mediaStreamCountBeforeConflictReload = mediaStreamRequests.filter(
    ({ owner }) => owner === secondPage,
  ).length;
  await secondPage.reload();
  await expectImageReady(secondPage.locator(`[data-node-id="${copiedImageRecord.id}"]`), 2);
  await expectVideoReady(secondPage.locator(`[data-node-id="${videoRecord.id}"]`));
  const conflictReloadRequests = previewRequests
    .filter(({ owner }) => owner === secondPage)
    .slice(previewCountBeforeConflictReload)
    .map(({ request }) => request);
  const conflictReloadPaths = new Set(
    conflictReloadRequests.map((request) => new URL(request.url()).pathname),
  );
  expect(conflictReloadRequests.length).toBeGreaterThanOrEqual(3);
  expect(conflictReloadPaths).toEqual(
    new Set([
      `/api/v1/artifact-revisions/${replacementRevision}`,
      `/api/v1/artifact-revisions/${replacementRevision}/content`,
      `/api/v1/artifact-revisions/${videoRevision}`,
    ]),
  );
  const conflictReloadMediaSessions = mediaSessionRequests
    .filter(({ owner }) => owner === secondPage)
    .slice(mediaSessionCountBeforeConflictReload)
    .map(({ request }) => request);
  const conflictReloadMediaStreams = mediaStreamRequests
    .filter(({ owner }) => owner === secondPage)
    .slice(mediaStreamCountBeforeConflictReload)
    .map(({ request }) => request);
  // The browser can satisfy the identical token-free video URL from its media cache
  // after reload, and may re-request its scoped media session while remounting the
  // preview. Every request below remains authenticated and receives a valid response.
  expect(conflictReloadMediaSessions.length).toBeGreaterThanOrEqual(1);
  expect(conflictReloadMediaStreams.length).toBeLessThanOrEqual(1);

  await expect
    .poll(
      () =>
        [
          ...previewRequests.map(({ request }) => request),
          ...mediaSessionRequests.map(({ request }) => request),
          ...mediaStreamRequests.map(({ request }) => request),
        ]
          .filter(
            (request) =>
              !previewResponseStatuses.has(request) && !previewRequestFailures.has(request),
          )
          .map((request) => `${request.method()} ${new URL(request.url()).pathname}`),
      { timeout: importCompletionTimeout },
    )
    .toEqual([]);

  const expectedOrigin = new URL(secondPage.url()).origin;
  for (const { request } of previewRequests) {
    const url = new URL(request.url());
    expect(url.origin).toBe(expectedOrigin);
    expect(url.search).toBe("");
    expect(url.hash).toBe("");
    expect(url.pathname).toMatch(
      /^\/api\/v1\/artifact-revisions\/[0-9a-f-]+(?:\/content)?$/i,
    );
    expect(request.headers().authorization === `Bearer ${sessionToken}`).toBe(true);
    const failure = previewRequestFailures.get(request);
    if (failure !== undefined) {
      expect(failure).toMatch(/net::ERR_ABORTED/);
    } else {
      expect(previewResponseStatuses.get(request)).toBe(200);
    }
  }
  for (const { request } of mediaSessionRequests) {
    const url = new URL(request.url());
    expect(url.origin).toBe(expectedOrigin);
    expect(url.search).toBe("");
    expect(url.hash).toBe("");
    expect(request.headers().authorization).toBe(`Bearer ${sessionToken}`);
    const failure = previewRequestFailures.get(request);
    if (failure !== undefined) {
      expect(failure).toMatch(/net::ERR_ABORTED/);
    } else {
      expect(previewResponseStatuses.get(request)).toBe(200);
    }
  }
  for (const { request } of mediaStreamRequests) {
    const url = new URL(request.url());
    expect(url.origin).toBe(expectedOrigin);
    expect(url.search).toBe("");
    expect(url.hash).toBe("");
    expect(url.pathname).toMatch(/^\/api\/v1\/media\/revisions\/[0-9a-f-]+$/i);
    expect(request.headers().authorization).toBeUndefined();
    expect([200, 206]).toContain(previewResponseStatuses.get(request));
  }
  expect(previewRequests.length).toBeGreaterThanOrEqual(12);
  expect(mediaSessionRequests.length).toBeGreaterThanOrEqual(4);
  expect(mediaStreamRequests.length).toBeGreaterThanOrEqual(2);
  expect(
    [...previewRequestFailures].every(
      ([request, failure]) => {
        const pathname = new URL(request.url()).pathname;
        const isScopedSession =
          request.method() === "POST" && pathname === "/api/v1/media-sessions";
        const isAuthenticatedPreview =
          request.method() === "GET" &&
          /^\/api\/v1\/artifact-revisions\/[0-9a-f-]+(?:\/content)?$/i.test(pathname);
        return (isScopedSession || isAuthenticatedPreview) && /net::ERR_ABORTED/.test(failure);
      },
    ),
  ).toBe(true);
  for (const request of browserHttpRequests) {
    expect(new URL(request.url()).origin).toBe(expectedOrigin);
  }
  for (const snapshot of graphMutationSnapshots) assertSnapshotIsPortable(snapshot);
});
