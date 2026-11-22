import { useReactFlow } from "@xyflow/react";
import {
  useContext,
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from "react";
import { useCanvasStore } from "../../canvasStore";
import type { CanvasNode, MediaInputNodeData } from "../../canvasTypes";
import {
  candidatesFromClipboard,
  candidatesFromDrop,
} from "../mediaIngressInput";
import { MediaIngressContext } from "../mediaIngressContext";
import { MediaTargetChoiceDialog } from "./MediaTargetChoiceDialog";

type ExternalDragIntent = "files" | "navigation" | "internal";
type DropFeedback =
  | Readonly<{ kind: "ready" }>
  | Readonly<{ kind: "error"; message: string }>
  | null;

const NAVIGATION_TYPES = new Set([
  "text/uri-list",
  "text/html",
  "text/plain",
]);
const CONFLICT_MESSAGE = "请先处理画布版本冲突";
const PROJECT_CHANGED_MESSAGE = "当前项目已发生变化，请重新粘贴";
const CODE_EDITOR_CLASSES = new Set([
  "CodeMirror",
  "cm-editor",
  "monaco-editor",
]);
type MediaTargetCanvasNode = CanvasNode & {
  type: "media_input";
  data: MediaInputNodeData;
};

function isMediaTargetNode(
  node: CanvasNode | null,
): node is MediaTargetCanvasNode {
  return (
    node?.type === "media_input" && node.data.kind === "media_input"
  );
}

function isIntrinsicEditablePasteTarget(target: EventTarget): boolean {
  if (!(target instanceof Element)) return false;
  if (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement
  ) {
    return true;
  }
  if (
    target instanceof HTMLElement &&
    target.getAttribute("role")?.toLowerCase() === "textbox"
  ) {
    return true;
  }
  if (
    target.hasAttribute("data-code-editor") ||
    target.hasAttribute("data-prompt-editor") ||
    target.hasAttribute("data-note-editor")
  ) {
    return true;
  }
  return Array.from(target.classList).some((value) =>
    CODE_EDITOR_CLASSES.has(value),
  );
}

function isEditablePaste(event: ClipboardEvent): boolean {
  let path: EventTarget[] = [];
  try {
    path = event.composedPath();
  } catch {
    path = [];
  }
  if (event.target && !path.includes(event.target)) path.push(event.target);
  if (
    typeof document !== "undefined" &&
    document.activeElement &&
    !path.includes(document.activeElement)
  ) {
    path.push(document.activeElement);
  }
  let hasExplicitContentEditableBoundary = false;
  for (const target of path) {
    if (isIntrinsicEditablePasteTarget(target)) return true;
    if (!(target instanceof Element) || hasExplicitContentEditableBoundary) {
      continue;
    }
    const contentEditable = target.getAttribute("contenteditable");
    if (contentEditable !== null) {
      hasExplicitContentEditableBoundary = true;
      if (contentEditable.trim().toLowerCase() !== "false") return true;
      continue;
    }
    if (target instanceof HTMLElement && target.isContentEditable) {
      return true;
    }
  }
  return false;
}

function mediaTargetAnchor(node: MediaTargetCanvasNode) {
  const measuredWidth = node.measured?.width;
  return {
    x:
      node.position.x +
      (typeof measuredWidth === "number" &&
      Number.isFinite(measuredWidth) &&
      measuredWidth > 0
        ? measuredWidth
        : 312) +
      40,
    y: node.position.y,
  };
}

function selectedMediaTarget(
  flowNodes: readonly CanvasNode[],
  flowEdges: readonly Readonly<{ selected?: boolean }>[],
  storeNodes: readonly CanvasNode[],
): MediaTargetCanvasNode | null {
  const selectedNodes = flowNodes.filter((node) => node.selected);
  if (
    selectedNodes.length !== 1 ||
    flowEdges.some((edge) => edge.selected)
  ) {
    return null;
  }
  const selectedFlowNode = selectedNodes[0];
  const storeNode =
    storeNodes.find((node) => node.id === selectedFlowNode.id) ?? null;
  if (!isMediaTargetNode(storeNode)) return null;
  return {
    ...storeNode,
    position: { ...selectedFlowNode.position },
    measured: selectedFlowNode.measured ?? storeNode.measured,
  };
}

function pasteChoiceTrigger(
  boundary: HTMLDivElement,
  targetNodeId: string,
): HTMLElement {
  const flowNode = Array.from(
    boundary.querySelectorAll<HTMLElement>(".react-flow__node[data-id]"),
  ).find((element) => element.dataset.id === targetNodeId);
  const focusable = flowNode?.querySelector<HTMLElement>(
    "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
  );
  if (focusable) return focusable;
  if (flowNode && flowNode.tabIndex >= 0) return flowNode;
  const activeElement = document.activeElement;
  if (
    activeElement instanceof HTMLElement &&
    boundary.contains(activeElement)
  ) {
    return activeElement;
  }
  return boundary;
}

function transferItems(dataTransfer: DataTransfer): DataTransferItem[] {
  try {
    return Array.from(dataTransfer.items ?? []);
  } catch {
    return [];
  }
}

function transferTypes(dataTransfer: DataTransfer): string[] {
  try {
    return Array.from(dataTransfer.types ?? [], (value) =>
      String(value).trim().toLowerCase(),
    );
  } catch {
    return [];
  }
}

function transferFileCount(dataTransfer: DataTransfer): number {
  try {
    return dataTransfer.files?.length ?? 0;
  } catch {
    return 0;
  }
}

function classifyExternalDrag(dataTransfer: DataTransfer): ExternalDragIntent {
  const items = transferItems(dataTransfer);
  const types = transferTypes(dataTransfer);
  if (
    items.some((item) => item.kind === "file") ||
    types.includes("files") ||
    transferFileCount(dataTransfer) > 0
  ) {
    return "files";
  }
  if (types.some((type) => NAVIGATION_TYPES.has(type))) return "navigation";
  return "internal";
}

export type CanvasMediaIngressBoundaryProps = Readonly<{
  children: ReactNode;
}>;

export function CanvasMediaIngressBoundary({
  children,
}: CanvasMediaIngressBoundaryProps) {
  const ingress = useContext(MediaIngressContext);
  const projectId = useCanvasStore((state) => state.projectId);
  const loadEpoch = useCanvasStore((state) => state.loadEpoch);
  const conflict = useCanvasStore((state) => state.status === "conflict");
  const { getEdges, getNodes, screenToFlowPosition } =
    useReactFlow<CanvasNode>();
  const flowApiRef = useRef({
    getEdges,
    getNodes,
    screenToFlowPosition,
  });
  flowApiRef.current = { getEdges, getNodes, screenToFlowPosition };
  const boundaryRef = useRef<HTMLDivElement>(null);
  const dragDepth = useRef(0);
  const choiceTriggers = useRef(new Map<string, HTMLElement>());
  const [feedback, setFeedback] = useState<DropFeedback>(null);
  const active =
    projectId !== null &&
    Boolean(ingress?.controller) &&
    ingress.projectId === projectId &&
    ingress.loadEpoch === loadEpoch;

  const clearDragFeedback = () => {
    dragDepth.current = 0;
    setFeedback(null);
  };

  useEffect(() => {
    if (active) return;
    dragDepth.current = 0;
    setFeedback(null);
  }, [active]);

  useEffect(
    () => () => {
      choiceTriggers.current.clear();
    },
    [],
  );

  useEffect(() => {
    if (conflict) return;
    setFeedback((current) =>
      current?.kind === "error" && current.message === CONFLICT_MESSAGE
        ? null
        : current,
    );
  }, [conflict]);

  useEffect(() => {
    const controller = ingress?.controller;
    const runtimeProjectId = ingress?.projectId;
    const runtimeLoadEpoch = ingress?.loadEpoch;
    if (
      !active ||
      !controller ||
      typeof runtimeProjectId !== "string" ||
      typeof runtimeLoadEpoch !== "number"
    ) {
      return;
    }

    const onPaste = (event: ClipboardEvent) => {
      if (isEditablePaste(event)) return;
      const clipboardData = event.clipboardData;
      if (!clipboardData) return;
      let candidates;
      try {
        candidates = candidatesFromClipboard(clipboardData);
      } catch {
        return;
      }
      if (candidates.length === 0) return;

      event.preventDefault();
      const binding = useCanvasStore.getState();
      if (
        binding.projectId !== runtimeProjectId ||
        binding.loadEpoch !== runtimeLoadEpoch
      ) {
        setFeedback({
          kind: "error",
          message: PROJECT_CHANGED_MESSAGE,
        });
        return;
      }
      if (binding.status === "conflict") {
        setFeedback({ kind: "error", message: CONFLICT_MESSAGE });
        return;
      }

      const boundary = boundaryRef.current;
      if (!boundary) return;
      let flowNodes: CanvasNode[] = [];
      let flowEdges: ReadonlyArray<Readonly<{ selected?: boolean }>> = [];
      try {
        flowNodes = flowApiRef.current.getNodes();
        flowEdges = flowApiRef.current.getEdges();
      } catch {
        flowNodes = [];
        flowEdges = [];
      }
      const targetNode = selectedMediaTarget(
        flowNodes,
        flowEdges,
        binding.nodes,
      );
      const targetAnchor = targetNode
        ? mediaTargetAnchor(targetNode)
        : null;
      const rect = boundary.getBoundingClientRect();
      const createAnchor =
        targetAnchor ??
        flowApiRef.current.screenToFlowPosition({
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
        });
      const singleImage = candidates.length === 1;
      const request =
        targetNode && targetAnchor && singleImage
          ? targetNode.data.artifactRevisionId === null
            ? ({
                projectId: runtimeProjectId,
                loadEpoch: runtimeLoadEpoch,
                source: "paste",
                candidates,
                anchor: targetAnchor,
                targetMode: "fill_existing",
                targetBinding: {
                  targetNodeId: targetNode.id,
                  expectedRevisionId: null,
                },
              } as const)
            : ({
                projectId: runtimeProjectId,
                loadEpoch: runtimeLoadEpoch,
                source: "paste",
                candidates,
                anchor: targetAnchor,
                targetMode: "choose_create_or_replace",
                targetBinding: {
                  targetNodeId: targetNode.id,
                  expectedRevisionId:
                    targetNode.data.artifactRevisionId,
                },
              } as const)
          : ({
              projectId: runtimeProjectId,
              loadEpoch: runtimeLoadEpoch,
              source: "paste",
              candidates,
              anchor: createAnchor,
              targetMode: "create",
              targetBinding: null,
            } as const);
      const admission = controller.enqueue(request);
      if (admission.kind === "choice_required") {
        choiceTriggers.current.set(
          admission.choiceId,
          pasteChoiceTrigger(boundary, targetNode?.id ?? ""),
        );
      }
      if (admission.kind === "rejected") {
        setFeedback({ kind: "error", message: admission.message });
      }
    };

    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("paste", onPaste);
    };
  }, [active, ingress?.controller, ingress?.loadEpoch, ingress?.projectId]);

  const onDragEnter = (event: DragEvent<HTMLDivElement>) => {
    const intent = classifyExternalDrag(event.dataTransfer);
    if (intent === "internal") return;
    event.preventDefault();
    if (intent === "navigation") {
      clearDragFeedback();
      return;
    }
    dragDepth.current += 1;
    setFeedback({ kind: "ready" });
  };

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    const intent = classifyExternalDrag(event.dataTransfer);
    if (intent === "internal") return;
    event.preventDefault();
    if (intent === "navigation") {
      clearDragFeedback();
      return;
    }
    if (dragDepth.current === 0) dragDepth.current = 1;
    setFeedback({ kind: "ready" });
  };

  const onDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (dragDepth.current === 0) return;
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setFeedback(null);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    const intent = classifyExternalDrag(event.dataTransfer);
    if (intent === "internal") return;
    event.preventDefault();
    clearDragFeedback();
    if (intent === "navigation" || !active || !ingress?.controller) return;
    const binding = useCanvasStore.getState();
    if (
      binding.projectId !== ingress.projectId ||
      binding.loadEpoch !== ingress.loadEpoch
    ) {
      setFeedback({
        kind: "error",
        message: "当前项目已发生变化，请重新拖放",
      });
      return;
    }
    if (binding.status === "conflict") {
      setFeedback({
        kind: "error",
        message: CONFLICT_MESSAGE,
      });
      return;
    }

    const candidates = candidatesFromDrop(event.dataTransfer);
    if (candidates.length === 0) return;
    const eventElement =
      event.target instanceof Element ? event.target : null;
    const flowNodeElement =
      eventElement?.closest<HTMLElement>(".react-flow__node[data-id]") ??
      null;
    const targetNodeId =
      flowNodeElement &&
      event.currentTarget.contains(flowNodeElement) &&
      typeof flowNodeElement.dataset.id === "string"
        ? flowNodeElement.dataset.id
        : null;
    const hitNode =
      targetNodeId === null
        ? null
        : binding.nodes.find((node) => node.id === targetNodeId) ?? null;
    const targetNode = isMediaTargetNode(hitNode) ? hitNode : null;
    const targetAnchor = targetNode ? mediaTargetAnchor(targetNode) : null;
    const singleFile =
      candidates.length === 1 && candidates[0].kind === "file";
    const request =
      targetNode && targetAnchor && singleFile
        ? targetNode.data.artifactRevisionId === null
          ? ({
              projectId: ingress.projectId,
              loadEpoch: ingress.loadEpoch,
              source: "drop",
              candidates,
              anchor: targetAnchor,
              targetMode: "fill_existing",
              targetBinding: {
                targetNodeId: targetNode.id,
                expectedRevisionId: null,
              },
            } as const)
          : ({
              projectId: ingress.projectId,
              loadEpoch: ingress.loadEpoch,
              source: "drop",
              candidates,
              anchor: targetAnchor,
              targetMode: "choose_create_or_replace",
              targetBinding: {
                targetNodeId: targetNode.id,
                expectedRevisionId:
                  targetNode.data.artifactRevisionId,
              },
            } as const)
        : ({
            projectId: ingress.projectId,
            loadEpoch: ingress.loadEpoch,
            source: "drop",
            candidates,
            anchor:
              targetAnchor ??
              screenToFlowPosition({
                x: event.clientX,
                y: event.clientY,
              }),
            targetMode: "create",
            targetBinding: null,
          } as const);
    const admission = ingress.controller.enqueue(request);
    if (admission.kind === "choice_required") {
      const focusable =
        eventElement?.closest<HTMLElement>(
          "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
        ) ??
        (flowNodeElement && flowNodeElement.tabIndex >= 0
          ? flowNodeElement
          : event.currentTarget);
      choiceTriggers.current.set(admission.choiceId, focusable);
    }
    if (admission.kind === "rejected") {
      setFeedback({ kind: "error", message: admission.message });
    }
  };

  const handlers = active
    ? {
        onDragEnd: clearDragFeedback,
        onDragEnter,
        onDragLeave,
        onDragOver,
        onDrop,
      }
    : {};

  return (
    <div
      className="canvas-media-ingress-boundary"
      data-testid="canvas-media-ingress-boundary"
      ref={boundaryRef}
      tabIndex={-1}
      {...handlers}
    >
      {children}
      <MediaTargetChoiceDialog
        fallbackFocusRef={boundaryRef}
        getTriggerElement={(choiceId) =>
          choiceTriggers.current.get(choiceId) ?? null
        }
        onChoiceRejected={(message) => {
          setFeedback({ kind: "error", message });
        }}
        onChoiceSettled={(choiceId) => {
          choiceTriggers.current.delete(choiceId);
        }}
      />
      {feedback?.kind === "ready" ? (
        <div
          aria-live="polite"
          className="canvas-media-drop-feedback"
          role="status"
        >
          <span aria-hidden="true" className="canvas-media-drop-feedback__icon">
            ↓
          </span>
          <strong>释放以导入媒体</strong>
          <span>图片和视频会从此处开始排列</span>
        </div>
      ) : null}
      {feedback?.kind === "error" ? (
        <div
          className="canvas-media-drop-feedback canvas-media-drop-feedback--error"
          role="alert"
          style={{ pointerEvents: "none", zIndex: 41 }}
        >
          {feedback.message}
        </div>
      ) : null}
    </div>
  );
}
