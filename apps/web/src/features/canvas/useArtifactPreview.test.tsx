import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { ApiRequestError, type ArtifactRevision } from "../../api/client";
import {
  ArtifactPreviewProvider,
  createArtifactPreviewCache,
  useArtifactPreview,
  type ArtifactPreviewCache,
  type ArtifactPreviewControls,
} from "./useArtifactPreview";

const REVISION_ONE = "11111111-1111-4111-8111-111111111111";
const REVISION_TWO = "22222222-2222-4222-8222-222222222222";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function imageRevision(revisionId = REVISION_ONE): ArtifactRevision {
  return {
    revision_id: revisionId,
    artifact_id: "33333333-3333-4333-8333-333333333333",
    sequence: 1,
    media_kind: "image",
    media_type: "image/png",
    byte_size: 3,
    display_filename: "safe.png",
    metadata: { kind: "image", width: 2, height: 1 },
    source_kind: "import",
    source_project_id: "44444444-4444-4444-8444-444444444444",
    origin_project_id: "44444444-4444-4444-8444-444444444444",
    created_at: "2026-07-21T01:02:03Z",
  };
}

function videoRevision(revisionId = REVISION_ONE): ArtifactRevision {
  return {
    ...imageRevision(revisionId),
    media_kind: "video",
    media_type: "video/mp4",
    display_filename: "safe.mp4",
    metadata: { kind: "video", width: 2, height: 1, duration_seconds: 1, frames_per_second: 24 },
  };
}

type GetRevision = (
  revisionId: string,
  signal?: AbortSignal,
) => Promise<ArtifactRevision>;
type GetContent = (revisionId: string, signal?: AbortSignal) => Promise<Blob>;
type CreateObjectURL = (blob: Blob) => string;
type RevokeObjectURL = (url: string) => void;
type CreateMediaStreamUrl = (revisionId: string) => Promise<string>;

type PreviewDeps = {
  getRevision: Mock<GetRevision>;
  getContent: Mock<GetContent>;
  createObjectURL: Mock<CreateObjectURL>;
  revokeObjectURL: Mock<RevokeObjectURL>;
  createMediaStreamUrl: Mock<CreateMediaStreamUrl>;
};

const caches: ArtifactPreviewCache[] = [];

function cacheWith(overrides: Partial<PreviewDeps> = {}) {
  let nextUrl = 0;
  const deps: PreviewDeps = {
    getRevision: vi.fn<GetRevision>().mockResolvedValue(imageRevision()),
    getContent: vi
      .fn<GetContent>()
      .mockResolvedValue(new Blob(["png"], { type: "image/png" })),
    createObjectURL: vi
      .fn<CreateObjectURL>()
      .mockImplementation(() => `blob:test-${++nextUrl}`),
    revokeObjectURL: vi.fn<RevokeObjectURL>(),
    createMediaStreamUrl: vi
      .fn<CreateMediaStreamUrl>()
      .mockImplementation(async (revisionId) => `/api/v1/media/revisions/${revisionId}`),
    ...overrides,
  };
  const cache = createArtifactPreviewCache({
    getArtifactRevision: (revisionId, signal) =>
      deps.getRevision(revisionId, signal) as Promise<ArtifactRevision>,
    getArtifactRevisionContent: (revisionId, signal) =>
      deps.getContent(revisionId, signal) as Promise<Blob>,
    createObjectURL: (blob) => deps.createObjectURL(blob) as string,
    revokeObjectURL: (url) => {
      deps.revokeObjectURL(url);
    },
    createMediaStreamUrl: (revisionId) => deps.createMediaStreamUrl(revisionId),
  });
  caches.push(cache);
  return { cache, deps };
}

const previewByLabel = new Map<string, ArtifactPreviewControls>();

function PreviewHarness({
  revisionId,
  label,
}: {
  revisionId: string | null;
  label: string;
}) {
  const preview = useArtifactPreview(revisionId);
  previewByLabel.set(label, preview);
  return (
    <div data-testid={label}>
      <span data-testid={`${label}-phase`}>{preview.phase}</span>
      <span data-testid={`${label}-url`}>{preview.objectUrl ?? ""}</span>
      {preview.errorMessage ? <span role="alert">{preview.errorMessage}</span> : null}
    </div>
  );
}

function withCache(cache: ArtifactPreviewCache, children: ReactNode) {
  return <ArtifactPreviewProvider cache={cache}>{children}</ArtifactPreviewProvider>;
}

describe("useArtifactPreview shared cache", () => {
  beforeEach(() => {
    cleanup();
    previewByLabel.clear();
  });

  afterEach(async () => {
    cleanup();
    if (vi.isFakeTimers()) await vi.runOnlyPendingTimersAsync();
    for (const cache of caches.splice(0)) cache.dispose();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("fetches authenticated summary then content and exposes one ready object URL", async () => {
    const { cache, deps } = cacheWith();
    render(withCache(cache, <PreviewHarness label="one" revisionId={REVISION_ONE} />));

    await waitFor(() => expect(screen.getByTestId("one-phase").textContent).toBe("ready"));
    expect(deps.getRevision).toHaveBeenCalledWith(REVISION_ONE, expect.any(AbortSignal));
    expect(deps.getContent).toHaveBeenCalledWith(REVISION_ONE, expect.any(AbortSignal));
    expect(deps.createObjectURL).toHaveBeenCalledOnce();
    expect(screen.getByTestId("one-url").textContent).toBe("blob:test-1");
    expect(previewByLabel.get("one")?.revision).toEqual(imageRevision());
  });

  it("uses a cookie-authorized token-free stream URL for video without fetching a full blob", async () => {
    const { cache, deps } = cacheWith({
      getRevision: vi.fn().mockResolvedValue(videoRevision()),
    });
    render(withCache(cache, <PreviewHarness label="video" revisionId={REVISION_ONE} />));

    await waitFor(() => expect(screen.getByTestId("video-phase").textContent).toBe("ready"));
    expect(deps.createMediaStreamUrl).toHaveBeenCalledWith(REVISION_ONE);
    expect(deps.getContent).not.toHaveBeenCalled();
    expect(deps.createObjectURL).not.toHaveBeenCalled();
    expect(screen.getByTestId("video-url").textContent).toBe(
      `/api/v1/media/revisions/${REVISION_ONE}`,
    );
    cache.dispose();
    expect(deps.revokeObjectURL).not.toHaveBeenCalled();
  });

  it("shares deferred summary, content, and object URL across two simultaneous consumers", async () => {
    const summary = deferred<ArtifactRevision>();
    const content = deferred<Blob>();
    const { cache, deps } = cacheWith({
      getRevision: vi.fn().mockReturnValue(summary.promise),
      getContent: vi.fn().mockReturnValue(content.promise),
    });
    render(
      withCache(
        cache,
        <>
          <PreviewHarness label="first" revisionId={REVISION_ONE} />
          <PreviewHarness label="copy" revisionId={REVISION_ONE} />
        </>,
      ),
    );

    expect(screen.getByTestId("first-phase").textContent).toBe("loading");
    expect(screen.getByTestId("copy-phase").textContent).toBe("loading");
    expect(deps.getRevision).toHaveBeenCalledOnce();
    await act(async () => {
      summary.resolve(imageRevision());
      await summary.promise;
    });
    expect(deps.getContent).toHaveBeenCalledOnce();
    await act(async () => {
      content.resolve(new Blob(["png"], { type: "image/png" }));
      await content.promise;
    });

    expect(screen.getByTestId("first-url").textContent).toBe("blob:test-1");
    expect(screen.getByTestId("copy-url").textContent).toBe("blob:test-1");
    expect(deps.createObjectURL).toHaveBeenCalledOnce();
  });

  it("survives StrictMode setup-cleanup-setup and releases only after real unmount", async () => {
    vi.useFakeTimers();
    const { cache, deps } = cacheWith();
    const rendered = render(
      withCache(
        cache,
        <StrictMode>
          <PreviewHarness label="strict" revisionId={REVISION_ONE} />
        </StrictMode>,
      ),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByTestId("strict-phase").textContent).toBe("ready");
    expect(deps.getRevision).toHaveBeenCalledOnce();
    expect(deps.getContent).toHaveBeenCalledOnce();
    expect(deps.createObjectURL).toHaveBeenCalledOnce();
    const signal = deps.getRevision.mock.calls[0][1] as AbortSignal;
    expect(signal.aborted).toBe(false);
    expect(deps.revokeObjectURL).not.toHaveBeenCalled();

    rendered.unmount();
    await vi.advanceTimersByTimeAsync(0);
    expect(deps.revokeObjectURL).toHaveBeenCalledOnce();
    expect(deps.revokeObjectURL).toHaveBeenCalledWith("blob:test-1");
  });

  it("keeps a shared pending request alive until the final consumer leaves, then aborts", async () => {
    vi.useFakeTimers();
    const summary = deferred<ArtifactRevision>();
    const { cache, deps } = cacheWith({
      getRevision: vi.fn().mockReturnValue(summary.promise),
    });
    const rendered = render(
      withCache(
        cache,
        <>
          <PreviewHarness label="first" revisionId={REVISION_ONE} />
          <PreviewHarness label="copy" revisionId={REVISION_ONE} />
        </>,
      ),
    );
    const signal = deps.getRevision.mock.calls[0][1] as AbortSignal;

    rendered.rerender(
      withCache(cache, <PreviewHarness label="copy" revisionId={REVISION_ONE} />),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(signal.aborted).toBe(false);

    rendered.unmount();
    await vi.advanceTimersByTimeAsync(0);
    expect(signal.aborted).toBe(true);
    summary.reject(new DOMException("Aborted", "AbortError"));
    await summary.promise.catch(() => undefined);
    expect(deps.createObjectURL).not.toHaveBeenCalled();
  });

  it("releases R1 when a consumer moves to R2 while preserving another R1 copy", async () => {
    vi.useFakeTimers();
    const { cache, deps } = cacheWith({
      getRevision: vi.fn().mockImplementation(async (id: string) => imageRevision(id)),
    });
    const rendered = render(
      withCache(
        cache,
        <>
          <PreviewHarness label="moving" revisionId={REVISION_ONE} />
          <PreviewHarness label="copy" revisionId={REVISION_ONE} />
        </>,
      ),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(deps.createObjectURL).toHaveBeenCalledOnce();

    rendered.rerender(
      withCache(
        cache,
        <>
          <PreviewHarness label="moving" revisionId={REVISION_TWO} />
          <PreviewHarness label="copy" revisionId={REVISION_ONE} />
        </>,
      ),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(deps.createObjectURL).toHaveBeenCalledTimes(2);
    expect(deps.revokeObjectURL).not.toHaveBeenCalled();

    rendered.rerender(
      withCache(cache, <PreviewHarness label="moving" revisionId={REVISION_TWO} />),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(deps.revokeObjectURL).toHaveBeenCalledOnce();
    expect(deps.revokeObjectURL).toHaveBeenCalledWith("blob:test-1");
  });

  it("retries one shared failed entry for all consumers", async () => {
    const { cache, deps } = cacheWith({
      getRevision: vi
        .fn()
        .mockRejectedValueOnce(new TypeError("offline"))
        .mockResolvedValueOnce(imageRevision()),
    });
    render(
      withCache(
        cache,
        <>
          <PreviewHarness label="first" revisionId={REVISION_ONE} />
          <PreviewHarness label="copy" revisionId={REVISION_ONE} />
        </>,
      ),
    );
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(deps.getRevision).toHaveBeenCalledOnce();

    act(() => previewByLabel.get("first")?.retry());
    await waitFor(() => expect(screen.getByTestId("first-phase").textContent).toBe("ready"));

    expect(screen.getByTestId("copy-phase").textContent).toBe("ready");
    expect(deps.getRevision).toHaveBeenCalledTimes(2);
    expect(deps.getContent).toHaveBeenCalledOnce();
    expect(deps.createObjectURL).toHaveBeenCalledOnce();
  });

  it("prevents an aborted old generation from overwriting a remounted request", async () => {
    vi.useFakeTimers();
    const oldSummary = deferred<ArtifactRevision>();
    const newSummary = deferred<ArtifactRevision>();
    const { cache, deps } = cacheWith({
      getRevision: vi
        .fn()
        .mockReturnValueOnce(oldSummary.promise)
        .mockReturnValueOnce(newSummary.promise),
    });
    const first = render(
      withCache(cache, <PreviewHarness label="old" revisionId={REVISION_ONE} />),
    );
    const oldSignal = deps.getRevision.mock.calls[0][1] as AbortSignal;
    first.unmount();
    await vi.advanceTimersByTimeAsync(0);
    expect(oldSignal.aborted).toBe(true);

    render(withCache(cache, <PreviewHarness label="new" revisionId={REVISION_ONE} />));
    expect(deps.getRevision).toHaveBeenCalledTimes(2);
    await act(async () => {
      oldSummary.reject(new Error("late old failure"));
      await oldSummary.promise.catch(() => undefined);
    });
    expect(screen.getByTestId("new-phase").textContent).toBe("loading");
    await act(async () => {
      newSummary.resolve(imageRevision());
      await newSummary.promise;
      await Promise.resolve();
    });

    expect(screen.getByTestId("new-phase").textContent).toBe("ready");
    expect(screen.queryByText("late old failure")).toBeNull();
  });

  it.each([
    ["content type", new Blob(["png"], { type: "video/webm" })],
    ["content length", new Blob(["too-long"], { type: "image/png" })],
  ])("rejects a %s mismatch before creating an object URL", async (_case, blob) => {
    const { cache, deps } = cacheWith({ getContent: vi.fn().mockResolvedValue(blob) });
    render(withCache(cache, <PreviewHarness label="bad" revisionId={REVISION_ONE} />));

    await waitFor(() => expect(screen.getByTestId("bad-phase").textContent).toBe("error"));
    expect(screen.getByRole("alert").textContent).toContain("不一致");
    expect(deps.createObjectURL).not.toHaveBeenCalled();
  });

  it("does not create an object URL for an authenticated content error", async () => {
    const { cache, deps } = cacheWith({
      getContent: vi.fn().mockRejectedValue(new ApiRequestError(404, "missing", false)),
    });
    render(withCache(cache, <PreviewHarness label="missing" revisionId={REVISION_ONE} />));

    await waitFor(() => expect(screen.getByTestId("missing-phase").textContent).toBe("error"));
    expect(deps.createObjectURL).not.toHaveBeenCalled();
    expect(previewByLabel.get("missing")?.retryable).toBe(false);
  });
});
