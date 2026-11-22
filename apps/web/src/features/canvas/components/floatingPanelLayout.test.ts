import { describe, expect, it } from "vitest";
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
  type FloatingPanelBounds,
} from "./floatingPanelLayout";

const bounds: FloatingPanelBounds = { width: 1200, height: 900 };

function mobileBatchTop(
  mobileBounds: FloatingPanelBounds & { viewportHeight?: number },
): number {
  return (
    mobileBounds.height -
    16 -
    Math.min(
      416,
      (mobileBounds.viewportHeight ?? mobileBounds.height) * 0.48,
    )
  );
}

describe("floatingPanelLayout", () => {
  it("places the Inspector flush against the workspace right edge", () => {
    const rect = defaultPanelRect("shot-inspector", bounds);

    expect(rect).toMatchObject({ x: 852, width: 348 });
    expect(rect.x + rect.width).toBe(bounds.width);
  });

  it("keeps the default Inspector above the B2 batch card maximum block lane", () => {
    const rect = defaultPanelRect("shot-inspector", bounds);

    expect(rect.y + rect.height).toBeLessThanOrEqual(bounds.height - 592);
  });

  it("moves the default desktop Inspector left of B2 when the block lane is too short", () => {
    const shortBounds = { width: 800, height: 720 };
    const rect = defaultPanelRect("shot-inspector", shortBounds);

    expect(rect.width).toBe(348);
    expect(rect.y).toBe(192);
    expect(rect.x + rect.width).toBeLessThanOrEqual(
      shortBounds.width - 416 - 16,
    );
  });

  it("uses the inline gap before the MiniMap when the short workspace cannot stack panels", () => {
    const shortBounds = { width: 1280, height: 456 };
    const rect = defaultPanelRect("shot-inspector", shortBounds);

    expect(rect).toMatchObject({ x: 284, y: 16, width: 348, height: 112 });
    expect(rect.x + rect.width).toBeLessThanOrEqual(
      shortBounds.width - 416 - 16 - 216,
    );
  });

  it("defaults the Track to a collapsed drawer flush with the workspace bottom", () => {
    const rect = defaultPanelRect("shot-track", bounds);

    expect(rect.x).toBe(16);
    expect(rect.height).toBe(SHOT_TRACK_COLLAPSED_HEIGHT);
    expect(rect.y + rect.height).toBe(bounds.height);
    expect(rect.x + rect.width).toBeLessThanOrEqual(bounds.width - 416);
  });

  it.each([
    { width: 600, height: 900 },
    { width: 720, height: 900 },
    { width: 600, height: 500 },
  ])(
    "vertically stacks both operable panels above B2 at $width×$height",
    (mobileBounds) => {
      const inspector = defaultPanelRect("shot-inspector", mobileBounds);
      const track = defaultPanelRect("shot-track", mobileBounds);

      expect(track.width).toBeGreaterThanOrEqual(280);
      expect(track.height).toBe(128);
      expect(inspector.width).toBeGreaterThanOrEqual(280);
      expect(inspector.height).toBeGreaterThanOrEqual(112);
      expect(track.y).toBeGreaterThanOrEqual(0);
      expect(track.y + track.height).toBeLessThanOrEqual(inspector.y);
      expect(inspector.y + inspector.height).toBeLessThanOrEqual(
        mobileBatchTop(mobileBounds),
      );
      expect(inspector.x + inspector.width).toBe(mobileBounds.width);
      expect(track.x + track.width).toBeLessThanOrEqual(
        mobileBounds.width - 160,
      );
    },
  );

  it("uses viewport 48vh and keeps both handles above B2 when the full stack cannot fit", () => {
    const mobileBounds: FloatingPanelBounds & { viewportHeight: number } = {
      width: 600,
      height: 500,
      viewportHeight: 600,
    };
    const inspector = defaultPanelRect("shot-inspector", mobileBounds);
    const track = defaultPanelRect("shot-track", mobileBounds);
    const batchTop = mobileBatchTop(mobileBounds);

    expect(batchTop).toBe(196);
    expect(track.width).toBeGreaterThanOrEqual(280);
    expect(track.height).toBe(128);
    expect(inspector.width).toBeGreaterThanOrEqual(280);
    expect(inspector.height).toBeGreaterThanOrEqual(112);
    expect(track.y).toBe(0);
    expect(track.y + track.height).toBeLessThanOrEqual(inspector.y);
    expect(track.y + 44).toBeLessThanOrEqual(batchTop);
    expect(inspector.y + 44).toBeLessThanOrEqual(batchTop);
  });

  it("keeps the complete mobile stack above viewport-based B2 when space is sufficient", () => {
    const mobileBounds: FloatingPanelBounds & { viewportHeight: number } = {
      width: 600,
      height: 560,
      viewportHeight: 600,
    };
    const inspector = defaultPanelRect("shot-inspector", mobileBounds);
    const track = defaultPanelRect("shot-track", mobileBounds);

    expect(track.y + track.height).toBeLessThanOrEqual(inspector.y);
    expect(inspector.y + inspector.height).toBeLessThanOrEqual(
      mobileBatchTop(mobileBounds),
    );
  });

  it("switches to horizontal left/right avoidance immediately above 720px", () => {
    const thresholdBounds = { width: 721, height: 900 };
    const inspector = defaultPanelRect("shot-inspector", thresholdBounds);
    const track = defaultPanelRect("shot-track", thresholdBounds);

    expect(track.x + track.width).toBeLessThanOrEqual(inspector.x);
    expect(track.x + track.width).toBeLessThanOrEqual(
      thresholdBounds.width - 416,
    );
    expect(track.y + track.height).toBe(thresholdBounds.height);
    expect(inspector.x + inspector.width).toBe(thresholdBounds.width);
  });

  it("builds a versioned project and panel scoped storage key", () => {
    expect(panelStorageKey("project-a", "shot-inspector")).toBe(
      "ai-canvas:floating-panel:v1:project-a:shot-inspector",
    );
    expect(panelStorageKey("project-b", "shot-track")).toBe(
      "ai-canvas:floating-panel:v1:project-b:shot-track",
    );
  });

  it("round-trips a valid version-one storage envelope", () => {
    const rect = { x: 42, y: 84, width: 360, height: 240 };

    expect(parseStoredPanelRect(serializePanelRect(rect), "shot-inspector")).toEqual(
      rect,
    );
  });

  it("accepts a collapsed Track rect without weakening the Inspector minimum height", () => {
    const collapsedTrack = {
      x: 16,
      y: bounds.height - SHOT_TRACK_COLLAPSED_HEIGHT,
      width: 760,
      height: SHOT_TRACK_COLLAPSED_HEIGHT,
    };
    const serialized = serializePanelRect(collapsedTrack);

    expect(parseStoredPanelRect(serialized, "shot-track")).toEqual(collapsedTrack);
    expect(parseStoredPanelRect(serialized, "shot-inspector")).toBeNull();
  });

  it("migrates a legacy 60px desktop Track drawer to the bottom-edge bar", () => {
    expect(
      restorePanelRect(
        { x: 16, y: 824, width: 760, height: 60 },
        bounds,
        "shot-track",
      ),
    ).toEqual({
      x: 16,
      y: bounds.height - SHOT_TRACK_COLLAPSED_HEIGHT,
      width: 760,
      height: SHOT_TRACK_COLLAPSED_HEIGHT,
    });
  });

  it("restores a legacy 60px Track record to the safe mobile default", () => {
    const mobileBounds = { width: 720, height: 900 };

    expect(
      restorePanelRect(
        { x: 16, y: 824, width: 544, height: 60 },
        mobileBounds,
        "shot-track",
      ),
    ).toEqual(defaultPanelRect("shot-track", mobileBounds));
  });

  it("restores a current collapsed Track record to the safe mobile default", () => {
    const mobileBounds = { width: 720, height: 900 };

    expect(
      restorePanelRect(
        { x: 16, y: 888, width: 544, height: SHOT_TRACK_COLLAPSED_HEIGHT },
        mobileBounds,
        "shot-track",
      ),
    ).toEqual(defaultPanelRect("shot-track", mobileBounds));
  });

  it("does not let Pointer resizing collapse the Track below 128px on mobile", () => {
    const mobileBounds = { width: 720, height: 900 };
    const track = defaultPanelRect("shot-track", mobileBounds);

    expect(
      resizePanelRect(
        track,
        { width: 0, height: 500 },
        mobileBounds,
        "top",
        "shot-track",
      ).height,
    ).toBe(128);
  });

  it.each([
    ["malformed JSON", "{bad json"],
    ["unsupported version", JSON.stringify({ version: 2, rect: { x: 0, y: 0, width: 348, height: 300 } })],
    ["missing rect", JSON.stringify({ version: 1 })],
    ["non-finite coordinate", JSON.stringify({ version: 1, rect: { x: "NaN", y: 0, width: 348, height: 300 } })],
    ["width below the minimum", JSON.stringify({ version: 1, rect: { x: 0, y: 0, width: 0, height: 300 } })],
    ["height below the minimum", JSON.stringify({ version: 1, rect: { x: 0, y: 0, width: 348, height: 0 } })],
  ])("rejects %s stored geometry", (_case, serialized) => {
    expect(parseStoredPanelRect(serialized, "shot-inspector")).toBeNull();
  });

  it("clamps width and height before clamping x and y", () => {
    expect(
      clampPanelRect(
        { x: 1190, y: 890, width: 500, height: 500 },
        bounds,
        "shot-inspector",
      ),
    ).toEqual({ x: 700, y: 400, width: 500, height: 500 });
  });

  it("keeps at least a 44px handle area visible when the workspace is smaller than the panel minimum", () => {
    expect(
      clampPanelRect(
        { x: -1000, y: -1000, width: 320, height: 180 },
        { width: 120, height: 80 },
        "shot-inspector",
      ),
    ).toEqual({ x: -276, y: -136, width: 320, height: 180 });
  });

  it("moves and resizes through the same workspace clamp", () => {
    const rect = { x: 100, y: 100, width: 348, height: 240 };

    expect(
      movePanelRect(rect, { x: 30, y: -20 }, bounds, "shot-inspector"),
    ).toEqual({
      x: 130,
      y: 80,
      width: 348,
      height: 240,
    });
    expect(
      resizePanelRect(
        rect,
        { width: 40, height: 30 },
        bounds,
        "bottom-right",
        "shot-inspector",
      ),
    ).toEqual({
      x: 100,
      y: 100,
      width: 388,
      height: 270,
    });
  });

  it("anchors the opposite edge for direct top and left resizing", () => {
    const rect = { x: 100, y: 100, width: 348, height: 240 };

    expect(
      resizePanelRect(
        rect,
        { width: -40, height: -30 },
        bounds,
        "top-left",
        "shot-inspector",
      ),
    ).toEqual({
      x: 60,
      y: 70,
      width: 388,
      height: 270,
    });
    expect(
      resizePanelRect(
        rect,
        { width: 200, height: 0 },
        bounds,
        "left",
        "shot-inspector",
      ),
    ).toEqual({
      x: 168,
      y: 100,
      width: 280,
      height: 240,
    });
    expect(
      resizePanelRect(
        rect,
        { width: -300, height: 0 },
        bounds,
        "left",
        "shot-inspector",
      ),
    ).toEqual({
      x: 0,
      y: 100,
      width: 448,
      height: 240,
    });
  });

  it("lets the Track collapse to its bottom-edge resize bar", () => {
    expect(
      resizePanelRect(
        { x: 16, y: 704, width: 760, height: 180 },
        { width: 0, height: 200 },
        bounds,
        "top",
        "shot-track",
      ),
    ).toEqual({
      x: 16,
      y: bounds.height - SHOT_TRACK_COLLAPSED_HEIGHT,
      width: 760,
      height: SHOT_TRACK_COLLAPSED_HEIGHT,
    });
  });
});
