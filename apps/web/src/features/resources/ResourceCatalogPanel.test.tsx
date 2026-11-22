import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCanvasStore } from "../canvas/canvasStore";
import { ResourceCatalogPanel } from "./ResourceCatalogPanel";

const api = vi.hoisted(() => ({
  archiveResource: vi.fn(),
  createResource: vi.fn(),
  createResourceRevision: vi.fn(),
  getResource: vi.fn(),
  listProjectResourceUpdates: vi.fn(),
  listResources: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  ...api,
}));

const projectId = "11111111-1111-4111-8111-111111111111";
const resourceId = "22222222-2222-4222-8222-222222222222";
const revisionId = "33333333-3333-4333-8333-333333333333";
const appStyles = readFileSync(resolve(process.cwd(), "src/app/styles.css"), "utf8");

const styleResource = {
  id: resourceId,
  origin_project_id: projectId,
  kind: "STYLE" as const,
  name: "电影暖调",
  created_at: "2026-08-28T00:00:00Z",
  archived_at: null,
  latest_revision: {
    id: revisionId,
    sequence: 2,
    payload_sha256: "a".repeat(64),
    created_at: "2026-08-28T00:00:00Z",
    resource_id: resourceId,
    schema_version: 1 as const,
    payload: { kind: "STYLE" as const, summary: "暖色电影质感" },
    tags: [{ display_tag: "电影", normalized_tag: "电影" }],
    artifacts: [],
    created_by_actor: "local-user",
  },
};

describe("ResourceCatalogPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useCanvasStore.getState().reset();
    useCanvasStore.getState().load({
      projectId,
      revisionId: "44444444-4444-4444-8444-444444444444",
      nodes: [],
      edges: [],
      shots: [],
    });
    api.listResources.mockResolvedValue([styleResource]);
    api.listProjectResourceUpdates.mockResolvedValue([]);
  });

  afterEach(cleanup);

  it("offers all four resource kinds and refreshes the catalog by active kind", async () => {
    render(<ResourceCatalogPanel projectId={projectId} />);
    fireEvent.click(screen.getByRole("button", { name: "打开资源目录" }));

    expect(await screen.findByText("电影暖调")).toBeTruthy();
    for (const name of ["风格", "角色", "提示词", "媒体"]) {
      expect(screen.getByRole("tab", { name })).toBeTruthy();
    }
    expect(api.listResources).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "STYLE", includeArchived: true }),
    );

    api.listResources.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("tab", { name: "提示词" }));
    await waitFor(() =>
      expect(api.listResources).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: "PROMPT", includeArchived: true }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "新建提示词资源" }));
    expect(
      screen.getByRole("dialog", { name: "新建资源" }).querySelector("select"),
    ).toHaveProperty("value", "PROMPT");
  });

  it("ignores an older search response that arrives after a newer query", async () => {
    let resolveOld!: (value: typeof styleResource[]) => void;
    const oldResult = new Promise<typeof styleResource[]>((resolve) => {
      resolveOld = resolve;
    });
    api.listResources
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(oldResult)
      .mockResolvedValueOnce([{ ...styleResource, name: "新结果" }]);

    render(<ResourceCatalogPanel projectId={projectId} />);
    fireEvent.click(screen.getByRole("button", { name: "打开资源目录" }));
    await waitFor(() => expect(api.listResources).toHaveBeenCalledTimes(1));

    const query = screen.getByRole("searchbox", { name: "搜索资源" });
    fireEvent.change(query, { target: { value: "旧" } });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 280));
    });
    fireEvent.change(query, { target: { value: "新" } });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 280));
    });
    expect(await screen.findByText("新结果")).toBeTruthy();

    await act(async () => resolveOld([styleResource]));
    expect(screen.queryByText("电影暖调")).toBeNull();
    expect(screen.getByText("新结果")).toBeTruthy();
  });

  it("keeps the fixed catalog above the bottom collapse bar at 720, 820, and 1200px", () => {
    expect(appStyles).toMatch(
      /@media \(max-width: 1200px\)[\s\S]*?\.resource-catalog\s*\{[^}]*bottom:\s*2\.25rem/s,
    );
    expect(appStyles).toMatch(
      /@media \(max-width: 820px\)[\s\S]*?\.resource-catalog\s*\{[^}]*bottom:\s*2\.5rem/s,
    );
    expect(appStyles).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?\.resource-catalog\s*\{[^}]*bottom:\s*2\.75rem/s,
    );
  });

  it("keeps an archived resource visible and marked when the project pins an older revision", async () => {
    const bindingId = "55555555-5555-4555-8555-555555555555";
    const pinnedRevisionId = "66666666-6666-4666-8666-666666666666";
    useCanvasStore.getState().setResourceBinding({
      id: bindingId,
      resourceKind: "STYLE",
      bindingKey: "style.primary",
      scopeKind: "PROJECT",
      scopeId: null,
      resourceRevisionId: pinnedRevisionId,
    });
    api.listResources.mockResolvedValue([
      { ...styleResource, archived_at: "2026-08-29T00:00:00Z" },
    ]);
    api.listProjectResourceUpdates.mockResolvedValue([
      {
        binding_id: bindingId,
        binding_key: "style.primary",
        scope_kind: "PROJECT",
        scope_id: null,
        resource_id: resourceId,
        resource_kind: "STYLE",
        pinned_revision_id: pinnedRevisionId,
        latest_revision_id: revisionId,
        pinned_sequence: 1,
        latest_sequence: 2,
      },
    ]);

    render(<ResourceCatalogPanel projectId={projectId} />);
    fireEvent.click(screen.getByRole("button", { name: "打开资源目录" }));

    const card = await screen.findByRole("article");
    expect(card.textContent).toContain("电影暖调");
    expect(card.textContent).toContain("已归档");
    expect(card.textContent).toContain("已固定");
    fireEvent.click(screen.getByRole("button", { name: "升级此绑定" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "升级此绑定" })).toBeNull(),
    );
    expect(useCanvasStore.getState().resourceBindings[0]?.resourceRevisionId).toBe(revisionId);
  });
});
