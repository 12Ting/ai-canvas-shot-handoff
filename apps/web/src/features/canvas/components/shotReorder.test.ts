import { describe, expect, it } from "vitest";
import {
  dropPlacementFromMidpoint,
  reorderIdsAtPlacement,
} from "./shotReorder";

describe("dropPlacementFromMidpoint", () => {
  it("uses the horizontal midpoint, including midpoint as after", () => {
    const rect = { left: 100, top: 20, width: 80, height: 30 };

    expect(dropPlacementFromMidpoint("horizontal", { clientX: 139, clientY: 0 }, rect)).toBe("before");
    expect(dropPlacementFromMidpoint("horizontal", { clientX: 140, clientY: 0 }, rect)).toBe("after");
    expect(dropPlacementFromMidpoint("horizontal", { clientX: 179, clientY: 0 }, rect)).toBe("after");
  });

  it("uses the vertical midpoint and rejects invalid geometry", () => {
    const rect = { left: 10, top: 60, width: 40, height: 100 };

    expect(dropPlacementFromMidpoint("vertical", { clientX: 0, clientY: 109 }, rect)).toBe("before");
    expect(dropPlacementFromMidpoint("vertical", { clientX: 0, clientY: 110 }, rect)).toBe("after");
    expect(dropPlacementFromMidpoint("vertical", { clientX: 0, clientY: Number.NaN }, rect)).toBeNull();
    expect(dropPlacementFromMidpoint("vertical", { clientX: 0, clientY: 80 }, { ...rect, height: 0 })).toBeNull();
    expect(dropPlacementFromMidpoint("horizontal", { clientX: Infinity, clientY: 0 }, rect)).toBeNull();
  });
});

describe("reorderIdsAtPlacement", () => {
  it("moves source before and after the target in either direction without changing the input", () => {
    const ids = ["one", "two", "three", "four"];

    expect(reorderIdsAtPlacement(ids, "four", "two", "before")).toEqual([
      "one",
      "four",
      "two",
      "three",
    ]);
    expect(reorderIdsAtPlacement(ids, "one", "three", "after")).toEqual([
      "two",
      "three",
      "one",
      "four",
    ]);
    expect(ids).toEqual(["one", "two", "three", "four"]);
  });

  it("rejects same, missing, and duplicate identities", () => {
    expect(reorderIdsAtPlacement(["one", "two"], "one", "one", "before")).toBeNull();
    expect(reorderIdsAtPlacement(["one", "two"], "missing", "two", "before")).toBeNull();
    expect(reorderIdsAtPlacement(["one", "two"], "one", "missing", "after")).toBeNull();
    expect(reorderIdsAtPlacement(["one", "two", "two"], "one", "two", "before")).toBeNull();
  });
});
