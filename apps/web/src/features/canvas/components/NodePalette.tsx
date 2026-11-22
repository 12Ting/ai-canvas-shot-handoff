import { useCanvasStore } from "../canvasStore";
import type { AddableNodeKind } from "../canvasTypes";

const choices: ReadonlyArray<{ kind: AddableNodeKind; label: string }> = [
  { kind: "text_source", label: "添加文案节点" },
  { kind: "prompt", label: "添加提示词节点" },
  { kind: "note", label: "添加便签" },
  { kind: "media_input", label: "添加媒体节点" },
];

export function NodePalette() {
  const addNode = useCanvasStore((state) => state.addNode);
  const count = useCanvasStore((state) => state.nodes.length);
  const locked = useCanvasStore((state) => state.status === "conflict");
  const nextPosition = {
    x: 120 + (count % 3) * 320,
    y: 120 + Math.floor(count / 3) * 220,
  };

  return (
    <aside aria-label="节点工具栏" className="node-palette">
      {choices.map(({ kind, label }) => (
        <button
          disabled={locked}
          key={kind}
          onClick={() => addNode(kind, nextPosition)}
          type="button"
        >
          {label}
        </button>
      ))}
    </aside>
  );
}
