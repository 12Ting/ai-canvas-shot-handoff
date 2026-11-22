import type { XYPosition } from "@xyflow/react";

export const MEDIA_RESERVATION_WIDTH = 312;
export const MEDIA_RESERVATION_HEIGHT = 360;
export const RESERVATION_GAP_X = 40;
export const RESERVATION_GAP_Y = 40;
export const RESERVATION_COLLISION_PADDING = 20;
export const RESERVATION_SEARCH_STEP = 40;

export type MediaIngressObstacle = Readonly<{
  x: number;
  y: number;
  width: number;
  height: number;
}>;

function intersectsObstacle(
  position: XYPosition,
  obstacle: MediaIngressObstacle,
): boolean {
  return (
    position.x <
      obstacle.x + obstacle.width + RESERVATION_COLLISION_PADDING &&
    position.x + MEDIA_RESERVATION_WIDTH + RESERVATION_COLLISION_PADDING >
      obstacle.x &&
    position.y <
      obstacle.y + obstacle.height + RESERVATION_COLLISION_PADDING &&
    position.y + MEDIA_RESERVATION_HEIGHT + RESERVATION_COLLISION_PADDING >
      obstacle.y
  );
}

type SearchOffset = readonly [i: number, j: number];

function directionRank([i, j]: SearchOffset): number {
  if (i === 0 && j === 0) return 0;
  if (i > 0 && Math.abs(i) >= Math.abs(j)) return 0;
  if (j > 0 && Math.abs(j) > Math.abs(i)) return 1;
  if (i < 0 && Math.abs(i) >= Math.abs(j)) return 2;
  return 3;
}

function compareOffsets(a: SearchOffset, b: SearchOffset): number {
  const distance = a[0] * a[0] + a[1] * a[1] - (b[0] * b[0] + b[1] * b[1]);
  if (distance !== 0) return distance;
  const direction = directionRank(a) - directionRank(b);
  if (direction !== 0) return direction;
  if (a[1] !== b[1]) return a[1] - b[1];
  return a[0] - b[0];
}

function offsetsAtRing(radius: number): SearchOffset[] {
  if (radius === 0) return [[0, 0]];
  const offsets: SearchOffset[] = [];
  for (let i = -radius; i <= radius; i += 1) {
    offsets.push([i, -radius], [i, radius]);
  }
  for (let j = -radius + 1; j < radius; j += 1) {
    offsets.push([-radius, j], [radius, j]);
  }
  return offsets.sort(compareOffsets);
}

export function layoutReservations(
  count: number,
  anchor: XYPosition,
  obstacles: readonly MediaIngressObstacle[],
): XYPosition[] {
  if (count <= 0) return [];
  const columns = Math.min(4, Math.ceil(Math.sqrt(count)));
  const positionsAt = ([i, j]: SearchOffset): XYPosition[] =>
    Array.from({ length: count }, (_, index) => ({
      x:
        anchor.x +
        i * RESERVATION_SEARCH_STEP +
        (index % columns) * (MEDIA_RESERVATION_WIDTH + RESERVATION_GAP_X),
      y:
        anchor.y +
        j * RESERVATION_SEARCH_STEP +
        Math.floor(index / columns) *
          (MEDIA_RESERVATION_HEIGHT + RESERVATION_GAP_Y),
    }));

  for (let radius = 0; ; radius += 1) {
    for (const offset of offsetsAtRing(radius)) {
      const positions = positionsAt(offset);
      if (
        positions.every((position) =>
          obstacles.every(
            (obstacle) => !intersectsObstacle(position, obstacle),
          ),
        )
      ) {
        return positions;
      }
    }
  }
}
