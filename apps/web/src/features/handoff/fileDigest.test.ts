import { createHash } from "node:crypto";
import { describe, it, expect } from "vitest";
import { digestStream } from "./fileDigest";
describe("result-file lookup digest", () => {
  it.each([0, 1, 55, 56, 63, 64, 65, 128, 2051])(
    "matches native SHA-256 across chunk boundaries (%i bytes)",
    async (size) => {
      const bytes = Uint8Array.from({ length: size }, (_, i) => (i * 17) % 251);
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          for (let i = 0; i < size; i += 23)
            c.enqueue(bytes.subarray(i, i + 23));
          c.close();
        },
      });
      expect(await digestStream(stream)).toBe(
        createHash("sha256").update(bytes).digest("hex"),
      );
    },
  );
});
