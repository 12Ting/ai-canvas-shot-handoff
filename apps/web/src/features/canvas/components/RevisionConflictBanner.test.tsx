import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCanvasStore } from "../canvasStore";
import { RevisionConflictBanner } from "./RevisionConflictBanner";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("RevisionConflictBanner", () => {
  beforeEach(() => {
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId: "project-1",
      nodes: [],
      edges: [],
      shots: [],
      revisionId: "revision-1",
    });
    useCanvasStore.getState().addNode("note", { x: 0, y: 0 });
    useCanvasStore
      .getState()
      .markConflict(useCanvasStore.getState().loadEpoch, "project-1", "revision-2");
  });
  afterEach(cleanup);

  it("waits for an explicit reload choice without changing the local graph", () => {
    const localNodes = useCanvasStore.getState().nodes;
    const reloadServer = vi.fn().mockResolvedValue(undefined);
    const keepLocalCopy = vi.fn().mockResolvedValue(undefined);

    render(
      <RevisionConflictBanner
        keepLocalCopy={keepLocalCopy}
        reloadServer={reloadServer}
      />,
    );

    expect(useCanvasStore.getState().nodes).toBe(localNodes);
    expect(reloadServer).not.toHaveBeenCalled();
    expect(keepLocalCopy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新加载服务端版本" }));
    expect(reloadServer).toHaveBeenCalledOnce();
    expect(keepLocalCopy).not.toHaveBeenCalled();
  });

  it("calls only the keep-local-copy action when explicitly selected", () => {
    const reloadServer = vi.fn().mockResolvedValue(undefined);
    const keepLocalCopy = vi.fn().mockResolvedValue(undefined);
    render(
      <RevisionConflictBanner
        keepLocalCopy={keepLocalCopy}
        reloadServer={reloadServer}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "保留本地副本" }));

    expect(keepLocalCopy).toHaveBeenCalledOnce();
    expect(reloadServer).not.toHaveBeenCalled();
  });

  it("disables both choices while one is pending and prevents duplicate actions", async () => {
    const pending = deferred();
    const reloadServer = vi.fn().mockReturnValue(pending.promise);
    const keepLocalCopy = vi.fn().mockResolvedValue(undefined);
    render(
      <RevisionConflictBanner
        keepLocalCopy={keepLocalCopy}
        reloadServer={reloadServer}
      />,
    );
    const reload = screen.getByRole("button", { name: "重新加载服务端版本" });
    const keep = screen.getByRole("button", { name: "保留本地副本" });

    fireEvent.click(reload);
    fireEvent.click(reload);
    fireEvent.click(keep);

    expect(reloadServer).toHaveBeenCalledOnce();
    expect(keepLocalCopy).not.toHaveBeenCalled();
    expect(reload).toHaveProperty("disabled", true);
    expect(keep).toHaveProperty("disabled", true);
    expect(reload.getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      pending.resolve();
      await pending.promise;
    });
    expect(reload).toHaveProperty("disabled", false);
    expect(keep).toHaveProperty("disabled", false);
  });

  it("handles a rejected parent action without an unhandled rejection", async () => {
    const reloadServer = vi.fn().mockRejectedValue(new Error("reload failed"));
    render(
      <StrictMode>
        <RevisionConflictBanner
          keepLocalCopy={vi.fn().mockResolvedValue(undefined)}
          reloadServer={reloadServer}
        />
      </StrictMode>,
    );
    const reload = screen.getByRole("button", { name: "重新加载服务端版本" });

    fireEvent.click(reload);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(reloadServer).toHaveBeenCalledOnce();
    expect(reload).toHaveProperty("disabled", false);
  });

  it("blocks both choices for an external reason without invoking either action", () => {
    const reloadServer = vi.fn().mockResolvedValue(undefined);
    const keepLocalCopy = vi.fn().mockResolvedValue(undefined);
    render(
      <RevisionConflictBanner
        disabledReason="等待媒体导入完成"
        keepLocalCopy={keepLocalCopy}
        reloadServer={reloadServer}
      />,
    );
    const reload = screen.getByRole("button", { name: "重新加载服务端版本" });
    const keep = screen.getByRole("button", { name: "保留本地副本" });

    expect(reload).toHaveProperty("disabled", true);
    expect(keep).toHaveProperty("disabled", true);
    expect(reload.getAttribute("title")).toBe("等待媒体导入完成");
    fireEvent.click(reload);
    fireEvent.click(keep);
    expect(reloadServer).not.toHaveBeenCalled();
    expect(keepLocalCopy).not.toHaveBeenCalled();
  });
});
