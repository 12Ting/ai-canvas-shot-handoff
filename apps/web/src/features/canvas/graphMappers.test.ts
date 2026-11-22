import { describe, expect, it } from "vitest";
import { fromGraphSnapshot, toGraphSnapshot } from "./graphMappers";
import type { CanvasEdge, CanvasNode, CanvasShot } from "./canvasTypes";

const TEXT_NODE_ID = "11111111-1111-4111-8111-111111111111";
const MEDIA_NODE_ID = "22222222-2222-4222-8222-222222222222";
const SHOT_NODE_ID = "33333333-3333-4333-8333-333333333333";
const SHOT_ID = "44444444-4444-4444-8444-444444444444";
const IMAGE_REVISION_ID = "55555555-5555-4555-8555-555555555555";
const VIDEO_REVISION_ID = "66666666-6666-4666-8666-666666666666";
const REFERENCE_REVISION_ID = "77777777-7777-4777-8777-777777777777";
const PROMPT_NODE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

const invalidHandleCases = (["sourceHandle", "targetHandle"] as const).flatMap((field) =>
  [
    ["missing", undefined],
    ["null", null],
    ["empty", ""],
    ["whitespace", "   "],
    ["invalid", "invalid/port"],
    ["too long", "x".repeat(65)],
  ].map(([description, value]) => [field, description, value] as const),
);

describe("graph mappers", () => {
  it("loads a V2 prompt as a V3 freeform block without text drift", () => {
    const text = "原文🙂\n第二行";
    const mapped = fromGraphSnapshot({
      schema_version: 2,
      nodes: [
        {
          id: PROMPT_NODE_ID,
          kind: "prompt",
          position: { x: 3, y: 4 },
          data: { text },
        },
      ],
      edges: [],
      shots: [],
    });

    expect(mapped.nodes[0].data).toMatchObject({
      kind: "prompt",
      document: {
        schema_version: 1,
        rendered_text: text,
        blocks: [expect.objectContaining({ kind: "FREEFORM", text })],
      },
    });
    expect(mapped.resourceBindings).toEqual([]);
  });

  it("deterministically upgrades V1 to isolated internal V2 state", () => {
    const snapshot = {
      schema_version: 1 as const,
      nodes: [
        {
          id: TEXT_NODE_ID,
          kind: "note" as const,
          position: { x: 12, y: 34 },
          data: { text: "idea" },
        },
      ],
      edges: [],
    };

    const first = fromGraphSnapshot(snapshot);
    const second = fromGraphSnapshot(snapshot);

    expect(first).toEqual(second);
    expect(first.shots).toEqual([]);
    expect(first.nodes[0]).toEqual({
      id: TEXT_NODE_ID,
      type: "note",
      position: { x: 12, y: 34 },
      data: { kind: "note", text: "idea" },
    });
    expect(first.nodes[0].position).not.toBe(snapshot.nodes[0].position);
    expect(first.nodes[0].data).not.toBe(snapshot.nodes[0].data);

    first.nodes[0].position.x = 99;
    if (first.nodes[0].data.kind === "note") first.nodes[0].data.text = "changed";
    expect(snapshot).toEqual({
      schema_version: 1,
      nodes: [
        {
          id: TEXT_NODE_ID,
          kind: "note",
          position: { x: 12, y: 34 },
          data: { text: "idea" },
        },
      ],
      edges: [],
    });
  });

  it("loads V2 text/media/Shot data and saves an exact V3 upgrade", () => {
    const snapshot = {
      schema_version: 2 as const,
      nodes: [
        {
          id: TEXT_NODE_ID,
          kind: "text_source" as const,
          position: { x: 12, y: 34 },
          data: { text: "source" },
        },
        {
          id: PROMPT_NODE_ID,
          kind: "prompt" as const,
          position: { x: 40, y: 60 },
          data: { text: "expand" },
        },
        {
          id: MEDIA_NODE_ID,
          kind: "media_input" as const,
          position: { x: 80, y: 120 },
          data: { artifact_revision_id: IMAGE_REVISION_ID, media_kind: "image" as const },
        },
        {
          id: SHOT_NODE_ID,
          kind: "shot" as const,
          position: { x: 160, y: 220 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [
        {
          id: "88888888-8888-4888-8888-888888888888",
          source: TEXT_NODE_ID,
          source_port: "text",
          target: PROMPT_NODE_ID,
          target_port: "text",
        },
      ],
      shots: [
        {
          id: SHOT_ID,
          order: 7,
          image_revision_id: IMAGE_REVISION_ID,
          video_revision_id: VIDEO_REVISION_ID,
          reference_revision_ids: [REFERENCE_REVISION_ID, IMAGE_REVISION_ID],
        },
      ],
    };

    const canvas = fromGraphSnapshot(snapshot);
    expect(canvas.nodes[2].data).toEqual({
      kind: "media_input",
      artifactRevisionId: IMAGE_REVISION_ID,
      mediaKind: "image",
    });
    expect(canvas.nodes[3].data).toEqual({ kind: "shot", shotId: SHOT_ID });
    expect(canvas.shots).toEqual([
      {
        id: SHOT_ID,
        order: 7,
        imageRevisionId: IMAGE_REVISION_ID,
        videoRevisionId: VIDEO_REVISION_ID,
        referenceRevisionIds: [REFERENCE_REVISION_ID, IMAGE_REVISION_ID],
      },
    ]);

    canvas.nodes[0].measured = { width: 180, height: 90 };
    canvas.nodes[0].selected = true;
    canvas.edges[0].selected = true;
    expect(toGraphSnapshot(canvas.nodes, canvas.edges, canvas.shots)).toEqual({
      ...snapshot,
      schema_version: 3,
      nodes: snapshot.nodes.map((node) =>
        node.kind !== "prompt"
          ? node
          : {
              ...node,
              data: {
                document: {
                  schema_version: 1,
                  blocks: [
                    {
                      id: PROMPT_NODE_ID,
                      kind: "FREEFORM",
                      text: "expand",
                      locked: false,
                    },
                  ],
                  rendered_text: "expand",
                  spans: [
                    {
                      block_id: PROMPT_NODE_ID,
                      start_utf16: 0,
                      end_utf16: 6,
                    },
                  ],
                },
              },
            },
      ),
      resource_bindings: [],
    });
  });

  it("normalizes omitted V2 media and Shot defaults without mutating the response", () => {
    const snapshot = {
      schema_version: 2 as const,
      nodes: [
        {
          id: MEDIA_NODE_ID,
          kind: "media_input" as const,
          position: { x: 1, y: 2 },
          data: {},
        },
        {
          id: SHOT_NODE_ID,
          kind: "shot" as const,
          position: { x: 3, y: 4 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [],
      shots: [{ id: SHOT_ID, order: 0 }],
    };

    const canvas = fromGraphSnapshot(snapshot);
    expect(canvas.nodes[0].data).toEqual({
      kind: "media_input",
      artifactRevisionId: null,
      mediaKind: null,
    });
    expect(canvas.shots[0]).toEqual({
      id: SHOT_ID,
      order: 0,
      imageRevisionId: null,
      videoRevisionId: null,
      referenceRevisionIds: [],
    });
    expect(snapshot.nodes[0].data).toEqual({});
    expect(snapshot.shots[0]).toEqual({ id: SHOT_ID, order: 0 });
  });

  it("round-trips V3 prompt documents and exact resource revision bindings", () => {
    const blockId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const resourceRevisionId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const bindingId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const snapshot = {
      schema_version: 3 as const,
      nodes: [
        {
          id: PROMPT_NODE_ID,
          kind: "prompt" as const,
          position: { x: 1, y: 2 },
          data: {
            document: {
              schema_version: 1 as const,
              blocks: [
                { id: blockId, kind: "SUBJECT" as const, text: "人物🙂", locked: true },
              ],
              rendered_text: "人物🙂",
              spans: [{ block_id: blockId, start_utf16: 0, end_utf16: 4 }],
            },
          },
        },
      ],
      edges: [],
      shots: [],
      resource_bindings: [
        {
          id: bindingId,
          resource_kind: "STYLE" as const,
          binding_key: "visual_style",
          scope_kind: "NODE" as const,
          scope_id: PROMPT_NODE_ID,
          resource_revision_id: resourceRevisionId,
        },
      ],
    };

    const canvas = fromGraphSnapshot(snapshot);
    expect(canvas.resourceBindings).toEqual([
      {
        id: bindingId,
        resourceKind: "STYLE",
        bindingKey: "visual_style",
        scopeKind: "NODE",
        scopeId: PROMPT_NODE_ID,
        resourceRevisionId,
      },
    ]);
    expect(
      toGraphSnapshot(canvas.nodes, canvas.edges, canvas.shots, canvas.resourceBindings),
    ).toEqual(snapshot);
  });

  it("persists only graph fields and never File, Blob URL, metadata, token, or local paths", () => {
    const file = new File(["image"], "private.png", { type: "image/png" });
    const node = {
      id: MEDIA_NODE_ID,
      type: "media_input",
      position: { x: 1, y: 2 },
      data: {
        kind: "media_input",
        artifactRevisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
        file,
        previewUrl: "blob:http://localhost/private",
        metadata: { width: 1, height: 1 },
        token: "secret",
        localPath: "C:\\private.png",
      },
      measured: { width: 120, height: 60 },
      selected: true,
    } as unknown as CanvasNode;

    const snapshot = toGraphSnapshot([node], [], []);
    expect(snapshot).toEqual({
      schema_version: 3,
      nodes: [
        {
          id: MEDIA_NODE_ID,
          kind: "media_input",
          position: { x: 1, y: 2 },
          data: {
            artifact_revision_id: IMAGE_REVISION_ID,
            media_kind: "image",
          },
        },
      ],
      edges: [],
      shots: [],
      resource_bindings: [],
    });
    const serialized = JSON.stringify(snapshot);
    expect(serialized).not.toContain("blob:");
    expect(serialized).not.toContain("secret");
    expect(serialized).not.toContain("private.png");
    expect(serialized).not.toContain("metadata");
  });

  it("drops nested ingress reservations and runtime handles from a promoted media node", () => {
    const node = {
      id: MEDIA_NODE_ID,
      type: "media_input",
      position: { x: 1, y: 2 },
      data: {
        kind: "media_input",
        artifactRevisionId: IMAGE_REVISION_ID,
        mediaKind: "image",
        batch: { id: "batch-private", file: new File(["raw"], "local.png") },
        slot: { id: "slot-private", reservation: { nodeId: "reservation-private" } },
        reservation: { nodeId: "reservation-private", localPath: "C:/private.png" },
        controller: new AbortController(),
      },
    } as unknown as CanvasNode;

    const snapshot = toGraphSnapshot([node], [], []);

    expect(snapshot.nodes[0]).toEqual({
      id: MEDIA_NODE_ID,
      kind: "media_input",
      position: { x: 1, y: 2 },
      data: {
        artifact_revision_id: IMAGE_REVISION_ID,
        media_kind: "image",
      },
    });
    expect(JSON.stringify(snapshot)).not.toContain("batch-private");
    expect(JSON.stringify(snapshot)).not.toContain("reservation-private");
    expect(JSON.stringify(snapshot)).not.toContain("local.png");
  });

  it.each([
    ["unknown kind", { id: TEXT_NODE_ID, kind: "unknown", position: { x: 0, y: 0 }, data: {} }],
    [
      "numeric text",
      { id: TEXT_NODE_ID, kind: "note", position: { x: 0, y: 0 }, data: { text: 42 } },
    ],
    [
      "mismatched media pair",
      {
        id: MEDIA_NODE_ID,
        kind: "media_input",
        position: { x: 0, y: 0 },
        data: { artifact_revision_id: IMAGE_REVISION_ID, media_kind: null },
      },
    ],
    [
      "malformed Shot data",
      { id: SHOT_NODE_ID, kind: "shot", position: { x: 0, y: 0 }, data: { text: SHOT_ID } },
    ],
  ])("rejects %s instead of coercing malformed node data", (_case, node) => {
    expect(() =>
      fromGraphSnapshot({
        schema_version: 2,
        nodes: [node],
        edges: [],
        shots: [],
      } as never),
    ).toThrow();
  });

  it("rejects malformed internal text instead of applying String coercion", () => {
    const node = {
      id: TEXT_NODE_ID,
      type: "note",
      position: { x: 1, y: 2 },
      data: { kind: "note", text: 42 },
    } as unknown as CanvasNode;

    expect(() => toGraphSnapshot([node], [], [])).toThrow(/text/i);
  });

  it("rejects an unknown internal kind even when the runtime type matches it", () => {
    const node = {
      id: TEXT_NODE_ID,
      type: "unknown",
      position: { x: 1, y: 2 },
      data: { kind: "unknown" },
    } as unknown as CanvasNode;

    expect(() => toGraphSnapshot([node], [], [])).toThrow(/kind/i);
  });

  it.each([
    [
      "null Shot identity",
      {
        nodeShotId: null,
        shotId: null,
        references: [],
      },
    ],
    [
      "null Shot reference",
      {
        nodeShotId: SHOT_ID,
        shotId: SHOT_ID,
        references: [null],
      },
    ],
  ])("rejects %s instead of serializing null as a required id", (_case, malformed) => {
    const node = {
      id: SHOT_NODE_ID,
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: malformed.nodeShotId },
    } as unknown as CanvasNode;
    const shot = {
      id: malformed.shotId,
      order: 0,
      imageRevisionId: null,
      videoRevisionId: null,
      referenceRevisionIds: malformed.references,
    } as unknown as CanvasShot;

    expect(() => toGraphSnapshot([node], [], [shot])).toThrow(/id|reference/i);
  });

  it.each([
    ["single-hole", new Array<string>(1)],
    ["multiple-hole", new Array<string>(3)],
  ])("rejects a %s sparse internal Shot reference collection", (_case, references) => {
    const node: CanvasNode = {
      id: SHOT_NODE_ID,
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: SHOT_ID },
    };
    const shot: CanvasShot = {
      id: SHOT_ID,
      order: 0,
      imageRevisionId: null,
      videoRevisionId: null,
      referenceRevisionIds: references,
    };

    expect(() => toGraphSnapshot([node], [], [shot])).toThrow(/reference/i);
  });

  it("isolates mutable Shot records in both directions", () => {
    const shots: CanvasShot[] = [
      {
        id: SHOT_ID,
        order: 0,
        imageRevisionId: IMAGE_REVISION_ID,
        videoRevisionId: null,
        referenceRevisionIds: [REFERENCE_REVISION_ID],
      },
    ];
    const shotNode: CanvasNode = {
      id: SHOT_NODE_ID,
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: SHOT_ID },
    };
    const mapped = toGraphSnapshot([shotNode], [], shots);
    expect(mapped.shots[0]).not.toBe(shots[0]);
    expect(mapped.shots[0].reference_revision_ids).not.toBe(shots[0].referenceRevisionIds);

    mapped.shots[0].reference_revision_ids?.push(IMAGE_REVISION_ID);
    expect(shots[0].referenceRevisionIds).toEqual([REFERENCE_REVISION_ID]);
  });

  it("emits only GraphSnapshotV3 fields when runtime objects carry transient Shot UI state", () => {
    const shotNode = {
      id: SHOT_NODE_ID,
      type: "shot",
      position: { x: 0, y: 0 },
      data: { kind: "shot", shotId: SHOT_ID },
      selectedShotId: SHOT_ID,
      associationTarget: { shotId: SHOT_ID, slot: "reference" },
    } as unknown as CanvasNode;
    const snapshot = toGraphSnapshot(
      [shotNode],
      [],
      [
        {
          id: SHOT_ID,
          order: 0,
          imageRevisionId: null,
          videoRevisionId: null,
          referenceRevisionIds: [],
        },
      ],
    );

    expect(snapshot).toEqual({
      schema_version: 3,
      nodes: [
        {
          id: SHOT_NODE_ID,
          kind: "shot",
          position: { x: 0, y: 0 },
          data: { shot_id: SHOT_ID },
        },
      ],
      edges: [],
      shots: [
        {
          id: SHOT_ID,
          order: 0,
          image_revision_id: null,
          video_revision_id: null,
          reference_revision_ids: [],
        },
      ],
      resource_bindings: [],
    });
    expect(JSON.stringify(snapshot)).not.toContain("associationTarget");
    expect(JSON.stringify(snapshot)).not.toContain("selectedShotId");
  });

  it.each(invalidHandleCases)(
    "rejects a %s that is %s before building a snapshot",
    (field, _description, value) => {
      const edge: CanvasEdge = {
        id: "99999999-9999-4999-8999-999999999999",
        source: TEXT_NODE_ID,
        sourceHandle: "output",
        target: MEDIA_NODE_ID,
        targetHandle: "input",
      };
      if (value === undefined) {
        delete edge[field];
      } else {
        edge[field] = value;
      }

      expect(() => toGraphSnapshot([], [edge], [])).toThrow(
        `Invalid ${field} for edge ${edge.id}`,
      );
    },
  );
});
