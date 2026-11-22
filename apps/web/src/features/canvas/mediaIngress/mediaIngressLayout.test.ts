import { describe, expect, it } from "vitest";
import {
  MEDIA_RESERVATION_HEIGHT,
  MEDIA_RESERVATION_WIDTH,
  RESERVATION_COLLISION_PADDING,
  RESERVATION_GAP_X,
  RESERVATION_GAP_Y,
  RESERVATION_SEARCH_STEP,
  layoutReservations,
} from "./mediaIngressLayout";

describe("layoutReservations", () => {
  it("uses the locked card, group-gap, collision-padding, and search-step values", () => {
    expect(MEDIA_RESERVATION_WIDTH).toBe(312);
    expect(MEDIA_RESERVATION_HEIGHT).toBe(360);
    expect(RESERVATION_GAP_X).toBe(40);
    expect(RESERVATION_GAP_Y).toBe(40);
    expect(RESERVATION_COLLISION_PADDING).toBe(20);
    expect(RESERVATION_SEARCH_STEP).toBe(40);
  });

  it.each([
    [1, 1, 1],
    [2, 2, 1],
    [3, 2, 2],
    [4, 2, 2],
    [5, 3, 2],
    [8, 3, 3],
    [9, 3, 3],
    [16, 4, 4],
    [20, 4, 5],
  ] as const)(
    "lays out %i reservations in %i columns and %i rows, horizontally first",
    (count, columns, rows) => {
      const positions = layoutReservations(count, { x: 10, y: 20 }, []);
      expect(positions).toHaveLength(count);
      expect(new Set(positions.map((position) => position.y)).size).toBe(rows);
      expect(positions).toEqual(
        Array.from({ length: count }, (_, index) => ({
          x: 10 + (index % columns) * (312 + 40),
          y: 20 + Math.floor(index / columns) * (360 + 40),
        })),
      );
    },
  );

  it("searches Chebyshev rings in the locked direction order and moves the whole group", () => {
    const positions = layoutReservations(2, { x: 0, y: 0 }, [
      { x: 0, y: 0, width: 312, height: 360 },
    ]);

    expect(positions).toEqual([
      { x: 360, y: 0 },
      { x: 712, y: 0 },
    ]);
  });

  it("allows an exact 20px collision gap but rejects one pixel less", () => {
    expect(
      layoutReservations(1, { x: 0, y: 0 }, [
        { x: 332, y: 0, width: 280, height: 180 },
      ]),
    ).toEqual([{ x: 0, y: 0 }]);

    expect(
      layoutReservations(1, { x: 0, y: 0 }, [
        { x: 331, y: 0, width: 280, height: 180 },
      ]),
    ).not.toEqual([{ x: 0, y: 0 }]);
  });

  it("has no arbitrary maximum search ring", () => {
    expect(
      layoutReservations(1, { x: 0, y: 0 }, [
        { x: -4_000, y: -4_000, width: 8_000, height: 8_000 },
      ]),
    ).toEqual([{ x: 4_040, y: 0 }]);
  });

  it("returns identical coordinates for identical snapshots", () => {
    const obstacles = [
      { x: -20, y: -20, width: 650, height: 760 },
      { x: 720, y: 10, width: 248, height: 180 },
    ];

    expect(layoutReservations(5, { x: 50, y: 70 }, obstacles)).toEqual(
      layoutReservations(5, { x: 50, y: 70 }, obstacles),
    );
  });
});
