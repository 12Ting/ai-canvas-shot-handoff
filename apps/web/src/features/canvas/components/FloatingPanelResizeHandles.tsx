import { useRef } from "react";
import {
  SHOT_TRACK_COLLAPSED_HEIGHT,
  type FloatingPanelResizeEdge,
} from "./floatingPanelLayout";
import type { FloatingPanelController } from "./useFloatingPanel";

type DirectFloatingPanelKind = "inspector" | "track";

const DEFAULT_EDGES: Readonly<Record<DirectFloatingPanelKind, readonly FloatingPanelResizeEdge[]>> = {
  inspector: ["left", "bottom", "bottom-left"],
  track: ["top", "left", "right", "top-left", "top-right"],
};

export function FloatingPanelResizeHandles({
  controller,
  kind,
}: {
  controller: FloatingPanelController;
  kind: DirectFloatingPanelKind;
}) {
  const draggedTopHandle = useRef(false);
  const topPointerStart = useRef<Readonly<{ x: number; y: number }> | null>(
    null,
  );
  const topHandleProps = controller.directResizeHandleProps("top");
  const collapsedTrack =
    kind === "track" &&
    controller.rect !== null &&
    controller.rect.height <= SHOT_TRACK_COLLAPSED_HEIGHT;
  const edges = collapsedTrack
    ? (["top"] as const)
    : controller.customized
    ? [...DEFAULT_EDGES[kind], "bottom-right" as const]
    : DEFAULT_EDGES[kind];
  return (
    <>
      {edges.map((edge) => (
        kind === "track" && edge === "top" ? (
          <button
            {...controller.directResizeHandleProps(edge)}
            aria-expanded={collapsedTrack ? "false" : undefined}
            aria-hidden={collapsedTrack ? undefined : "true"}
            aria-label={collapsedTrack ? "展开分镜轨道" : undefined}
            className={`floating-panel__edge-grip floating-panel__edge-grip--${edge}`}
            data-floating-panel-resize-edge={edge}
            key={edge}
            onClick={
              collapsedTrack
                ? (event) => {
                    if (event.detail === 0) {
                      draggedTopHandle.current = false;
                      event.currentTarget.blur();
                      controller.expand();
                      return;
                    }
                    if (draggedTopHandle.current) {
                      draggedTopHandle.current = false;
                      return;
                    }
                    event.currentTarget.blur();
                    controller.expand();
                  }
                : undefined
            }
            onPointerMove={(event) => {
              const start = topPointerStart.current;
              if (
                start !== null &&
                (event.clientX !== start.x || event.clientY !== start.y)
              ) {
                draggedTopHandle.current = true;
              }
              topHandleProps.onPointerMove?.(event);
            }}
            onPointerDown={(event) => {
              draggedTopHandle.current = false;
              topPointerStart.current = {
                x: event.clientX,
                y: event.clientY,
              };
              topHandleProps.onPointerDown?.(event);
            }}
            tabIndex={collapsedTrack ? 0 : -1}
            title={collapsedTrack ? "向上拖动或按 Enter 展开分镜轨道" : undefined}
            type="button"
          />
        ) : (
          <span
            {...controller.directResizeHandleProps(edge)}
            aria-hidden="true"
            className={`floating-panel__edge-grip floating-panel__edge-grip--${edge}`}
            data-floating-panel-resize-edge={edge}
            key={edge}
          />
        )
      ))}
    </>
  );
}
