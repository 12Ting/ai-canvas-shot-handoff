import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { APIResponse, Locator, Page, Request } from "@playwright/test";
import { expect, test } from "./fixtures";

const here = path.dirname(fileURLToPath(import.meta.url));
const artifactImportCollectionPattern =
  /\/api\/v1\/projects\/[0-9a-f-]+\/artifact-imports$/i;
const artifactImportContentPattern =
  /\/api\/v1\/projects\/[0-9a-f-]+\/artifact-imports\/[0-9a-f-]+\/content$/i;
const artifactImportSessionPattern =
  /\/api\/v1\/projects\/[0-9a-f-]+\/artifact-imports\/[0-9a-f-]+$/i;
const artifactImportResultPattern =
  /\/api\/v1\/projects\/[0-9a-f-]+\/artifact-imports\/[0-9a-f-]+\/result$/i;

type BrowserFile = Readonly<{
  name: string;
  mimeType: string;
  base64: string;
}>;

type ClipboardDispatch = Readonly<{
  defaultPrevented: boolean;
  fileCount: number;
  itemTypes: readonly string[];
}>;

type GraphNode = Readonly<{
  id: string;
  kind: string;
  position: Readonly<{ x: number; y: number }>;
  data: Record<string, unknown>;
}>;

type GraphResponse = Readonly<{
  revision_id: string;
  snapshot: Readonly<{
    nodes: readonly GraphNode[];
  }>;
}>;

function fixtureBuffer(relativePath: string): Buffer {
  const encoded = fs.readFileSync(path.resolve(here, relativePath), "ascii").trim();
  return Buffer.from(encoded, "base64");
}

const sourcePng = fixtureBuffer("../../api/tests/fixtures/tiny_png.b64");
const sourceWebm = fixtureBuffer("../../api/tests/fixtures/tiny_webm.b64");
const replacementPng = fixtureBuffer("./fixtures/replacement_png.b64");
const unsupportedGif = fixtureBuffer("./fixtures/unsupported_gif.b64");
const corruptPng = Buffer.from("this is not a PNG", "utf8");

function browserFile(name: string, mimeType: string, buffer: Buffer): BrowserFile {
  return { name, mimeType, base64: buffer.toString("base64") };
}

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function importIdFromContentUrl(url: string): string {
  const segments = new URL(url).pathname.split("/");
  const importId = segments.at(-2);
  if (!importId) throw new Error(`artifact-import content URL has no import id: ${url}`);
  return importId;
}

function importIdFromSessionUrl(url: string): string {
  const importId = new URL(url).pathname.split("/").at(-1);
  if (!importId) throw new Error(`artifact-import session URL has no import id: ${url}`);
  return importId;
}

function importIdFromResultUrl(url: string): string {
  const importId = new URL(url).pathname.split("/").at(-2);
  if (!importId) throw new Error(`artifact-import result URL has no import id: ${url}`);
  return importId;
}

function boxesOverlap(
  first: { x: number; y: number; width: number; height: number },
  second: { x: number; y: number; width: number; height: number },
): boolean {
  return !(
    first.x + first.width <= second.x ||
    second.x + second.width <= first.x ||
    first.y + first.height <= second.y ||
    second.y + second.height <= first.y
  );
}

async function canvasPosition(locator: Locator): Promise<{ x: number; y: number }> {
  return locator.evaluate((element) => {
    const flow = element.closest<HTMLElement>(".react-flow");
    const viewport = flow?.querySelector<HTMLElement>(".react-flow__viewport");
    if (!flow || !viewport) {
      throw new Error("reservation is not attached to a React Flow viewport");
    }
    const transform = getComputedStyle(viewport).transform;
    const matrix = new DOMMatrixReadOnly(
      transform === "none" ? undefined : transform,
    );
    const flowBounds = flow.getBoundingClientRect();
    const reservationBounds = element.getBoundingClientRect();
    if (!Number.isFinite(matrix.a) || !Number.isFinite(matrix.d) || matrix.a === 0 || matrix.d === 0) {
      throw new Error("React Flow viewport has an invalid transform");
    }
    return {
      x: (reservationBounds.left - flowBounds.left - matrix.e) / matrix.a,
      y: (reservationBounds.top - flowBounds.top - matrix.f) / matrix.d,
    };
  });
}

async function canvasViewportCenterInFlow(page: Page): Promise<{ x: number; y: number }> {
  return page.getByTestId("canvas-media-ingress-boundary").evaluate((boundary) => {
    const flow = boundary.querySelector<HTMLElement>(".react-flow");
    const viewport = flow?.querySelector<HTMLElement>(".react-flow__viewport");
    if (!flow || !viewport) {
      throw new Error("React Flow viewport is unavailable");
    }
    const boundaryBounds = boundary.getBoundingClientRect();
    const flowBounds = flow.getBoundingClientRect();
    const transform = getComputedStyle(viewport).transform;
    const matrix = new DOMMatrixReadOnly(
      transform === "none" ? undefined : transform,
    );
    if (!Number.isFinite(matrix.a) || !Number.isFinite(matrix.d) || matrix.a === 0 || matrix.d === 0) {
      throw new Error("React Flow viewport has an invalid transform");
    }
    const centerX = boundaryBounds.left + boundaryBounds.width / 2;
    const centerY = boundaryBounds.top + boundaryBounds.height / 2;
    return {
      x: (centerX - flowBounds.left - matrix.e) / matrix.a,
      y: (centerY - flowBounds.top - matrix.f) / matrix.d,
    };
  });
}

function assertPortableSnapshot(snapshot: unknown): void {
  const serialized = JSON.stringify(snapshot);
  expect(serialized).not.toMatch(/blob:|file:|[a-z]:\\/i);
  expect(serialized).not.toContain("client_import_id");
  for (const forbiddenField of [
    "filename",
    "path",
    "blob",
    "file",
    "batch",
    "reservation",
  ]) {
    expect(serialized.toLowerCase()).not.toContain(`"${forbiddenField}`);
  }
  if (
    typeof snapshot !== "object" ||
    snapshot === null ||
    !("nodes" in snapshot) ||
    !Array.isArray(snapshot.nodes)
  ) {
    return;
  }
  for (const node of snapshot.nodes as readonly GraphNode[]) {
    if (node.kind !== "media_input") continue;
    expect(Object.keys(node.data).sort()).toEqual([
      "artifact_revision_id",
      "media_kind",
    ]);
  }
}

function assertNoSessionCredentialInUrl(rawUrl: string, sessionToken: string): void {
  const url = new URL(rawUrl);
  expect(url.search).not.toContain(sessionToken);
  expect(url.hash).not.toContain(sessionToken);
  expect(url.search).not.toMatch(/(?:bootstrap|access[_-]?token|session)/i);
  expect(url.hash).not.toMatch(/(?:bootstrap|access[_-]?token|session)/i);
}

async function createProject(page: Page, name: string): Promise<string> {
  await page.goto("/");
  await page.getByLabel("项目名称").fill(name);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("project"))
    .not.toBeNull();
  const projectId = new URL(page.url()).searchParams.get("project");
  if (!projectId) throw new Error("project query parameter is missing");
  return projectId;
}

async function readGraph(
  page: Page,
  sessionToken: string,
  projectId: string,
): Promise<GraphResponse> {
  const response = await page.request.get(`/api/v1/projects/${projectId}/graph`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  expect(response.status()).toBe(200);
  return (await response.json()) as GraphResponse;
}

function mediaNodes(graph: GraphResponse): readonly GraphNode[] {
  return graph.snapshot.nodes.filter((node) => node.kind === "media_input");
}

async function dispatchDrop(
  target: Locator,
  files: readonly BrowserFile[],
  point?: Readonly<{ clientX: number; clientY: number }>,
): Promise<void> {
  await target.evaluate((element, input) => {
    const transfer = new DataTransfer();
    for (const entry of input.entries) {
      const bytes = Uint8Array.from(atob(entry.base64), (character) =>
        character.charCodeAt(0),
      );
      transfer.items.add(
        new File([bytes], entry.name, { type: entry.mimeType }),
      );
    }
    const bounds = element.getBoundingClientRect();
    const coordinates = input.point ?? {
      clientX: bounds.left + Math.max(8, bounds.width / 2),
      clientY: bounds.top + Math.max(8, bounds.height / 2),
    };
    for (const type of ["dragenter", "dragover", "drop"] as const) {
      element.dispatchEvent(
        new DragEvent(type, {
          bubbles: true,
          cancelable: true,
          dataTransfer: transfer,
          ...coordinates,
        }),
      );
    }
  }, { entries: files, point });
}

async function dispatchMediaPaste(
  page: Page,
  files: readonly BrowserFile[],
): Promise<boolean> {
  return (await dispatchMediaPasteWithDiagnostics(page, files)).defaultPrevented;
}

async function dispatchMediaPasteWithDiagnostics(
  page: Page,
  files: readonly BrowserFile[],
): Promise<ClipboardDispatch> {
  return page.evaluate((entries) => {
    const transfer = new DataTransfer();
    for (const entry of entries) {
      const bytes = Uint8Array.from(atob(entry.base64), (character) =>
        character.charCodeAt(0),
      );
      transfer.items.add(
        new File([bytes], entry.name, { type: entry.mimeType }),
      );
    }
    const event = new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      clipboardData: transfer,
    });
    window.dispatchEvent(event);
    return {
      defaultPrevented: event.defaultPrevented,
      fileCount: transfer.files.length,
      itemTypes: Array.from(transfer.items, (item) => item.type),
    };
  }, files);
}

async function dispatchTextPaste(target: Locator, text: string): Promise<boolean> {
  return target.evaluate((element, pastedText) => {
    const textarea = element instanceof HTMLTextAreaElement ? element : null;
    const transfer = new DataTransfer();
    transfer.setData("text/plain", pastedText);
    const event = new ClipboardEvent("paste", {
      bubbles: true,
      cancelable: true,
      clipboardData: transfer,
    });
    element.dispatchEvent(event);
    if (!event.defaultPrevented && textarea) {
      const nativeValueSetter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      )?.set;
      if (!nativeValueSetter) {
        throw new Error("textarea native value setter is unavailable");
      }
      nativeValueSetter.call(textarea, pastedText);
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertFromPaste", data: pastedText }));
    }
    return event.defaultPrevented;
  }, text);
}

async function dispatchExternalNavigationDrop(target: Locator, href: string): Promise<boolean> {
  return target.evaluate((element, externalHref) => {
    const transfer = new DataTransfer();
    transfer.setData("text/uri-list", externalHref);
    transfer.setData("text/html", `<a href="${externalHref}">outside</a>`);
    const bounds = element.getBoundingClientRect();
    let defaultPrevented = false;
    for (const type of ["dragenter", "dragover", "drop"] as const) {
      const event = new DragEvent(type, {
        bubbles: true,
        cancelable: true,
        dataTransfer: transfer,
        clientX: bounds.left + 12,
        clientY: bounds.top + 12,
      });
      element.dispatchEvent(
        event,
      );
      if (type === "drop") defaultPrevented = event.defaultPrevented;
    }
    return defaultPrevented;
  }, href);
}

async function expectImageWidth(node: Locator, width: number): Promise<void> {
  const image = node.locator("img");
  await expect(image).toBeVisible({ timeout: 15_000 });
  await expect.poll(
    () => image.evaluate((element) => (element as HTMLImageElement).naturalWidth),
    { timeout: 15_000 },
  ).toBe(width);
}

async function selectFlowMediaNode(page: Page, nodeId: string): Promise<void> {
  const flowNode = page.locator(`.react-flow__node[data-id="${nodeId}"]`);
  await expect(flowNode).toBeVisible();
  await flowNode.dispatchEvent("click");
  await expect(flowNode).toHaveClass(/selected/);
}

async function approveGraphChange(page: Page, timeout = 15_000): Promise<void> {
  await expect(page.locator(".save-status")).toHaveText("有未保存修改", { timeout });
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存", { timeout });
}

test.describe.configure({ timeout: 60_000 });

test("keeps picker and drop batches under one FIFO three-permit gate and persists only durable media", async ({
  page,
  sessionToken,
}) => {
  // This exercises real server-side probe and validation work; it verifies
  // eventual permit admission, not a five-second performance budget.
  const importAdmissionTimeout = 15_000;
  const declarations = new Map<string, string>();
  const promotedRevisionIds = new Map<string, string>();
  const graphSnapshots: unknown[] = [];
  const graphRequestUrls: string[] = [];
  const gates = Array.from({ length: 4 }, () => deferred<void>());
  const held: Array<Readonly<{ importId: string; name: string; response: APIResponse }>> = [];
  const startedNames: string[] = [];
  let contentAttempt = 0;

  page.on("request", (request: Request) => {
    const url = new URL(request.url());
    if (request.method() !== "POST" || !/\/graph-change-sets$/i.test(url.pathname)) return;
    const body = request.postData();
    if (body) {
      graphRequestUrls.push(request.url());
      graphSnapshots.push(
        (JSON.parse(body) as { operations?: Array<{ snapshot?: unknown }> })
          .operations?.[0]?.snapshot,
      );
    }
  });

  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as { filename?: string };
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and filename");
    }
    declarations.set(session.import_id, declaration.filename);
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const index = contentAttempt++;
    const importId = importIdFromContentUrl(route.request().url());
    const name = declarations.get(importId);
    if (!name) throw new Error(`content started before its declaration was observed: ${importId}`);
    startedNames.push(name);
    const response = await route.fetch();
    const artifact = (await response.json()) as { revision_id?: string };
    if (!artifact.revision_id) {
      throw new Error(`artifact import did not return a revision id for ${name}`);
    }
    promotedRevisionIds.set(name, artifact.revision_id);
    held.push({ importId, name, response });
    await gates[index]!.promise;
    await route.fulfill({ response });
  });

  try {
    const projectName = `ingress-gate-${Date.now()}`;
    const projectId = await createProject(page, projectName);
    const pickerFiles = [
      browserFile("picker-image.png", "image/png", sourcePng),
      browserFile("picker-video.webm", "video/webm", sourceWebm),
    ];
    const dropFiles = [
      browserFile("drop-image.png", "image/png", sourcePng),
      browserFile("drop-video.webm", "video/webm", sourceWebm),
    ];

    await page.getByLabel("选择多个本地媒体").setInputFiles(
      pickerFiles.map((file) => ({
        name: file.name,
        mimeType: file.mimeType,
        buffer: Buffer.from(file.base64, "base64"),
      })),
    );
    await expect(page.locator(".media-ingress-batch")).toHaveCount(1);
    await dispatchDrop(page.getByTestId("canvas-media-ingress-boundary"), dropFiles);
    await expect(page.locator(".media-ingress-batch")).toHaveCount(2);
    await expect.poll(() => held.length, { timeout: importAdmissionTimeout }).toBe(3);
    await expect(page.locator(".media-ingress-reservation")).toHaveCount(4);

    await expect(page.locator(".media-ingress-reservation strong")).toHaveText(
      [...dropFiles, ...pickerFiles].map((file) => file.name),
    );

    const reservationPositions = new Map<string, { x: number; y: number }>();

    const reservationBoxes = await Promise.all(
      [...pickerFiles, ...dropFiles].map(async (file) => {
        const reservation = page
          .locator(".media-ingress-reservation")
          .filter({ hasText: file.name });
        await expect(reservation).toBeVisible();
        const box = await reservation.boundingBox();
        if (!box) throw new Error(`missing reservation geometry for ${file.name}`);
        reservationPositions.set(file.name, await canvasPosition(reservation));
        return box;
      }),
    );
    for (let first = 0; first < reservationBoxes.length; first += 1) {
      for (let second = first + 1; second < reservationBoxes.length; second += 1) {
        expect(boxesOverlap(reservationBoxes[first]!, reservationBoxes[second]!)).toBe(false);
      }
    }

    expect(new Set(startedNames)).toEqual(
      new Set(["picker-image.png", "picker-video.webm", "drop-image.png"]),
    );
    expect(startedNames).not.toContain("drop-video.webm");
    gates[0]!.resolve();
    await expect.poll(() => held.length, { timeout: importAdmissionTimeout }).toBe(4);
    expect(startedNames[3]).toBe("drop-video.webm");
    for (const gate of gates.slice(1)) gate.resolve();

    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(4, {
      timeout: importAdmissionTimeout,
    });
    await expect(page.locator('article[data-node-kind="media_input"] img')).toHaveCount(2);
    await expect(page.locator('article[data-node-kind="media_input"] video')).toHaveCount(2);
    await approveGraphChange(page);

    const graph = await readGraph(page, sessionToken, projectId);
    expect(mediaNodes(graph)).toHaveLength(4);
    expect(promotedRevisionIds.size).toBe(4);
    for (const file of [...pickerFiles, ...dropFiles]) {
      const revisionId = promotedRevisionIds.get(file.name);
      const reservationPosition = reservationPositions.get(file.name);
      if (!revisionId || !reservationPosition) {
        throw new Error(`missing pre-promotion identity or coordinates for ${file.name}`);
      }
      const promotedNode = mediaNodes(graph).find(
        (node) => node.data.artifact_revision_id === revisionId,
      );
      if (!promotedNode) {
        throw new Error(`formal media node is missing promoted revision ${revisionId}`);
      }
      expect(promotedNode.position).toEqual(reservationPosition);
      const formalDomNode = page.locator(`article[data-node-id="${promotedNode.id}"]`);
      await expect(formalDomNode).toHaveCount(1);
      await expect(formalDomNode).toHaveAttribute("data-node-id", promotedNode.id);
    }
    assertPortableSnapshot(graph.snapshot);
    expect(graphSnapshots.length).toBeGreaterThan(0);
    for (const snapshot of graphSnapshots) assertPortableSnapshot(snapshot);
    for (const graphRequestUrl of graphRequestUrls) {
      assertNoSessionCredentialInUrl(graphRequestUrl, sessionToken);
    }

    await page.reload();
    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(4);
    await expect(page.locator('article[data-node-kind="media_input"] img')).toHaveCount(2);
    const videos = page.locator('article[data-node-kind="media_input"] video');
    await expect(videos).toHaveCount(2);
    expect(
      await videos.evaluateAll((elements) =>
        elements.map((element) => (element as HTMLVideoElement).autoplay),
      ),
    ).toEqual([false, false]);

    await page.getByRole("button", { name: "\u8fd4\u56de\u9879\u76ee\u5217\u8868" }).click();
    await page.getByRole("button", { name: projectName }).click();
    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(4);
    await expect(page.locator('article[data-node-kind="media_input"] img')).toHaveCount(2);
    await expect(page.locator('article[data-node-kind="media_input"] video')).toHaveCount(2);
  } finally {
    for (const gate of gates) gate.resolve();
  }
});

test("does not start a fourth node-picker import until a shared FIFO permit releases", async ({
  page,
}) => {
  const declarations = new Map<string, string>();
  const declaredNames: string[] = [];
  const contentNames: string[] = [];
  const contentGates = Array.from({ length: 3 }, () => deferred<void>());
  const contentStarted = Array.from({ length: 3 }, () => deferred<void>());
  const contentSettled = Array.from({ length: 4 }, () => deferred<void>());
  let contentAttempt = 0;

  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as { filename?: string };
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and filename");
    }
    declarations.set(session.import_id, declaration.filename);
    declaredNames.push(declaration.filename);
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const index = contentAttempt++;
    const importId = importIdFromContentUrl(route.request().url());
    const filename = declarations.get(importId);
    if (!filename) throw new Error(`content started before declaration: ${importId}`);
    contentNames.push(filename);
    try {
      const response = await route.fetch();
      if (index < 3) {
        contentStarted[index]!.resolve();
        await contentGates[index]!.promise;
      }
      await route.fulfill({ response });
    } finally {
      contentSettled[index]?.resolve();
    }
  });

  try {
    await createProject(page, `ingress-node-permit-${Date.now()}`);
    await page.getByRole("button", { name: "\u6dfb\u52a0\u5a92\u4f53\u8282\u70b9" }).click();
    const targetNode = page.locator('article[data-node-kind="media_input"]');
    await expect(targetNode).toHaveCount(1);
    await approveGraphChange(page);

    await page.getByLabel("\u9009\u62e9\u591a\u4e2a\u672c\u5730\u5a92\u4f53").setInputFiles([
      { name: "permit-first.png", mimeType: "image/png", buffer: sourcePng },
      { name: "permit-second.webm", mimeType: "video/webm", buffer: sourceWebm },
      { name: "permit-third.png", mimeType: "image/png", buffer: replacementPng },
    ]);
    await Promise.all(contentStarted.map(({ promise }) => promise));

    const nodePicker = targetNode.locator('input[type="file"]');
    await nodePicker.setInputFiles({
      name: "permit-fourth-node.png",
      mimeType: "image/png",
      buffer: sourcePng,
    });
    await expect(nodePicker).toBeDisabled();
    expect(declaredNames).not.toContain("permit-fourth-node.png");
    expect(contentNames).not.toContain("permit-fourth-node.png");

    contentGates[0]!.resolve();
    await expect.poll(() => declaredNames).toContain("permit-fourth-node.png");
    await expect.poll(() => contentNames).toContain("permit-fourth-node.png");
    expect(declaredNames.indexOf("permit-fourth-node.png")).toBe(3);
    expect(contentNames.indexOf("permit-fourth-node.png")).toBe(3);

    contentGates[1]!.resolve();
    contentGates[2]!.resolve();
    await Promise.all(contentSettled.map(({ promise }) => promise));
    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(4);
    await approveGraphChange(page);
  } finally {
    for (const gate of contentGates) gate.resolve();
    await Promise.all(
      contentSettled.slice(0, contentAttempt).map(({ promise }) => promise),
    );
  }
});

test("fills targets, defaults occupied drop and paste to create, and leaves editor paste native", async ({
  page,
  sessionToken,
}) => {
  const importCompletionTimeout = 15_000;
  const projectId = await createProject(page, `ingress-target-${Date.now()}`);
  await page.getByRole("button", { name: "添加媒体节点" }).click();
  let nodes = page.locator('article[data-node-kind="media_input"]');
  await expect(nodes).toHaveCount(1);
  const originalId = await nodes.nth(0).getAttribute("data-node-id");
  if (!originalId) throw new Error("initial media node id is missing");
  const original = page.locator(`[data-node-id="${originalId}"]`);

  await dispatchDrop(original, [browserFile("target-source.png", "image/png", sourcePng)]);
  await expectImageWidth(original, 4);

  await original.getByRole("button", { name: "复制媒体节点" }).click();
  await expect(nodes).toHaveCount(2);
  const copiedId = (await nodes.evaluateAll((elements) =>
    elements.map((element) => element.getAttribute("data-node-id")),
  )).find((id) => id && id !== originalId);
  if (!copiedId) throw new Error("copied media node id is missing");
  const copied = page.locator(`[data-node-id="${copiedId}"]`);
  await expectImageWidth(copied, 4);

  const replacement = browserFile("target-replacement.png", "image/png", replacementPng);
  await dispatchDrop(original, [replacement]);
  const choice = page.getByRole("dialog", { name: "选择导入方式" });
  await expect(choice).toBeVisible();
  await choice.getByRole("button", { name: "新建节点" }).click();
  await expect(nodes).toHaveCount(3, { timeout: importCompletionTimeout });
  await expectImageWidth(original, 4);
  await expectImageWidth(copied, 4);
  await approveGraphChange(page);

  await dispatchDrop(original, [replacement]);
  await expect(choice).toBeVisible();
  await choice.getByRole("button", { name: "替换当前" }).click();
  await expectImageWidth(original, 2);
  await expectImageWidth(copied, 4);

  await page.getByRole("button", { name: "添加媒体节点" }).click();
  await expect(nodes).toHaveCount(4, { timeout: importCompletionTimeout });
  const emptyTarget = nodes.nth(3);
  const emptyTargetId = await emptyTarget.getAttribute("data-node-id");
  if (!emptyTargetId) throw new Error("empty media target id is missing");
  await selectFlowMediaNode(page, emptyTargetId);
  expect(
    await dispatchMediaPaste(page, [
      browserFile("paste-empty.png", "image/png", sourcePng),
    ]),
  ).toBe(true);
  await expectImageWidth(emptyTarget, 4);

  await selectFlowMediaNode(page, emptyTargetId);
  expect(await dispatchMediaPaste(page, [replacement])).toBe(true);
  await expect(choice).toBeVisible();
  await choice.getByRole("button", { name: "新建节点" }).click();
  await expect(nodes).toHaveCount(5, { timeout: importCompletionTimeout });

  const batchesBeforeMultiplePaste = await page.locator(".media-ingress-batch").count();
  const multiplePaste = await dispatchMediaPasteWithDiagnostics(page, [
      browserFile("paste-multiple.png", "image/png", sourcePng),
      browserFile("paste-multiple-second.png", "image/png", replacementPng),
    ]);
  expect(multiplePaste).toEqual({
    defaultPrevented: true,
    fileCount: 2,
    itemTypes: ["image/png", "image/png"],
  });
  await expect(choice).toHaveCount(0);
  await expect(page.locator(".media-ingress-batch")).toHaveCount(
    batchesBeforeMultiplePaste + 1,
  );
  await expect(nodes).toHaveCount(7, { timeout: importCompletionTimeout });

  await page.getByRole("button", { name: "添加文案节点" }).click();
  const editor = page.getByLabel("文案内容").last();
  expect(await dispatchTextPaste(editor, "原生粘贴文字")).toBe(false);
  await expect(editor).toHaveValue("原生粘贴文字");
  await approveGraphChange(page);
  await expect
    .poll(
      async () => {
        const graph = await readGraph(page, sessionToken, projectId);
        return graph.snapshot.nodes.find((node) => node.kind === "text_source")?.data.text;
      },
      { timeout: 15_000 },
    )
    .toBe("原生粘贴文字");
  await expect(nodes).toHaveCount(7, { timeout: importCompletionTimeout });
});

test("places one targetless pasted image at the canvas viewport center", async ({
  page,
  sessionToken,
}) => {
  const projectId = await createProject(page, `ingress-paste-center-${Date.now()}`);
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(0);
  await expect(page.locator(".react-flow__node.selected")).toHaveCount(0);
  const viewportCenter = await canvasViewportCenterInFlow(page);

  expect(
    await dispatchMediaPaste(page, [
      browserFile("paste-at-center.png", "image/png", sourcePng),
    ]),
  ).toBe(true);
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(1);
  await approveGraphChange(page);
  await expect
    .poll(
      async () => mediaNodes(await readGraph(page, sessionToken, projectId)).length,
      { timeout: 15_000 },
    )
    .toBe(1);
  const nodes = mediaNodes(await readGraph(page, sessionToken, projectId));
  expect(nodes).toHaveLength(1);
  expect(nodes[0]?.position.x).toBeCloseTo(viewportCenter.x, 8);
  expect(nodes[0]?.position.y).toBeCloseTo(viewportCenter.y, 8);
});

test("isolates corrupt and real GIF imports, reselects only the failed slot, and ignores external navigation drops", async ({
  page,
  sessionToken,
}) => {
  // Valid videos complete real probe/validation while invalid images fail;
  // wait for the asserted terminal state rather than the default 5 seconds.
  const importCompletionTimeout = 15_000;
  const declarationIds = new Map<string, string[]>();
  const importFilenames = new Map<string, string>();
  const expectedContentFailureStatuses = new Map<string, number>([
    ["corrupt.png", 422],
    ["unsupported.gif", 422],
  ]);
  const observedExpectedContentFailures = new Map<string, number>();
  const externalRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin === "https://outside.invalid") externalRequests.push(request.url());
  });
  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as {
      client_import_id?: string;
      filename?: string;
    };
    if (declaration.filename && declaration.client_import_id) {
      const values = declarationIds.get(declaration.filename) ?? [];
      values.push(declaration.client_import_id);
      declarationIds.set(declaration.filename, values);
    }
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and filename");
    }
    importFilenames.set(session.import_id, declaration.filename);
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const importId = importIdFromContentUrl(route.request().url());
    const filename = importFilenames.get(importId);
    if (!filename) {
      throw new Error(`content started before its declaration was observed: ${importId}`);
    }
    const response = await route.fetch();
    const expectedStatus = expectedContentFailureStatuses.get(filename);
    if (response.status() === expectedStatus) {
      observedExpectedContentFailures.set(filename, response.status());
      await route.fulfill({
        response,
        headers: {
          ...response.headers(),
          "X-E2E-Expected-Artifact-Import-Content-Client-Error": "true",
        },
      });
      return;
    }
    await route.fulfill({ response });
  });

  const projectId = await createProject(page, `ingress-failure-${Date.now()}`);
  const picker = page.getByLabel("选择多个本地媒体");
  await picker.setInputFiles([
    {
      name: "valid-image.png",
      mimeType: "image/png",
      buffer: sourcePng,
    },
    {
      name: "valid-video.webm",
      mimeType: "video/webm",
      buffer: sourceWebm,
    },
    {
      name: "corrupt.png",
      mimeType: "image/png",
      buffer: corruptPng,
    },
    {
      name: "unsupported.gif",
      mimeType: "image/gif",
      buffer: unsupportedGif,
    },
  ]);

  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(2, {
    timeout: importCompletionTimeout,
  });
  const corruptRow = page.getByRole("listitem", { name: "失败项 corrupt.png" });
  const gifRow = page.getByRole("listitem", { name: "失败项 unsupported.gif" });
  await expect(corruptRow).toBeVisible();
  await expect(gifRow).toBeVisible();
  await expect(corruptRow).toContainText("重新选择");
  await expect(gifRow).toContainText("重新选择");
  expect([...observedExpectedContentFailures.entries()].sort()).toEqual(
    [...expectedContentFailureStatuses.entries()].sort(),
  );

  const reselect = corruptRow.getByLabel("为 corrupt.png 重新选择文件");
  await reselect.setInputFiles({
    name: "corrupt-reselected.png",
    mimeType: "image/png",
    buffer: replacementPng,
  });
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(3, {
    timeout: importCompletionTimeout,
  });
  await expect(corruptRow).toHaveCount(0);
  const attempts = declarationIds.get("corrupt.png") ?? [];
  expect(attempts).toHaveLength(1);
  const reselectedAttempts = declarationIds.get("corrupt-reselected.png") ?? [];
  expect(reselectedAttempts).toHaveLength(1);
  expect(reselectedAttempts[0]).not.toBe(attempts[0]);

  const beforeExternalDrop = {
    url: page.url(),
    batches: await page.locator(".media-ingress-batch").count(),
    nodes: await page.locator('article[data-node-kind="media_input"]').count(),
  };
  expect(
    await dispatchExternalNavigationDrop(
      page.getByTestId("canvas-media-ingress-boundary"),
      "https://outside.invalid/never-requested",
    ),
  ).toBe(true);
  await expect(page).toHaveURL(beforeExternalDrop.url);
  await expect(page.locator(".media-ingress-batch")).toHaveCount(beforeExternalDrop.batches);
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(beforeExternalDrop.nodes);
  expect(externalRequests).toEqual([]);
  await approveGraphChange(page);
  await expect.poll(
    async () => mediaNodes(await readGraph(page, sessionToken, projectId)).length,
    { timeout: 15_000 },
  ).toBe(3);
});

test("reuses the exact reservation coordinates when a failed slot is reselected", async ({
  page,
  sessionToken,
}) => {
  const originalName = "stable-reselect-corrupt.png";
  const reselectedName = "stable-reselect-fixed.png";
  const declarations = new Map<string, string>();
  const originalFailureFetched = deferred<void>();
  const originalFailureGate = deferred<void>();
  const originalFailureSettled = deferred<void>();
  const reselectedContentFetched = deferred<void>();
  const reselectedContentGate = deferred<void>();
  const reselectedContentSettled = deferred<void>();
  let originalContentStarted = false;
  let reselectedContentStarted = false;

  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as { filename?: string };
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and filename");
    }
    declarations.set(session.import_id, declaration.filename);
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const importId = importIdFromContentUrl(route.request().url());
    const filename = declarations.get(importId);
    if (!filename) throw new Error(`content started before declaration: ${importId}`);
    const response = await route.fetch();
    if (filename === originalName) {
      originalContentStarted = true;
      try {
        expect(response.status()).toBe(422);
        originalFailureFetched.resolve();
        await originalFailureGate.promise;
        await route.fulfill({
          response,
          headers: {
            ...response.headers(),
            "X-E2E-Expected-Artifact-Import-Content-Client-Error": "true",
          },
        });
      } finally {
        originalFailureSettled.resolve();
      }
      return;
    }
    if (filename === reselectedName) {
      reselectedContentStarted = true;
      try {
        expect(response.status()).toBe(200);
        reselectedContentFetched.resolve();
        await reselectedContentGate.promise;
        await route.fulfill({ response });
      } finally {
        reselectedContentSettled.resolve();
      }
      return;
    }
    throw new Error(`unexpected reselect content request for ${filename}`);
  });

  try {
    const projectId = await createProject(page, `ingress-stable-reselect-${Date.now()}`);
    await page.getByLabel("\u9009\u62e9\u591a\u4e2a\u672c\u5730\u5a92\u4f53").setInputFiles({
      name: originalName,
      mimeType: "image/png",
      buffer: corruptPng,
    });
    await originalFailureFetched.promise;
    const originalReservation = page
      .locator(".media-ingress-reservation")
      .filter({ hasText: originalName });
    await expect(originalReservation).toBeVisible();
    const originalPosition = await canvasPosition(originalReservation);

    originalFailureGate.resolve();
    await originalFailureSettled.promise;
    const failedRow = page.getByRole("listitem", {
      name: `\u5931\u8d25\u9879 ${originalName}`,
    });
    await expect(failedRow).toBeVisible({ timeout: 15_000 });
    await failedRow
      .getByLabel(`\u4e3a ${originalName} \u91cd\u65b0\u9009\u62e9\u6587\u4ef6`)
      .setInputFiles({
        name: reselectedName,
        mimeType: "image/png",
        buffer: replacementPng,
      });
    await reselectedContentFetched.promise;
    const reselectedReservation = page
      .locator(".media-ingress-reservation")
      .filter({ hasText: reselectedName });
    await expect(reselectedReservation).toBeVisible();
    expect(await canvasPosition(reselectedReservation)).toEqual(originalPosition);

    reselectedContentGate.resolve();
    await reselectedContentSettled.promise;
    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(1);
    await approveGraphChange(page);
    await expect
      .poll(
        async () => mediaNodes(await readGraph(page, sessionToken, projectId)).length,
        { timeout: 15_000 },
      )
      .toBe(1);
    expect(mediaNodes(await readGraph(page, sessionToken, projectId))[0]?.position).toEqual(
      originalPosition,
    );
  } finally {
    originalFailureGate.resolve();
    reselectedContentGate.resolve();
    await Promise.all([
      originalContentStarted ? originalFailureSettled.promise : Promise.resolve(),
      reselectedContentStarted ? reselectedContentSettled.promise : Promise.resolve(),
    ]);
  }
});

test("holds FIFO after an unknown running cancellation until exact retry confirmation", async ({
  page,
  allowArtifactImportStatusNetworkFailure,
}) => {
  const activeNames = [
    "unknown-first.png",
    "unknown-second.webm",
    "unknown-third.png",
  ];
  const queuedName = "unknown-fourth.png";
  const declarations = new Map<string, string>();
  const declaredNames: string[] = [];
  const contentNames: string[] = [];
  const contentGates = new Map(activeNames.map((name) => [name, deferred<void>()]));
  const contentStarted = new Map(activeNames.map((name) => [name, deferred<void>()]));
  const contentSettled = new Map(
    [...activeNames, queuedName].map((name) => [name, deferred<void>()]),
  );
  const preCancellationStatusObserved = deferred<void>();
  const preCancellationStatusGate = deferred<void>();
  const preCancellationStatusSettled = deferred<void>();
  const cancellationStatusFailure = deferred<void>();
  let unknownFirstStatusRequests = 0;
  let preCancellationStatusStarted = false;
  const startedContentNames = new Set<string>();
  let firstContentFailure: string | null = null;

  page.on("requestfailed", (request) => {
    const url = new URL(request.url());
    if (
      request.method() === "PUT" &&
      artifactImportContentPattern.test(url.pathname) &&
      declarations.get(importIdFromContentUrl(request.url())) === "unknown-first.png"
    ) {
      firstContentFailure = request.failure()?.errorText ?? null;
    }
  });
  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as { filename?: string };
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and filename");
    }
    declarations.set(session.import_id, declaration.filename);
    declaredNames.push(declaration.filename);
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const importId = importIdFromContentUrl(route.request().url());
    const filename = declarations.get(importId);
    if (!filename) throw new Error(`content started before declaration: ${importId}`);
    startedContentNames.add(filename);
    contentNames.push(filename);
    try {
      const response = await route.fetch();
      const gate = contentGates.get(filename);
      if (gate) {
        contentStarted.get(filename)?.resolve();
        await gate.promise;
      }
      const failure = route.request().failure()?.errorText;
      if (failure) {
        if (filename !== "unknown-first.png") {
          throw new Error(`unexpected aborted content request for ${filename}: ${failure}`);
        }
        expect(failure).toMatch(/net::ERR_ABORTED/);
        return;
      }
      await route.fulfill({ response });
    } finally {
      contentSettled.get(filename)?.resolve();
    }
  });
  await page.route(artifactImportSessionPattern, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const filename = declarations.get(importIdFromSessionUrl(route.request().url()));
    if (filename !== "unknown-first.png") {
      const response = await route.fetch();
      await route.fulfill({ response });
      return;
    }
    const requestNumber = ++unknownFirstStatusRequests;
    if (requestNumber === 1) {
      preCancellationStatusStarted = true;
      preCancellationStatusObserved.resolve();
      try {
        await preCancellationStatusGate.promise;
        const failure = route.request().failure()?.errorText;
        if (failure) {
          expect(failure).toMatch(/net::ERR_ABORTED/);
          return;
        }
        const response = await route.fetch();
        await route.fulfill({ response });
      } finally {
        preCancellationStatusSettled.resolve();
      }
      return;
    }
    if (requestNumber === 2) {
      allowArtifactImportStatusNetworkFailure(route.request().url());
      cancellationStatusFailure.resolve();
      await route.abort("failed");
      return;
    }
    const response = await route.fetch();
    await route.fulfill({ response });
  });

  try {
    await createProject(page, `ingress-unknown-cancel-${Date.now()}`);
    await page.getByLabel("\u9009\u62e9\u591a\u4e2a\u672c\u5730\u5a92\u4f53").setInputFiles([
      { name: "unknown-first.png", mimeType: "image/png", buffer: sourcePng },
      { name: "unknown-second.webm", mimeType: "video/webm", buffer: sourceWebm },
      { name: "unknown-third.png", mimeType: "image/png", buffer: replacementPng },
      { name: queuedName, mimeType: "image/png", buffer: sourcePng },
    ]);
    await Promise.all(activeNames.map((name) => contentStarted.get(name)!.promise));
    await preCancellationStatusObserved.promise;
    expect(declaredNames).not.toContain(queuedName);
    expect(contentNames).not.toContain(queuedName);

    await page.getByRole("button", { name: "\u53d6\u6d88 unknown-first.png" }).click();
    await cancellationStatusFailure.promise;
    await expect.poll(() => firstContentFailure).toMatch(/net::ERR_ABORTED/);
    contentGates.get("unknown-first.png")!.resolve();
    await contentSettled.get("unknown-first.png")!.promise;
    preCancellationStatusGate.resolve();
    await preCancellationStatusSettled.promise;

    const cancellingRow = page.getByRole("listitem", {
      name: "\u53d6\u6d88\u786e\u8ba4\u9879 unknown-first.png",
    });
    await expect(cancellingRow).toBeVisible();
    await expect(
      cancellingRow.getByRole("button", { name: "\u91cd\u8bd5\u786e\u8ba4 unknown-first.png" }),
    ).toBeVisible();
    expect(declaredNames).not.toContain(queuedName);
    expect(contentNames).not.toContain(queuedName);

    await cancellingRow
      .getByRole("button", { name: "\u91cd\u8bd5\u786e\u8ba4 unknown-first.png" })
      .click();
    const savedRow = page.getByRole("listitem", {
      name: "\u5f85\u653e\u7f6e\u9879 unknown-first.png",
    });
    await expect(savedRow).toBeVisible({ timeout: 15_000 });
    await expect.poll(() => declaredNames).toContain(queuedName);
    await expect.poll(() => contentNames).toContain(queuedName);
    expect(declaredNames.indexOf(queuedName)).toBe(3);
    expect(contentNames.indexOf(queuedName)).toBe(3);

    contentGates.get("unknown-second.webm")!.resolve();
    contentGates.get("unknown-third.png")!.resolve();
    await Promise.all(
      [...activeNames, queuedName].map((name) => contentSettled.get(name)!.promise),
    );
    await savedRow
      .getByRole("button", { name: "\u653e\u5165\u753b\u5e03 unknown-first.png" })
      .click();
    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(4);
    await approveGraphChange(page);
  } finally {
    for (const gate of contentGates.values()) gate.resolve();
    preCancellationStatusGate.resolve();
    await Promise.all(
      [...startedContentNames].map((name) => contentSettled.get(name)!.promise),
    );
    if (preCancellationStatusStarted) await preCancellationStatusSettled.promise;
  }
});

test("cancels an individually clicked queued item before it declares or uploads", async ({
  page,
}) => {
  const activeNames = [
    "queued-cancel-first.png",
    "queued-cancel-second.webm",
    "queued-cancel-third.png",
  ];
  const queuedName = "queued-cancel-fourth.png";
  const declarations = new Map<string, string>();
  const declaredNames: string[] = [];
  const contentNames: string[] = [];
  const contentGates = new Map(activeNames.map((name) => [name, deferred<void>()]));
  const contentStarted = new Map(activeNames.map((name) => [name, deferred<void>()]));
  const contentSettled = new Map(activeNames.map((name) => [name, deferred<void>()]));
  const startedNames = new Set<string>();

  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as { filename?: string };
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and filename");
    }
    declarations.set(session.import_id, declaration.filename);
    declaredNames.push(declaration.filename);
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const importId = importIdFromContentUrl(route.request().url());
    const filename = declarations.get(importId);
    if (!filename) throw new Error(`content started before declaration: ${importId}`);
    if (filename === queuedName) {
      throw new Error("queued item declared or uploaded after its direct cancellation");
    }
    const gate = contentGates.get(filename);
    if (!gate) throw new Error(`unexpected active content request for ${filename}`);
    startedNames.add(filename);
    contentNames.push(filename);
    try {
      const response = await route.fetch();
      contentStarted.get(filename)?.resolve();
      await gate.promise;
      await route.fulfill({ response });
    } finally {
      contentSettled.get(filename)?.resolve();
    }
  });

  try {
    await createProject(page, `ingress-queued-cancel-${Date.now()}`);
    await page.getByLabel("\u9009\u62e9\u591a\u4e2a\u672c\u5730\u5a92\u4f53").setInputFiles([
      { name: activeNames[0]!, mimeType: "image/png", buffer: sourcePng },
      { name: activeNames[1]!, mimeType: "video/webm", buffer: sourceWebm },
      { name: activeNames[2]!, mimeType: "image/png", buffer: replacementPng },
      { name: queuedName, mimeType: "image/png", buffer: sourcePng },
    ]);
    await Promise.all(activeNames.map((name) => contentStarted.get(name)!.promise));
    const queuedRow = page.getByRole("listitem", {
      name: `\u6392\u961f\u9879 ${queuedName}`,
    });
    const queuedReservation = page
      .locator(".media-ingress-reservation")
      .filter({ hasText: queuedName });
    await expect(queuedRow).toBeVisible();
    await expect(queuedReservation).toBeVisible();
    expect(declaredNames).not.toContain(queuedName);
    expect(contentNames).not.toContain(queuedName);

    await queuedRow.getByRole("button", { name: `\u53d6\u6d88 ${queuedName}` }).click();
    await expect(queuedRow).toHaveCount(0);
    await expect(queuedReservation).toHaveCount(0);
    await expect(page.locator(".media-ingress-batch__states")).toContainText(
      /\u5df2\u53d6\u6d88\s*1/,
    );
    expect(declaredNames).not.toContain(queuedName);
    expect(contentNames).not.toContain(queuedName);

    for (const gate of contentGates.values()) gate.resolve();
    await Promise.all(activeNames.map((name) => contentSettled.get(name)!.promise));
    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(3);
    expect([...startedNames].sort()).toEqual([...activeNames].sort());
    expect(declaredNames).not.toContain(queuedName);
    expect(contentNames).not.toContain(queuedName);
  } finally {
    for (const gate of contentGates.values()) gate.resolve();
    await Promise.all(
      [...startedNames].map((name) => contentSettled.get(name)!.promise),
    );
  }
});

test("stops queued and running batch work without removing an already placed result", async ({
  page,
}) => {
  const placedName = "stop-placed.png";
  const runningNames = [
    "stop-running-second.webm",
    "stop-running-third.png",
    "stop-running-fourth.png",
  ];
  const queuedName = "stop-queued-fifth.png";
  const declarations = new Map<string, string>();
  const declaredNames: string[] = [];
  const contentNames: string[] = [];
  const runningContentGates = new Map(
    runningNames.map((name) => [name, deferred<void>()]),
  );
  const runningContentStarted = new Map(
    runningNames.map((name) => [name, deferred<void>()]),
  );
  const contentSettled = new Map(
    [placedName, ...runningNames, queuedName].map((name) => [name, deferred<void>()]),
  );
  const statusGate = deferred<void>();
  const statusFilenames = new Set<string>();
  const startedContentNames = new Set<string>();
  const abortedRunningNames = new Set<string>();
  let activeContentHandlers = 0;
  let activeStatusHandlers = 0;
  let stopRequested = false;

  page.on("requestfailed", (request) => {
    const url = new URL(request.url());
    if (request.method() !== "PUT" || !artifactImportContentPattern.test(url.pathname)) {
      return;
    }
    const filename = declarations.get(importIdFromContentUrl(request.url()));
    if (
      filename &&
      runningContentGates.has(filename) &&
      /net::ERR_ABORTED/.test(request.failure()?.errorText ?? "")
    ) {
      abortedRunningNames.add(filename);
    }
  });
  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as { filename?: string };
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and filename");
    }
    declarations.set(session.import_id, declaration.filename);
    declaredNames.push(declaration.filename);
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const importId = importIdFromContentUrl(route.request().url());
    const filename = declarations.get(importId);
    if (!filename) throw new Error(`content started before declaration: ${importId}`);
    startedContentNames.add(filename);
    contentNames.push(filename);
    activeContentHandlers += 1;
    try {
      const response = await route.fetch();
      const gate = runningContentGates.get(filename);
      if (gate) {
        runningContentStarted.get(filename)?.resolve();
        await gate.promise;
      }
      const failure = route.request().failure()?.errorText;
      if (failure) {
        if (!runningContentGates.has(filename)) {
          throw new Error(`unexpected aborted content request for ${filename}: ${failure}`);
        }
        expect(failure).toMatch(/net::ERR_ABORTED/);
        return;
      }
      await route.fulfill({ response });
    } finally {
      contentSettled.get(filename)?.resolve();
      activeContentHandlers -= 1;
    }
  });
  await page.route(artifactImportSessionPattern, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const filename = declarations.get(importIdFromSessionUrl(route.request().url()));
    if (!stopRequested || !filename || !runningContentGates.has(filename)) {
      const response = await route.fetch();
      await route.fulfill({ response });
      return;
    }
    const settled = deferred<void>();
    statusFilenames.add(filename);
    activeStatusHandlers += 1;
    try {
      const response = await route.fetch();
      await statusGate.promise;
      await route.fulfill({ response });
    } finally {
      settled.resolve();
      activeStatusHandlers -= 1;
    }
  });

  try {
    await createProject(page, `ingress-stop-batch-${Date.now()}`);
    await page.getByLabel("\u9009\u62e9\u591a\u4e2a\u672c\u5730\u5a92\u4f53").setInputFiles([
      { name: placedName, mimeType: "image/png", buffer: sourcePng },
      { name: runningNames[0]!, mimeType: "video/webm", buffer: sourceWebm },
      { name: runningNames[1]!, mimeType: "image/png", buffer: replacementPng },
      { name: runningNames[2]!, mimeType: "image/png", buffer: sourcePng },
      { name: queuedName, mimeType: "image/png", buffer: replacementPng },
    ]);
    await expect(page.locator(`img[alt="${placedName}"]`)).toBeVisible({
      timeout: 15_000,
    });
    await Promise.all(
      runningNames.map((name) => runningContentStarted.get(name)!.promise),
    );
    expect(declaredNames).not.toContain(queuedName);
    expect(contentNames).not.toContain(queuedName);

    const batch = page.locator(".media-ingress-batch");
    stopRequested = true;
    await batch.getByRole("button", { name: /\u505c\u6b62\u5269\u4f59\u4efb\u52a1/ }).click();
    for (const filename of runningNames) {
      await expect(
        page.getByRole("listitem", {
          name: `\u53d6\u6d88\u786e\u8ba4\u9879 ${filename}`,
        }),
      ).toBeVisible();
    }
    await expect(batch.locator(".media-ingress-batch__states")).toContainText(
      /\u5df2\u53d6\u6d88\s*1/,
    );
    await expect(page.locator(`img[alt="${placedName}"]`)).toBeVisible();
    expect(declaredNames).not.toContain(queuedName);
    expect(contentNames).not.toContain(queuedName);
    await expect.poll(() => abortedRunningNames.size).toBe(runningNames.length);
    await expect.poll(() => statusFilenames.size).toBe(runningNames.length);
    expect([...startedContentNames].sort()).toEqual(
      [placedName, ...runningNames].sort(),
    );

    for (const gate of runningContentGates.values()) gate.resolve();
    statusGate.resolve();
    await expect.poll(() => activeContentHandlers).toBe(0);
    await expect.poll(() => activeStatusHandlers).toBe(0);
  } finally {
    if (stopRequested) {
      await expect.poll(() => statusFilenames.size).toBe(runningNames.length);
    }
    for (const gate of runningContentGates.values()) gate.resolve();
    statusGate.resolve();
    await expect.poll(() => activeContentHandlers).toBe(0);
    await expect.poll(() => activeStatusHandlers).toBe(0);
  }
});

test("keeps a real failed import protected through pending, network, and conflict discard retries", async ({
  page,
  allowArtifactImportResultNetworkFailure,
}) => {
  const filename = "discard-retry-corrupt.png";
  const declarations = new Map<string, { clientImportId: string; filename: string }>();
  const deleteBodies: unknown[] = [];
  let deleteAttempt = 0;
  const deleteSettled = new Set<Promise<void>>();

  await page.route(artifactImportCollectionPattern, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const declaration = route.request().postDataJSON() as {
      client_import_id?: string;
      filename?: string;
    };
    const response = await route.fetch();
    const session = (await response.json()) as { import_id?: string };
    if (!session.import_id || !declaration.client_import_id || !declaration.filename) {
      throw new Error("artifact-import declaration did not return an import id and exact metadata");
    }
    declarations.set(session.import_id, {
      clientImportId: declaration.client_import_id,
      filename: declaration.filename,
    });
    await route.fulfill({ response });
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const importId = importIdFromContentUrl(route.request().url());
    const declaration = declarations.get(importId);
    if (!declaration || declaration.filename !== filename) {
      throw new Error("unexpected artifact-import content request");
    }
    const response = await route.fetch();
    expect(response.status()).toBe(422);
    await route.fulfill({
      response,
      headers: {
        ...response.headers(),
        "X-E2E-Expected-Artifact-Import-Content-Client-Error": "true",
      },
    });
  });
  await page.route(artifactImportResultPattern, async (route) => {
    if (route.request().method() !== "DELETE") return route.continue();
    const settled = deferred<void>();
    deleteSettled.add(settled.promise);
    try {
      const importId = importIdFromResultUrl(route.request().url());
      const declaration = declarations.get(importId);
      if (!declaration || declaration.filename !== filename) {
        throw new Error("discard request does not belong to the real failed import");
      }
      const body: unknown = route.request().postDataJSON();
      deleteBodies.push(body);
      expect(body).toEqual({
        client_import_id: declaration.clientImportId,
        expected_revision_id: null,
      });
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      deleteAttempt += 1;
      if (deleteAttempt === 1) {
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({ outcome: "discarded", content_cleanup: "pending" }),
        });
        return;
      }
      if (deleteAttempt === 2) {
        allowArtifactImportResultNetworkFailure(route.request().url());
        await route.abort("failed");
        return;
      }
      if (deleteAttempt === 3) {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          headers: { "X-E2E-Expected-Discard-Conflict": "true" },
          body: JSON.stringify({
            code: "artifact_import_conflict",
            import_id: importId,
            reason: "result_referenced",
          }),
        });
        return;
      }
      await route.fulfill({ response });
    } finally {
      settled.resolve();
    }
  });

  try {
    await createProject(page, `ingress-real-discard-retry-${Date.now()}`);
    await page.getByLabel("\u9009\u62e9\u591a\u4e2a\u672c\u5730\u5a92\u4f53").setInputFiles({
      name: filename,
      mimeType: "image/png",
      buffer: corruptPng,
    });
    const failedRow = page.getByRole("listitem", {
      name: `\u5931\u8d25\u9879 ${filename}`,
    });
    await expect(failedRow).toBeVisible({ timeout: 15_000 });
    await failedRow
      .getByRole("button", { name: `\u5220\u9664\u672c\u5730\u7d20\u6750\u5e76\u5ffd\u7565 ${filename}` })
      .click();

    const deletingRow = page.getByRole("listitem", {
      name: `\u5220\u9664\u672c\u5730\u7d20\u6750\u9879 ${filename}`,
    });
    const retry = deletingRow.getByRole("button", {
      name: `\u91cd\u8bd5\u5220\u9664\u672c\u5730\u7d20\u6750 ${filename}`,
    });
    const returnToProjects = page.getByRole("button", {
      name: "\u8fd4\u56de\u9879\u76ee\u5217\u8868",
    });
    const expectDiscardLocked = async (): Promise<void> => {
      await expect(deletingRow).toBeVisible();
      await expect(returnToProjects).toBeDisabled();
      await expect(returnToProjects).toHaveAttribute("aria-busy", "true");
      expect(
        await page.evaluate(() => {
          const event = new Event("beforeunload", { cancelable: true });
          return {
            dispatched: window.dispatchEvent(event),
            prevented: event.defaultPrevented,
          };
        }),
      ).toEqual({ dispatched: false, prevented: true });
      await expect(
        deletingRow.getByRole("button", { name: /\u91cd\u65b0\u9009\u62e9|\u653e\u5165\u753b\u5e03/ }),
      ).toHaveCount(0);
      await expect(
        deletingRow.locator("xpath=ancestor::article[1]").getByRole("button", {
          name: /\u5173\u95ed\u6279\u6b21/,
        }),
      ).toHaveCount(0);
    };

    await expect(deletingRow).toContainText("\u672c\u5730\u7d20\u6750\u6e05\u7406\u5f85\u5b8c\u6210");
    await expect(retry).toBeVisible();
    await expectDiscardLocked();

    await retry.click();
    await expect(deletingRow).toContainText("\u65e0\u6cd5\u8fde\u63a5\u672c\u5730\u670d\u52a1");
    await expectDiscardLocked();
    await retry.click();
    await expect(deletingRow).toContainText("\u4ecd\u88ab\u9879\u76ee\u5386\u53f2\u6216\u5176\u4ed6\u7ed3\u679c\u5f15\u7528");
    await expectDiscardLocked();
    await retry.click();
    await expect(deletingRow).toHaveCount(0);
    expect(deleteAttempt).toBe(4);
    expect(deleteBodies).toHaveLength(4);
    expect(new Set(deleteBodies.map((body) => JSON.stringify(body))).size).toBe(1);
  } finally {
    await Promise.all(deleteSettled);
  }
});

test("preserves a promoted media attachment when a second page wins the original project revision", async ({
  page,
  context,
  sessionToken,
  allowGraphRevisionConflict,
}) => {
  const filename = "cross-page-media.png";
  const contentGate = deferred<void>();
  const contentFetched = deferred<void>();
  const contentSettled = deferred<void>();
  const graphPostUrls: string[] = [];
  let contentStarted = false;

  const recordGraphPostUrl = (request: Request): void => {
    const url = new URL(request.url());
    if (request.method() === "POST" && /\/graph-change-sets$/i.test(url.pathname)) {
      graphPostUrls.push(request.url());
    }
  };
  page.on("request", recordGraphPostUrl);

  await page.route(artifactImportContentPattern, async (route) => {
    contentStarted = true;
    try {
      const response = await route.fetch();
      contentFetched.resolve();
      await contentGate.promise;
      await route.fulfill({ response });
    } finally {
      contentSettled.resolve();
    }
  });

  try {
    const projectName = `ingress-cross-page-${Date.now()}`;
    const projectId = await createProject(page, projectName);
    await expect(page.locator(".save-status")).toHaveText("\u5df2\u4fdd\u5b58", {
      timeout: 15_000,
    });

    const secondPage = await context.newPage();
    await secondPage.goto("/");
    assertNoSessionCredentialInUrl(secondPage.url(), sessionToken);
    secondPage.on("request", recordGraphPostUrl);
    await secondPage.getByRole("button", { name: projectName }).click();
    assertNoSessionCredentialInUrl(secondPage.url(), sessionToken);
    await expect(secondPage.locator(".save-status")).toHaveText("\u5df2\u4fdd\u5b58", {
      timeout: 15_000,
    });

    await page.getByLabel("\u9009\u62e9\u591a\u4e2a\u672c\u5730\u5a92\u4f53").setInputFiles({
      name: filename,
      mimeType: "image/png",
      buffer: sourcePng,
    });
    await contentFetched.promise;

    await secondPage
      .getByRole("button", { name: "\u6dfb\u52a0\u6587\u6848\u8282\u70b9" })
      .click();
    await approveGraphChange(secondPage);
    expect(
      mediaNodes(await readGraph(secondPage, sessionToken, projectId)),
    ).toHaveLength(0);

    allowGraphRevisionConflict(
      page,
      `${new URL(page.url()).origin}/api/v1/projects/${projectId}/graph-change-sets`,
    );
    contentGate.resolve();
    await contentSettled.promise;
    await page.getByRole("button", { name: "\u5ba1\u9605\u5e76\u5e94\u7528\u753b\u5e03\u53d8\u66f4" }).click();
    const conflict = page.getByRole("alert");
    await expect(conflict).toContainText("\u53e6\u4e00\u9875\u9762\u5df2\u4fdd\u5b58\u4e86\u65b0\u7248\u672c", {
      timeout: 15_000,
    });
    await expect(page.locator(`img[alt="${filename}"]`)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "\u8fd4\u56de\u9879\u76ee\u5217\u8868" }),
    ).toBeDisabled();

    await page.getByRole("button", { name: "\u4fdd\u7559\u672c\u5730\u526f\u672c" }).click();
    await expect(
      page.getByRole("dialog", { name: "\u51b2\u7a81\u526f\u672c\u53d8\u66f4\u5ba1\u9605" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "\u786e\u8ba4\u521b\u5efa\u526f\u672c" }).click();
    await expect
      .poll(() => new URL(page.url()).searchParams.get("project"), {
        timeout: 15_000,
      })
      .not.toBe(projectId);
    const copyProjectId = new URL(page.url()).searchParams.get("project");
    if (!copyProjectId) throw new Error("copied project query parameter is missing");
    assertNoSessionCredentialInUrl(page.url(), sessionToken);
    const copyGraph = await readGraph(page, sessionToken, copyProjectId);
    expect(mediaNodes(copyGraph)).toHaveLength(1);
    assertPortableSnapshot(copyGraph.snapshot);
    await expect(page.locator(`img[alt="${filename}"]`)).toBeVisible();
    expect(
      mediaNodes(await readGraph(secondPage, sessionToken, projectId)),
    ).toHaveLength(0);
    expect(graphPostUrls.length).toBeGreaterThan(0);
    for (const graphPostUrl of graphPostUrls) {
      assertNoSessionCredentialInUrl(graphPostUrl, sessionToken);
    }
  } finally {
    contentGate.resolve();
    if (contentStarted) await contentSettled.promise;
  }
});

test("recovers a late successful cancellation and keeps saved-media actions usable in a narrow sidebar", async ({
  page,
}) => {
  const originalContentGate = deferred<void>();
  const duringDiscardContentGate = deferred<void>();
  const afterConfirmationContentGate = deferred<void>();
  const deleteGate = deferred<void>();
  const originalContentFetched = deferred<void>();
  const duringDiscardContentFetched = deferred<void>();
  const afterConfirmationContentFetched = deferred<void>();
  const deleteStarted = deferred<void>();
  const originalContentSettled = deferred<void>();
  const duringDiscardContentSettled = deferred<void>();
  const afterConfirmationContentSettled = deferred<void>();
  const deleteSettled = deferred<void>();
  let deleteBody: unknown = null;
  let contentRequestFailure: string | null = null;
  let contentRequestIndex = 0;
  let originalContentStarted = false;
  let duringDiscardContentStarted = false;
  let afterConfirmationContentStarted = false;
  let deleteStartedRouting = false;
  page.on("requestfailed", (request) => {
    const url = new URL(request.url());
    if (
      request.method() === "PUT" &&
      artifactImportContentPattern.test(url.pathname)
    ) {
      contentRequestFailure = request.failure()?.errorText ?? null;
    }
  });
  await page.route(artifactImportContentPattern, async (route) => {
    const index = contentRequestIndex++;
    if (index === 0) {
      originalContentStarted = true;
      try {
        const response = await route.fetch();
        originalContentFetched.resolve();
        await originalContentGate.promise;
        const failure = route.request().failure()?.errorText;
        if (failure) {
          expect(failure).toMatch(/net::ERR_ABORTED/);
          return;
        }
        await route.fulfill({ response });
      } finally {
        originalContentSettled.resolve();
      }
      return;
    }
    if (index === 1) {
      duringDiscardContentStarted = true;
      try {
        const response = await route.fetch();
        duringDiscardContentFetched.resolve();
        await duringDiscardContentGate.promise;
        await route.fulfill({ response });
      } finally {
        duringDiscardContentSettled.resolve();
      }
      return;
    }
    if (index === 2) {
      afterConfirmationContentStarted = true;
      try {
        const response = await route.fetch();
        afterConfirmationContentFetched.resolve();
        await afterConfirmationContentGate.promise;
        await route.fulfill({ response });
      } finally {
        afterConfirmationContentSettled.resolve();
      }
      return;
    }
    throw new Error(`unexpected artifact-import content request ${index + 1}`);
  });
  await page.route(artifactImportResultPattern, async (route) => {
    deleteStartedRouting = true;
    try {
      expect(route.request().method()).toBe("DELETE");
      deleteBody = route.request().postDataJSON();
      const response = await route.fetch();
      expect(response.status()).toBe(200);
      deleteStarted.resolve();
      await deleteGate.promise;
      await route.fulfill({ response });
    } finally {
      deleteSettled.resolve();
    }
  });

  try {
    await createProject(page, `ingress-late-success-${Date.now()}`);
    await page.getByLabel("选择多个本地媒体").setInputFiles({
      name: "late-success.png",
      mimeType: "image/png",
      buffer: sourcePng,
    });
    await originalContentFetched.promise;
    const reservation = page
      .locator(".media-ingress-reservation")
      .filter({ hasText: "late-success.png" });
    await expect(reservation).toBeVisible();
    const originalReservationBox = await reservation.boundingBox();
    if (!originalReservationBox) throw new Error("late-success reservation geometry is missing");
    const originalReservationPosition = await canvasPosition(reservation);
    const originalReservationCenter = {
      clientX: originalReservationBox.x + originalReservationBox.width / 2,
      clientY: originalReservationBox.y + originalReservationBox.height / 2,
    };
    await page.getByRole("button", { name: "取消 late-success.png" }).click();

    const savedRow = page.getByRole("listitem", { name: "待放置项 late-success.png" });
    await expect(savedRow).toBeVisible({ timeout: 15_000 });
    await expect.poll(() => contentRequestFailure).toMatch(/net::ERR_ABORTED/);
    await page.setViewportSize({ width: 360, height: 900 });
    const savedActions = savedRow.locator(".media-ingress-saved-unplaced-actions");
    const placeSavedUnplaced = savedActions.getByRole("button", {
      name: "放入画布 late-success.png",
    });
    const discardSavedUnplaced = savedActions.getByRole("button", {
      name: "删除本地素材并忽略 late-success.png",
    });
    await expect(placeSavedUnplaced).toBeVisible();
    await expect(discardSavedUnplaced).toBeVisible();
    await expect(placeSavedUnplaced).toHaveText("放入画布");
    await expect(discardSavedUnplaced).toHaveText("删除本地素材");
    const savedActionGeometry = await savedActions.evaluate((actions) => {
      const buttons = Array.from(actions.querySelectorAll<HTMLButtonElement>("button"));
      const batches = actions.closest<HTMLElement>(".media-ingress-batches");
      if (buttons.length !== 2) {
        throw new Error("saved media recovery action count is invalid");
      }
      if (!batches) throw new Error("saved media recovery actions are outside the batch sidebar");
      return {
        actions: {
          clientWidth: actions.clientWidth,
          scrollWidth: actions.scrollWidth,
        },
        batches: {
          clientWidth: batches.clientWidth,
          scrollWidth: batches.scrollWidth,
        },
        buttons: buttons.map((button) => {
          const { x, y, width, height } = button.getBoundingClientRect();
          return {
            x,
            y,
            width,
            height,
            clientWidth: button.clientWidth,
            scrollWidth: button.scrollWidth,
          };
        }),
      };
    });
    expect(savedActionGeometry.actions.scrollWidth).toBeLessThanOrEqual(
      savedActionGeometry.actions.clientWidth,
    );
    expect(savedActionGeometry.batches.scrollWidth).toBeLessThanOrEqual(
      savedActionGeometry.batches.clientWidth,
    );
    expect(
      boxesOverlap(savedActionGeometry.buttons[0]!, savedActionGeometry.buttons[1]!),
    ).toBe(false);
    for (const action of savedActionGeometry.buttons) {
      expect(action.scrollWidth).toBeLessThanOrEqual(action.clientWidth);
    }
    originalContentGate.resolve();
    await originalContentSettled.promise;
    await savedRow
      .getByRole("button", { name: "删除本地素材并忽略 late-success.png" })
      .click();
    await deleteStarted.promise;

    const deletingRow = page.getByRole("listitem", {
      name: "删除本地素材项 late-success.png",
    });
    await expect(deletingRow).toContainText("正在删除本地素材，请勿关闭工作台");
    await expect(reservation).toHaveCount(0);
    const returnToProjects = page.getByRole("button", { name: "返回项目列表" });
    await expect(returnToProjects).toBeDisabled();
    await expect(returnToProjects).toHaveAttribute("aria-busy", "true");
    expect(
      await page.evaluate(() => {
        const event = new Event("beforeunload", { cancelable: true });
        return {
          dispatched: window.dispatchEvent(event),
          prevented: event.defaultPrevented,
        };
      }),
    ).toEqual({ dispatched: false, prevented: true });
    await expect(
      deletingRow.getByRole("button", { name: /重新选择|放入画布/ }),
    ).toHaveCount(0);
    await expect(
      deletingRow.locator("xpath=ancestor::article[1]").getByRole("button", { name: /关闭批次/ }),
    ).toHaveCount(0);
    expect(deleteBody).toEqual({
      client_import_id: expect.stringMatching(/^[0-9a-f-]{36}$/i),
      expected_revision_id: expect.stringMatching(/^[0-9a-f-]{36}$/i),
    });

    const boundary = page.getByTestId("canvas-media-ingress-boundary");
    await dispatchDrop(
      boundary,
      [browserFile("during-discard.png", "image/png", replacementPng)],
      originalReservationCenter,
    );
    await duringDiscardContentFetched.promise;
    const duringDiscardReservation = page
      .locator(".media-ingress-reservation")
      .filter({ hasText: "during-discard.png" });
    await expect(duringDiscardReservation).toBeVisible();
    const duringDiscardBox = await duringDiscardReservation.boundingBox();
    if (!duringDiscardBox) throw new Error("during-discard reservation geometry is missing");
    const duringDiscardPosition = await canvasPosition(duringDiscardReservation);
    expect(duringDiscardPosition).not.toEqual(
      originalReservationPosition,
    );

    deleteGate.resolve();
    await deleteSettled.promise;
    await expect(deletingRow).toHaveCount(0);
    await dispatchDrop(
      boundary,
      [browserFile("after-confirmation.png", "image/png", sourcePng)],
      originalReservationCenter,
    );
    await afterConfirmationContentFetched.promise;
    const afterConfirmationReservation = page
      .locator(".media-ingress-reservation")
      .filter({ hasText: "after-confirmation.png" });
    await expect(afterConfirmationReservation).toBeVisible();
    const afterConfirmationBox = await afterConfirmationReservation.boundingBox();
    if (!afterConfirmationBox) {
      throw new Error("after-confirmation reservation geometry is missing");
    }
    expect(await canvasPosition(afterConfirmationReservation)).not.toEqual(
      duringDiscardPosition,
    );

    duringDiscardContentGate.resolve();
    afterConfirmationContentGate.resolve();
    await duringDiscardContentSettled.promise;
    await afterConfirmationContentSettled.promise;
    await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(2);
  } finally {
    originalContentGate.resolve();
    duringDiscardContentGate.resolve();
    afterConfirmationContentGate.resolve();
    deleteGate.resolve();
    await Promise.all([
      originalContentStarted ? originalContentSettled.promise : Promise.resolve(),
      duringDiscardContentStarted ? duringDiscardContentSettled.promise : Promise.resolve(),
      afterConfirmationContentStarted
        ? afterConfirmationContentSettled.promise
        : Promise.resolve(),
      deleteStartedRouting ? deleteSettled.promise : Promise.resolve(),
    ]);
  }
});
