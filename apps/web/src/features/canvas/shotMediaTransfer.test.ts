import { describe, expect, it, vi } from "vitest";
import * as canvasTypes from "./canvasTypes";
import {
  isCompatibleShotSlot,
  readShotMediaTransfer,
  SHOT_MEDIA_TRANSFER_TYPE,
  writeShotMediaTransfer,
} from "./shotMediaTransfer";

const REVISION_ID = "11111111-1111-4111-8111-111111111111";
const NODE_ID = "22222222-2222-4222-8222-222222222222";

function dragEventWith(dataTransfer: Partial<DataTransfer> | null): DragEvent {
  return { dataTransfer } as DragEvent;
}

describe("Shot media transfer", () => {
  it("shares canonical UUID validation with Shot store operations", () => {
    const { isCanvasUuid } = canvasTypes as typeof canvasTypes & {
      isCanvasUuid?: (value: unknown) => boolean;
    };

    expect(isCanvasUuid?.(REVISION_ID)).toBe(true);
    expect(isCanvasUuid?.("not-a-uuid")).toBe(false);
  });

  it("writes the typed revision reference with its source node to DataTransfer", () => {
    const setData = vi.fn();
    const event = dragEventWith({ setData });

    writeShotMediaTransfer(event, {
      revisionId: REVISION_ID,
      mediaKind: "image",
      nodeId: NODE_ID,
    });

    expect(setData).toHaveBeenCalledExactlyOnceWith(
      SHOT_MEDIA_TRANSFER_TYPE,
      JSON.stringify({ revisionId: REVISION_ID, mediaKind: "image", nodeId: NODE_ID }),
    );
  });

  it("reads a complete, exact, typed revision reference from DataTransfer", () => {
    const getData = vi.fn(() =>
      JSON.stringify({ revisionId: REVISION_ID, mediaKind: "video", nodeId: NODE_ID }),
    );

    expect(readShotMediaTransfer(dragEventWith({ getData }))).toEqual({
      revisionId: REVISION_ID,
      mediaKind: "video",
      nodeId: NODE_ID,
    });
    expect(getData).toHaveBeenCalledExactlyOnceWith(SHOT_MEDIA_TRANSFER_TYPE);
  });

  it.each([
    ["no DataTransfer", null],
    ["missing data", { getData: () => "" }],
    ["malformed JSON", { getData: () => "{" }],
    ["missing source node", { getData: () => JSON.stringify({ revisionId: REVISION_ID, mediaKind: "image" }) }],
    ["bad UUID", { getData: () => JSON.stringify({ revisionId: "bad", mediaKind: "image" }) }],
    ["bad kind", { getData: () => JSON.stringify({ revisionId: REVISION_ID, mediaKind: "audio" }) }],
    [
      "extra fields",
      {
        getData: () =>
          JSON.stringify({ revisionId: REVISION_ID, mediaKind: "image", nodeId: NODE_ID, localPath: "C:/secret" }),
      },
    ],
    ["array payload", { getData: () => JSON.stringify([REVISION_ID, "image"]) }],
  ] as const)("rejects %s transfer payloads", (_label, dataTransfer) => {
    expect(readShotMediaTransfer(dragEventWith(dataTransfer))).toBeNull();
  });

  it.each([
    ["image", "image", true],
    ["image", "video", false],
    ["video", "video", true],
    ["video", "image", false],
    ["reference", "image", true],
    ["reference", "video", false],
  ] as const)("reports %s slot compatibility for %s media", (slot, mediaKind, compatible) => {
    expect(isCompatibleShotSlot(slot, mediaKind)).toBe(compatible);
  });
});
