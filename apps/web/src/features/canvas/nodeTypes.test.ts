import { describe, expect, it } from "vitest";
import type { CanvasNode } from "./canvasTypes";
import { documentFromText } from "../prompting/promptDocument";
import { isCanvasConnectionValid, nodePorts, nodeTypes } from "./nodeTypes";

const nodes: CanvasNode[] = [
  {
    id: "source",
    type: "text_source",
    position: { x: 0, y: 0 },
    data: { kind: "text_source", text: "" },
  },
  {
    id: "prompt",
    type: "prompt",
    position: { x: 100, y: 0 },
    data: {
      kind: "prompt",
      document: documentFromText("", "11111111-1111-4111-8111-111111111111"),
    },
  },
  {
    id: "note",
    type: "note",
    position: { x: 200, y: 0 },
    data: { kind: "note", text: "" },
  },
  {
    id: "media",
    type: "media_input",
    position: { x: 300, y: 0 },
    data: { kind: "media_input", artifactRevisionId: null, mediaKind: null },
  },
  {
    id: "shot",
    type: "shot",
    position: { x: 400, y: 0 },
    data: { kind: "shot", shotId: "11111111-1111-4111-8111-111111111111" },
  },
];

describe("canvas node registry", () => {
  it("keeps the exact backend port contract with every Task 6 renderer registered", () => {
    expect(nodePorts).toEqual({
      text_source: { source: ["text"], target: [] },
      prompt: { source: ["prompt_document"], target: ["text"] },
      note: { source: [], target: [] },
      media_input: { source: [], target: [] },
      shot: { source: [], target: ["prompt_document"] },
    });
    expect(Object.keys(nodeTypes).sort()).toEqual([
      "media_input",
      "note",
      "prompt",
      "shot",
      "text_source",
    ]);
  });

  it("accepts only text_source.text to prompt.text", () => {
    expect(
      isCanvasConnectionValid(
        {
          source: "source",
          sourceHandle: "text",
          target: "prompt",
          targetHandle: "text",
        },
        nodes,
      ),
    ).toBe(true);
  });

  it("accepts prompt.prompt_document to shot.prompt_document", () => {
    expect(isCanvasConnectionValid({
      source: "prompt",
      sourceHandle: "prompt_document",
      target: "shot",
      targetHandle: "prompt_document",
    }, nodes)).toBe(true);
  });

  it.each([
    ["missing source handle", "source", null, "prompt", "text"],
    ["unknown source handle", "source", "other", "prompt", "text"],
    ["missing target handle", "source", "text", "prompt", null],
    ["reverse direction", "prompt", "text", "source", "text"],
    ["note endpoint", "source", "text", "note", "text"],
    ["media endpoint", "source", "text", "media", "text"],
    ["Shot text endpoint", "source", "text", "shot", "text"],
    ["missing node", "missing", "text", "prompt", "text"],
    ["self connection", "source", "text", "source", "text"],
  ] as const)(
    "rejects %s",
    (_case, source, sourceHandle, target, targetHandle) => {
      expect(
        isCanvasConnectionValid(
          { source, sourceHandle, target, targetHandle },
          nodes,
        ),
      ).toBe(false);
    },
  );

  it("returns false instead of throwing for malformed runtime node kinds", () => {
    const malformed = {
      id: "malformed",
      type: "unknown",
      position: { x: 0, y: 0 },
      data: { kind: "unknown" },
    } as unknown as CanvasNode;

    expect(
      isCanvasConnectionValid(
        {
          source: "source",
          sourceHandle: "text",
          target: "malformed",
          targetHandle: "text",
        },
        [...nodes, malformed],
      ),
    ).toBe(false);
  });
});
