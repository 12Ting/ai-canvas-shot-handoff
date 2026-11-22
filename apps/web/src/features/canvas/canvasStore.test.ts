import { beforeEach, describe, expect, it } from "vitest";
import { useCanvasStore } from "./canvasStore";
import type {
  CanvasEdge,
  CanvasNode,
  CanvasResourceBinding,
  CanvasShot,
} from "./canvasTypes";
import { documentFromText } from "../prompting/promptDocument";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const IMAGE_REVISION_ID = "22222222-2222-4222-8222-222222222222";
const SHOT_ID = "33333333-3333-4333-8333-333333333333";
const VIDEO_REVISION_ID = "44444444-4444-4444-8444-444444444444";
const REFERENCE_REVISION_ID = "55555555-5555-4555-8555-555555555555";
const SECOND_SHOT_ID = "66666666-6666-4666-8666-666666666666";
const THIRD_SHOT_ID = "77777777-7777-4777-8777-777777777777";

function makeNode(id: string, text = ""): CanvasNode {
  return {
    id,
    type: "note",
    position: { x: 0, y: 0 },
    data: { kind: "note", text },
  };
}

function makePromptNode(id: string, text = ""): CanvasNode {
  return {
    id,
    type: "prompt",
    position: { x: 0, y: 0 },
    data: { kind: "prompt", document: documentFromText(text, id) },
  };
}

function makeEdge(id: string, source: string, target: string): CanvasEdge {
  return {
    id,
    source,
    sourceHandle: "output",
    target,
    targetHandle: "input",
  };
}

function makeShotNode(id: string, shotId: string, x = 0, y = 0): CanvasNode {
  return {
    id,
    type: "shot",
    position: { x, y },
    data: { kind: "shot", shotId },
  };
}

function makeShot(
  id: string,
  order: number,
  options: Partial<Omit<CanvasShot, "id" | "order">> = {},
): CanvasShot {
  return {
    id,
    order,
    imageRevisionId: null,
    videoRevisionId: null,
    referenceRevisionIds: [],
    ...options,
  };
}

function generatedRevisionId(index: number): string {
  return `00000000-0000-4000-8000-${index.toString().padStart(12, "0")}`;
}

function loadCanvas(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  revisionId: string,
  options: {
    projectId?: string;
    resourceBindings?: CanvasResourceBinding[];
    shots?: CanvasShot[];
    viewport?: { x: number; y: number; zoom: number };
  } = {},
): void {
  useCanvasStore.getState().load({
    projectId: options.projectId ?? PROJECT_ID,
    nodes,
    edges,
    shots: options.shots ?? [],
    resourceBindings: options.resourceBindings,
    revisionId,
    viewport: options.viewport,
  });
}

describe("canvasStore save sequencing", () => {
  beforeEach(() => useCanvasStore.getState().reset());

  it("captures branch CAS context and ignores callbacks after that context changes", () => {
    useCanvasStore.getState().load({
      projectId: PROJECT_ID,
      nodes: [],
      edges: [],
      shots: [],
      revisionId: "88888888-8888-4888-8888-888888888888",
      branchContext: {
        branches: [],
        selectedBranchId: "99999999-9999-4999-8999-999999999999",
        selectionVersion: 3,
        viewedBranchId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        viewedBranchHeadRevisionId: "88888888-8888-4888-8888-888888888888",
        viewedBranchHeadVersion: 5,
        isViewingUnpromotedBranch: true,
      },
    });
    useCanvasStore.getState().addNode("note", { x: 1, y: 2 });
    const capture = useCanvasStore.getState().beginSave();

    expect(capture).toMatchObject({
      viewedBranchId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      viewedBranchHeadRevisionId: "88888888-8888-4888-8888-888888888888",
      viewedBranchHeadVersion: 5,
      selectionVersion: 3,
    });
    useCanvasStore.setState({ viewedBranchHeadVersion: 6 });
    useCanvasStore.getState().failSave(
      capture.loadEpoch,
      capture.projectId,
      "stale failure",
      capture.viewedBranchId,
      capture.viewedBranchHeadVersion,
      capture.selectionVersion,
    );

    expect(useCanvasStore.getState().status).toBe("saving");
    expect(useCanvasStore.getState().errorMessage).toBeNull();
  });

  it("applies prompt-block edits immutably and preserves state identity for no-ops", () => {
    const nodeId = "88888888-8888-4888-8888-888888888888";
    const secondBlockId = "99999999-9999-4999-8999-999999999999";
    loadCanvas([makePromptNode(nodeId, "主体")], [], "revision-1");

    expect(
      useCanvasStore.getState().addPromptBlock(nodeId, {
        id: secondBlockId,
        kind: "MOTION",
        text: "向前🙂",
        locked: false,
      }),
    ).toBe(true);
    expect(useCanvasStore.getState().editSequence).toBe(1);
    const prompt = useCanvasStore.getState().nodes[0];
    expect(prompt.data.kind === "prompt" && prompt.data.document.rendered_text).toBe(
      "主体\n向前🙂",
    );

    expect(useCanvasStore.getState().movePromptBlock(nodeId, secondBlockId, -1)).toBe(true);
    expect(useCanvasStore.getState().setPromptBlockLocked(nodeId, secondBlockId, true)).toBe(
      true,
    );
    const beforeNoop = useCanvasStore.getState();
    expect(useCanvasStore.getState().updatePromptBlock(nodeId, secondBlockId, { text: "禁止" })).toBe(
      false,
    );
    expect(useCanvasStore.getState()).toBe(beforeNoop);
    expect(useCanvasStore.getState().removePromptBlock(nodeId, secondBlockId)).toBe(false);
    expect(useCanvasStore.getState()).toBe(beforeNoop);
  });

  it("sets exact resource-revision bindings and emits them in GraphSnapshotV3", () => {
    const nodeId = "88888888-8888-4888-8888-888888888888";
    const binding: CanvasResourceBinding = {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      resourceKind: "STYLE",
      bindingKey: "visual_style",
      scopeKind: "NODE",
      scopeId: nodeId,
      resourceRevisionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    };
    loadCanvas([makePromptNode(nodeId, "主体")], [], "revision-1");

    expect(useCanvasStore.getState().setResourceBinding(binding)).toBe(true);
    const beforeNoop = useCanvasStore.getState();
    expect(useCanvasStore.getState().setResourceBinding({ ...binding })).toBe(false);
    expect(useCanvasStore.getState()).toBe(beforeNoop);

    const capture = useCanvasStore.getState().beginSave();
    expect(capture.snapshot.schema_version).toBe(3);
    expect(capture.snapshot.resource_bindings).toEqual([
      {
        id: binding.id,
        resource_kind: "STYLE",
        binding_key: "visual_style",
        scope_kind: "NODE",
        scope_id: nodeId,
        resource_revision_id: binding.resourceRevisionId,
      },
    ]);
  });

  it("removes scoped resource bindings when their node or Shot is deleted", () => {
    const nodeId = "88888888-8888-4888-8888-888888888888";
    const shotNodeId = "99999999-9999-4999-8999-999999999999";
    const nodeBinding: CanvasResourceBinding = {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      resourceKind: "PROMPT",
      bindingKey: "prompt.primary",
      scopeKind: "NODE",
      scopeId: nodeId,
      resourceRevisionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    };
    const shotBinding: CanvasResourceBinding = {
      id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      resourceKind: "STYLE",
      bindingKey: "style.primary",
      scopeKind: "SHOT",
      scopeId: SHOT_ID,
      resourceRevisionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    };
    loadCanvas(
      [makePromptNode(nodeId, "主体"), makeShotNode(shotNodeId, SHOT_ID)],
      [],
      "revision-1",
      {
        shots: [makeShot(SHOT_ID, 0)],
        resourceBindings: [nodeBinding, shotBinding],
      },
    );

    useCanvasStore.getState().deleteNode(nodeId);
    expect(useCanvasStore.getState().resourceBindings).toEqual([shotBinding]);
    useCanvasStore.getState().deleteShot(SHOT_ID);
    expect(useCanvasStore.getState().resourceBindings).toEqual([]);
  });

  it("does not mark clean when edits arrive during a save", () => {
    const store = useCanvasStore.getState();
    loadCanvas([], [], "revision-1");
    store.addNode("note", { x: 0, y: 0 });
    const captured = useCanvasStore.getState().beginSave();
    useCanvasStore.getState().addNode("note", { x: 20, y: 20 });
    useCanvasStore
      .getState()
      .completeSave(captured.editSequence, captured.loadEpoch, captured.projectId, "revision-2");

    const current = useCanvasStore.getState();
    expect(current.baseRevisionId).toBe("revision-2");
    expect(current.status).toBe("dirty");
    expect(current.savedSequence).toBe(captured.editSequence);
    expect(current.nodes).toHaveLength(2);
  });

  it("keeps newer edits dirty when an older save fails", () => {
    const store = useCanvasStore.getState();
    loadCanvas([makeNode("node-1")], [], "revision-1");
    store.updateNodeText("node-1", "captured edit");
    const capture = useCanvasStore.getState().beginSave();
    useCanvasStore.getState().updateNodeText("node-1", "newer edit");
    const localNodes = useCanvasStore.getState().nodes;
    const localEdges = useCanvasStore.getState().edges;
    const editSequence = useCanvasStore.getState().editSequence;

    useCanvasStore.getState().failSave(capture.loadEpoch, capture.projectId, "save failed");

    const current = useCanvasStore.getState();
    expect(current.nodes).toBe(localNodes);
    expect(current.edges).toBe(localEdges);
    expect(current.nodes[0].data.text).toBe("newer edit");
    expect(current.editSequence).toBe(editSequence);
    expect(current.status).toBe("dirty");
    expect(current.errorMessage).toBe("save failed");
    expect(current.conflictRevisionId).toBeNull();
  });

  it("marks an unmodified save as error when it fails", () => {
    const store = useCanvasStore.getState();
    loadCanvas([makeNode("node-1")], [], "revision-1");
    store.updateNodeText("node-1", "captured edit");
    const capture = useCanvasStore.getState().beginSave();
    const localNodes = useCanvasStore.getState().nodes;
    const editSequence = useCanvasStore.getState().editSequence;

    useCanvasStore.getState().failSave(capture.loadEpoch, capture.projectId, "save failed");

    const current = useCanvasStore.getState();
    expect(current.nodes).toBe(localNodes);
    expect(current.editSequence).toBe(editSequence);
    expect(current.status).toBe("error");
    expect(current.errorMessage).toBe("save failed");
  });

  it("preserves the local graph on a revision conflict", () => {
    const store = useCanvasStore.getState();
    loadCanvas(
      [makeNode("node-1"), makeNode("node-2")],
      [makeEdge("edge-1", "node-1", "node-2")],
      "revision-1",
    );
    store.updateNodeText("node-1", "local edit");
    const localNodes = useCanvasStore.getState().nodes;
    const localEdges = useCanvasStore.getState().edges;

    useCanvasStore
      .getState()
      .markConflict(useCanvasStore.getState().loadEpoch, PROJECT_ID, "revision-2");

    const current = useCanvasStore.getState();
    expect(current.nodes).toBe(localNodes);
    expect(current.edges).toBe(localEdges);
    expect(current.status).toBe("conflict");
    expect(current.conflictRevisionId).toBe("revision-2");
  });

  it.each(["load", "reset"] as const)(
    "%s invalidates stale complete, failure, and conflict callbacks",
    (boundary) => {
      const store = useCanvasStore.getState();
      loadCanvas([makeNode("old")], [], "revision-old");
      store.updateNodeText("old", "pending");
      const capture = useCanvasStore.getState().beginSave();

      if (boundary === "load") {
        loadCanvas([makeNode("fresh")], [], "revision-fresh", {
          projectId: "44444444-4444-4444-8444-444444444444",
          viewport: { x: 7, y: 8, zoom: 2 },
        });
      } else {
        useCanvasStore.getState().reset();
      }

      const expected = useCanvasStore.getState();
      useCanvasStore
        .getState()
        .completeSave(
          capture.editSequence,
          capture.loadEpoch,
          capture.projectId,
          "stale-complete",
        );
      useCanvasStore.getState().failSave(capture.loadEpoch, capture.projectId, "stale failure");
      useCanvasStore
        .getState()
        .markConflict(capture.loadEpoch, capture.projectId, "stale-conflict");

      const current = useCanvasStore.getState();
      expect(current.nodes).toBe(expected.nodes);
      expect(current.edges).toBe(expected.edges);
      expect(current.shots).toBe(expected.shots);
      expect(current.projectId).toBe(expected.projectId);
      expect(current.baseRevisionId).toBe(expected.baseRevisionId);
      expect(current.status).toBe(expected.status);
      expect(current.errorMessage).toBe(expected.errorMessage);
      expect(current.conflictRevisionId).toBe(expected.conflictRevisionId);
      expect(current.loadEpoch).toBe(expected.loadEpoch);
    },
  );

  it("load and reset advance the epoch and clear save state", () => {
    const initialEpoch = useCanvasStore.getState().loadEpoch;
    loadCanvas([makeNode("node-1")], [], "revision-1", {
      viewport: { x: 10, y: 20, zoom: 1.5 },
    });
    useCanvasStore.getState().updateNodeText("node-1", "changed");
    useCanvasStore
      .getState()
      .failSave(useCanvasStore.getState().loadEpoch, PROJECT_ID, "network");

    loadCanvas([makeNode("node-2")], [], "revision-2", {
      projectId: "55555555-5555-4555-8555-555555555555",
    });
    const loaded = useCanvasStore.getState();
    expect(loaded.loadEpoch).toBe(initialEpoch + 2);
    expect(loaded.editSequence).toBe(0);
    expect(loaded.savedSequence).toBe(0);
    expect(loaded.status).toBe("clean");
    expect(loaded.errorMessage).toBeNull();
    expect(loaded.conflictRevisionId).toBeNull();
    expect(loaded.projectId).toBe("55555555-5555-4555-8555-555555555555");
    expect(loaded.shots).toEqual([]);
    expect(loaded.viewport).toEqual({ x: 0, y: 0, zoom: 1 });

    useCanvasStore.getState().markConflict(loaded.loadEpoch, loaded.projectId!, "revision-3");
    useCanvasStore.getState().reset();
    const reset = useCanvasStore.getState();
    expect(reset.loadEpoch).toBe(initialEpoch + 3);
    expect(reset.nodes).toEqual([]);
    expect(reset.edges).toEqual([]);
    expect(reset.shots).toEqual([]);
    expect(reset.projectId).toBeNull();
    expect(reset.baseRevisionId).toBeNull();
    expect(reset.status).toBe("clean");
    expect(reset.errorMessage).toBeNull();
    expect(reset.conflictRevisionId).toBeNull();
    expect(reset.viewport).toEqual({ x: 0, y: 0, zoom: 1 });
  });

  it("deletes incident edges and ignores a missing node", () => {
    const store = useCanvasStore.getState();
    loadCanvas(
      [makeNode("node-1"), makeNode("node-2")],
      [makeEdge("edge-1", "node-1", "node-2")],
      "revision-1",
    );

    store.deleteNode("missing");
    expect(useCanvasStore.getState().editSequence).toBe(0);
    expect(useCanvasStore.getState().status).toBe("clean");

    store.deleteNode("node-1");
    const current = useCanvasStore.getState();
    expect(current.nodes.map((node) => node.id)).toEqual(["node-2"]);
    expect(current.edges).toEqual([]);
    expect(current.editSequence).toBe(1);
    expect(current.status).toBe("dirty");
  });

  it("copies only domain node data with an offset", () => {
    const source: CanvasNode = {
      ...makeNode("node-1", "idea"),
      position: { x: 10, y: 20 },
      measured: { width: 180, height: 90 },
      width: 180,
      height: 90,
      selected: true,
      dragging: true,
      style: { opacity: 0.5 },
    };
    loadCanvas([source], [], "revision-1");

    useCanvasStore.getState().copyNode("node-1");

    const current = useCanvasStore.getState();
    const copy = current.nodes[1];
    expect(copy.id).not.toBe(source.id);
    expect(copy.type).toBe("note");
    expect(copy.position).toEqual({ x: 42, y: 52 });
    expect(copy.data).toEqual({ kind: "note", text: "idea" });
    expect(copy.data).not.toBe(source.data);
    expect(copy).not.toHaveProperty("measured");
    expect(copy).not.toHaveProperty("width");
    expect(copy).not.toHaveProperty("height");
    expect(copy).not.toHaveProperty("selected");
    expect(copy).not.toHaveProperty("dragging");
    expect(copy).not.toHaveProperty("style");
    expect(current.editSequence).toBe(1);
  });

  it.each(["text_source", "note"] as const)(
    "copies %s text data without UI state",
    (kind) => {
      const source: CanvasNode = {
        id: `source-${kind}`,
        type: kind,
        position: { x: 10, y: 20 },
        data: { kind, text: `${kind} text` },
        selected: true,
      } as CanvasNode;
      loadCanvas([source], [], "revision-1");

      useCanvasStore.getState().copyNode(source.id);

      const copy = useCanvasStore.getState().nodes[1];
      expect(copy.id).not.toBe(source.id);
      expect(copy.position).toEqual({ x: 42, y: 52 });
      expect(copy.data).toEqual({ kind, text: `${kind} text` });
      expect(copy.data).not.toBe(source.data);
      expect(copy).not.toHaveProperty("selected");
    },
  );

  it("copies a prompt document without UI state", () => {
    const source = {
      ...makePromptNode("88888888-8888-4888-8888-888888888888", "prompt text"),
      position: { x: 10, y: 20 },
      selected: true,
    } as CanvasNode;
    loadCanvas([source], [], "revision-1");

    useCanvasStore.getState().copyNode(source.id);

    const copy = useCanvasStore.getState().nodes[1];
    expect(copy.id).not.toBe(source.id);
    expect(copy.position).toEqual({ x: 42, y: 52 });
    expect(copy.data).toEqual(source.data);
    expect(copy.data).not.toBe(source.data);
    if (copy.data.kind === "prompt" && source.data.kind === "prompt") {
      expect(copy.data.document).not.toBe(source.data.document);
      expect(copy.data.document.blocks).not.toBe(source.data.document.blocks);
    }
    expect(copy).not.toHaveProperty("selected");
  });

  it.each([
    ["attached", IMAGE_REVISION_ID, "image"],
    ["empty", null, null],
  ] as const)("copies an %s media node by immutable revision reference", (_case, revisionId, kind) => {
    const source = {
      id: "media-source",
      type: "media_input",
      position: { x: 10, y: 20 },
      data: {
        kind: "media_input",
        artifactRevisionId: revisionId,
        mediaKind: kind,
        file: new File(["private"], "private.png"),
        previewUrl: "blob:http://localhost/private",
        metadata: { width: 1, height: 1 },
      },
    } as unknown as CanvasNode;
    loadCanvas([source], [], "revision-1");

    useCanvasStore.getState().copyNode(source.id);

    const current = useCanvasStore.getState();
    expect(current.nodes).toHaveLength(2);
    expect(current.nodes[1].data).toEqual({
      kind: "media_input",
      artifactRevisionId: revisionId,
      mediaKind: kind,
    });
    expect(current.nodes[1].data).not.toBe(source.data);
    expect(current.editSequence).toBe(1);
  });

  it("does not copy a Shot node or dirty the graph", () => {
    const shotNode = {
      id: "shot-node",
      type: "shot",
      position: { x: 10, y: 20 },
      data: { kind: "shot", shotId: SHOT_ID },
    } as CanvasNode;
    const shots: CanvasShot[] = [
      {
        id: SHOT_ID,
        order: 0,
        imageRevisionId: null,
        videoRevisionId: null,
        referenceRevisionIds: [],
      },
    ];
    loadCanvas([shotNode], [], "revision-1", { shots });

    useCanvasStore.getState().copyNode(shotNode.id);

    const current = useCanvasStore.getState();
    expect(current.nodes).toHaveLength(1);
    expect(current.shots).toEqual(shots);
    expect(current.editSequence).toBe(0);
    expect(current.status).toBe("clean");
  });

  it("adds an empty media node without runtime import state", () => {
    loadCanvas([], [], "revision-1");

    useCanvasStore.getState().addNode("media_input", { x: 5, y: 6 });

    const node = useCanvasStore.getState().nodes[0];
    expect(node).toMatchObject({
      type: "media_input",
      position: { x: 5, y: 6 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    });
    expect(Object.keys(node.data).sort()).toEqual([
      "artifactRevisionId",
      "kind",
      "mediaKind",
    ]);
  });

  it("atomically attaches only a persistent media revision and marks a normal canvas dirty", () => {
    const node = {
      id: "media-node",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadCanvas([node], [], "revision-1");
    const { loadEpoch } = useCanvasStore.getState();

    const attached = useCanvasStore.getState().attachMediaRevision({
      projectId: PROJECT_ID,
      loadEpoch,
      nodeId: node.id,
      expectedRevisionId: null,
      revisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
      file: new File(["private"], "private.png"),
      previewUrl: "blob:http://localhost/private",
      metadata: { localPath: "D:/private.png" },
    } as never);

    const current = useCanvasStore.getState();
    expect(attached).toBe(true);
    expect(current.nodes[0].data).toEqual({
      kind: "media_input",
      artifactRevisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
    });
    expect(Object.keys(current.nodes[0].data).sort()).toEqual([
      "artifactRevisionId",
      "kind",
      "mediaKind",
    ]);
    expect(current.editSequence).toBe(1);
    expect(current.status).toBe("dirty");
  });

  it("treats an identical completion as an exact no-op", () => {
    const node = {
      id: "media-node",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
      },
    } as CanvasNode;
    loadCanvas([node], [], "revision-1");
    const { loadEpoch } = useCanvasStore.getState();

    const attached = useCanvasStore.getState().attachMediaRevision({
      projectId: PROJECT_ID,
      loadEpoch,
      nodeId: node.id,
      expectedRevisionId: IMAGE_REVISION_ID,
      revisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
    });

    expect(attached).toBe(true);
    expect(useCanvasStore.getState().nodes[0]).toBe(node);
    expect(useCanvasStore.getState().editSequence).toBe(0);
    expect(useCanvasStore.getState().status).toBe("clean");
  });

  it("rejects a stale expected-old context even when its result matches the current revision", () => {
    const node = {
      id: "media-node",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: {
        kind: "media_input",
        artifactRevisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
      },
    } as CanvasNode;
    loadCanvas([node], [], "revision-1");
    const before = useCanvasStore.getState();

    const attached = useCanvasStore.getState().attachMediaRevision({
      projectId: PROJECT_ID,
      loadEpoch: before.loadEpoch,
      nodeId: node.id,
      expectedRevisionId: null,
      revisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
    });

    expect(attached).toBe(false);
    expect(useCanvasStore.getState().nodes).toBe(before.nodes);
    expect(useCanvasStore.getState().editSequence).toBe(0);
  });

  it.each([
    ["project changed", { projectId: "99999999-9999-4999-8999-999999999999" }],
    ["epoch changed", { loadEpoch: -1 }],
    ["node removed", { nodeId: "missing" }],
    ["old revision changed", { expectedRevisionId: IMAGE_REVISION_ID }],
  ] as const)("ignores a late attach after %s", (_case, override) => {
    const node = {
      id: "media-node",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadCanvas([node], [], "revision-1");
    const before = useCanvasStore.getState();

    const attached = useCanvasStore.getState().attachMediaRevision({
      projectId: PROJECT_ID,
      loadEpoch: before.loadEpoch,
      nodeId: node.id,
      expectedRevisionId: null,
      revisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
      ...override,
    });

    const current = useCanvasStore.getState();
    expect(attached).toBe(false);
    expect(current.nodes).toBe(before.nodes);
    expect(current.editSequence).toBe(0);
    expect(current.status).toBe("clean");
  });

  it("attaches an already-running import during conflict without clearing conflict state", () => {
    const node = {
      id: "media-node",
      type: "media_input",
      position: { x: 0, y: 0 },
      data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
    } as CanvasNode;
    loadCanvas([node], [], "revision-1");
    const { loadEpoch } = useCanvasStore.getState();
    useCanvasStore.getState().markConflict(loadEpoch, PROJECT_ID, "server-revision");

    const attached = useCanvasStore.getState().attachMediaRevision({
      projectId: PROJECT_ID,
      loadEpoch,
      nodeId: node.id,
      expectedRevisionId: null,
      revisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
    });

    const current = useCanvasStore.getState();
    expect(attached).toBe(true);
    expect(current.editSequence).toBe(1);
    expect(current.nodes[0].data).toEqual({
      kind: "media_input",
      artifactRevisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
    });
    expect(current.status).toBe("conflict");
    expect(current.conflictRevisionId).toBe("server-revision");
  });

  it("ignores unchanged text and marks only a real text edit dirty", () => {
    const store = useCanvasStore.getState();
    loadCanvas([makeNode("node-1", "idea")], [], "revision-1");
    store.failSave(useCanvasStore.getState().loadEpoch, PROJECT_ID, "previous failure");

    store.updateNodeText("node-1", "idea");
    store.updateNodeText("missing", "changed");
    store.deleteNode("missing");
    expect(useCanvasStore.getState().editSequence).toBe(0);
    expect(useCanvasStore.getState().status).toBe("error");

    store.updateNodeText("node-1", "changed");
    const current = useCanvasStore.getState();
    expect(current.nodes[0].data.text).toBe("changed");
    expect(current.editSequence).toBe(1);
    expect(current.status).toBe("dirty");
    expect(current.errorMessage).toBeNull();
    expect(current.conflictRevisionId).toBeNull();
  });

  it("does not count selection, dimensions, or an in-progress drag as edits", () => {
    loadCanvas([makeNode("node-1")], [], "revision-1");

    useCanvasStore.getState().onNodesChange([
      { id: "node-1", type: "select", selected: true },
      {
        id: "node-1",
        type: "dimensions",
        dimensions: { width: 160, height: 80 },
        setAttributes: true,
      },
      { id: "node-1", type: "position", position: { x: 8, y: 12 }, dragging: true },
    ]);

    let current = useCanvasStore.getState();
    expect(current.editSequence).toBe(0);
    expect(current.status).toBe("clean");
    expect(current.nodes[0].position).toEqual({ x: 8, y: 12 });

    useCanvasStore.getState().onNodesChange([
      { id: "node-1", type: "position", position: { x: 9, y: 13 }, dragging: false },
    ]);
    current = useCanvasStore.getState();
    expect(current.editSequence).toBe(1);
    expect(current.status).toBe("dirty");
  });

  it("rejects generic Shot node adds without changing persistent or save state", () => {
    loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
      shots: [makeShot(SHOT_ID, 0)],
    });
    const loaded = useCanvasStore.getState();
    loaded.failSave(loaded.loadEpoch, PROJECT_ID, "previous save failure");
    const before = useCanvasStore.getState();

    useCanvasStore.getState().onNodesChange([
      { type: "add", item: makeShotNode("rogue-shot-node", SECOND_SHOT_ID, 20, 40) },
    ]);

    const current = useCanvasStore.getState();
    expect(current.nodes).toBe(before.nodes);
    expect(current.edges).toBe(before.edges);
    expect(current.shots).toBe(before.shots);
    expect(current.editSequence).toBe(before.editSequence);
    expect(current.savedSequence).toBe(before.savedSequence);
    expect(current.status).toBe(before.status);
    expect(current.conflictRevisionId).toBe(before.conflictRevisionId);
    expect(current.errorMessage).toBe(before.errorMessage);
  });

  it.each([
    [
      "type",
      {
        ...makeNode("node-1", "mismatched type"),
        type: "shot",
      } as unknown as CanvasNode,
    ],
    [
      "data kind",
      {
        ...makeNode("node-1", "mismatched data"),
        data: { kind: "shot", shotId: SECOND_SHOT_ID },
      } as unknown as CanvasNode,
    ],
  ] as const)(
    "rejects a generic replacement whose %s identifies it as a Shot without changing state",
    (_field, replacement) => {
      const source = makeNode("node-1", "original");
      const target = makeNode("node-2", "target");
      loadCanvas([source, target], [makeEdge("edge-1", source.id, target.id)], "revision-1");
      const loaded = useCanvasStore.getState();
      loaded.failSave(loaded.loadEpoch, PROJECT_ID, "previous save failure");
      const before = useCanvasStore.getState();

      useCanvasStore.getState().onNodesChange([
        { type: "replace", id: source.id, item: replacement },
      ]);

      const current = useCanvasStore.getState();
      expect(current.nodes).toBe(before.nodes);
      expect(current.edges).toBe(before.edges);
      expect(current.shots).toBe(before.shots);
      expect(current.editSequence).toBe(before.editSequence);
      expect(current.savedSequence).toBe(before.savedSequence);
      expect(current.status).toBe(before.status);
      expect(current.conflictRevisionId).toBe(before.conflictRevisionId);
      expect(current.errorMessage).toBe(before.errorMessage);
    },
  );

  it("continues to apply generic replacements between ordinary nodes", () => {
    loadCanvas([makeNode("node-1", "original")], [], "revision-1");

    useCanvasStore.getState().onNodesChange([
      { type: "replace", id: "node-1", item: makeNode("node-1", "replacement") },
    ]);

    const current = useCanvasStore.getState();
    expect(current.nodes[0].data).toEqual({ kind: "note", text: "replacement" });
    expect(current.editSequence).toBe(1);
    expect(current.status).toBe("dirty");
  });

  it("counts node add, replace, and remove changes and cascades removed edges", () => {
    const node1 = makeNode("node-1");
    const node2 = makeNode("node-2");
    loadCanvas([node1, node2], [makeEdge("edge-1", "node-1", "node-2")], "revision-1");

    useCanvasStore.getState().onNodesChange([
      { type: "add", item: { ...makeNode("node-3"), position: { x: 30, y: 30 } } },
    ]);
    expect(useCanvasStore.getState().editSequence).toBe(1);

    useCanvasStore.getState().onNodesChange([
      { type: "replace", id: "node-2", item: makeNode("node-2", "replacement") },
    ]);
    expect(useCanvasStore.getState().editSequence).toBe(2);
    expect(useCanvasStore.getState().nodes.find((node) => node.id === "node-2")?.data.text).toBe(
      "replacement",
    );

    useCanvasStore.getState().onNodesChange([{ type: "remove", id: "node-1" }]);
    const current = useCanvasStore.getState();
    expect(current.editSequence).toBe(3);
    expect(current.edges).toEqual([]);
  });

  it("keeps protected target ids through direct remove and replace changes", () => {
    const source = makeNode("protected", "original");
    const other = makeNode("other", "other");
    loadCanvas([source, other], [], "revision-1");

    const guardedChange = useCanvasStore.getState().onNodesChange as unknown as (
      changes: import("@xyflow/react").NodeChange<CanvasNode>[],
      protectedNodeIds: ReadonlySet<string>,
    ) => void;
    guardedChange(
      [
        { id: "protected", type: "remove" },
        {
          id: "protected",
          type: "replace",
          item: { ...source, data: { kind: "note", text: "replacement" } },
        },
        { id: "other", type: "remove" },
      ],
      new Set(["protected"]),
    );

    expect(useCanvasStore.getState().nodes).toEqual([source]);
  });

  it("does not count edge selection but counts edge add, replace, and remove", () => {
    const edge1 = makeEdge("edge-1", "node-1", "node-2");
    loadCanvas([makeNode("node-1"), makeNode("node-2")], [edge1], "revision-1");

    useCanvasStore
      .getState()
      .onEdgesChange([{ type: "select", id: "edge-1", selected: true }]);
    expect(useCanvasStore.getState().editSequence).toBe(0);

    const edge2 = makeEdge("edge-2", "node-2", "node-1");
    useCanvasStore.getState().onEdgesChange([{ type: "add", item: edge2 }]);
    expect(useCanvasStore.getState().editSequence).toBe(1);

    useCanvasStore.getState().onEdgesChange([
      {
        type: "replace",
        id: "edge-2",
        item: { ...edge2, sourceHandle: "alternate" },
      },
    ]);
    expect(useCanvasStore.getState().editSequence).toBe(2);

    useCanvasStore.getState().onEdgesChange([{ type: "remove", id: "edge-1" }]);
    expect(useCanvasStore.getState().editSequence).toBe(3);
  });

  it("connects nodes with a generated edge id and marks the graph dirty", () => {
    loadCanvas([makeNode("node-1"), makeNode("node-2")], [], "revision-1");

    useCanvasStore.getState().onConnect({
      source: "node-1",
      sourceHandle: null,
      target: "node-2",
      targetHandle: null,
    });

    const current = useCanvasStore.getState();
    expect(current.edges).toHaveLength(1);
    expect(current.edges[0]).toMatchObject({ source: "node-1", target: "node-2" });
    expect(current.edges[0].id).toMatch(/^[0-9a-f-]{36}$/i);
    expect(current.editSequence).toBe(1);
    expect(current.status).toBe("dirty");
  });

  it("updates viewport without counting a graph edit", () => {
    loadCanvas([], [], "revision-1");
    useCanvasStore.getState().setViewport({ x: 11, y: 22, zoom: 1.25 });

    const current = useCanvasStore.getState();
    expect(current.viewport).toEqual({ x: 11, y: 22, zoom: 1.25 });
    expect(current.editSequence).toBe(0);
    expect(current.status).toBe("clean");
  });

  it("captures a domain-only snapshot and requires a base revision", () => {
    expect(() => useCanvasStore.getState().beginSave()).toThrow("Canvas has no active project");
    expect(useCanvasStore.getState().status).toBe("clean");

    const node: CanvasNode = {
      ...makeNode("node-1", "idea"),
      position: { x: 4, y: 5 },
      measured: { width: 180, height: 90 },
      selected: true,
      dragging: true,
      style: { opacity: 0.4 },
    };
    const edge: CanvasEdge = {
      ...makeEdge("edge-1", "node-1", "node-2"),
      selected: true,
      style: { strokeWidth: 3 },
    };
    loadCanvas([node, makeNode("node-2")], [edge], "revision-1");
    useCanvasStore
      .getState()
      .failSave(useCanvasStore.getState().loadEpoch, PROJECT_ID, "old error");

    const capture = useCanvasStore.getState().beginSave();
    expect(capture.baseRevisionId).toBe("revision-1");
    expect(capture.projectId).toBe(PROJECT_ID);
    expect(capture.editSequence).toBe(0);
    expect(capture.loadEpoch).toBe(useCanvasStore.getState().loadEpoch);
    expect(capture.snapshot).toEqual({
      schema_version: 3,
      nodes: [
        {
          id: "node-1",
          kind: "note",
          position: { x: 4, y: 5 },
          data: { text: "idea" },
        },
        {
          id: "node-2",
          kind: "note",
          position: { x: 0, y: 0 },
          data: { text: "" },
        },
      ],
      edges: [
        {
          id: "edge-1",
          source: "node-1",
          source_port: "output",
          target: "node-2",
          target_port: "input",
        },
      ],
      shots: [],
      resource_bindings: [],
    });
    expect(useCanvasStore.getState().status).toBe("saving");
    expect(useCanvasStore.getState().errorMessage).toBeNull();
  });

  it("deeply preserves Shots through load and save capture", () => {
    const shotNode = {
      id: "shot-node",
      type: "shot",
      position: { x: 10, y: 20 },
      data: { kind: "shot", shotId: SHOT_ID },
    } as CanvasNode;
    const shots: CanvasShot[] = [
      {
        id: SHOT_ID,
        order: 2,
        imageRevisionId: IMAGE_REVISION_ID,
        videoRevisionId: null,
        referenceRevisionIds: [IMAGE_REVISION_ID],
      },
    ];
    loadCanvas([shotNode], [], "revision-1", { shots });
    shots[0].referenceRevisionIds.push("late-external-change");

    const loaded = useCanvasStore.getState();
    expect(loaded.shots[0].referenceRevisionIds).toEqual([IMAGE_REVISION_ID]);
    const capture = loaded.beginSave();
    expect(capture.snapshot).toEqual({
      schema_version: 3,
      nodes: [
        {
          id: "shot-node",
          kind: "shot",
          position: { x: 10, y: 20 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [],
      shots: [
        {
          id: SHOT_ID,
          order: 2,
          image_revision_id: IMAGE_REVISION_ID,
          video_revision_id: null,
          reference_revision_ids: [IMAGE_REVISION_ID],
        },
      ],
      resource_bindings: [],
    });
    capture.snapshot.shots[0].reference_revision_ids?.push("capture-change");
    expect(useCanvasStore.getState().shots[0].referenceRevisionIds).toEqual([
      IMAGE_REVISION_ID,
    ]);
  });

  it("guards save callbacks by both active project and load epoch", () => {
    loadCanvas([makeNode("a")], [], "revision-a", { projectId: PROJECT_ID });
    useCanvasStore.getState().updateNodeText("a", "pending");
    const capture = useCanvasStore.getState().beginSave();

    loadCanvas([makeNode("b")], [], "revision-b", {
      projectId: "99999999-9999-4999-8999-999999999999",
    });
    const freshEpoch = useCanvasStore.getState().loadEpoch;
    const freshProjectId = useCanvasStore.getState().projectId;
    useCanvasStore
      .getState()
      .completeSave(capture.editSequence, freshEpoch, capture.projectId, "stale-revision");
    useCanvasStore.getState().failSave(freshEpoch, capture.projectId, "stale failure");
    useCanvasStore.getState().markConflict(freshEpoch, capture.projectId, "stale conflict");

    const current = useCanvasStore.getState();
    expect(current.projectId).toBe(freshProjectId);
    expect(current.baseRevisionId).toBe("revision-b");
    expect(current.status).toBe("clean");
    expect(current.errorMessage).toBeNull();
    expect(current.conflictRevisionId).toBeNull();
  });

  describe("media reservation promotion", () => {
    it("atomically creates the reserved media node with the exact id, position, and persistent data", () => {
      loadCanvas([makeNode("existing")], [], "revision-1");
      const before = useCanvasStore.getState();

      const promoted = before.promoteMediaReservation({
        projectId: PROJECT_ID,
        loadEpoch: before.loadEpoch,
        nodeId: "reserved-media",
        position: { x: 120, y: 240 },
        revisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
      });

      const current = useCanvasStore.getState();
      expect(promoted).toBe(true);
      expect(before.nodes).toHaveLength(1);
      expect(current.nodes).toHaveLength(2);
      expect(current.nodes[1]).toEqual({
        id: "reserved-media",
        type: "media_input",
        position: { x: 120, y: 240 },
        data: {
          kind: "media_input",
          artifactRevisionId: IMAGE_REVISION_ID,
          mediaKind: "image",
        },
      });
      expect(Object.keys(current.nodes[1].data).sort()).toEqual([
        "artifactRevisionId",
        "kind",
        "mediaKind",
      ]);
      expect(current.editSequence).toBe(before.editSequence + 1);
      expect(current.status).toBe("dirty");
    });

    it.each([
      ["duplicate id", { nodeId: "existing" }],
      ["wrong project", { projectId: "other-project" }],
      ["stale epoch", { loadEpoch: -1 }],
      ["empty revision", { revisionId: "" }],
      ["invalid kind", { mediaKind: "audio" }],
    ] as const)("rejects %s without changing any graph state", (_label, override) => {
      loadCanvas([makeNode("existing")], [], "revision-1");
      const before = useCanvasStore.getState();

      const promoted = before.promoteMediaReservation({
        projectId: PROJECT_ID,
        loadEpoch: before.loadEpoch,
        nodeId: "reserved-media",
        position: { x: 120, y: 240 },
        revisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
        ...override,
      } as never);

      const current = useCanvasStore.getState();
      expect(promoted).toBe(false);
      expect(current.nodes).toBe(before.nodes);
      expect(current.edges).toBe(before.edges);
      expect(current.editSequence).toBe(before.editSequence);
      expect(current.status).toBe(before.status);
    });

    it("preserves a conflict that appeared after admission while promoting the completed media", () => {
      loadCanvas([], [], "revision-1");
      const { loadEpoch } = useCanvasStore.getState();
      useCanvasStore.getState().markConflict(loadEpoch, PROJECT_ID, "server-revision");
      useCanvasStore.setState({ errorMessage: "conflict detail" });

      const promoted = useCanvasStore.getState().promoteMediaReservation({
        projectId: PROJECT_ID,
        loadEpoch,
        nodeId: "reserved-media",
        position: { x: 5, y: 7 },
        revisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
      });

      const current = useCanvasStore.getState();
      expect(promoted).toBe(true);
      expect(current.status).toBe("conflict");
      expect(current.conflictRevisionId).toBe("server-revision");
      expect(current.errorMessage).toBe("conflict detail");
      expect(current.editSequence).toBe(1);
      expect(current.nodes).toHaveLength(1);
    });
  });

  describe("atomic Shot operations", () => {
    it("creates one top-level Shot node after a target and appends when no target is given", () => {
      loadCanvas(
        [makeShotNode("shot-node-a", SHOT_ID, 4, 8)],
        [],
        "revision-1",
        { shots: [makeShot(SHOT_ID, 0)] },
      );

      const first = useCanvasStore
        .getState()
        .createShot({ afterShotId: SHOT_ID, position: { x: 20, y: 40 } });
      const second = useCanvasStore
        .getState()
        .createShot({ afterShotId: null, position: { x: 60, y: 80 } });

      expect(first).not.toBeNull();
      expect(second).not.toBeNull();
      const current = useCanvasStore.getState();
      expect(current.selectedShotId).toBeNull();
      expect(current.shots.map((shot) => [shot.id, shot.order])).toEqual([
        [SHOT_ID, 0],
        [first!.shotId, 1],
        [second!.shotId, 2],
      ]);
      expect(current.nodes.filter((node) => node.data.kind === "shot")).toEqual([
        makeShotNode("shot-node-a", SHOT_ID, 4, 8),
        {
          id: first!.nodeId,
          type: "shot",
          deletable: false,
          position: { x: 20, y: 40 },
          data: { kind: "shot", shotId: first!.shotId },
        },
        {
          id: second!.nodeId,
          type: "shot",
          deletable: false,
          position: { x: 60, y: 80 },
          data: { kind: "shot", shotId: second!.shotId },
        },
      ]);
      expect(current.edges).toEqual([]);
    });

    it("rejects a non-UUID revision through setShotSlot without changing state", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0)],
      });
      const before = useCanvasStore.getState();

      expect(
        useCanvasStore.getState().setShotSlot(SHOT_ID, "image", {
          revisionId: "not-a-uuid",
          mediaKind: "image",
        }),
      ).toBe(false);

      const current = useCanvasStore.getState();
      expect(current.nodes).toBe(before.nodes);
      expect(current.edges).toBe(before.edges);
      expect(current.shots).toBe(before.shots);
      expect(current.editSequence).toBe(before.editSequence);
      expect(current.savedSequence).toBe(before.savedSequence);
      expect(current.status).toBe(before.status);
      expect(current.conflictRevisionId).toBe(before.conflictRevisionId);
      expect(current.errorMessage).toBe(before.errorMessage);
    });

    it("sets an image or video slot only when its expected revision is still current", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0, { imageRevisionId: IMAGE_REVISION_ID })],
      });
      const store = useCanvasStore.getState() as ReturnType<typeof useCanvasStore.getState> & {
        setShotSlotIfCurrent?: (
          shotId: string,
          slot: "image" | "video",
          expectedRevisionId: string | null,
          revision: { revisionId: string; mediaKind: "image" | "video" } | null,
        ) => boolean;
      };
      const setShotSlotIfCurrent = store.setShotSlotIfCurrent;

      expect(setShotSlotIfCurrent).toBeTypeOf("function");
      if (!setShotSlotIfCurrent) return;

      const beforeRejected = useCanvasStore.getState();
      expect(
        setShotSlotIfCurrent(SHOT_ID, "image", VIDEO_REVISION_ID, {
          revisionId: generatedRevisionId(41),
          mediaKind: "image",
        }),
      ).toBe(false);
      expect(useCanvasStore.getState().shots).toBe(beforeRejected.shots);

      expect(
        setShotSlotIfCurrent(SHOT_ID, "image", IMAGE_REVISION_ID, {
          revisionId: generatedRevisionId(42),
          mediaKind: "image",
        }),
      ).toBe(true);
      expect(useCanvasStore.getState().shots[0]!.imageRevisionId).toBe(
        generatedRevisionId(42),
      );
    });

    it("rejects a non-UUID revision through addShotReference without changing state", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0)],
      });
      const before = useCanvasStore.getState();

      expect(
        useCanvasStore.getState().addShotReference(SHOT_ID, {
          revisionId: "not-a-uuid",
          mediaKind: "image",
        }),
      ).toBe(false);

      const current = useCanvasStore.getState();
      expect(current.nodes).toBe(before.nodes);
      expect(current.edges).toBe(before.edges);
      expect(current.shots).toBe(before.shots);
      expect(current.editSequence).toBe(before.editSequence);
      expect(current.savedSequence).toBe(before.savedSequence);
      expect(current.status).toBe(before.status);
      expect(current.conflictRevisionId).toBe(before.conflictRevisionId);
      expect(current.errorMessage).toBe(before.errorMessage);
    });

    it("rejects a non-UUID revision through replaceShotReference without changing state", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0, { referenceRevisionIds: [REFERENCE_REVISION_ID] })],
      });
      const before = useCanvasStore.getState();

      expect(
        useCanvasStore.getState().replaceShotReference(SHOT_ID, REFERENCE_REVISION_ID, {
          revisionId: "not-a-uuid",
          mediaKind: "image",
        }),
      ).toBe(false);

      const current = useCanvasStore.getState();
      expect(current.nodes).toBe(before.nodes);
      expect(current.edges).toBe(before.edges);
      expect(current.shots).toBe(before.shots);
      expect(current.editSequence).toBe(before.editSequence);
      expect(current.savedSequence).toBe(before.savedSequence);
      expect(current.status).toBe(before.status);
      expect(current.conflictRevisionId).toBe(before.conflictRevisionId);
      expect(current.errorMessage).toBe(before.errorMessage);
    });

    it("deletes a Shot atomically with its node and incident edges, then selects its next neighbour", () => {
      const media = {
        id: "media-node",
        type: "media_input",
        position: { x: 0, y: 0 },
        data: {
          kind: "media_input" as const,
          artifactRevisionId: IMAGE_REVISION_ID,
          mediaKind: "image" as const,
        },
      } as CanvasNode;
      loadCanvas(
        [
          makeShotNode("shot-node-a", SHOT_ID),
          makeShotNode("shot-node-b", SECOND_SHOT_ID),
          makeShotNode("shot-node-c", THIRD_SHOT_ID),
          media,
        ],
        [
          makeEdge("edge-a-b", "shot-node-a", "shot-node-b"),
          makeEdge("edge-b-c", "shot-node-b", "shot-node-c"),
          makeEdge("edge-a-media", "shot-node-a", "media-node"),
        ],
        "revision-1",
        {
          shots: [
            makeShot(SHOT_ID, 0),
            makeShot(SECOND_SHOT_ID, 1),
            makeShot(THIRD_SHOT_ID, 2),
          ],
        },
      );
      useCanvasStore.getState().selectShot(SECOND_SHOT_ID);
      useCanvasStore.getState().beginShotAssociation(SECOND_SHOT_ID, "reference");

      expect(useCanvasStore.getState().deleteShot(SECOND_SHOT_ID)).toBe(THIRD_SHOT_ID);

      const current = useCanvasStore.getState();
      expect(current.selectedShotId).toBe(THIRD_SHOT_ID);
      expect(current.associationTarget).toBeNull();
      expect(current.shots.map((shot) => [shot.id, shot.order])).toEqual([
        [SHOT_ID, 0],
        [THIRD_SHOT_ID, 1],
      ]);
      expect(current.nodes.map((node) => node.id)).toEqual([
        "shot-node-a",
        "shot-node-c",
        "media-node",
      ]);
      expect(current.edges).toEqual([makeEdge("edge-a-media", "shot-node-a", "media-node")]);
    });

    it("reorders complete Shot sets and moves only within their boundaries", () => {
      loadCanvas(
        [
          makeShotNode("shot-node-a", SHOT_ID),
          makeShotNode("shot-node-b", SECOND_SHOT_ID),
          makeShotNode("shot-node-c", THIRD_SHOT_ID),
        ],
        [],
        "revision-1",
        {
          shots: [
            makeShot(SHOT_ID, 0),
            makeShot(SECOND_SHOT_ID, 1),
            makeShot(THIRD_SHOT_ID, 2),
          ],
        },
      );
      const beforeInvalid = useCanvasStore.getState();
      expect(useCanvasStore.getState().reorderShots([SHOT_ID, SHOT_ID, THIRD_SHOT_ID])).toBe(false);
      expect(useCanvasStore.getState().shots).toBe(beforeInvalid.shots);
      expect(useCanvasStore.getState().reorderShots([THIRD_SHOT_ID, SHOT_ID, SECOND_SHOT_ID])).toBe(
        true,
      );
      expect(useCanvasStore.getState().shots.map((shot) => [shot.id, shot.order])).toEqual([
        [THIRD_SHOT_ID, 0],
        [SHOT_ID, 1],
        [SECOND_SHOT_ID, 2],
      ]);
      const beforeBoundary = useCanvasStore.getState();
      expect(useCanvasStore.getState().moveShot(THIRD_SHOT_ID, -1)).toBe(false);
      expect(useCanvasStore.getState().shots).toBe(beforeBoundary.shots);
      expect(useCanvasStore.getState().moveShot(SHOT_ID, 1)).toBe(true);
      expect(useCanvasStore.getState().shots.map((shot) => shot.id)).toEqual([
        THIRD_SHOT_ID,
        SECOND_SHOT_ID,
        SHOT_ID,
      ]);
    });

    it.each([
      ["image accepts image", "image", { revisionId: IMAGE_REVISION_ID, mediaKind: "image" }, true],
      ["image rejects video", "image", { revisionId: VIDEO_REVISION_ID, mediaKind: "video" }, false],
      ["video accepts video", "video", { revisionId: VIDEO_REVISION_ID, mediaKind: "video" }, true],
      ["video rejects image", "video", { revisionId: IMAGE_REVISION_ID, mediaKind: "image" }, false],
    ] as const)("%s revisions", (_label, slot, revision, accepted) => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0)],
      });

      expect(useCanvasStore.getState().setShotSlot(SHOT_ID, slot, revision)).toBe(accepted);

      const shot = useCanvasStore.getState().shots[0];
      expect(slot === "image" ? shot.imageRevisionId : shot.videoRevisionId).toBe(
        accepted ? revision.revisionId : null,
      );
    });

    it("clears only image or video slots and rejects malformed or incompatible revisions", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [
          makeShot(SHOT_ID, 0, {
            imageRevisionId: IMAGE_REVISION_ID,
            videoRevisionId: VIDEO_REVISION_ID,
          }),
        ],
      });
      const store = useCanvasStore.getState();
      expect(store.setShotSlot(SHOT_ID, "image", null)).toBe(true);
      expect(store.setShotSlot(SHOT_ID, "video", null)).toBe(true);
      const beforeInvalid = useCanvasStore.getState();
      expect(
        store.setShotSlot(SHOT_ID, "image", {
          revisionId: "",
          mediaKind: "image",
        }),
      ).toBe(false);
      expect(
        store.setShotSlot(SHOT_ID, "reference" as never, null as never),
      ).toBe(false);
      expect(useCanvasStore.getState().shots).toBe(beforeInvalid.shots);
    });

    it("associates only unique image references, bounds them to 64, and replaces or reorders complete sets", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0)],
      });
      const store = useCanvasStore.getState();
      expect(
        store.addShotReference(SHOT_ID, {
          revisionId: REFERENCE_REVISION_ID,
          mediaKind: "image",
        }),
      ).toBe(true);
      expect(
        store.addShotReference(SHOT_ID, {
          revisionId: VIDEO_REVISION_ID,
          mediaKind: "video",
        }),
      ).toBe(false);
      expect(
        store.addShotReference(SHOT_ID, {
          revisionId: REFERENCE_REVISION_ID,
          mediaKind: "image",
        }),
      ).toBe(false);
      expect(
        store.addShotReference(
          SHOT_ID,
          { revisionId: IMAGE_REVISION_ID, mediaKind: "image" },
          0,
        ),
      ).toBe(true);
      expect(useCanvasStore.getState().shots[0].referenceRevisionIds).toEqual([
        IMAGE_REVISION_ID,
        REFERENCE_REVISION_ID,
      ]);
      expect(
        store.replaceShotReference(SHOT_ID, IMAGE_REVISION_ID, {
          revisionId: REFERENCE_REVISION_ID,
          mediaKind: "image",
        }),
      ).toBe(false);
      expect(
        store.replaceShotReference(SHOT_ID, IMAGE_REVISION_ID, {
          revisionId: generatedRevisionId(1),
          mediaKind: "image",
        }),
      ).toBe(true);
      expect(
        store.reorderShotReferences(SHOT_ID, [REFERENCE_REVISION_ID, generatedRevisionId(1)]),
      ).toBe(true);
      const beforeBadOrder = useCanvasStore.getState();
      expect(
        store.reorderShotReferences(SHOT_ID, [REFERENCE_REVISION_ID, REFERENCE_REVISION_ID]),
      ).toBe(false);
      expect(useCanvasStore.getState().shots).toBe(beforeBadOrder.shots);

      for (let index = 2; index <= 63; index += 1) {
        expect(
          store.addShotReference(SHOT_ID, {
            revisionId: generatedRevisionId(index),
            mediaKind: "image",
          }),
        ).toBe(true);
      }
      expect(useCanvasStore.getState().shots[0].referenceRevisionIds).toHaveLength(64);
      const beforeOverLimit = useCanvasStore.getState();
      expect(
        store.addShotReference(SHOT_ID, {
          revisionId: generatedRevisionId(65),
          mediaKind: "image",
        }),
      ).toBe(false);
      expect(useCanvasStore.getState().shots).toBe(beforeOverLimit.shots);
      expect(store.removeShotReference(SHOT_ID, REFERENCE_REVISION_ID)).toBe(true);
      expect(useCanvasStore.getState().shots[0].referenceRevisionIds).not.toContain(
        REFERENCE_REVISION_ID,
      );
    });

    it("keeps selection and association transient across conflicts, load, reset, and save capture", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0)],
      });
      const initial = useCanvasStore.getState();
      initial.selectShot(SHOT_ID);
      initial.beginShotAssociation(SHOT_ID, "reference");
      expect(useCanvasStore.getState().selectedShotId).toBe(SHOT_ID);
      expect(useCanvasStore.getState().associationTarget).toEqual({
        shotId: SHOT_ID,
        slot: "reference",
      });
      expect(useCanvasStore.getState().editSequence).toBe(initial.editSequence);
      const capture = useCanvasStore.getState().beginSave();
      expect(capture.snapshot).not.toHaveProperty("selectedShotId");
      expect(capture.snapshot).not.toHaveProperty("associationTarget");
      expect(JSON.stringify(capture.snapshot)).not.toContain("associationTarget");

      useCanvasStore
        .getState()
        .markConflict(capture.loadEpoch, capture.projectId, "server-revision");
      const conflicted = useCanvasStore.getState();
      expect(conflicted.createShot({ afterShotId: null, position: { x: 0, y: 0 } })).toBeNull();
      expect(conflicted.deleteShot(SHOT_ID)).toBeNull();
      expect(conflicted.reorderShots([SHOT_ID])).toBe(false);
      expect(conflicted.setShotSlot(SHOT_ID, "image", {
        revisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
      })).toBe(false);
      expect(conflicted.shots).toBe(initial.shots);

      loadCanvas([makeShotNode("other-shot-node", SECOND_SHOT_ID)], [], "revision-2", {
        shots: [makeShot(SECOND_SHOT_ID, 0)],
      });
      expect(useCanvasStore.getState().selectedShotId).toBeNull();
      expect(useCanvasStore.getState().associationTarget).toBeNull();
      useCanvasStore.getState().selectShot(SECOND_SHOT_ID);
      useCanvasStore.getState().beginShotAssociation(SECOND_SHOT_ID, "image");
      useCanvasStore.getState().reset();
      expect(useCanvasStore.getState().selectedShotId).toBeNull();
      expect(useCanvasStore.getState().associationTarget).toBeNull();
    });

    it("clears dangling selection or association input without graph edits", () => {
      loadCanvas([makeShotNode("shot-node", SHOT_ID)], [], "revision-1", {
        shots: [makeShot(SHOT_ID, 0)],
      });
      const store = useCanvasStore.getState();
      store.selectShot(SHOT_ID);
      store.beginShotAssociation(SHOT_ID, "video");
      const beforeInvalid = useCanvasStore.getState();
      store.selectShot("missing-shot");
      expect(useCanvasStore.getState().selectedShotId).toBeNull();
      store.beginShotAssociation("missing-shot", "image");
      expect(useCanvasStore.getState().associationTarget).toBeNull();
      expect(useCanvasStore.getState().nodes).toBe(beforeInvalid.nodes);
      expect(useCanvasStore.getState().shots).toBe(beforeInvalid.shots);
      expect(useCanvasStore.getState().editSequence).toBe(beforeInvalid.editSequence);
    });
  });
});
