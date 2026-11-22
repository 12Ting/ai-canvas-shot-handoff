import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MediaIngressContext, type MediaIngressRuntimePort } from "../mediaIngressContext";
import type { IngressItemState, MediaIngressController, MediaIngressSnapshot } from "../mediaIngressTypes";
import { MediaTargetLocks } from "../mediaTargetLocks";
import { IngressOverlayLayer } from "./IngressOverlayLayer";

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return { ...actual, ViewportPortal: ({ children }: { children: React.ReactNode }) => <div data-testid="viewport-portal">{children}</div> };
});

afterEach(cleanup);

function snapshot(
  states: IngressItemState[],
  targetBoundIndexes: ReadonlySet<number> = new Set(),
): MediaIngressSnapshot {
  const slots = states.map((state, index) => ({
    slotId: `slot-${index}`,
    reservation: { nodeId: `node-${index}`, position: { x: index * 20, y: index * 30 }, width: 312, height: 360 } as const,
    currentItem: { itemId: `item-${index}`, clientImportId: `client-${index}`, state, displayName: `${state.toLowerCase()}.png`, mediaKind: "image" as const, errorMessage: null, discardErrorMessage: null, canCancel: false, canRetryCancellation: false, canRetryDiscard: false, canForceRefresh: false, resumeScopeMessage: null },
    previousAttempts: [], attemptCount: 1, targetNodeId: targetBoundIndexes.has(index) ? `target-${index}` : null, canReselect: false, canIgnore: false, canPlaceSavedUnplaced: false,
  }));
  return {
    batches: [{ batchId: "batch", source: "multi_picker", createdOrder: 1, collapsed: false, counts: { total: states.length, queued: 1, processing: 2, placed: 0, failed: 1, cancelled: 1, savedUnplaced: 0, ignored: 1, terminal: 3 }, slots, canStop: true, canClose: false }],
    pendingTargetChoices: [], protectedTargetNodeIds: [], guardState: { pendingOperationCount: 3, hasSavedUnplaced: false },
  };
}

it("renders only live reservation states in canvas coordinates without object URLs", () => {
  const createObjectURL = vi.spyOn(URL, "createObjectURL");
  const value = snapshot(["QUEUED", "RECEIVING", "CANCELLING", "DISCARDING", "FAILED", "CANCELLED", "SAVED_UNPLACED", "IGNORED"]);
  const controller = { getSnapshot: () => value, subscribe: () => () => undefined } as unknown as MediaIngressController;
  const runtime: MediaIngressRuntimePort = { projectId: "project-1", loadEpoch: 1, nodeImport: {} as never, controller, targetLocks: new MediaTargetLocks() };
  render(<MediaIngressContext.Provider value={runtime}><IngressOverlayLayer /></MediaIngressContext.Provider>);

  expect(screen.getByTestId("viewport-portal")).toBeTruthy();
  expect(screen.getByText(/排队中/)).toBeTruthy();
  expect(screen.getByText(/正在处理/)).toBeTruthy();
  expect(screen.getByText(/正在确认取消结果/)).toBeTruthy();
  expect(screen.queryByText("discarding.png")).toBeNull();
  expect(screen.queryByText("failed.png")).toBeNull();
  expect(screen.queryByText("cancelled.png")).toBeNull();
  expect(screen.queryByText("saved_unplaced.png")).toBeNull();
  expect(screen.queryByText("ignored.png")).toBeNull();
  expect(screen.getByText("queued.png").closest("article")?.getAttribute("style")).toContain("left: 0px");
  expect(screen.getByText("queued.png").closest("article")?.tabIndex).toBe(-1);
  expect(createObjectURL).not.toHaveBeenCalled();
});

it("keeps target-bound fallback reservations hidden in every state while create-near-target remains visible", () => {
  const value = snapshot(
    [
      "QUEUED",
      "INITIALIZING",
      "RECEIVING",
      "CANCELLING",
      "DISCARDING",
      "FAILED",
      "CANCELLED",
      "SAVED_UNPLACED",
    ],
    new Set([1, 2, 3, 4, 5, 6]),
  );
  const controller = {
    getSnapshot: () => value,
    subscribe: () => () => undefined,
  } as unknown as MediaIngressController;
  const runtime: MediaIngressRuntimePort = {
    projectId: "project-1",
    loadEpoch: 1,
    nodeImport: {} as never,
    controller,
    targetLocks: new MediaTargetLocks(),
  };

  render(
    <MediaIngressContext.Provider value={runtime}>
      <IngressOverlayLayer />
    </MediaIngressContext.Provider>,
  );

  expect(screen.getByText("queued.png")).toBeTruthy();
  expect(screen.queryByText("initializing.png")).toBeNull();
  expect(screen.queryByText("receiving.png")).toBeNull();
  expect(screen.queryByText("cancelling.png")).toBeNull();
  expect(screen.queryByText("discarding.png")).toBeNull();
  expect(screen.queryByText("failed.png")).toBeNull();
  expect(screen.queryByText("cancelled.png")).toBeNull();
  expect(screen.queryByText("saved_unplaced.png")).toBeNull();
});

it("reuses the exact reservation for a queued reselect while failed and ignored states stay off-canvas", () => {
  const createObjectURL = vi.spyOn(URL, "createObjectURL");
  let value = snapshot(["FAILED"]);
  const controller = {
    getSnapshot: () => value,
    subscribe: () => () => undefined,
  } as unknown as MediaIngressController;
  const runtime: MediaIngressRuntimePort = {
    projectId: "project-1",
    loadEpoch: 1,
    nodeImport: {} as never,
    controller,
    targetLocks: new MediaTargetLocks(),
  };
  const rendered = render(
    <MediaIngressContext.Provider value={runtime}>
      <IngressOverlayLayer />
    </MediaIngressContext.Provider>,
  );

  expect(screen.queryByText("failed.png")).toBeNull();

  value = snapshot(["QUEUED"]);
  rendered.rerender(
    <MediaIngressContext.Provider value={runtime}>
      <IngressOverlayLayer />
    </MediaIngressContext.Provider>,
  );
  const queued = screen.getByText("queued.png").closest("article");
  expect(queued?.getAttribute("style")).toContain("left: 0px");
  expect(queued?.getAttribute("style")).toContain("top: 0px");
  expect(queued?.getAttribute("style")).toContain("width: 312px");
  expect(queued?.getAttribute("style")).toContain("height: 360px");

  value = snapshot(["IGNORED"]);
  rendered.rerender(
    <MediaIngressContext.Provider value={runtime}>
      <IngressOverlayLayer />
    </MediaIngressContext.Provider>,
  );
  expect(screen.queryByText("ignored.png")).toBeNull();
  expect(createObjectURL).not.toHaveBeenCalled();
});
