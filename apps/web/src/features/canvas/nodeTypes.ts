import type { Connection, Edge, NodeTypes } from "@xyflow/react";
import type { CanvasNode, NodeKind } from "./canvasTypes";
import { NoteNode } from "./nodes/NoteNode";
import { MediaInputNode } from "./nodes/MediaInputNode";
import { PromptNode } from "./nodes/PromptNode";
import { ShotNode } from "./nodes/ShotNode";
import { TextSourceNode } from "./nodes/TextSourceNode";

export const nodePorts = {
  text_source: { source: ["text"], target: [] },
  prompt: { source: ["prompt_document"], target: ["text"] },
  note: { source: [], target: [] },
  media_input: { source: [], target: [] },
  shot: { source: [], target: ["prompt_document"] },
} as const satisfies Record<
  NodeKind,
  { source: readonly string[]; target: readonly string[] }
>;

export const nodeTypes = {
  text_source: TextSourceNode,
  prompt: PromptNode,
  note: NoteNode,
  media_input: MediaInputNode,
  shot: ShotNode,
} satisfies NodeTypes;

function includesPort(ports: readonly string[], port: string | null | undefined): boolean {
  return typeof port === "string" && ports.includes(port);
}

export function isCanvasConnectionValid(
  connection: Connection | Edge,
  nodes: CanvasNode[],
): boolean {
  if (connection.source === connection.target) return false;
  const source = nodes.find((node) => node.id === connection.source);
  const target = nodes.find((node) => node.id === connection.target);
  if (!source || !target) return false;
  const registry = nodePorts as Partial<
    Record<string, { source: readonly string[]; target: readonly string[] }>
  >;
  const sourcePorts = registry[source.data.kind];
  const targetPorts = registry[target.data.kind];
  if (!sourcePorts || !targetPorts) return false;

  return (
    includesPort(sourcePorts.source, connection.sourceHandle) &&
    includesPort(targetPorts.target, connection.targetHandle)
  );
}
