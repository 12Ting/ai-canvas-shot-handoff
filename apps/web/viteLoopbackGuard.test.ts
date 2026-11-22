import { describe, expect, it, vi } from "vitest";
import {
  createLoopbackPeerGuard,
  isAllowedLoopbackPeer,
  normalizeLoopbackAddress,
} from "./viteLoopbackGuard";

describe("Vite loopback peer guard", () => {
  it.each([
    ["127.0.0.1", "127.0.0.1", true],
    ["::ffff:127.0.0.1", "127.0.0.1", true],
    ["::1", null, false],
    ["127.0.0.2", null, false],
    ["::ffff:127.0.0.2", null, false],
    ["192.168.1.20", null, false],
    ["", null, false],
    [undefined, null, false],
  ] as const)(
    "normalizes %s to %s and allows=%s",
    (input, normalized, allowed) => {
      expect(normalizeLoopbackAddress(input)).toBe(normalized);
      expect(isAllowedLoopbackPeer(input)).toBe(allowed);
    },
  );

  it("continues middleware handling for the literal allowed peer", () => {
    const next = vi.fn();
    const response = {
      statusCode: 200,
      setHeader: vi.fn(),
      end: vi.fn(),
    };

    createLoopbackPeerGuard()(
      { socket: { remoteAddress: "127.0.0.1" } },
      response,
      next,
    );

    expect(next).toHaveBeenCalledOnce();
    expect(response.end).not.toHaveBeenCalled();
  });

  it("returns 403 and never continues for a disallowed peer", () => {
    const next = vi.fn();
    const response = {
      statusCode: 200,
      setHeader: vi.fn(),
      end: vi.fn(),
    };

    createLoopbackPeerGuard()(
      { socket: { remoteAddress: "::1" } },
      response,
      next,
    );

    expect(response.statusCode).toBe(403);
    expect(response.setHeader).toHaveBeenCalledWith(
      "Content-Type",
      "text/plain; charset=utf-8",
    );
    expect(response.end).toHaveBeenCalledWith("Loopback peer required.");
    expect(next).not.toHaveBeenCalled();
  });
});
