import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MediaIngressContext,
  type MediaIngressRuntimePort,
} from "../mediaIngressContext";
import { MediaTargetLocks } from "../mediaTargetLocks";
import type {
  MediaIngressAdmission,
  MediaIngressController,
  MediaIngressSnapshot,
  PendingTargetChoiceSnapshot,
} from "../mediaIngressTypes";
import { MediaTargetChoiceDialog } from "./MediaTargetChoiceDialog";

function choice(
  choiceId: string,
  targetNodeId: string,
  displayName: string,
  overrides: Partial<PendingTargetChoiceSnapshot> = {},
): PendingTargetChoiceSnapshot {
  return {
    choiceId,
    targetNodeId,
    displayName,
    requestedMediaKind: "image",
    currentMediaKind: "image",
    canReplace: true,
    replacementBlockedReason: null,
    ...overrides,
  };
}

function dialogHarness(
  pendingTargetChoices: readonly PendingTargetChoiceSnapshot[],
) {
  let snapshot: MediaIngressSnapshot = {
    batches: [],
    pendingTargetChoices,
    protectedTargetNodeIds: pendingTargetChoices.map(
      (pending) => pending.targetNodeId,
    ),
    guardState: {
      pendingOperationCount: pendingTargetChoices.length,
      hasSavedUnplaced: false,
    },
  };
  const listeners = new Set<() => void>();
  const resolveTargetChoice = vi.fn(
    (
      choiceId: string,
      selected: "create" | "replace" | "cancel",
    ): MediaIngressAdmission => {
      const exists = snapshot.pendingTargetChoices.some(
        (pending) => pending.choiceId === choiceId,
      );
      if (!exists) return { kind: "empty" };
      snapshot = {
        ...snapshot,
        pendingTargetChoices: snapshot.pendingTargetChoices.filter(
          (pending) => pending.choiceId !== choiceId,
        ),
        guardState: {
          ...snapshot.guardState,
          pendingOperationCount:
            snapshot.guardState.pendingOperationCount - 1,
        },
      };
      for (const listener of listeners) listener();
      return selected === "cancel"
        ? { kind: "empty" }
        : { kind: "accepted", batchId: `batch-${choiceId}` };
    },
  );
  const controller = {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    resolveTargetChoice,
  } as unknown as MediaIngressController;
  const runtime: MediaIngressRuntimePort = {
    projectId: "project-1",
    loadEpoch: 1,
    nodeImport: {} as never,
    controller,
    targetLocks: new MediaTargetLocks(),
  };
  return { runtime, resolveTargetChoice };
}

function renderDialog(
  pendingTargetChoices: readonly PendingTargetChoiceSnapshot[],
  options: { triggerConnected?: boolean } = {},
) {
  const { runtime, resolveTargetChoice } = dialogHarness(
    pendingTargetChoices,
  );
  const fallbackRef = createRef<HTMLDivElement>();
  const triggers = new Map<string, HTMLButtonElement>();
  const view = render(
    <MediaIngressContext.Provider value={runtime}>
      {pendingTargetChoices.map((pending) => (
        <button
          key={pending.choiceId}
          ref={(element) => {
            if (element) triggers.set(pending.choiceId, element);
          }}
          type="button"
        >
          trigger {pending.choiceId}
        </button>
      ))}
      <div ref={fallbackRef} data-testid="canvas-focus-fallback" tabIndex={-1} />
      <MediaTargetChoiceDialog
        fallbackFocusRef={fallbackRef}
        getTriggerElement={(choiceId) => triggers.get(choiceId) ?? null}
      />
    </MediaIngressContext.Provider>,
  );
  if (options.triggerConnected === false) {
    triggers.set(
      pendingTargetChoices[0].choiceId,
      document.createElement("button"),
    );
  }
  return { ...view, resolveTargetChoice, fallbackRef };
}

describe("MediaTargetChoiceDialog", () => {
  afterEach(cleanup);

  it("presents the decision gate, initially focuses 新建节点, and treats Enter as create", () => {
    const { resolveTargetChoice } = renderDialog([
      choice("choice-1", "media-1", "portrait.png"),
    ]);

    const dialog = screen.getByRole("dialog", { name: "选择导入方式" });
    expect(dialog.textContent).toContain("portrait.png");
    expect(screen.getByRole("button", { name: "新建节点" })).toBe(
      document.activeElement,
    );
    expect(
      screen.getByRole("button", { name: "替换当前" }),
    ).toHaveProperty("disabled", false);
    expect(screen.getByRole("button", { name: "取消" })).toHaveProperty(
      "disabled",
      false,
    );

    fireEvent.keyDown(dialog, { key: "Enter" });

    expect(resolveTargetChoice).toHaveBeenCalledOnce();
    expect(resolveTargetChoice).toHaveBeenCalledWith("choice-1", "create");
  });

  it("traps forward and reverse Tab at the dialog edges", () => {
    renderDialog([choice("choice-1", "media-1", "portrait.png")]);
    const dialog = screen.getByRole("dialog");
    const create = screen.getByRole("button", { name: "新建节点" });
    const cancel = screen.getByRole("button", { name: "取消" });

    cancel.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(create);

    create.focus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(cancel);
  });

  it("cancels with Escape and restores the connected trigger focus", async () => {
    const { resolveTargetChoice } = renderDialog([
      choice("choice-1", "media-1", "portrait.png"),
    ]);

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await act(async () => undefined);

    expect(resolveTargetChoice).toHaveBeenCalledWith("choice-1", "cancel");
    expect(document.activeElement).toBe(
      screen.getByText("trigger choice-1"),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("restores a safe canvas fallback when the original trigger was removed", async () => {
    const { fallbackRef } = renderDialog(
      [choice("choice-1", "media-1", "portrait.png")],
      { triggerConnected: false },
    );

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await act(async () => undefined);

    expect(document.activeElement).toBe(fallbackRef.current);
  });

  it("keeps create available while disabling an incompatible replacement with its reason", () => {
    renderDialog([
      choice("choice-1", "media-1", "clip.webm", {
        requestedMediaKind: "video",
        currentMediaKind: "image",
        canReplace: false,
        replacementBlockedReason: "仅可替换为相同媒体类型",
      }),
    ]);

    expect(
      screen.getByRole("button", { name: "新建节点" }),
    ).toHaveProperty("disabled", false);
    expect(
      screen.getByRole("button", { name: "替换当前" }),
    ).toHaveProperty("disabled", true);
    expect(screen.getByText("仅可替换为相同媒体类型")).toBeTruthy();
  });

  it("shows independent pending choices in FIFO order", () => {
    const { resolveTargetChoice } = renderDialog([
      choice("choice-1", "media-1", "first.png"),
      choice("choice-2", "media-2", "second.png"),
    ]);

    expect(screen.getByRole("dialog").textContent).toContain("first.png");
    expect(screen.getByRole("dialog").textContent).not.toContain("second.png");

    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(resolveTargetChoice).toHaveBeenNthCalledWith(
      1,
      "choice-1",
      "cancel",
    );
    expect(screen.getByRole("dialog").textContent).toContain("second.png");
  });
});
