import { isCanvasUuid, type MediaKind } from "./canvasTypes";

export type ShotMediaTransfer = Readonly<{
  revisionId: string;
  mediaKind: MediaKind;
  nodeId: string;
}>;

export const SHOT_MEDIA_TRANSFER_TYPE = "application/x-ai-canvas-media-revision";

function isShotMediaTransfer(value: unknown): value is ShotMediaTransfer {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const fields = value as Record<string, unknown>;
  return (
    Object.keys(fields).length === 3 &&
    Object.prototype.hasOwnProperty.call(fields, "revisionId") &&
    Object.prototype.hasOwnProperty.call(fields, "mediaKind") &&
    Object.prototype.hasOwnProperty.call(fields, "nodeId") &&
    isCanvasUuid(fields.revisionId) &&
    isCanvasUuid(fields.nodeId) &&
    (fields.mediaKind === "image" || fields.mediaKind === "video")
  );
}

export function isCompatibleShotSlot(
  slot: "image" | "video" | "reference",
  mediaKind: MediaKind,
): boolean {
  return (
    (slot === "image" && mediaKind === "image") ||
    (slot === "video" && mediaKind === "video") ||
    (slot === "reference" && mediaKind === "image")
  );
}

export function writeShotMediaTransfer(event: DragEvent, value: ShotMediaTransfer): void {
  event.dataTransfer?.setData(SHOT_MEDIA_TRANSFER_TYPE, JSON.stringify(value));
}

export function readShotMediaTransfer(event: DragEvent): ShotMediaTransfer | null {
  try {
    const raw = event.dataTransfer?.getData(SHOT_MEDIA_TRANSFER_TYPE);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    return isShotMediaTransfer(value)
      ? {
          revisionId: value.revisionId,
          mediaKind: value.mediaKind,
          nodeId: value.nodeId,
        }
      : null;
  } catch {
    return null;
  }
}
