export type FloatingPanelRect = Readonly<{
  x: number;
  y: number;
  width: number;
  height: number;
}>;

export type FloatingPanelBounds = Readonly<{
  width: number;
  height: number;
  viewportHeight?: number;
}>;

export type FloatingPanelId = "shot-inspector" | "shot-track";

export type FloatingPanelResizeEdge =
  | "top"
  | "right"
  | "bottom"
  | "left"
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right";

export const FLOATING_PANEL_MIN_WIDTH = 280;
export const FLOATING_PANEL_MIN_HEIGHT = 112;
export const FLOATING_PANEL_VISIBLE_HANDLE_SIZE = 44;
export const SHOT_TRACK_COLLAPSED_HEIGHT = 12;
export const SHOT_TRACK_LEGACY_COLLAPSED_HEIGHT = 60;
export const SHOT_TRACK_EXPANDED_HEIGHT = 176;

const STORAGE_VERSION = 1;
const INSPECTOR_WIDTH = 348;
const INSPECTOR_HEIGHT = 560;
const TRACK_WIDTH = 760;
const MOBILE_TRACK_HEIGHT = 128;
const PANEL_EDGE_GAP = 16;
const TRACK_BATCH_LANE_RESERVE = 416;
const INSPECTOR_BATCH_BLOCK_RESERVE = 576;
const MINIMAP_BLOCK_RESERVE = 176;
const MINIMAP_INLINE_RESERVE = 216;
const MOBILE_BREAKPOINT = 720;
const MOBILE_BATCH_MAX_HEIGHT = 416;
const MOBILE_BATCH_HEIGHT_RATIO = 0.48;
const MOBILE_MINIMAP_LANE_RESERVE = 160;

function finiteOr(value: number, fallback: number): number {
  return Number.isFinite(value) ? value : fallback;
}

function normalizedBounds(bounds: FloatingPanelBounds): FloatingPanelBounds {
  const height = Math.max(0, finiteOr(bounds.height, 0));
  return {
    width: Math.max(0, finiteOr(bounds.width, 0)),
    height,
    viewportHeight: Math.max(
      0,
      finiteOr(bounds.viewportHeight ?? height, height),
    ),
  };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(finiteOr(value, minimum), minimum), maximum);
}

function clampAxis(position: number, size: number, bound: number): number {
  if (size <= bound) return clamp(position, 0, bound - size);
  return clamp(
    position,
    FLOATING_PANEL_VISIBLE_HANDLE_SIZE - size,
    bound - FLOATING_PANEL_VISIBLE_HANDLE_SIZE,
  );
}

function clampSize(size: number, minimum: number, bound: number): number {
  if (bound >= minimum) return clamp(size, minimum, bound);
  return Math.max(finiteOr(size, minimum), minimum);
}

function panelMinimumHeight(
  panelId: FloatingPanelId,
  bounds?: FloatingPanelBounds,
): number {
  if (panelId !== "shot-track") return FLOATING_PANEL_MIN_HEIGHT;
  return bounds !== undefined && bounds.width <= MOBILE_BREAKPOINT
    ? MOBILE_TRACK_HEIGHT
    : SHOT_TRACK_COLLAPSED_HEIGHT;
}

function dockCollapsedTrackRect(
  rect: FloatingPanelRect,
  bounds: FloatingPanelBounds,
): FloatingPanelRect {
  if (
    bounds.width <= MOBILE_BREAKPOINT ||
    rect.height > SHOT_TRACK_COLLAPSED_HEIGHT
  ) {
    return rect;
  }
  return {
    ...rect,
    y: bounds.height - SHOT_TRACK_COLLAPSED_HEIGHT,
    height: SHOT_TRACK_COLLAPSED_HEIGHT,
  };
}

export function clampPanelRect(
  rect: FloatingPanelRect,
  rawBounds: FloatingPanelBounds,
  panelId: FloatingPanelId,
): FloatingPanelRect {
  const bounds = normalizedBounds(rawBounds);
  const compatibleRect =
    panelId === "shot-track" ? dockCollapsedTrackRect(rect, bounds) : rect;
  const width = clampSize(
    compatibleRect.width,
    FLOATING_PANEL_MIN_WIDTH,
    bounds.width,
  );
  const height = clampSize(
    compatibleRect.height,
    panelMinimumHeight(panelId, bounds),
    bounds.height,
  );
  return {
    x: clampAxis(compatibleRect.x, width, bounds.width),
    y: clampAxis(compatibleRect.y, height, bounds.height),
    width,
    height,
  };
}

export function restorePanelRect(
  rect: FloatingPanelRect,
  rawBounds: FloatingPanelBounds,
  panelId: FloatingPanelId,
): FloatingPanelRect {
  const bounds = normalizedBounds(rawBounds);
  if (
    panelId === "shot-track" &&
    (rect.height <= SHOT_TRACK_COLLAPSED_HEIGHT ||
      rect.height === SHOT_TRACK_LEGACY_COLLAPSED_HEIGHT) &&
    bounds.width <= MOBILE_BREAKPOINT
  ) {
    return defaultPanelRect(panelId, bounds);
  }
  const compatibleRect =
    panelId === "shot-track" &&
    rect.height === SHOT_TRACK_LEGACY_COLLAPSED_HEIGHT
      ? {
          ...rect,
          y: bounds.height - SHOT_TRACK_COLLAPSED_HEIGHT,
          height: SHOT_TRACK_COLLAPSED_HEIGHT,
        }
      : rect;
  return clampPanelRect(compatibleRect, bounds, panelId);
}

function mobileDefaultPanelRects(
  bounds: FloatingPanelBounds,
): Readonly<Record<FloatingPanelId, FloatingPanelRect>> {
  const trackWidth = Math.min(
    TRACK_WIDTH,
    Math.max(
      FLOATING_PANEL_MIN_WIDTH,
      bounds.width - MOBILE_MINIMAP_LANE_RESERVE - PANEL_EDGE_GAP,
    ),
  );
  const inspectorWidth = Math.min(
    INSPECTOR_WIDTH,
    Math.max(FLOATING_PANEL_MIN_WIDTH, bounds.width),
  );
  const trackHeight = Math.min(
    MOBILE_TRACK_HEIGHT,
    Math.max(FLOATING_PANEL_MIN_HEIGHT, bounds.height),
  );
  const batchHeight = Math.min(
    MOBILE_BATCH_MAX_HEIGHT,
    (bounds.viewportHeight ?? bounds.height) * MOBILE_BATCH_HEIGHT_RATIO,
  );
  const safeBottom = bounds.height - PANEL_EDGE_GAP - batchHeight;
  const spacing = Math.min(
    PANEL_EDGE_GAP,
    Math.max(
      0,
      (safeBottom - trackHeight - FLOATING_PANEL_MIN_HEIGHT) / 3,
    ),
  );
  const trackY = spacing;
  const inspectorY = trackY + trackHeight + spacing;
  const inspectorHeight = Math.min(
    INSPECTOR_HEIGHT,
    Math.max(
      FLOATING_PANEL_MIN_HEIGHT,
      safeBottom - spacing - inspectorY,
    ),
  );
  return {
    "shot-inspector": clampPanelRect(
      {
        x: bounds.width - inspectorWidth,
        y: inspectorY,
        width: inspectorWidth,
        height: inspectorHeight,
      },
      bounds,
      "shot-inspector",
    ),
    "shot-track": clampPanelRect(
      {
        x: PANEL_EDGE_GAP,
        y: trackY,
        width: trackWidth,
        height: trackHeight,
      },
      bounds,
      "shot-track",
    ),
  };
}

export function defaultPanelRect(
  panelId: FloatingPanelId,
  rawBounds: FloatingPanelBounds,
): FloatingPanelRect {
  const bounds = normalizedBounds(rawBounds);
  if (bounds.width <= MOBILE_BREAKPOINT) {
    return mobileDefaultPanelRects(bounds)[panelId];
  }
  if (panelId === "shot-inspector") {
    const blockLaneHeight =
      bounds.height - INSPECTOR_BATCH_BLOCK_RESERVE - PANEL_EDGE_GAP * 2;
    const useInlineBatchAvoidance =
      blockLaneHeight <= FLOATING_PANEL_MIN_HEIGHT;
    const canStackBetweenMinimapAndTrack =
      bounds.height >=
      MINIMAP_BLOCK_RESERVE +
        PANEL_EDGE_GAP * 2 +
        FLOATING_PANEL_MIN_HEIGHT +
        SHOT_TRACK_EXPANDED_HEIGHT;
    const inlineLaneWidth = useInlineBatchAvoidance
      ? Math.max(
          FLOATING_PANEL_MIN_WIDTH,
          bounds.width -
            TRACK_BATCH_LANE_RESERVE -
            PANEL_EDGE_GAP -
            (canStackBetweenMinimapAndTrack ? 0 : MINIMAP_INLINE_RESERVE),
        )
      : bounds.width;
    const width = Math.min(
      INSPECTOR_WIDTH,
      Math.max(FLOATING_PANEL_MIN_WIDTH, inlineLaneWidth),
    );
    const height = Math.min(
      INSPECTOR_HEIGHT,
      Math.max(
        FLOATING_PANEL_MIN_HEIGHT,
        bounds.height -
          INSPECTOR_BATCH_BLOCK_RESERVE -
          PANEL_EDGE_GAP * 2,
      ),
    );
    return clampPanelRect(
      {
        x: useInlineBatchAvoidance ? inlineLaneWidth - width : bounds.width - width,
        y: useInlineBatchAvoidance && canStackBetweenMinimapAndTrack
          ? MINIMAP_BLOCK_RESERVE + PANEL_EDGE_GAP
          : PANEL_EDGE_GAP,
        width,
        height,
      },
      bounds,
      "shot-inspector",
    );
  }

  const width = Math.min(
    TRACK_WIDTH,
    Math.max(
      FLOATING_PANEL_MIN_WIDTH,
      bounds.width - TRACK_BATCH_LANE_RESERVE - PANEL_EDGE_GAP,
    ),
  );
  const height = SHOT_TRACK_COLLAPSED_HEIGHT;
  return clampPanelRect(
    {
      x: PANEL_EDGE_GAP,
      y: bounds.height - height,
      width,
      height,
    },
    bounds,
    "shot-track",
  );
}

export function movePanelRect(
  rect: FloatingPanelRect,
  delta: Readonly<{ x: number; y: number }>,
  bounds: FloatingPanelBounds,
  panelId: FloatingPanelId,
): FloatingPanelRect {
  return clampPanelRect(
    {
      ...rect,
      x: rect.x + finiteOr(delta.x, 0),
      y: rect.y + finiteOr(delta.y, 0),
    },
    bounds,
    panelId,
  );
}

export function resizePanelRect(
  rect: FloatingPanelRect,
  delta: Readonly<{ width: number; height: number }>,
  bounds: FloatingPanelBounds,
  edge: FloatingPanelResizeEdge = "bottom-right",
  panelId: FloatingPanelId,
): FloatingPanelRect {
  const normalized = normalizedBounds(bounds);
  const resizeLeft = edge === "left" || edge === "top-left" || edge === "bottom-left";
  const resizeRight = edge === "right" || edge === "top-right" || edge === "bottom-right";
  const resizeTop = edge === "top" || edge === "top-left" || edge === "top-right";
  const resizeBottom = edge === "bottom" || edge === "bottom-left" || edge === "bottom-right";
  const horizontal = resizeAxis(
    rect.x,
    rect.x + rect.width,
    finiteOr(delta.width, 0),
    resizeLeft,
    resizeRight,
    FLOATING_PANEL_MIN_WIDTH,
  );
  const vertical = resizeAxis(
    rect.y,
    rect.y + rect.height,
    finiteOr(delta.height, 0),
    resizeTop,
    resizeBottom,
    panelMinimumHeight(panelId, normalized),
  );
  const clampedHorizontal = clampResizedAxis(
    horizontal,
    normalized.width,
    resizeLeft,
    resizeRight,
    FLOATING_PANEL_MIN_WIDTH,
  );
  const clampedVertical = clampResizedAxis(
    vertical,
    normalized.height,
    resizeTop,
    resizeBottom,
    panelMinimumHeight(panelId, normalized),
  );
  return clampPanelRect(
    {
      x: clampedHorizontal.start,
      y: clampedVertical.start,
      width: clampedHorizontal.end - clampedHorizontal.start,
      height: clampedVertical.end - clampedVertical.start,
    },
    normalized,
    panelId,
  );
}

function resizeAxis(
  start: number,
  end: number,
  delta: number,
  resizeStart: boolean,
  resizeEnd: boolean,
  minimum: number,
): Readonly<{ start: number; end: number }> {
  let nextStart = resizeStart ? start + delta : start;
  let nextEnd = resizeEnd ? end + delta : end;
  if (nextEnd - nextStart >= minimum) return { start: nextStart, end: nextEnd };
  if (resizeStart && !resizeEnd) {
    nextStart = nextEnd - minimum;
  } else {
    nextEnd = nextStart + minimum;
  }
  return { start: nextStart, end: nextEnd };
}

function clampResizedAxis(
  axis: Readonly<{ start: number; end: number }>,
  bound: number,
  resizeStart: boolean,
  resizeEnd: boolean,
  minimum: number,
): Readonly<{ start: number; end: number }> {
  if (bound < minimum) return axis;
  if (resizeStart && !resizeEnd) {
    return { start: Math.max(0, axis.start), end: axis.end };
  }
  if (resizeEnd && !resizeStart) {
    return { start: axis.start, end: Math.min(bound, axis.end) };
  }
  return axis;
}

export function panelStorageKey(
  projectId: string,
  panelId: FloatingPanelId,
): string {
  return `ai-canvas:floating-panel:v${STORAGE_VERSION}:${projectId}:${panelId}`;
}

export function serializePanelRect(rect: FloatingPanelRect): string {
  return JSON.stringify({ version: STORAGE_VERSION, rect });
}

export function parseStoredPanelRect(
  serialized: string | null,
  panelId: FloatingPanelId,
): FloatingPanelRect | null {
  if (serialized === null) return null;
  try {
    const envelope: unknown = JSON.parse(serialized);
    if (
      typeof envelope !== "object" ||
      envelope === null ||
      !("version" in envelope) ||
      envelope.version !== STORAGE_VERSION ||
      !("rect" in envelope) ||
      typeof envelope.rect !== "object" ||
      envelope.rect === null
    ) {
      return null;
    }
    const rect = envelope.rect as Record<string, unknown>;
    if (
      typeof rect.x !== "number" ||
      !Number.isFinite(rect.x) ||
      typeof rect.y !== "number" ||
      !Number.isFinite(rect.y) ||
      typeof rect.width !== "number" ||
      !Number.isFinite(rect.width) ||
      rect.width < FLOATING_PANEL_MIN_WIDTH ||
      typeof rect.height !== "number" ||
      !Number.isFinite(rect.height) ||
      rect.height < panelMinimumHeight(panelId)
    ) {
      return null;
    }
    return {
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    };
  } catch {
    return null;
  }
}
