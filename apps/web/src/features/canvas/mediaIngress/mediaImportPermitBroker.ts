export interface MediaImportPermit {
  readonly operationId: string;
  release(): void;
}

type MediaImportPermitSnapshot = Readonly<{
  runningOperationIds: readonly string[];
  queuedOperationIds: readonly string[];
  queueHeld: boolean;
}>;

const EMPTY_SNAPSHOT: MediaImportPermitSnapshot = Object.freeze({
  runningOperationIds: Object.freeze([]),
  queuedOperationIds: Object.freeze([]),
  queueHeld: false,
});

type Queued = {
  operationId: string;
  signal: AbortSignal;
  resolve: (permit: MediaImportPermit) => void;
  reject: (reason: unknown) => void;
  onAbort: () => void;
};

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

export class MediaImportPermitBroker {
  private readonly limit: number;
  private readonly running = new Set<string>();
  private readonly queued: Queued[] = [];
  private readonly holds = new Set<string>();
  private readonly listeners = new Set<() => void>();
  private snapshot: MediaImportPermitSnapshot = EMPTY_SNAPSHOT;
  private disposed = false;

  constructor(limit = 3) {
    if (!Number.isSafeInteger(limit) || limit < 1)
      throw new TypeError("permit limit must be positive");
    this.limit = limit;
  }

  acquire(
    operationId: string,
    signal: AbortSignal,
  ): Promise<MediaImportPermit> {
    if (this.disposed)
      return Promise.reject(
        new Error("media import permit broker is disposed"),
      );
    if (
      this.running.has(operationId) ||
      this.queued.some((entry) => entry.operationId === operationId)
    ) {
      return Promise.reject(
        new Error("operation already has or awaits a permit"),
      );
    }
    if (signal.aborted) return Promise.reject(abortError());
    return new Promise((resolve, reject) => {
      const entry: Queued = {
        operationId,
        signal,
        resolve,
        reject,
        onAbort: () => this.cancel(entry),
      };
      this.queued.push(entry);
      signal.addEventListener("abort", entry.onAbort, { once: true });
      this.drain();
      this.notify();
    });
  }

  holdQueue(holdId: string): void {
    if (this.disposed) return;
    if (!this.holds.has(holdId)) {
      this.holds.add(holdId);
      this.notify();
    }
  }
  releaseQueueHold(holdId: string): void {
    if (this.disposed) return;
    if (this.holds.delete(holdId)) {
      this.drain();
      this.notify();
    }
  }
  getSnapshot(): MediaImportPermitSnapshot {
    return this.snapshot;
  }
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    const snapshotChanged = this.queued.length > 0 || this.holds.size > 0;
    for (const entry of this.queued.splice(0)) {
      entry.signal.removeEventListener("abort", entry.onAbort);
      entry.reject(new Error("media import permit broker is disposed"));
    }
    this.holds.clear();
    if (snapshotChanged) this.notify();
    this.listeners.clear();
  }

  private cancel(entry: Queued): void {
    const index = this.queued.indexOf(entry);
    if (index < 0) return;
    this.queued.splice(index, 1);
    entry.signal.removeEventListener("abort", entry.onAbort);
    entry.reject(abortError());
    this.notify();
  }
  private drain(): void {
    while (
      !this.disposed &&
      this.holds.size === 0 &&
      this.running.size < this.limit &&
      this.queued.length > 0
    ) {
      const entry = this.queued.shift();
      if (!entry) return;
      entry.signal.removeEventListener("abort", entry.onAbort);
      if (entry.signal.aborted) {
        entry.reject(abortError());
        continue;
      }
      this.running.add(entry.operationId);
      let released = false;
      entry.resolve(
        Object.freeze({
          operationId: entry.operationId,
          release: () => {
            if (released) return;
            released = true;
            if (this.running.delete(entry.operationId)) {
              this.drain();
              this.notify();
            }
          },
        }),
      );
    }
  }
  private notify(): void {
    this.snapshot = Object.freeze({
      runningOperationIds: Object.freeze([...this.running]),
      queuedOperationIds: Object.freeze(
        this.queued.map((entry) => entry.operationId),
      ),
      queueHeld: this.holds.size > 0,
    });
    for (const listener of this.listeners) listener();
  }
}
