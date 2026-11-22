export interface MediaTargetLease {
  readonly operationId: string;
  readonly targetNodeId: string;
  release(): void;
}

type MediaTargetLocksSnapshot = Readonly<{
  protectedNodeIds: readonly string[];
}>;

const EMPTY_SNAPSHOT: MediaTargetLocksSnapshot = Object.freeze({
  protectedNodeIds: Object.freeze([]),
});

export class MediaTargetLocks {
  private readonly targets = new Map<string, string>();
  private readonly listeners = new Set<() => void>();
  private snapshot: MediaTargetLocksSnapshot = EMPTY_SNAPSHOT;

  acquire(operationId: string, targetNodeId: string): MediaTargetLease | null {
    if (this.targets.has(targetNodeId)) return null;
    this.targets.set(targetNodeId, operationId);
    this.notify();
    let released = false;
    return Object.freeze({
      operationId,
      targetNodeId,
      release: () => {
        if (released || this.targets.get(targetNodeId) !== operationId) return;
        released = true;
        this.targets.delete(targetNodeId);
        this.notify();
      },
    });
  }
  getSnapshot(): MediaTargetLocksSnapshot {
    return this.snapshot;
  }
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  private notify(): void {
    this.snapshot = Object.freeze({
      protectedNodeIds: Object.freeze([...this.targets.keys()].sort()),
    });
    for (const listener of this.listeners) listener();
  }
}
