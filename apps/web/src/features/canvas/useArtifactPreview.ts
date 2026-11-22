import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  ApiRequestError,
  createMediaStreamUrl,
  getArtifactRevision,
  getArtifactRevisionContent,
  type ArtifactRevision,
} from "../../api/client";

export type ArtifactPreviewSnapshot =
  | Readonly<{
      phase: "idle" | "loading";
      revision: null;
      objectUrl: null;
      errorMessage: null;
      retryable: false;
    }>
  | Readonly<{
      phase: "ready";
      revision: ArtifactRevision;
      objectUrl: string;
      errorMessage: null;
      retryable: false;
    }>
  | Readonly<{
      phase: "error";
      revision: null;
      objectUrl: null;
      errorMessage: string;
      retryable: boolean;
    }>;

export type ArtifactPreviewControls = ArtifactPreviewSnapshot & {
  retry(): void;
};

export type ArtifactPreviewDependencies = {
  getArtifactRevision(
    revisionId: string,
    signal?: AbortSignal,
  ): Promise<ArtifactRevision>;
  getArtifactRevisionContent(revisionId: string, signal?: AbortSignal): Promise<Blob>;
  createMediaStreamUrl(revisionId: string): Promise<string>;
  createObjectURL(blob: Blob): string;
  revokeObjectURL(url: string): void;
};

type PreviewEntry = {
  readonly revisionId: string;
  readonly listeners: Set<() => void>;
  snapshot: ArtifactPreviewSnapshot;
  controller: AbortController | null;
  generation: number;
  disposeTimer: ReturnType<typeof setTimeout> | null;
  objectUrl: string | null;
  started: boolean;
};

const IDLE_SNAPSHOT: ArtifactPreviewSnapshot = Object.freeze({
  phase: "idle",
  revision: null,
  objectUrl: null,
  errorMessage: null,
  retryable: false,
});
const LOADING_SNAPSHOT: ArtifactPreviewSnapshot = Object.freeze({
  phase: "loading",
  revision: null,
  objectUrl: null,
  errorMessage: null,
  retryable: false,
});

class PreviewContentMismatchError extends Error {}

function isAbortError(error: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      error instanceof DOMException &&
      error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function previewErrorMessage(error: unknown): string {
  if (error instanceof PreviewContentMismatchError) return error.message;
  if (error instanceof ApiRequestError) {
    if (error.status === 404) return "素材版本不存在或已不可用。";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "素材预览加载失败。";
}

export interface ArtifactPreviewCache {
  getSnapshot(revisionId: string | null): ArtifactPreviewSnapshot;
  subscribe(revisionId: string | null, listener: () => void): () => void;
  retry(revisionId: string | null): void;
  dispose(): void;
}

class ArtifactPreviewCacheImpl implements ArtifactPreviewCache {
  private readonly entries = new Map<string, PreviewEntry>();
  private readonly dependencies: ArtifactPreviewDependencies;
  private disposed = false;

  constructor(dependencies: ArtifactPreviewDependencies) {
    this.dependencies = dependencies;
  }

  getSnapshot = (revisionId: string | null): ArtifactPreviewSnapshot => {
    if (revisionId === null) return IDLE_SNAPSHOT;
    return this.entries.get(revisionId)?.snapshot ?? LOADING_SNAPSHOT;
  }

  subscribe = (revisionId: string | null, listener: () => void): (() => void) => {
    if (revisionId === null || this.disposed) return () => undefined;
    let entry = this.entries.get(revisionId);
    if (!entry) {
      entry = {
        revisionId,
        listeners: new Set(),
        snapshot: LOADING_SNAPSHOT,
        controller: null,
        generation: 0,
        disposeTimer: null,
        objectUrl: null,
        started: false,
      };
      this.entries.set(revisionId, entry);
    }
    if (entry.disposeTimer !== null) {
      clearTimeout(entry.disposeTimer);
      entry.disposeTimer = null;
    }
    entry.listeners.add(listener);
    if (!entry.started) this.start(entry);

    let subscribed = true;
    return () => {
      if (!subscribed) return;
      subscribed = false;
      entry?.listeners.delete(listener);
      if (entry && entry.listeners.size === 0 && entry.disposeTimer === null) {
        entry.disposeTimer = setTimeout(() => {
          if (entry) this.releaseEntry(entry);
        }, 0);
      }
    };
  }

  retry = (revisionId: string | null): void => {
    if (revisionId === null || this.disposed) return;
    const entry = this.entries.get(revisionId);
    if (!entry || entry.listeners.size === 0 || entry.snapshot.phase !== "error") return;
    this.start(entry);
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const entry of [...this.entries.values()]) this.releaseEntry(entry, true);
    this.entries.clear();
  }

  private isCurrent(
    entry: PreviewEntry,
    generation: number,
    controller: AbortController,
  ): boolean {
    return (
      !this.disposed &&
      this.entries.get(entry.revisionId) === entry &&
      entry.generation === generation &&
      entry.controller === controller
    );
  }

  private notify(entry: PreviewEntry): void {
    for (const listener of entry.listeners) listener();
  }

  private start(entry: PreviewEntry): void {
    entry.controller?.abort();
    entry.generation += 1;
    const generation = entry.generation;
    const controller = new AbortController();
    entry.controller = controller;
    entry.started = true;
    entry.snapshot = LOADING_SNAPSHOT;
    this.notify(entry);

    void (async () => {
      try {
        const revision = await this.dependencies.getArtifactRevision(
          entry.revisionId,
          controller.signal,
        );
        if (!this.isCurrent(entry, generation, controller)) return;
        let objectUrl: string;
        if (revision.media_kind === "video") {
          objectUrl = await this.dependencies.createMediaStreamUrl(entry.revisionId);
        } else {
          const content = await this.dependencies.getArtifactRevisionContent(
            entry.revisionId,
            controller.signal,
          );
          if (!this.isCurrent(entry, generation, controller)) return;
          if (
            content.size !== revision.byte_size ||
            content.type.trim().toLowerCase() !== revision.media_type.trim().toLowerCase()
          ) {
            throw new PreviewContentMismatchError(
              "预览内容的类型或长度与素材摘要不一致，未创建临时预览地址。",
            );
          }
          objectUrl = this.dependencies.createObjectURL(content);
        }
        if (!this.isCurrent(entry, generation, controller)) {
          this.dependencies.revokeObjectURL(objectUrl);
          return;
        }
        entry.objectUrl = objectUrl;
        entry.snapshot = Object.freeze({
          phase: "ready",
          revision,
          objectUrl,
          errorMessage: null,
          retryable: false,
        });
        this.notify(entry);
      } catch (error: unknown) {
        if (!this.isCurrent(entry, generation, controller) || isAbortError(error)) return;
        entry.started = false;
        entry.snapshot = Object.freeze({
          phase: "error",
          revision: null,
          objectUrl: null,
          errorMessage: previewErrorMessage(error),
          retryable:
            error instanceof TypeError ||
            (error instanceof ApiRequestError && error.retryable),
        });
        this.notify(entry);
      } finally {
        if (this.isCurrent(entry, generation, controller)) entry.controller = null;
      }
    })();
  }

  private releaseEntry(entry: PreviewEntry, force = false): void {
    if (
      this.entries.get(entry.revisionId) !== entry ||
      (!force && entry.listeners.size !== 0)
    ) {
      return;
    }
    if (entry.disposeTimer !== null) clearTimeout(entry.disposeTimer);
    entry.disposeTimer = null;
    this.entries.delete(entry.revisionId);
    entry.generation += 1;
    entry.controller?.abort();
    entry.controller = null;
    if (entry.objectUrl?.startsWith("blob:") ?? false) {
      this.dependencies.revokeObjectURL(entry.objectUrl);
    }
    entry.objectUrl = null;
  }
}

const defaultDependencies: ArtifactPreviewDependencies = {
  getArtifactRevision,
  getArtifactRevisionContent,
  createMediaStreamUrl,
  createObjectURL: (blob) => URL.createObjectURL(blob),
  revokeObjectURL: (url) => URL.revokeObjectURL(url),
};

export function createArtifactPreviewCache(
  dependencies: Partial<ArtifactPreviewDependencies> = {},
): ArtifactPreviewCache {
  return new ArtifactPreviewCacheImpl({ ...defaultDependencies, ...dependencies });
}

const defaultCache = createArtifactPreviewCache();
const ArtifactPreviewContext = createContext<ArtifactPreviewCache>(defaultCache);

export function ArtifactPreviewProvider({
  cache,
  children,
}: {
  cache: ArtifactPreviewCache;
  children: ReactNode;
}) {
  return createElement(ArtifactPreviewContext.Provider, { value: cache }, children);
}

export function useArtifactPreview(revisionId: string | null): ArtifactPreviewControls {
  const cache = useContext(ArtifactPreviewContext);
  const subscribe = useCallback(
    (listener: () => void) => cache.subscribe(revisionId, listener),
    [cache, revisionId],
  );
  const getSnapshot = useCallback(
    () => cache.getSnapshot(revisionId),
    [cache, revisionId],
  );
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const retry = useCallback(() => cache.retry(revisionId), [cache, revisionId]);
  return { ...snapshot, retry } as ArtifactPreviewControls;
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => defaultCache.dispose());
}
