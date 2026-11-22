const FNV_1A_64_OFFSET = 14_695_981_039_346_656_037n;
const FNV_1A_64_PRIME = 1_099_511_628_211n;
const UNSIGNED_64_MASK = (1n << 64n) - 1n;

export function stableShotCode(shotId: string): string {
  const normalized = shotId.replaceAll("-", "").toLowerCase();
  let hash = FNV_1A_64_OFFSET;
  for (let index = 0; index < normalized.length; index += 1) {
    hash ^= BigInt(normalized.charCodeAt(index));
    hash = (hash * FNV_1A_64_PRIME) & UNSIGNED_64_MASK;
  }
  const suffix = hash.toString(36).toUpperCase().slice(-10).padStart(10, "0");
  return `S-${suffix}`;
}

export function shotOrderLabel(order: number): string {
  return `分镜 ${String(order + 1).padStart(2, "0")}`;
}
