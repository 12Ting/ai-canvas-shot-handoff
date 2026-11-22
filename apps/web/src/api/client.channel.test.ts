import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type ChannelListener = (event: { data: unknown }) => void;

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = [];

  readonly name: string;
  readonly postMessage = vi.fn();
  readonly close = vi.fn();
  private readonly listeners = new Set<ChannelListener>();

  constructor(name: string) {
    this.name = name;
    FakeBroadcastChannel.instances.push(this);
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (type === "message") this.listeners.add(listener as unknown as ChannelListener);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (type === "message") this.listeners.delete(listener as unknown as ChannelListener);
  }

  emit(data: unknown): void {
    for (const listener of this.listeners) listener({ data });
  }
}

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function authorization(init?: RequestInit): string | null {
  return new Headers(init?.headers).get("Authorization");
}

describe("BroadcastChannel session handoff", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.clear();
    history.replaceState({}, "", "/");
    FakeBroadcastChannel.instances = [];
    vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
  });

  afterEach(() => {
    window.dispatchEvent(new Event("pagehide"));
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("ignores malformed channel payloads without fetching or mutating the committed session", async () => {
    sessionStorage.setItem("ai-canvas-session", "existing-token");
    const fetchMock = vi.fn().mockResolvedValue(response({ mode: "session" }));
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    await bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    const storageSpy = vi.spyOn(Storage.prototype, "setItem");
    fetchMock.mockClear();
    channel.postMessage.mockClear();

    const malformedPayloads: unknown[] = [
      null,
      42,
      "message",
      {},
      { type: "session-rotated", token: "" },
      { type: "session-rotated", token: "   " },
      { type: "session-rotated", token: { nested: true } },
      { type: "response", requestId: "request-1", token: null },
      { type: "request", requestId: { nested: true } },
    ];

    for (const payload of malformedPayloads) {
      expect(() => channel.emit(payload)).not.toThrow();
    }

    expect(fetchMock).not.toHaveBeenCalled();
    expect(storageSpy).not.toHaveBeenCalled();
    expect(channel.postMessage).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("existing-token");
  });

  it("attests a rotation before committing it and responds only with the committed token", async () => {
    sessionStorage.setItem("ai-canvas-session", "existing-token");
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const token = authorization(init);
      if (token === "Bearer existing-token" || token === "Bearer rotated-token") {
        return response({ mode: "session" });
      }
      throw new Error(`unexpected authorization: ${token}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    await bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    channel.postMessage.mockClear();

    channel.emit({ type: "session-rotated", token: "rotated-token" });
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("existing-token");
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotated-token");
    });
    channel.emit({ type: "request", requestId: "request-1" });

    expect(channel.postMessage).toHaveBeenCalledWith({
      type: "response",
      requestId: "request-1",
      token: "rotated-token",
    });
  });

  it("rejects a rotation whose protected status fails and ignores a forged mode field", async () => {
    sessionStorage.setItem("ai-canvas-session", "existing-token");
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (authorization(init) === "Bearer existing-token") {
        return response({ mode: "session" });
      }
      return response({}, 401);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    await bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];

    channel.emit({
      type: "session-rotated",
      token: "untrusted-token",
      mode: "development-auto-session",
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    expect(sessionStorage.getItem("ai-canvas-session")).toBe("existing-token");
    expect(authorization(fetchMock.mock.calls[1]?.[1])).toBe("Bearer untrusted-token");
  });

  it("lets the latest verified rotation win over a slower stale verification", async () => {
    sessionStorage.setItem("ai-canvas-session", "existing-token");
    const slowStatus = deferred<Response>();
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const token = authorization(init);
      if (token === "Bearer existing-token" || token === "Bearer fast-token") {
        return response({ mode: "session" });
      }
      if (token === "Bearer slow-token") return slowStatus.promise;
      throw new Error(`unexpected authorization: ${token}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    await bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];

    channel.emit({ type: "session-rotated", token: "slow-token" });
    channel.emit({ type: "session-rotated", token: "fast-token" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("fast-token");
    });
    slowStatus.resolve(response({ mode: "session" }));
    await slowStatus.promise;
    await Promise.resolve();

    expect(sessionStorage.getItem("ai-canvas-session")).toBe("fast-token");
  });

  it("does not let a stale rejected rotation clear a newer committed token", async () => {
    sessionStorage.setItem("ai-canvas-session", "existing-token");
    const staleStatus = deferred<Response>();
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const token = authorization(init);
      if (token === "Bearer existing-token" || token === "Bearer current-token") {
        return response({ mode: "session" });
      }
      if (token === "Bearer stale-token") return staleStatus.promise;
      throw new Error(`unexpected authorization: ${token}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    await bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];

    channel.emit({ type: "session-rotated", token: "stale-token" });
    channel.emit({ type: "session-rotated", token: "current-token" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("current-token");
    });
    staleStatus.resolve(response({}, 401));
    await staleStatus.promise;
    await Promise.resolve();

    expect(sessionStorage.getItem("ai-canvas-session")).toBe("current-token");
  });

  it("lets a newer rotation replace a delayed exchange before its token is attested", async () => {
    history.replaceState({}, "", "/#mode=review&bootstrap=one-time");
    const exchangeResponse = deferred<Response>();
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), location.href).pathname;
      if (path === "/api/v1/session/exchange") return exchangeResponse.promise;
      if (authorization(init) === "Bearer rotation-token-b") {
        return response({ mode: "development-auto-session" });
      }
      if (authorization(init) === "Bearer exchange-token-a") {
        return response({ mode: "session" });
      }
      throw new Error(`unexpected request: ${path} ${authorization(init)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    const pendingBootstrap = bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    expect(location.hash).toBe("#mode=review");

    channel.emit({ type: "session-rotated", token: "rotation-token-b" });
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
    });
    exchangeResponse.resolve(response({ access_token: "exchange-token-a" }));

    await expect(pendingBootstrap).resolves.toBe("development-auto-session");
    expect(fetchMock.mock.calls.some(([, init]) => (
      authorization(init) === "Bearer exchange-token-a"
    ))).toBe(false);
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
  });

  it("lets a newer rotation replace a delayed development response before attestation", async () => {
    const developmentResponse = deferred<Response>();
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), location.href).pathname;
      if (path === "/api/v1/session/development") return developmentResponse.promise;
      if (authorization(init) === "Bearer rotation-token-b") {
        return response({ mode: "development-auto-session" });
      }
      if (authorization(init) === "Bearer development-token-a") {
        return response({ mode: "session" });
      }
      throw new Error(`unexpected request: ${path} ${authorization(init)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    const pendingBootstrap = bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    expect(fetchMock).toHaveBeenCalledOnce();

    channel.emit({ type: "session-rotated", token: "rotation-token-b" });
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
    });
    developmentResponse.resolve(response({ access_token: "development-token-a" }));

    await expect(pendingBootstrap).resolves.toBe("development-auto-session");
    expect(fetchMock.mock.calls.some(([, init]) => (
      authorization(init) === "Bearer development-token-a"
    ))).toBe(false);
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
  });

  it("lets a newer rotation replace a delayed peer response before attestation", async () => {
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), location.href).pathname;
      if (path === "/api/v1/session/development") {
        return response({ code: "development_session_disabled" }, 403);
      }
      if (authorization(init) === "Bearer rotation-token-b") {
        return response({ mode: "development-auto-session" });
      }
      if (authorization(init) === "Bearer peer-token-a") {
        return response({ mode: "session" });
      }
      throw new Error(`unexpected request: ${path} ${authorization(init)}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    const pendingBootstrap = bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    await vi.waitFor(() => {
      expect(channel.postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ type: "request" }),
      );
    });
    const request = channel.postMessage.mock.calls.find(
      ([message]) => (message as { type?: string }).type === "request",
    )?.[0] as { requestId: string };

    channel.emit({ type: "session-rotated", token: "rotation-token-b" });
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
    });
    channel.emit({
      type: "response",
      requestId: request.requestId,
      token: "peer-token-a",
    });

    await expect(pendingBootstrap).resolves.toBe("development-auto-session");
    expect(fetchMock.mock.calls.some(([, init]) => (
      authorization(init) === "Bearer peer-token-a"
    ))).toBe(false);
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
  });

  it("lets a newer rotation replace a slow stored-token attestation and its mode", async () => {
    sessionStorage.setItem("ai-canvas-session", "stored-token-a");
    const storedStatus = deferred<Response>();
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const token = authorization(init);
      if (token === "Bearer stored-token-a") return storedStatus.promise;
      if (token === "Bearer rotation-token-b") {
        return response({ mode: "development-auto-session" });
      }
      throw new Error(`unexpected authorization: ${token}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    const pendingBootstrap = bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    expect(fetchMock).toHaveBeenCalledOnce();

    channel.emit({ type: "session-rotated", token: "rotation-token-b" });
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
    });
    storedStatus.resolve(response({ mode: "session" }));

    await expect(pendingBootstrap).resolves.toBe("development-auto-session");
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
  });

  it("lets a newer rotation replace a slow development-token attestation and its mode", async () => {
    const developmentStatus = deferred<Response>();
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), location.href).pathname;
      if (path === "/api/v1/session/development") {
        return response({ access_token: "development-token-a" });
      }
      const token = authorization(init);
      if (token === "Bearer development-token-a") return developmentStatus.promise;
      if (token === "Bearer rotation-token-b") {
        return response({ mode: "development-auto-session" });
      }
      throw new Error(`unexpected authorization: ${token}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    const pendingBootstrap = bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some(([, init]) => (
        authorization(init) === "Bearer development-token-a"
      ))).toBe(true);
    });

    channel.emit({ type: "session-rotated", token: "rotation-token-b" });
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
    });
    developmentStatus.resolve(response({ mode: "session" }));

    await expect(pendingBootstrap).resolves.toBe("development-auto-session");
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
  });

  it("lets a newer rotation replace a slow peer-token attestation and its mode", async () => {
    const peerStatus = deferred<Response>();
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), location.href).pathname;
      if (path === "/api/v1/session/development") {
        return response({ code: "development_session_disabled" }, 403);
      }
      const token = authorization(init);
      if (token === "Bearer peer-token-a") return peerStatus.promise;
      if (token === "Bearer rotation-token-b") {
        return response({ mode: "development-auto-session" });
      }
      throw new Error(`unexpected authorization: ${token}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession } = await import("./client");
    const pendingBootstrap = bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    await vi.waitFor(() => {
      expect(channel.postMessage).toHaveBeenCalledWith(
        expect.objectContaining({ type: "request" }),
      );
    });
    const request = channel.postMessage.mock.calls.find(
      ([message]) => (message as { type?: string }).type === "request",
    )?.[0] as { requestId: string };
    channel.emit({
      type: "response",
      requestId: request.requestId,
      token: "peer-token-a",
    });
    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some(([, init]) => (
        authorization(init) === "Bearer peer-token-a"
      ))).toBe(true);
    });

    channel.emit({ type: "session-rotated", token: "rotation-token-b" });
    await vi.waitFor(() => {
      expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
    });
    peerStatus.resolve(response({ mode: "session" }));

    await expect(pendingBootstrap).resolves.toBe("development-auto-session");
    expect(sessionStorage.getItem("ai-canvas-session")).toBe("rotation-token-b");
  });

  it.each(["stored", "development", "peer"] as const)(
    "cancels a pending %s attestation on pagehide without committing or recreating the channel",
    async (source) => {
      const token = `${source}-token-a`;
      if (source === "stored") {
        sessionStorage.setItem("ai-canvas-session", token);
      }
      const pendingStatus = deferred<Response>();
      const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(url), location.href).pathname;
        if (path === "/api/v1/session/development") {
          return source === "development"
            ? response({ access_token: token })
            : response({ code: "development_session_disabled" }, 403);
        }
        if (path === "/api/v1/session/status" && authorization(init) === `Bearer ${token}`) {
          return pendingStatus.promise;
        }
        throw new Error(`unexpected request: ${path} ${authorization(init)}`);
      });
      vi.stubGlobal("fetch", fetchMock);
      const { bootstrapSession } = await import("./client");
      const pendingBootstrap = bootstrapSession();
      const channel = FakeBroadcastChannel.instances[0];

      if (source === "peer") {
        await vi.waitFor(() => {
          expect(channel.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: "request" }),
          );
        });
        const request = channel.postMessage.mock.calls.find(
          ([message]) => (message as { type?: string }).type === "request",
        )?.[0] as { requestId: string };
        channel.emit({ type: "response", requestId: request.requestId, token });
      }
      await vi.waitFor(() => {
        expect(fetchMock.mock.calls.some(([, init]) => (
          authorization(init) === `Bearer ${token}`
        ))).toBe(true);
      });
      channel.postMessage.mockClear();

      window.dispatchEvent(new PageTransitionEvent("pagehide"));
      pendingStatus.resolve(response({ mode: "session" }));

      await expect(pendingBootstrap).rejects.toThrow("cancelled");
      await Promise.resolve();
      expect(sessionStorage.getItem("ai-canvas-session")).toBeNull();
      expect(channel.postMessage).not.toHaveBeenCalled();
      expect(channel.close).toHaveBeenCalledOnce();
      expect(FakeBroadcastChannel.instances).toHaveLength(1);
    },
  );

  it.each(["exchange", "development", "peer"] as const)(
    "cancels pending %s token acquisition on pagehide before any attestation or channel recreation",
    async (source) => {
      const token = `${source}-token-a`;
      if (source === "exchange") {
        history.replaceState({}, "", "/#bootstrap=one-time");
      }
      const tokenResponse = deferred<Response>();
      const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(url), location.href).pathname;
        if (path === "/api/v1/session/exchange" && source === "exchange") {
          return tokenResponse.promise;
        }
        if (path === "/api/v1/session/development") {
          if (source === "development") return tokenResponse.promise;
          if (source === "peer") {
            return response({ code: "development_session_disabled" }, 403);
          }
        }
        if (path === "/api/v1/session/status" && authorization(init) === `Bearer ${token}`) {
          return response({ mode: "session" });
        }
        throw new Error(`unexpected request: ${path} ${authorization(init)}`);
      });
      vi.stubGlobal("fetch", fetchMock);
      const { bootstrapSession } = await import("./client");
      const pendingBootstrap = bootstrapSession();
      const channel = FakeBroadcastChannel.instances[0];
      let peerRequestId: string | null = null;

      if (source === "peer") {
        await vi.waitFor(() => {
          expect(channel.postMessage).toHaveBeenCalledWith(
            expect.objectContaining({ type: "request" }),
          );
        });
        const peerRequest = channel.postMessage.mock.calls.find(
          ([message]) => (message as { type?: string }).type === "request",
        )?.[0];
        expect(peerRequest).toBeDefined();
        peerRequestId = (peerRequest as { requestId: string }).requestId;
      } else {
        await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
      }
      channel.postMessage.mockClear();

      window.dispatchEvent(new PageTransitionEvent("pagehide"));
      if (source === "peer") {
        channel.emit({ type: "response", requestId: peerRequestId, token });
      } else {
        tokenResponse.resolve(response({ access_token: token }));
      }

      await expect(pendingBootstrap).rejects.toThrow("cancelled");
      await Promise.resolve();
      await Promise.resolve();
      expect(fetchMock.mock.calls.some(([, init]) => (
        authorization(init) === `Bearer ${token}`
      ))).toBe(false);
      expect(sessionStorage.getItem("ai-canvas-session")).toBeNull();
      expect(channel.postMessage).not.toHaveBeenCalled();
      expect(channel.close).toHaveBeenCalledOnce();
      expect(FakeBroadcastChannel.instances).toHaveLength(1);
    },
  );

  it("accepts a peer response only after protected status attests it", async () => {
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), location.href).pathname;
      if (path === "/api/v1/session/development") {
        return response({ code: "development_session_disabled" }, 403);
      }
      expect(path).toBe("/api/v1/session/status");
      expect(authorization(init)).toBe("Bearer peer-token");
      return response({ mode: "session" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { bootstrapSession, getSessionToken } = await import("./client");
    const pendingBootstrap = bootstrapSession();
    const channel = FakeBroadcastChannel.instances[0];
    await vi.waitFor(() => expect(channel.postMessage).toHaveBeenCalled());
    const request = channel.postMessage.mock.calls.find(
      ([message]) => (message as { type?: string }).type === "request",
    )?.[0] as { requestId: string };

    channel.emit({
      type: "response",
      requestId: request.requestId,
      token: "peer-token",
      mode: "development-auto-session",
    });

    await expect(pendingBootstrap).resolves.toBe("session");
    expect(getSessionToken()).toBe("peer-token");
  });

  it("restores the channel when a persisted page returns from bfcache", async () => {
    sessionStorage.setItem("ai-canvas-session", "existing-token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ mode: "session" })));
    const { bootstrapSession } = await import("./client");
    await bootstrapSession();
    const firstChannel = FakeBroadcastChannel.instances[0];
    expect(fetch).toHaveBeenCalledOnce();

    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));

    expect(firstChannel.close).toHaveBeenCalledOnce();
    expect(FakeBroadcastChannel.instances).toHaveLength(2);
    const secondChannel = FakeBroadcastChannel.instances[1];
    secondChannel.emit({ type: "request", requestId: "restored-request" });
    expect(secondChannel.postMessage).toHaveBeenCalledWith({
      type: "response",
      requestId: "restored-request",
      token: "existing-token",
    });
  });
});
