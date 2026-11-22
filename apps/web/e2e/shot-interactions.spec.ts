import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Locator, Page } from "@playwright/test";
import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));

type BrowserFile = Readonly<{
  base64: string;
  mimeType: string;
  name: string;
}>;

type Rectangle = Readonly<{
  height: number;
  width: number;
  x: number;
  y: number;
}>;

function fixtureBuffer(relativePath: string): Buffer {
  const encoded = fs.readFileSync(path.resolve(here, relativePath), "ascii").trim();
  return Buffer.from(encoded, "base64");
}

const sourcePng = fixtureBuffer("../../api/tests/fixtures/tiny_png.b64");
const sourceWebm = fixtureBuffer("../../api/tests/fixtures/tiny_webm.b64");
const replacementPng = fixtureBuffer("./fixtures/replacement_png.b64");

function browserFile(name: string, mimeType: string, buffer: Buffer): BrowserFile {
  return { name, mimeType, base64: buffer.toString("base64") };
}

function boxesOverlap(first: Rectangle, second: Rectangle): boolean {
  return !(
    first.x + first.width <= second.x ||
    second.x + second.width <= first.x ||
    first.y + first.height <= second.y ||
    second.y + second.height <= first.y
  );
}

async function rectangle(locator: Locator, label: string): Promise<Rectangle> {
  await expect(locator, `${label} should remain visible`).toBeVisible();
  const box = await locator.boundingBox();
  if (!box) throw new Error(`${label} has no viewport geometry`);
  return box;
}

async function dispatchDrop(
  target: Locator,
  files: readonly BrowserFile[],
): Promise<void> {
  await target.evaluate((element, entries) => {
    const transfer = new DataTransfer();
    for (const entry of entries) {
      const bytes = Uint8Array.from(atob(entry.base64), (character) =>
        character.charCodeAt(0),
      );
      transfer.items.add(new File([bytes], entry.name, { type: entry.mimeType }));
    }
    const bounds = element.getBoundingClientRect();
    for (const type of ["dragenter", "dragover", "drop"] as const) {
      element.dispatchEvent(
        new DragEvent(type, {
          bubbles: true,
          cancelable: true,
          clientX: bounds.left + Math.max(8, bounds.width / 2),
          clientY: bounds.top + Math.max(8, bounds.height / 2),
          dataTransfer: transfer,
        }),
      );
    }
  }, files);
}

type ReorderAxis = "horizontal" | "vertical";

async function beginInternalReorder(source: Locator): Promise<void> {
  await source.evaluate((element) => {
    const transfer = new DataTransfer();
    (window as typeof window & { __aiCanvasReorderTransfer?: DataTransfer })
      .__aiCanvasReorderTransfer = transfer;
    element.dispatchEvent(
      new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: transfer }),
    );
  });
}

async function dragOverInternalReorder(
  target: Locator,
  axis: ReorderAxis,
  fraction: number,
): Promise<void> {
  await target.evaluate((element, input) => {
    const transfer = (window as typeof window & { __aiCanvasReorderTransfer?: DataTransfer })
      .__aiCanvasReorderTransfer;
    if (!transfer) throw new Error("Internal reorder transfer was not initialized");
    const bounds = element.getBoundingClientRect();
    const clientX = bounds.left + bounds.width * (input.axis === "horizontal" ? input.fraction : 0.5);
    const clientY = bounds.top + bounds.height * (input.axis === "vertical" ? input.fraction : 0.5);
    for (const type of ["dragenter", "dragover"] as const) {
      element.dispatchEvent(
        new DragEvent(type, { bubbles: true, cancelable: true, clientX, clientY, dataTransfer: transfer }),
      );
    }
  }, { axis, fraction });
}

async function dropInternalReorder(
  target: Locator,
  axis: ReorderAxis,
  fraction: number,
): Promise<void> {
  await target.evaluate((element, input) => {
    const transfer = (window as typeof window & { __aiCanvasReorderTransfer?: DataTransfer })
      .__aiCanvasReorderTransfer;
    if (!transfer) throw new Error("Internal reorder transfer was not initialized");
    const bounds = element.getBoundingClientRect();
    const clientX = bounds.left + bounds.width * (input.axis === "horizontal" ? input.fraction : 0.5);
    const clientY = bounds.top + bounds.height * (input.axis === "vertical" ? input.fraction : 0.5);
    element.dispatchEvent(
      new DragEvent("drop", { bubbles: true, cancelable: true, clientX, clientY, dataTransfer: transfer }),
    );
  }, { axis, fraction });
}

async function directPanelPointerGesture(
  page: Page,
  handle: Locator,
  start: { x: number; y: number },
  end: { x: number; y: number },
): Promise<void> {
  await expect(handle).toBeVisible();
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();
}

async function associateFromCanvas(
  page: Page,
  inspector: Locator,
  controlName: string,
  mediaNodeId: string,
): Promise<void> {
  const stableIdentity = inspector.locator(
    ".shot-inspector__identity .shot-stable-id",
  );
  await expect(stableIdentity).toHaveText(/^镜头 ID S-[0-9A-Z]{10}$/);
  const identityBefore = (await stableIdentity.textContent())?.trim();
  if (!identityBefore) throw new Error("The selected Shot stable identity is missing");
  const slotName =
    controlName === "关联图片"
      ? "图片槽位"
      : controlName === "关联视频"
        ? "视频槽位"
        : "参考槽位";
  const slot = inspector.getByRole("region", { name: slotName, exact: true });
  const referenceCountBefore =
    slotName === "参考槽位" ? await slot.locator("ol > li").count() : 0;

  await inspector.getByRole("button", { name: controlName, exact: true }).click();
  const flowNode = page.locator(`.react-flow__node[data-id="${mediaNodeId}"]`);
  await expect(flowNode).toBeVisible();
  // The floating inspector can legitimately cover a canvas node at narrow widths.
  // Dispatch the node's semantic selection event without treating panel geometry as
  // an association failure.
  await flowNode.dispatchEvent("click");

  await expect(inspector).toBeVisible();
  await expect(stableIdentity).toHaveText(identityBefore);
  await expect(
    inspector.getByRole("button", { name: "取消关联", exact: true }),
  ).toHaveCount(0);
  if (slotName === "图片槽位") {
    await expect(slot.locator("img")).toBeVisible();
  } else if (slotName === "视频槽位") {
    await expect(slot.locator("video")).toBeVisible();
  } else {
    await expect(slot.locator("ol > li")).toHaveCount(
      referenceCountBefore + 1,
    );
  }
}

async function assertWorkspaceGeometry(page: Page, width: number): Promise<void> {
  await page.setViewportSize({ width, height: 1_200 });
  const layoutWidths = await page.evaluate(() => {
    const measured = (selector: string) =>
      document.querySelector<HTMLElement>(selector)?.getBoundingClientRect().width ?? null;
    return {
      canvasPage: measured(".canvas-page"),
      controlDock: measured(".f1-control-dock"),
      document: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
      workspace: measured(".canvas-workspace"),
    };
  });
  expect(layoutWidths, `${width}px layout must not overflow the viewport`).toMatchObject({
    canvasPage: width,
    controlDock: width,
    document: width,
    viewport: width,
    workspace: width,
  });
  const batch = page.locator(".media-ingress-batches");
  const miniMap = page.locator(".react-flow__minimap");
  const track = page.getByTestId("shot-track");
  const inspector = page.getByTestId("shot-inspector");
  const history = page.getByTestId("artifact-history-drawer");
  const activePanel = await history.isVisible() ? history : inspector;

  // The history drawer deliberately replaces the inspector in the same lane.
  if (activePanel === history) {
    await expect(inspector).toHaveCount(0);
  }

  await expect.poll(async () => {
    const entries = await Promise.all([
      ["B2 batch card", batch.boundingBox()] as const,
      ["MiniMap", miniMap.boundingBox()] as const,
      ["Shot track", track.boundingBox()] as const,
      ["active shot panel", activePanel.boundingBox()] as const,
    ]).then(async (pending) => Promise.all(
      pending.map(async ([label, boxPromise]) => [label, await boxPromise] as const),
    ));
    if (entries.some(([, box]) => box === null)) return ["missing overlay geometry"];
    const measured = entries as readonly (readonly [string, Rectangle])[];
    return measured.flatMap((first, firstIndex) =>
      measured.slice(firstIndex + 1).flatMap((second) =>
        boxesOverlap(first[1], second[1])
          ? [`${first[0]} ${JSON.stringify(first[1])} overlaps ${second[0]} ${JSON.stringify(second[1])}`]
          : [],
      ),
    );
  }, { message: `${width}px workspace overlays should settle without collisions` }).toEqual([]);

  const surfaces = [
    ["B2 batch card", await rectangle(batch, "B2 batch card")],
    ["MiniMap", await rectangle(miniMap, "MiniMap")],
    ["Shot track", await rectangle(track, "Shot track")],
    [
      activePanel === history ? "Artifact history drawer" : "Shot inspector",
      await rectangle(activePanel, "active shot panel"),
    ],
  ] as const;
  for (let firstIndex = 0; firstIndex < surfaces.length; firstIndex += 1) {
    const first = surfaces[firstIndex]!;
    for (let secondIndex = firstIndex + 1; secondIndex < surfaces.length; secondIndex += 1) {
      const second = surfaces[secondIndex]!;
      expect(
        boxesOverlap(first[1], second[1]),
        `${width}px: ${first[0]} ${JSON.stringify(first[1])} must not overlap ` +
          `${second[0]} ${JSON.stringify(second[1])}`,
      ).toBe(false);
    }
  }
}

async function approveGraphChange(page: Page): Promise<void> {
  await expect(page.locator(".save-status")).toHaveText("有未保存修改", {
    timeout: 15_000,
  });
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存", { timeout: 15_000 });
}

test.describe.configure({ timeout: 90_000 });

test("uses B2 media in minimal Shots, persists history and reorder, preserves media on delete, and surfaces a CAS conflict", async ({
  context,
  page,
}) => {
  await page.goto("/");
  const projectName = `shot-noauth-${Date.now()}`;
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("project"))
    .not.toBeNull();

  const media = [
    browserFile("shot-image.png", "image/png", sourcePng),
    browserFile("shot-motion.webm", "video/webm", sourceWebm),
    browserFile("shot-reference.png", "image/png", replacementPng),
  ];
  await page.getByLabel("选择多个本地媒体").setInputFiles(
    media.map((file) => ({
      name: file.name,
      mimeType: file.mimeType,
      buffer: Buffer.from(file.base64, "base64"),
    })),
  );
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(3, {
    timeout: 15_000,
  });
  await expect(page.locator(".media-ingress-batches")).toBeVisible();
  await approveGraphChange(page);

  const imageNode = page.locator('article[data-node-kind="media_input"]:has(img)').first();
  const referenceNode = page.locator('article[data-node-kind="media_input"]:has(img)').nth(1);
  const videoNode = page.locator('article[data-node-kind="media_input"]:has(video)');
  const imageNodeId = await imageNode.getAttribute("data-node-id");
  const referenceNodeId = await referenceNode.getAttribute("data-node-id");
  const videoNodeId = await videoNode.getAttribute("data-node-id");
  if (!imageNodeId || !referenceNodeId || !videoNodeId) {
    throw new Error("B2 media node identities are missing");
  }

  const track = page.getByTestId("shot-track");
  const inspector = page.getByTestId("shot-inspector");
  await expect(inspector).toHaveCount(0);
  await expect(track).toHaveAttribute("data-track-state", "collapsed");
  const collapsedTrack = await rectangle(track, "collapsed Shot track");
  const workspaceBox = await rectangle(page.locator(".canvas-workspace"), "canvas workspace");
  expect(collapsedTrack.height).toBeCloseTo(12, 0);
  expect(collapsedTrack.y + collapsedTrack.height).toBeCloseTo(
    workspaceBox.y + workspaceBox.height,
    0,
  );
  await expect(track.getByRole("heading", { name: "分镜轨道" })).toHaveCount(0);
  await expect(track.getByRole("button", { name: "新建分镜", exact: true })).toHaveCount(0);
  await expect(track.getByRole("button", { name: "重置分镜轨道布局" })).toHaveCount(0);
  await directPanelPointerGesture(
    page,
    track.locator('[data-floating-panel-resize-edge="top"]'),
    { x: collapsedTrack.x + collapsedTrack.width / 2, y: collapsedTrack.y + 4 },
    { x: collapsedTrack.x + collapsedTrack.width / 2, y: collapsedTrack.y - 164 },
  );
  await expect(track).toHaveAttribute("data-track-state", "expanded");
  await expect(track.getByRole("heading", { name: "分镜轨道" })).toBeVisible();
  const headerActions = track.locator(".shot-track__header-actions");
  const createButton = headerActions.getByRole("button", { name: "新建分镜", exact: true });
  const resetButton = headerActions.getByRole("button", { name: "重置分镜轨道布局" });
  const createBox = await rectangle(createButton, "create Shot button");
  const resetBox = await rectangle(resetButton, "reset Shot track button");
  expect(resetBox.x - (createBox.x + createBox.width)).toBeGreaterThanOrEqual(0);
  expect(resetBox.x - (createBox.x + createBox.width)).toBeLessThanOrEqual(8);
  for (let index = 0; index < 3; index += 1) {
    await createButton.click();
  }
  await expect(track.locator("li")).toHaveCount(3);
  await expect(inspector).toBeVisible();
  const shotCards = track.locator(":scope > ol > li");
  await expect(shotCards).toHaveCount(3);
  const shotCardWidths = await shotCards.evaluateAll((cards) =>
    cards.map((card) => card.getBoundingClientRect().width),
  );
  for (const width of shotCardWidths) {
    expect(width).toBeGreaterThan(254);
    expect(width).toBeLessThan(256);
  }
  await expect.poll(() => shotCards.evaluateAll((cards) =>
    cards.flatMap((card, cardIndex) => {
      const frame = card.getBoundingClientRect();
      const tolerance = 0.5;
      const measured = [
        ...Array.from(card.children),
        ...Array.from(card.querySelectorAll(
          ".shot-thumbnail, .shot-track__labels, .shot-track__actions > button",
        )),
      ];
      return measured.flatMap((child, childIndex) => {
        const bounds = child.getBoundingClientRect();
        const contained =
          bounds.left >= frame.left - tolerance &&
          bounds.top >= frame.top - tolerance &&
          bounds.right <= frame.right + tolerance &&
          bounds.bottom <= frame.bottom + tolerance;
        return contained
          ? []
          : [{ cardIndex, childIndex, frameHeight: frame.height, childHeight: bounds.height }];
      });
    }),
  )).toEqual([]);
  await expect.poll(() => shotCards.evaluateAll((cards) =>
    cards.flatMap((card, cardIndex) =>
      Array.from(card.querySelectorAll(".shot-track__actions > button")).flatMap(
        (button, buttonIndex) => getComputedStyle(button).textAlign === "center"
          ? []
          : [{ cardIndex, buttonIndex, textAlign: getComputedStyle(button).textAlign }],
      ),
    ),
  )).toEqual([]);
  await expect.poll(() => shotCards.evaluateAll((cards) =>
    cards.flatMap((card, cardIndex) => {
      const select = card.querySelector(".shot-track__select")?.getBoundingClientRect();
      const actions = card.querySelector(".shot-track__actions")?.getBoundingClientRect();
      return select && actions && actions.top >= select.bottom - 0.5
        ? []
        : [{ cardIndex, selectBottom: select?.bottom, actionsTop: actions?.top }];
    }),
  )).toEqual([]);

  await track.getByRole("button", { name: "分镜 01", exact: true }).click();
  const flowViewport = page.locator(".react-flow__viewport");
  const viewportBeforePanelDrag = await flowViewport.getAttribute("style");
  const inspectorBefore = await rectangle(inspector, "Shot inspector before title drag");
  await directPanelPointerGesture(
    page,
    inspector.locator("[data-floating-panel-drag-region]"),
    { x: inspectorBefore.x + 80, y: inspectorBefore.y + 20 },
    { x: inspectorBefore.x + 40, y: inspectorBefore.y + 40 },
  );
  const inspectorAfter = await rectangle(inspector, "Shot inspector after title drag");
  expect(inspectorAfter.x).toBeLessThan(inspectorBefore.x);
  expect(inspectorAfter.y).toBeGreaterThan(inspectorBefore.y);
  await expect(flowViewport).toHaveAttribute("style", viewportBeforePanelDrag ?? "");
  const inspectorBottomRight = inspector.locator(
    '[data-floating-panel-resize-edge="bottom-right"]',
  );
  await expect(inspectorBottomRight).toBeVisible();
  await directPanelPointerGesture(
    page,
    inspectorBottomRight,
    { x: inspectorAfter.x + inspectorAfter.width - 4, y: inspectorAfter.y + inspectorAfter.height - 4 },
    { x: inspectorAfter.x + inspectorAfter.width + 16, y: inspectorAfter.y + inspectorAfter.height + 12 },
  );
  const inspectorResized = await rectangle(inspector, "Shot inspector after bottom-right resize");
  expect(inspectorResized.width).toBeGreaterThan(inspectorAfter.width);
  expect(inspectorResized.height).toBeGreaterThan(inspectorAfter.height);
  await inspector.locator(".floating-panel__reset").click();

  const imageSlot = inspector.getByRole("region", { name: "图片槽位", exact: true });
  const videoSlot = inspector.getByRole("region", { name: "视频槽位", exact: true });
  const referenceSlot = inspector.getByRole("region", { name: "参考槽位", exact: true });
  await associateFromCanvas(page, inspector, "关联图片", imageNodeId);
  await associateFromCanvas(page, inspector, "关联视频", videoNodeId);
  await associateFromCanvas(page, inspector, "关联参考", imageNodeId);
  await associateFromCanvas(page, inspector, "关联参考", referenceNodeId);
  await expect(imageSlot.locator("img")).toBeVisible();
  await expect(videoSlot.locator("video")).toBeVisible();
  await expect(referenceSlot.locator("ol > li")).toHaveCount(2);
  await expect(
    track.locator('li[aria-current="true"] .shot-thumbnail img'),
  ).toBeVisible();
  await expect(
    page.locator('article[data-node-kind="shot"] .shot-thumbnail img'),
  ).toHaveCount(1);
  const shotNodePreview = page.locator(
    'article[data-node-kind="shot"] .shot-thumbnail:not(.shot-thumbnail--compact) img',
  );
  await expect.poll(() => shotNodePreview.evaluate((image) => {
    const element = image as HTMLImageElement;
    const bounds = element.getBoundingClientRect();
    return {
      naturalRatio: element.naturalWidth / element.naturalHeight,
      renderedRatio: bounds.width / bounds.height,
    };
  })).toEqual(expect.objectContaining({
    naturalRatio: expect.any(Number),
    renderedRatio: expect.any(Number),
  }));
  const previewRatios = await shotNodePreview.evaluate((image) => {
    const element = image as HTMLImageElement;
    const bounds = element.getBoundingClientRect();
    return {
      naturalRatio: element.naturalWidth / element.naturalHeight,
      renderedRatio: bounds.width / bounds.height,
    };
  });
  expect(previewRatios.naturalRatio).not.toBeCloseTo(16 / 9, 2);
  expect(previewRatios.renderedRatio).toBeCloseTo(previewRatios.naturalRatio, 2);
  const previewWidthRatio = await shotNodePreview.evaluate((image) => {
    const preview = image.closest(".shot-thumbnail")?.getBoundingClientRect();
    const shotCard = image.closest('article[data-node-kind="shot"]')?.getBoundingClientRect();
    if (!preview || !shotCard) throw new Error("Shot preview geometry is unavailable");
    return preview.width / shotCard.width;
  });
  expect(previewWidthRatio).toBeGreaterThan(0.45);
  expect(previewWidthRatio).toBeLessThan(0.55);
  await approveGraphChange(page);

  const referenceItems = referenceSlot.locator("ol > li");
  const firstReference = referenceItems.nth(0);
  const secondReference = referenceItems.nth(1);
  const firstReferenceFilename = await firstReference.locator("img").getAttribute("alt");
  const secondReferenceFilename = await secondReference.locator("img").getAttribute("alt");
  if (!firstReferenceFilename || !secondReferenceFilename) {
    throw new Error("Reference filenames are required for reorder assertions");
  }
  await beginInternalReorder(secondReference);
  await dragOverInternalReorder(firstReference, "vertical", 0.25);
  await expect(firstReference).toHaveClass(/is-drop-before/);
  await expect(referenceSlot.getByRole("status")).toHaveText(
    `将放到${firstReferenceFilename}之前`,
  );
  await dropInternalReorder(firstReference, "vertical", 0.25);
  await expect(referenceSlot.getByRole("status")).toHaveText(
    `参考文件 ${secondReferenceFilename} 现在位于第 1 位`,
  );
  await expect.poll(() => referenceItems.locator("img").evaluateAll(
    (images) => images.map((image) => image.getAttribute("alt")),
  )).toEqual([secondReferenceFilename, firstReferenceFilename]);
  await approveGraphChange(page);

  await dispatchDrop(imageNode, [
    browserFile("shot-replacement.png", "image/png", replacementPng),
  ]);
  const targetChoice = page.getByRole("dialog", { name: "选择导入方式" });
  await expect(targetChoice).toBeVisible();
  await targetChoice.getByRole("button", { name: "替换当前", exact: true }).click();
  await expect(imageNode.getByText("shot-replacement.png", { exact: true })).toBeVisible();
  await approveGraphChange(page);

  await imageSlot.getByRole("button", { name: "查看历史", exact: true }).click();
  const history = page.getByTestId("artifact-history-drawer");
  await expect(history).toBeVisible();
  await history.getByRole("button", { name: "shot-replacement.png", exact: true }).click();
  await history.getByRole("button", { name: "应用所选素材", exact: true }).click();
  await expect(history).toHaveCount(0);
  await expect(imageSlot.getByText("shot-replacement.png", { exact: true })).toBeVisible();

  await imageSlot.getByRole("button", { name: "查看历史", exact: true }).click();
  await expect(history).toBeVisible();
  await track.locator(".floating-panel__reset").click();
  await expect(track).toHaveAttribute("data-track-state", "collapsed");
  await expect(track.getByRole("heading", { name: "分镜轨道" })).toHaveCount(0);
  await expect(track.getByRole("button", { name: "新建分镜", exact: true })).toHaveCount(0);
  await expect(track.getByRole("button", { name: "重置分镜轨道布局" })).toHaveCount(0);
  for (const width of [720, 721, 820, 821, 1_200, 1_201]) {
    await assertWorkspaceGeometry(page, width);
  }
  await history.getByRole("button", { name: "关闭素材版本历史", exact: true }).click();

  const collapsedBeforeReorder = await rectangle(track, "collapsed Shot track before reorder");
  await directPanelPointerGesture(
    page,
    track.locator('[data-floating-panel-resize-edge="top"]'),
    {
      x: collapsedBeforeReorder.x + collapsedBeforeReorder.width / 2,
      y: collapsedBeforeReorder.y + 4,
    },
    {
      x: collapsedBeforeReorder.x + collapsedBeforeReorder.width / 2,
      y: collapsedBeforeReorder.y - 164,
    },
  );
  await expect(track).toHaveAttribute("data-track-state", "expanded");

  const shotThree = track.locator("li").filter({ hasText: "分镜 03" });
  const shotOne = track.locator("li").filter({ hasText: "分镜 01" });
  const selectedStableId = (await shotThree.locator(".shot-stable-id").textContent())?.trim();
  if (!selectedStableId) throw new Error("The reordered Shot stable identity is missing");
  await beginInternalReorder(shotThree);
  await dragOverInternalReorder(shotOne, "horizontal", 0.75);
  await expect(shotOne).toHaveClass(/is-drop-after/);
  await expect(
    track.getByRole("status"),
  ).toHaveText("将放到分镜 01之后");
  await dropInternalReorder(shotOne, "horizontal", 0.75);
  await expect(track.getByRole("status")).toHaveText(
    `镜头 ID ${selectedStableId.replace("镜头 ID ", "")} 现在位于第 2 位`,
  );
  await expect(track.locator("li").nth(1).locator(".shot-stable-id")).toHaveText(selectedStableId);
  await approveGraphChange(page);
  const stableShotOrder = await track.locator("li .shot-stable-id").allTextContents();
  const referenceFilenameOrder = await referenceItems.locator("img").evaluateAll(
    (images) => images.map((image) => image.getAttribute("alt")),
  );

  await page.reload();
  await expect(track.locator("li")).toHaveCount(3);
  await expect(track.locator("li .shot-stable-id")).toHaveText(stableShotOrder);
  await track.getByRole("button", { name: "分镜 01", exact: true }).click();
  await expect(imageSlot.getByText("shot-replacement.png", { exact: true })).toBeVisible();
  await expect(videoSlot.locator("video")).toBeVisible();
  await expect(referenceSlot.locator("ol > li")).toHaveCount(2);
  await expect.poll(() => referenceSlot.locator("ol > li img").evaluateAll(
    (images) => images.map((image) => image.getAttribute("alt")),
  )).toEqual(referenceFilenameOrder);

  await track.getByRole("button", { name: "删除分镜 1", exact: true }).click();
  const deleteDialog = page.getByTestId("shot-delete-dialog");
  await expect(deleteDialog).toBeVisible();
  await deleteDialog.getByRole("button", { name: "确认删除分镜", exact: true }).click();
  await expect(track.locator("li")).toHaveCount(2);
  await expect(page.locator('article[data-node-kind="shot"]')).toHaveCount(2);
  await expect(page.locator('article[data-node-kind="media_input"]')).toHaveCount(3);
  for (const mediaNodeId of [imageNodeId, referenceNodeId, videoNodeId]) {
    await expect(page.locator(`.react-flow__node[data-id="${mediaNodeId}"]`)).toBeVisible();
  }
  await approveGraphChange(page);

  const secondPage = await context.newPage();
  try {
    await secondPage.goto(page.url());
    const secondTrack = secondPage.getByTestId("shot-track");
    await expect(secondTrack.locator("li")).toHaveCount(2);

    await track.getByRole("button", { name: "新建分镜", exact: true }).click();
    await approveGraphChange(page);
    await secondTrack.getByRole("button", { name: "下移分镜 1", exact: true }).click();
    await secondPage.getByRole("button", { name: "审阅并应用画布变更" }).click();
    await expect(secondPage.getByRole("alert")).toContainText("另一页面已保存了新版本");
  } finally {
    await secondPage.close();
  }
});
