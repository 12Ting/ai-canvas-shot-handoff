import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  MediaIngressContext,
  type MediaIngressRuntimePort,
} from "../mediaIngressContext";
import type {
  IngressBatchSnapshot,
  IngressItemState,
  IngressSlotSnapshot,
  MediaIngressController,
  MediaIngressSnapshot,
} from "../mediaIngressTypes";
import { MediaTargetLocks } from "../mediaTargetLocks";
import { BatchResultCard } from "./BatchResultCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function slot(
  state: IngressItemState,
  displayName = `${state.toLowerCase()}.png`,
  errorMessage: string | null = null,
  itemOverrides: Partial<IngressSlotSnapshot["currentItem"]> = {},
): IngressSlotSnapshot {
  return {
    slotId: `slot-${state.toLowerCase()}`,
    reservation: {
      nodeId: `node-${state.toLowerCase()}`,
      position: { x: 40, y: 80 },
      width: 312,
      height: 360,
    },
    currentItem: {
      itemId: `item-${state.toLowerCase()}`,
      clientImportId: `client-${state.toLowerCase()}`,
      state,
      displayName,
      mediaKind: "image",
      errorMessage,
      discardErrorMessage: null,
      canCancel: false,
      canRetryCancellation: false,
      canRetryDiscard: false,
      canForceRefresh: false,
      resumeScopeMessage: null,
      ...itemOverrides,
    },
    previousAttempts: [],
    attemptCount: 1,
    targetNodeId: null,
    canReselect: state === "FAILED",
    canIgnore: state === "FAILED",
    canPlaceSavedUnplaced: false,
  };
}

it("offers honest cancel, stop, unknown-recovery, and saved-revision actions without allowing an active batch to close", () => {
  const cancelItem = vi.fn();
  const stopBatch = vi.fn();
  const retryCancellation = vi.fn();
  const requestForceRefresh = vi.fn();
  const placeSavedUnplaced = vi.fn();
  const ignoreSlot = vi.fn();
  const running = slot("RECEIVING", "running.png", null, {
    canCancel: true,
    resumeScopeMessage:
      "大文件仅可在当前页面会话内断点续传；刷新或关闭页面后请重新上传。",
    largeUploadProgress: {
      receivedByteSize: 8 * 1024 * 1024,
      totalByteSize: 65 * 1024 * 1024,
      estimatedSecondsRemaining: 21,
    },
  });
  const cancelling = slot(
    "CANCELLING",
    "unknown.png",
    "暂时无法确认服务端导入结果，请重试确认或确认后强制刷新",
    {
      canRetryCancellation: true,
      canForceRefresh: true,
    },
  );
  const saved = {
    ...slot("SAVED_UNPLACED", "saved.png"),
    canIgnore: true,
    canPlaceSavedUnplaced: true,
  };
  const active: IngressBatchSnapshot = {
    ...batch("active-actions", 1, false, [running, cancelling, saved]),
    counts: {
      total: 3,
      queued: 0,
      processing: 2,
      placed: 0,
      failed: 0,
      cancelled: 0,
      savedUnplaced: 1,
      ignored: 0,
      terminal: 1,
    },
    canStop: true,
    canClose: false,
  };
  renderCenter(
    {
      batches: [active],
      pendingTargetChoices: [],
      protectedTargetNodeIds: [],
      guardState: { pendingOperationCount: 2, hasSavedUnplaced: true },
    },
    {
      cancelItem,
      stopBatch,
      retryCancellation,
      requestForceRefresh,
      placeSavedUnplaced,
      ignoreSlot,
    },
  );
  expect(
    screen.getByText("大文件仅可在当前页面会话内断点续传；刷新或关闭页面后请重新上传。"),
  ).not.toBeNull();
  expect(screen.getByText(/已传 8,388,608 \/ 68,157,440 字节/)).toBeTruthy();
  expect(screen.getByText(/预计剩余 21 秒/)).toBeTruthy();

  expect(screen.queryByRole("button", { name: "关闭批次 active-actions" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "停止剩余任务 active-actions" }));
  expect(stopBatch).toHaveBeenCalledWith("active-actions");
  fireEvent.click(screen.getByRole("button", { name: "取消 running.png" }));
  expect(cancelItem).toHaveBeenCalledWith(
    "active-actions",
    "item-receiving",
  );

  const unknown = screen.getByRole("listitem", {
    name: "取消确认项 unknown.png",
  });
  expect(within(unknown).getByText("正在确认取消结果")).toBeTruthy();
  expect(within(unknown).getByText(/暂时无法确认服务端导入结果/)).toBeTruthy();
  fireEvent.click(within(unknown).getByRole("button", { name: "重试确认 unknown.png" }));
  expect(retryCancellation).toHaveBeenCalledWith(
    "active-actions",
    "item-cancelling",
  );
  fireEvent.click(
    within(unknown).getByRole("button", {
      name: "强制刷新并放弃内存批次",
    }),
  );
  expect(requestForceRefresh).toHaveBeenCalledOnce();
  expect(within(unknown).queryByRole("button", { name: /删除|取消成功/ })).toBeNull();

  const savedRow = screen.getByRole("listitem", {
    name: "待放置项 saved.png",
  });
  expect(
    within(savedRow).getByText("素材已保存，但尚未放入画布"),
  ).toBeTruthy();
  fireEvent.click(within(savedRow).getByRole("button", { name: "放入画布 saved.png" }));
  expect(placeSavedUnplaced).toHaveBeenCalledWith(
    "active-actions",
    "slot-saved_unplaced",
  );
  fireEvent.click(
    within(savedRow).getByRole("button", {
      name: "删除本地素材并忽略 saved.png",
    }),
  );
  expect(ignoreSlot).toHaveBeenCalledWith(
    "active-actions",
    "slot-saved_unplaced",
  );
});

it("marks long saved-media recovery actions for the responsive action layout", () => {
  const displayName = "非常长的分镜素材名称，用于确认恢复操作不会互相挤压.webm";
  const saved = {
    ...slot("SAVED_UNPLACED", displayName),
    canIgnore: true,
    canPlaceSavedUnplaced: true,
  };
  const value: MediaIngressSnapshot = {
    batches: [batch("saved-actions-layout", 1, true, [saved])],
    pendingTargetChoices: [],
    protectedTargetNodeIds: [],
    guardState: { pendingOperationCount: 1, hasSavedUnplaced: true },
  };

  renderCenter(value);

  const row = screen.getByRole("listitem", {
    name: `待放置项 ${displayName}`,
  });
  const actions = row.querySelector(".media-ingress-saved-unplaced-actions");
  expect(actions).not.toBeNull();
  const place = within(actions as HTMLElement).getByRole("button", {
    name: `放入画布 ${displayName}`,
  });
  const discard = within(actions as HTMLElement).getByRole("button", {
    name: `删除本地素材并忽略 ${displayName}`,
  });
  expect(place.classList.contains("media-ingress-saved-unplaced-action")).toBe(true);
  expect(discard.classList.contains("media-ingress-saved-unplaced-action")).toBe(true);
  expect(place.textContent).toBe("放入画布");
  expect(discard.textContent).toBe("删除本地素材");
});

it("labels a cancellable queued item as queued rather than processing", () => {
  const queued = slot("QUEUED", "queued.png", null, {
    canCancel: true,
  });
  const active: IngressBatchSnapshot = {
    ...batch("queued-label", 1, false, [queued]),
    counts: {
      total: 1,
      queued: 1,
      processing: 0,
      placed: 0,
      failed: 0,
      cancelled: 0,
      savedUnplaced: 0,
      ignored: 0,
      terminal: 0,
    },
    canStop: true,
    canClose: false,
  };
  renderCenter({
    batches: [active],
    pendingTargetChoices: [],
    protectedTargetNodeIds: [],
    guardState: { pendingOperationCount: 1, hasSavedUnplaced: false },
  });

  const row = screen.getByText("queued.png").closest("li");
  expect(row).not.toBeNull();
  expect(within(row as HTMLElement).getByText("排队中")).toBeTruthy();
  expect(within(row as HTMLElement).queryByText("处理中")).toBeNull();
});

it("labels an ignored completed large upload as pending cleanup instead of immediate deletion", () => {
  const ignoreSlot = vi.fn();
  const saved = {
    ...slot("SAVED_UNPLACED", "large.mov", null, {
      resumeScopeMessage:
        "大文件仅可在当前页面会话内断点续传；刷新或关闭页面后请重新上传。",
    }),
    canIgnore: true,
    canPlaceSavedUnplaced: true,
  };
  const active: IngressBatchSnapshot = {
    ...batch("large-pending-cleanup", 1, false, [saved]),
    counts: {
      total: 1,
      queued: 0,
      processing: 0,
      placed: 0,
      failed: 0,
      cancelled: 0,
      savedUnplaced: 1,
      ignored: 0,
      terminal: 1,
    },
    canStop: false,
    canClose: true,
  };
  renderCenter(
    {
      batches: [active],
      pendingTargetChoices: [],
      protectedTargetNodeIds: [],
      guardState: { pendingOperationCount: 0, hasSavedUnplaced: true },
    },
    { ignoreSlot },
  );

  const cleanup = screen.getByRole("button", {
    name: "标记待清理并忽略 large.mov",
  });
  expect(cleanup.textContent).toBe("标记待清理");
  expect(cleanup.getAttribute("title")).toBe("标记待清理并忽略 large.mov");
  fireEvent.click(cleanup);
  expect(ignoreSlot).toHaveBeenCalledWith(
    "large-pending-cleanup",
    "slot-saved_unplaced",
  );
});

it("hides batch stop when cancellation confirmation is the only remaining work", () => {
  const cancelling = slot(
    "CANCELLING",
    "unknown-only.png",
    "暂时无法确认服务端导入结果",
    {
      canRetryCancellation: true,
      canForceRefresh: true,
    },
  );
  const active: IngressBatchSnapshot = {
    ...batch("unknown-only", 1, false, [cancelling]),
    counts: {
      total: 1,
      queued: 0,
      processing: 1,
      placed: 0,
      failed: 0,
      cancelled: 0,
      savedUnplaced: 0,
      ignored: 0,
      terminal: 0,
    },
    canStop: false,
    canClose: false,
  };
  renderCenter({
    batches: [active],
    pendingTargetChoices: [],
    protectedTargetNodeIds: [],
    guardState: { pendingOperationCount: 1, hasSavedUnplaced: false },
  });

  expect(
    screen.queryByRole("button", {
      name: "停止剩余任务 unknown-only",
    }),
  ).toBeNull();
});

function batch(
  id: string,
  order: number,
  terminal = false,
  slots: readonly IngressSlotSnapshot[] = [],
): IngressBatchSnapshot {
  return {
    batchId: id,
    source: "multi_picker",
    createdOrder: order,
    collapsed: false,
    counts: {
      total: 4,
      queued: terminal ? 0 : 1,
      processing: terminal ? 0 : 2,
      placed: terminal ? 2 : 1,
      failed: terminal ? 1 : 0,
      cancelled: terminal ? 0 : 0,
      savedUnplaced: terminal ? 0 : 0,
      ignored: terminal ? 1 : 0,
      terminal: terminal ? 4 : 1,
    },
    slots,
    canStop: !terminal,
    canClose: terminal,
  };
}

function renderCenter(
  value: MediaIngressSnapshot,
  methods: Partial<MediaIngressController> = {},
) {
  const controller = {
    getSnapshot: () => value,
    subscribe: () => () => undefined,
    ...methods,
  } as unknown as MediaIngressController;
  const runtime: MediaIngressRuntimePort = {
    projectId: "p",
    loadEpoch: 1,
    nodeImport: {} as never,
    controller,
    targetLocks: new MediaTargetLocks(),
  };
  render(
    <MediaIngressContext.Provider value={runtime}>
      <BatchResultCard />
    </MediaIngressContext.Provider>,
  );
  return controller;
}

it("shows one newest-first batch center with aggregate announcements, state counts, and honest close rules", () => {
  const toggleBatchCollapsed = vi.fn();
  const closeBatch = vi.fn();
  const value: MediaIngressSnapshot = {
    batches: [batch("old", 1, true), batch("new", 2, false)],
    pendingTargetChoices: [],
    protectedTargetNodeIds: [],
    guardState: { pendingOperationCount: 3, hasSavedUnplaced: false },
  };
  renderCenter(value, { toggleBatchCollapsed, closeBatch });

  const center = screen.getByRole("region", {
    name: "媒体导入批次中心",
  });
  expect(center.parentElement).toBe(document.body);
  const groups = within(center).getAllByRole("group");
  expect(groups[0].textContent).toContain("new");
  expect(groups[1].textContent).toContain("old");
  expect(groups[0].textContent).toContain("多文件选择");
  expect(groups[0].textContent).toContain("完成 1 / 4");
  expect(groups[0].textContent).toContain("排队 1");
  expect(groups[0].textContent).toContain("处理中 2");
  expect(groups[0].textContent).toContain("已取消 0");
  expect(groups[0].textContent).toContain("待放置 0");
  expect(groups[1].textContent).toContain("已忽略 1");
  expect(center.textContent).not.toMatch(/\d+%/);

  const liveRegions = within(center).getAllByRole("status");
  expect(liveRegions).toHaveLength(1);
  expect(liveRegions[0].getAttribute("aria-live")).toBe("polite");
  expect(liveRegions[0].textContent).toContain("共 2 个批次");
  expect(liveRegions[0].textContent).toContain("完成 5 / 8");

  expect(
    within(groups[0]).queryByRole("button", { name: "关闭批次 new" }),
  ).toBeNull();
  fireEvent.click(
    within(groups[0]).getByRole("button", { name: "折叠批次 new" }),
  );
  expect(toggleBatchCollapsed).toHaveBeenCalledWith("new");
  fireEvent.click(
    within(groups[1]).getByRole("button", {
      name: "删除失败项本地素材并关闭批次 old",
    }),
  );
  expect(closeBatch).toHaveBeenCalledWith("old");
  expect(groups[1].textContent).toContain(
    "删除失败项本地素材并关闭批次",
  );
});

it("offers recoverable FAILED rows and clears the hidden picker before opening and after selection", () => {
  const reselect = vi.fn();
  const ignoreSlot = vi.fn();
  const createObjectURL = vi.spyOn(URL, "createObjectURL");
  const failed = slot("FAILED", "分镜 03.png", "服务暂时不可用");
  const value: MediaIngressSnapshot = {
    batches: [batch("recoverable", 1, true, [failed])],
    pendingTargetChoices: [],
    protectedTargetNodeIds: [],
    guardState: { pendingOperationCount: 0, hasSavedUnplaced: false },
  };
  renderCenter(value, { reselect, ignoreSlot });

  const row = screen.getByRole("listitem", { name: "失败项 分镜 03.png" });
  const filename = within(row).getByText("分镜 03.png");
  expect(filename.tagName).toBe("BDI");
  expect(filename.getAttribute("dir")).toBe("auto");
  expect(within(row).getByText("服务暂时不可用")).toBeTruthy();
  expect(
    within(row).getByText(
      "本批已成功 2 项（已放置 2 项，待放置 0 项）；其他任务均已结束；可重新选择文件，或删除本地素材并忽略此项。",
    ),
  ).toBeTruthy();

  const input = within(row).getByLabelText("为 分镜 03.png 重新选择文件");
  Object.defineProperty(input, "value", {
    configurable: true,
    value: "stale-selection",
    writable: true,
  });
  let valueWhenOpened = "not-opened";
  vi.spyOn(input as HTMLInputElement, "click").mockImplementation(() => {
    valueWhenOpened = (input as HTMLInputElement).value;
  });
  fireEvent.click(
    within(row).getByRole("button", { name: "重新选择 分镜 03.png" }),
  );
  expect(valueWhenOpened).toBe("");

  const file = new File(["replacement"], "replacement.png", {
    type: "image/png",
  });
  fireEvent.change(input, { target: { files: [file] } });
  expect((input as HTMLInputElement).value).toBe("");
  expect(reselect).toHaveBeenCalledWith(
    "recoverable",
    "slot-failed",
    file,
  );

  fireEvent.click(
    within(row).getByRole("button", {
      name: "删除本地素材并忽略 分镜 03.png",
    }),
  );
  expect(ignoreSlot).toHaveBeenCalledWith("recoverable", "slot-failed");
  expect(createObjectURL).not.toHaveBeenCalled();
});

it("shows deletion progress and an accessible retry without leaking implementation details", () => {
  const retryDiscard = vi.fn();
  const deleting = {
    ...slot("DISCARDING", "删除中.png"),
    slotId: "slot-deleting",
  };
  const retryable = {
    ...slot("DISCARDING", "待重试.png", "原始导入错误", {
      discardErrorMessage:
        "本地素材仍被项目历史或其他结果引用，当前无法删除；请先处理相关引用后重试",
      canRetryDiscard: true,
    }),
    slotId: "slot-retryable",
  };
  const active: IngressBatchSnapshot = {
    ...batch("discarding", 1, false, [deleting, retryable]),
    counts: {
      total: 2,
      queued: 0,
      processing: 2,
      placed: 0,
      failed: 0,
      cancelled: 0,
      savedUnplaced: 0,
      ignored: 0,
      terminal: 0,
    },
    canStop: false,
    canClose: false,
  };
  renderCenter(
    {
      batches: [active],
      pendingTargetChoices: [],
      protectedTargetNodeIds: [],
      guardState: { pendingOperationCount: 2, hasSavedUnplaced: false },
    },
    { retryDiscard },
  );

  const deletingRow = screen.getByRole("listitem", {
    name: "删除本地素材项 删除中.png",
  });
  expect(
    within(deletingRow).getByText(
      "正在删除本地素材，请勿关闭工作台",
    ),
  ).toBeTruthy();
  expect(
    within(deletingRow).queryByRole("button", { name: /重试/ }),
  ).toBeNull();

  const retryRow = screen.getByRole("listitem", {
    name: "删除本地素材项 待重试.png",
  });
  const liveRegions = screen.getAllByRole("status");
  expect(liveRegions).toHaveLength(1);
  expect(liveRegions[0].getAttribute("aria-live")).toBe("polite");
  expect(liveRegions[0].getAttribute("aria-atomic")).toBe("true");
  expect(liveRegions[0].textContent).toContain(
    "本地素材仍被项目历史或其他结果引用",
  );
  const filename = within(retryRow).getByText("待重试.png");
  expect(filename.tagName).toBe("BDI");
  expect(filename.getAttribute("dir")).toBe("auto");
  expect(
    within(retryRow).getByText(
      "本地素材仍被项目历史或其他结果引用，当前无法删除；请先处理相关引用后重试",
    ),
  ).toBeTruthy();
  expect(within(retryRow).getByText("原始导入错误")).toBeTruthy();
  fireEvent.click(
    within(retryRow).getByRole("button", {
      name: "重试删除本地素材 待重试.png",
    }),
  );
  expect(retryDiscard).toHaveBeenCalledWith(
    "discarding",
    "slot-retryable",
  );
  expect(retryRow.textContent).not.toContain("D:/");
  expect(retryRow.textContent).not.toContain("sha256");
});

it("explains a failed item's batch impact, continuing peers, and recovery choices", () => {
  const failed = slot("FAILED", "分镜 04.png", "服务返回格式错误");
  const activeBatch: IngressBatchSnapshot = {
    ...batch("active-recovery", 1, false, [failed]),
    counts: {
      total: 4,
      queued: 1,
      processing: 1,
      placed: 1,
      failed: 1,
      cancelled: 0,
      savedUnplaced: 0,
      ignored: 0,
      terminal: 2,
    },
    canStop: true,
    canClose: false,
  };
  renderCenter({
    batches: [activeBatch],
    pendingTargetChoices: [],
    protectedTargetNodeIds: [],
    guardState: {
      pendingOperationCount: 2,
      hasSavedUnplaced: false,
    },
  });

  const row = screen.getByRole("listitem", {
    name: "失败项 分镜 04.png",
  });
  expect(within(row).getByText("此项导入失败。")).toBeTruthy();
  expect(
    within(row).getByText(
      "本批已成功 1 项（已放置 1 项，待放置 0 项）；另有 2 项继续处理；可重新选择文件，或删除本地素材并忽略此项。",
    ),
  ).toBeTruthy();
});

it("keeps the permanent stale-target warning visible after a retry is saved unplaced", () => {
  const warning =
    "目标已变化，成功后需决定是否作为新节点放置";
  const stale = slot("SAVED_UNPLACED", "分镜 08.png", warning);
  const value: MediaIngressSnapshot = {
    batches: [
      {
        ...batch("stale-target", 1, false, [stale]),
        counts: {
          total: 1,
          queued: 0,
          processing: 0,
          placed: 0,
          failed: 0,
          cancelled: 0,
          savedUnplaced: 1,
          ignored: 0,
          terminal: 1,
        },
        canStop: false,
        canClose: false,
      },
    ],
    pendingTargetChoices: [],
    protectedTargetNodeIds: [],
    guardState: { pendingOperationCount: 0, hasSavedUnplaced: true },
  };

  renderCenter(value);

  expect(screen.getByText(warning)).toBeTruthy();
  const filename = screen.getByText("分镜 08.png");
  expect(filename.tagName).toBe("BDI");
  expect(filename.getAttribute("dir")).toBe("auto");

  cleanup();
  renderCenter({
    ...value,
    batches: [
      {
        ...value.batches[0],
        slots: [slot("IGNORED", "分镜 08.png", warning)],
        counts: {
          ...value.batches[0].counts,
          savedUnplaced: 0,
          ignored: 1,
        },
        canClose: true,
      },
    ],
    guardState: {
      pendingOperationCount: 0,
      hasSavedUnplaced: false,
    },
  });
  expect(screen.queryByText(warning)).toBeNull();
  expect(screen.queryByText("分镜 08.png")).toBeNull();
});
