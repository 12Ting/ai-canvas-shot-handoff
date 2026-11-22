export type DropAxis = "horizontal" | "vertical";
export type DropPlacement = "before" | "after";

type DropPointer = Pick<MouseEvent, "clientX" | "clientY">;
type DropRect = Pick<DOMRect, "left" | "top" | "width" | "height">;

export function dropPlacementFromMidpoint(
  axis: DropAxis,
  pointer: DropPointer,
  rect: DropRect,
): DropPlacement | null {
  const coordinate = axis === "horizontal" ? pointer.clientX : pointer.clientY;
  const offset = axis === "horizontal" ? rect.left : rect.top;
  const size = axis === "horizontal" ? rect.width : rect.height;
  if (![coordinate, offset, size].every(Number.isFinite) || size <= 0) return null;
  return coordinate < offset + size / 2 ? "before" : "after";
}

export function reorderIdsAtPlacement(
  ids: readonly string[],
  sourceId: string,
  targetId: string,
  placement: DropPlacement,
): string[] | null {
  if (sourceId === targetId || new Set(ids).size !== ids.length) return null;
  if (!ids.includes(sourceId) || !ids.includes(targetId)) return null;
  const reordered = ids.filter((id) => id !== sourceId);
  const targetIndex = reordered.indexOf(targetId);
  if (targetIndex < 0) return null;
  reordered.splice(targetIndex + (placement === "after" ? 1 : 0), 0, sourceId);
  return reordered;
}
