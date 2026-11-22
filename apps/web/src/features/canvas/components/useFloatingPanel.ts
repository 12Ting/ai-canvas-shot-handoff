import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type HTMLAttributes,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";
import {
  clampPanelRect,
  defaultPanelRect,
  movePanelRect,
  panelStorageKey,
  parseStoredPanelRect,
  resizePanelRect,
  restorePanelRect,
  serializePanelRect,
  SHOT_TRACK_COLLAPSED_HEIGHT,
  SHOT_TRACK_EXPANDED_HEIGHT,
  SHOT_TRACK_LEGACY_COLLAPSED_HEIGHT,
  type FloatingPanelBounds,
  type FloatingPanelId,
  type FloatingPanelRect,
  type FloatingPanelResizeEdge,
} from "./floatingPanelLayout";

type PointerHandleProps = Pick<
  HTMLAttributes<HTMLElement>,
  | "onLostPointerCapture"
  | "onPointerCancel"
  | "onPointerDown"
  | "onPointerMove"
  | "onPointerUp"
>;

type ButtonHandleProps = Pick<
  ButtonHTMLAttributes<HTMLButtonElement>,
  | "onLostPointerCapture"
  | "onPointerCancel"
  | "onPointerDown"
  | "onPointerMove"
  | "onPointerUp"
>;

type PointerInteraction = Readonly<{
  captureTarget: HTMLElement;
  edge: FloatingPanelResizeEdge | null;
  kind: "move" | "resize";
  pointerId: number;
  startClientX: number;
  startClientY: number;
  startRect: FloatingPanelRect;
}>;

export type FloatingPanelController = Readonly<{
  active: boolean;
  customized: boolean;
  directMoveHandleProps: PointerHandleProps;
  directResizeHandleProps(edge: FloatingPanelResizeEdge): PointerHandleProps;
  expand(): void;
  moveHandleProps: ButtonHandleProps;
  panelRef: RefObject<HTMLElement | null>;
  rect: FloatingPanelRect | null;
  reset(): void;
  resizeHandleProps: ButtonHandleProps;
  style: CSSProperties | undefined;
}>;

function fallbackBounds(): FloatingPanelBounds {
  return {
    width: Math.max(
      44,
      document.documentElement.clientWidth,
      window.innerWidth,
    ),
    height: Math.max(
      44,
      document.documentElement.clientHeight,
      window.innerHeight,
    ),
    viewportHeight: window.innerHeight,
  };
}

function readWorkspaceBounds(workspace: HTMLElement | null): FloatingPanelBounds {
  if (workspace !== null) {
    const rect = workspace.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      return {
        width: rect.width,
        height: rect.height,
        viewportHeight: window.innerHeight,
      };
    }
  }
  return fallbackBounds();
}

function safelyReadStoredRect(
  projectId: string | null,
  panelId: FloatingPanelId,
): FloatingPanelRect | null {
  if (projectId === null) return null;
  try {
    return parseStoredPanelRect(
      localStorage.getItem(panelStorageKey(projectId, panelId)),
      panelId,
    );
  } catch {
    return null;
  }
}

function restoredPanelState(
  projectId: string | null,
  panelId: FloatingPanelId,
  bounds: FloatingPanelBounds,
): Readonly<{ customized: boolean; rect: FloatingPanelRect }> {
  const fallback = defaultPanelRect(panelId, bounds);
  const stored = safelyReadStoredRect(projectId, panelId);
  if (stored === null) return { customized: false, rect: fallback };
  const collapsedTrackPreference =
    panelId === "shot-track" &&
    (stored.height <= SHOT_TRACK_COLLAPSED_HEIGHT ||
      stored.height === SHOT_TRACK_LEGACY_COLLAPSED_HEIGHT);
  return {
    customized: !collapsedTrackPreference,
    rect: restorePanelRect(stored, bounds, panelId),
  };
}

function isInteractivePointerTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return target.closest(
    "button, a, input, textarea, select, option, [contenteditable='true'], [data-floating-panel-drag-ignore]",
  ) !== null;
}

function hasRectChanged(
  next: FloatingPanelRect,
  previous: FloatingPanelRect,
): boolean {
  return (
    next.x !== previous.x ||
    next.y !== previous.y ||
    next.width !== previous.width ||
    next.height !== previous.height
  );
}

export function useFloatingPanel({
  panelId,
  projectId,
}: {
  panelId: FloatingPanelId;
  projectId: string | null;
}): FloatingPanelController {
  const panelRef = useRef<HTMLElement>(null);
  const boundsRef = useRef<FloatingPanelBounds>(fallbackBounds());
  const rectRef = useRef<FloatingPanelRect | null>(null);
  const customizedRef = useRef(false);
  const interactionRef = useRef<PointerInteraction | null>(null);
  const [customized, setCustomized] = useState(false);
  const [rect, setRect] = useState<FloatingPanelRect | null>(null);

  const updateRect = useCallback((next: FloatingPanelRect) => {
    rectRef.current = next;
    setRect(next);
  }, []);

  const commit = useCallback(
    (next: FloatingPanelRect) => {
      const collapsedTrackPreference =
        panelId === "shot-track" &&
        next.height <= SHOT_TRACK_COLLAPSED_HEIGHT;
      customizedRef.current = !collapsedTrackPreference;
      setCustomized(!collapsedTrackPreference);
      if (projectId === null) return;
      try {
        localStorage.setItem(
          panelStorageKey(projectId, panelId),
          serializePanelRect(next),
        );
      } catch {
        // Browser UI preferences are best-effort and never block graph editing.
      }
    },
    [panelId, projectId],
  );

  const rollbackInteraction = useCallback(
    (releaseCapture: boolean) => {
      const interaction = interactionRef.current;
      if (interaction === null) return;
      interactionRef.current = null;
      updateRect(
        clampPanelRect(interaction.startRect, boundsRef.current, panelId),
      );
      if (!releaseCapture) return;
      try {
        interaction.captureTarget.releasePointerCapture?.(
          interaction.pointerId,
        );
      } catch {
        // Capture can already be gone when the browser interrupts a pointer.
      }
    },
    [panelId, updateRect],
  );

  useLayoutEffect(() => {
    const workspace =
      panelRef.current?.closest<HTMLElement>(".canvas-workspace") ??
      document.querySelector<HTMLElement>(".canvas-workspace");
    const initialBounds = readWorkspaceBounds(workspace);
    boundsRef.current = initialBounds;
    const restored = restoredPanelState(
      projectId,
      panelId,
      initialBounds,
    );
    customizedRef.current = restored.customized;
    setCustomized(restored.customized);
    updateRect(restored.rect);

    const observer =
      workspace !== null && typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            const nextBounds = readWorkspaceBounds(workspace);
            boundsRef.current = nextBounds;
            const current = rectRef.current;
            if (current === null) {
              const nextRestored = restoredPanelState(
                projectId,
                panelId,
                nextBounds,
              );
              customizedRef.current = nextRestored.customized;
              setCustomized(nextRestored.customized);
              updateRect(nextRestored.rect);
              return;
            }
            updateRect(
              customizedRef.current
                ? clampPanelRect(current, nextBounds, panelId)
                : defaultPanelRect(panelId, nextBounds),
            );
          })
        : null;
    if (workspace !== null) observer?.observe(workspace);
    return () => {
      rollbackInteraction(true);
      observer?.disconnect();
    };
  }, [panelId, projectId, rollbackInteraction, updateRect]);

  const applyPointer = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const interaction = interactionRef.current;
      if (interaction === null || interaction.pointerId !== event.pointerId) {
        return null;
      }
      const deltaX = event.clientX - interaction.startClientX;
      const deltaY = event.clientY - interaction.startClientY;
      const next =
        interaction.kind === "move"
          ? movePanelRect(
              interaction.startRect,
              { x: deltaX, y: deltaY },
              boundsRef.current,
              panelId,
            )
          : resizePanelRect(
              interaction.startRect,
              { width: deltaX, height: deltaY },
              boundsRef.current,
              interaction.edge ?? "bottom-right",
              panelId,
            );
      updateRect(next);
      return next;
    },
    [panelId, updateRect],
  );

  const pointerDown = useCallback(
    (kind: PointerInteraction["kind"], edge: FloatingPanelResizeEdge | null = null) =>
      (event: ReactPointerEvent<HTMLElement>) => {
        if (
          event.button > 0 ||
          (event.isPrimary === false &&
            (event.pointerType === "mouse" ||
              event.pointerType === "pen" ||
              event.pointerType === "touch")) ||
          interactionRef.current !== null
        ) {
          event.stopPropagation();
          return;
        }
        const current = rectRef.current;
        if (current === null) return;
        event.preventDefault();
        event.stopPropagation();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        interactionRef.current = {
          captureTarget: event.currentTarget,
          edge,
          kind,
          pointerId: event.pointerId,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startRect: current,
        };
      },
    [],
  );

  const pointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      if (interactionRef.current === null) return;
      event.preventDefault();
      applyPointer(event);
    },
    [applyPointer],
  );

  const pointerEnd = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const interaction = interactionRef.current;
      if (interaction === null || interaction.pointerId !== event.pointerId) {
        return;
      }
      event.preventDefault();
      const next = applyPointer(event) ?? rectRef.current;
      interactionRef.current = null;
      try {
        event.currentTarget.releasePointerCapture?.(event.pointerId);
      } catch {
        // Pointer capture may already be released by the browser on cancellation.
      }
      if (next !== null && hasRectChanged(next, interaction.startRect)) {
        commit(next);
      }
    },
    [applyPointer, commit],
  );

  const pointerCancel = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const interaction = interactionRef.current;
      if (interaction === null || interaction.pointerId !== event.pointerId) {
        return;
      }
      event.preventDefault();
      rollbackInteraction(true);
    },
    [rollbackInteraction],
  );

  const lostPointerCapture = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const interaction = interactionRef.current;
      if (interaction === null || interaction.pointerId !== event.pointerId) {
        return;
      }
      rollbackInteraction(false);
    },
    [rollbackInteraction],
  );

  const reset = useCallback(() => {
    const next = defaultPanelRect(panelId, boundsRef.current);
    customizedRef.current = false;
    setCustomized(false);
    updateRect(next);
    if (projectId === null) return;
    try {
      localStorage.removeItem(panelStorageKey(projectId, panelId));
    } catch {
      // Browser UI preferences are best-effort and never block graph editing.
    }
  }, [panelId, projectId, updateRect]);

  const expand = useCallback(() => {
    const current = rectRef.current;
    if (
      panelId !== "shot-track" ||
      current === null ||
      current.height > SHOT_TRACK_COLLAPSED_HEIGHT
    ) {
      return;
    }
    const next = resizePanelRect(
      current,
      {
        width: 0,
        height: current.height - SHOT_TRACK_EXPANDED_HEIGHT,
      },
      boundsRef.current,
      "top",
      panelId,
    );
    updateRect(next);
    if (hasRectChanged(next, current)) commit(next);
  }, [commit, panelId, updateRect]);

  const commonPointerProps = {
    onLostPointerCapture: lostPointerCapture,
    onPointerCancel: pointerCancel,
    onPointerMove: pointerMove,
    onPointerUp: pointerEnd,
  };

  const directMoveHandleProps: PointerHandleProps = {
    ...commonPointerProps,
    onPointerDown: (event) => {
      if (isInteractivePointerTarget(event.target)) return;
      pointerDown("move")(event);
    },
  };

  const directResizeHandleProps = (
    edge: FloatingPanelResizeEdge,
  ): PointerHandleProps => ({
    ...commonPointerProps,
    onPointerDown: pointerDown("resize", edge),
  });

  return {
    active: rect !== null,
    customized,
    directMoveHandleProps,
    directResizeHandleProps,
    expand,
    moveHandleProps: {
      ...commonPointerProps,
      onPointerDown: pointerDown("move"),
    },
    panelRef,
    rect,
    reset,
    resizeHandleProps: {
      ...commonPointerProps,
      onPointerDown: pointerDown("resize", "bottom-right"),
    },
    style:
      rect === null
        ? undefined
        : {
            height: rect.height,
            left: rect.x,
            top: rect.y,
            width: rect.width,
          },
  };
}
