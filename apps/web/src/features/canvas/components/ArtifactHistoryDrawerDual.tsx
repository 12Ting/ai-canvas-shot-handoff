import { useEffect, useMemo, useRef, useState } from "react";
import {
  listArtifactRevisions,
  listShotSlotAssociations,
  type ArtifactRevision,
  type ShotSlotAssociationHistoryItem,
  type ShotSlotAssociationHistoryResponse,
} from "../../../api/client";
import { useCanvasStore } from "../canvasStore";
import type { CanvasRuntimeIdentity, MediaKind } from "../canvasTypes";
import { useArtifactPreview } from "../useArtifactPreview";
import { ArtifactProvenance } from "./ArtifactProvenance";
import { FloatingPanelControls } from "./FloatingPanelControls";
import type { FloatingPanelController } from "./useFloatingPanel";

const ASSOCIATION_PAGE_SIZE = 20;

export type ShotHistoryTarget = Readonly<{
  shotId: string;
  slot: "image" | "video" | "reference";
  revisionId: string;
}>;

export type BoundShotHistoryTarget = ShotHistoryTarget &
  Readonly<{ anchorRevisionId: string }>;

type ArtifactHistoryState = Readonly<{
  bindingKey: string | null;
  error: string | null;
  items: ArtifactRevision[];
  loading: boolean;
}>;

type AssociationHistoryState = Readonly<{
  bindingKey: string | null;
  error: string | null;
  items: ShotSlotAssociationHistoryItem[];
  loading: boolean;
  nextCursor: string | null;
}>;

function expectedMediaKind(slot: ShotHistoryTarget["slot"]): MediaKind {
  return slot === "video" ? "video" : "image";
}

function eventKey(item: ShotSlotAssociationHistoryItem): string {
  return `${item.graph_revision_id}:${item.event_ordinal}`;
}

function artifactSelectionKey(revisionId: string): string {
  return `artifact:${revisionId}`;
}

function associationSelectionKey(item: ShotSlotAssociationHistoryItem): string {
  return `association:${eventKey(item)}`;
}

function currentSelectionKey(revisionId: string): string {
  return `current:${revisionId}`;
}

function formatOccurredAt(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "未知时间";
  return new Date(timestamp)
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z")
    .replace("T", " ")
    .replace(/Z$/, " UTC");
}

function actionLabel(item: ShotSlotAssociationHistoryItem): string {
  if (item.action === "cleared") return "已清除，不能应用";
  return item.action === "removed" ? "已移除，可重新应用" : "已关联";
}

function associationPageProblem(
  page: ShotSlotAssociationHistoryResponse,
  binding: {
    projectId: string;
    shotId: string;
    slot: ShotHistoryTarget["slot"];
    anchorRevisionId: string;
  },
  existingEventKeys: ReadonlySet<string>,
  seenCursors: ReadonlySet<string>,
): string | null {
  if (
    page.project_id !== binding.projectId ||
    page.shot_id !== binding.shotId ||
    page.slot !== binding.slot ||
    page.anchor_graph_revision_id !== binding.anchorRevisionId
  ) {
    return "关联记录绑定已变化，未合并该页。";
  }
  const pageKeys = new Set<string>();
  for (const item of page.items) {
    const key = eventKey(item);
    if (existingEventKeys.has(key) || pageKeys.has(key)) {
      return "关联记录包含重复事件，未合并该页。";
    }
    pageKeys.add(key);
    const artifact = item.artifact_revision ?? null;
    const kind = expectedMediaKind(binding.slot);
    const validPayload =
      binding.slot === "reference"
        ? (item.action === "associated" || item.action === "removed") &&
          item.reference_index !== null &&
          item.reference_index !== undefined &&
          artifact !== null &&
          artifact.media_kind === "image"
        : (item.action === "associated" || item.action === "cleared") &&
          (item.reference_index === null || item.reference_index === undefined) &&
          (item.action === "cleared" ? artifact === null : artifact?.media_kind === kind);
    if (!validPayload) return "关联记录内容与当前槽位不匹配，未合并该页。";
  }
  if (
    page.next_cursor !== null &&
    page.next_cursor !== undefined &&
    seenCursors.has(page.next_cursor)
  ) {
    return "关联记录分页游标循环，未合并该页。";
  }
  return null;
}

function RevisionPreview({ revisionId }: { revisionId: string | null }) {
  const preview = useArtifactPreview(revisionId);
  if (preview.phase !== "ready") {
    return (
      <p aria-live="polite" className="shot-history__preview-state" role="status">
        正在安全加载预览…
      </p>
    );
  }
  const { revision } = preview;
  return (
    <div className="shot-history__preview">
      {revision.media_kind === "image" ? (
        <img alt={revision.display_filename} src={preview.objectUrl ?? undefined} />
      ) : (
        <video
          aria-label={`${revision.display_filename} 预览`}
          controls
          muted
          src={preview.objectUrl ?? undefined}
        />
      )}
      <p><bdi>{revision.display_filename}</bdi></p>
      <ArtifactProvenance revision={revision} />
    </div>
  );
}

export function ArtifactHistoryDrawer({
  floatingPanel,
  identity,
  onClose,
  target,
}: {
  floatingPanel: FloatingPanelController;
  identity?: CanvasRuntimeIdentity | null;
  onClose(): void;
  target: BoundShotHistoryTarget | null;
}) {
  const currentProjectId = useCanvasStore((state) => state.projectId);
  const currentLoadEpoch = useCanvasStore((state) => state.loadEpoch);
  const fallbackAnchorRevisionId = useCanvasStore((state) => state.baseRevisionId);
  const targetIdentityIsCurrent =
    identity === undefined ||
    (identity !== null &&
      identity.projectId === currentProjectId &&
      identity.loadEpoch === currentLoadEpoch);
  const activeTarget = targetIdentityIsCurrent ? target : null;
  const activeProjectId = identity?.projectId ?? currentProjectId;
  const anchorRevisionId = activeTarget?.anchorRevisionId ?? fallbackAnchorRevisionId;
  const bindingKey =
    activeTarget && activeProjectId && anchorRevisionId
      ? JSON.stringify([
          activeProjectId,
          currentLoadEpoch,
          activeTarget.shotId,
          activeTarget.slot,
          activeTarget.revisionId,
          anchorRevisionId,
        ])
      : null;
  const locked = useCanvasStore((state) => state.status === "conflict");
  const setShotSlotIfCurrent = useCanvasStore((state) => state.setShotSlotIfCurrent);
  const replaceShotReference = useCanvasStore((state) => state.replaceShotReference);
  const targetStillCurrent = useCanvasStore((state) => {
    if (!activeTarget) return false;
    const shot = state.shots.find((candidate) => candidate.id === activeTarget.shotId);
    if (!shot) return false;
    if (activeTarget.slot === "reference") {
      return shot.referenceRevisionIds.includes(activeTarget.revisionId);
    }
    return (
      (activeTarget.slot === "image" ? shot.imageRevisionId : shot.videoRevisionId) ===
      activeTarget.revisionId
    );
  });
  const currentPreview = useArtifactPreview(activeTarget?.revisionId ?? null);
  const currentArtifactId =
    activeTarget !== null &&
    currentPreview.phase === "ready" &&
    currentPreview.revision.revision_id === activeTarget.revisionId
      ? currentPreview.revision.artifact_id
      : null;
  const [artifactState, setArtifactState] = useState<ArtifactHistoryState>({
    bindingKey: null,
    error: null,
    items: [],
    loading: false,
  });
  const [associationState, setAssociationState] = useState<AssociationHistoryState>({
    bindingKey: null,
    error: null,
    items: [],
    loading: false,
    nextCursor: null,
  });
  const [selection, setSelection] = useState<{
    bindingKey: string | null;
    key: string | null;
  }>({ bindingKey: null, key: null });
  const [artifactRetry, setArtifactRetry] = useState(0);
  const [associationRetry, setAssociationRetry] = useState(0);
  const artifactGenerationRef = useRef(0);
  const associationGenerationRef = useRef(0);
  const associationAbortRef = useRef<AbortController | null>(null);
  const associationEventKeysRef = useRef(new Set<string>());
  const associationCursorsRef = useRef(new Set<string>());
  const titleRef = useRef<HTMLHeadingElement>(null);

  const visibleArtifactState =
    artifactState.bindingKey === bindingKey
      ? artifactState
      : { bindingKey, error: null, items: [], loading: bindingKey !== null };
  const visibleAssociationState =
    associationState.bindingKey === bindingKey
      ? associationState
      : {
          bindingKey,
          error: null,
          items: [],
          loading: bindingKey !== null,
          nextCursor: null,
        };
  const selectedKey =
    selection.bindingKey === bindingKey
      ? selection.key
      : activeTarget
        ? currentSelectionKey(activeTarget.revisionId)
        : null;

  useEffect(() => {
    if (!bindingKey || !activeTarget) return;
    setSelection({ bindingKey, key: currentSelectionKey(activeTarget.revisionId) });
    queueMicrotask(() => titleRef.current?.focus({ preventScroll: true }));
  }, [activeTarget, bindingKey]);

  useEffect(() => {
    if (!bindingKey || !activeTarget || currentArtifactId === null) return;
    const generation = ++artifactGenerationRef.current;
    const controller = new AbortController();
    const kind = expectedMediaKind(activeTarget.slot);
    setArtifactState({ bindingKey, error: null, items: [], loading: true });
    void listArtifactRevisions(currentArtifactId, controller.signal)
      .then((items) => {
        if (controller.signal.aborted || generation !== artifactGenerationRef.current) return;
        const revisionIds = new Set<string>();
        if (
          items.some((item) => {
            if (
              item.artifact_id !== currentArtifactId ||
              item.media_kind !== kind ||
              revisionIds.has(item.revision_id)
            ) {
              return true;
            }
            revisionIds.add(item.revision_id);
            return false;
          })
        ) {
          setArtifactState({
            bindingKey,
            error: "素材历史完整性错误，未应用任何版本。",
            items: [],
            loading: false,
          });
          return;
        }
        setArtifactState({ bindingKey, error: null, items, loading: false });
      })
      .catch((error: unknown) => {
        if (
          controller.signal.aborted ||
          generation !== artifactGenerationRef.current ||
          (error instanceof DOMException && error.name === "AbortError")
        ) {
          return;
        }
        setArtifactState({
          bindingKey,
          error: "无法加载此素材的历史版本。",
          items: [],
          loading: false,
        });
      });
    return () => {
      controller.abort();
      if (generation === artifactGenerationRef.current) artifactGenerationRef.current += 1;
    };
  }, [activeTarget, artifactRetry, bindingKey, currentArtifactId]);

  useEffect(() => {
    if (!bindingKey || !activeTarget || !activeProjectId || !anchorRevisionId) return;
    const generation = ++associationGenerationRef.current;
    const controller = new AbortController();
    associationAbortRef.current?.abort();
    associationAbortRef.current = controller;
    associationEventKeysRef.current = new Set();
    associationCursorsRef.current = new Set();
    setAssociationState({
      bindingKey,
      error: null,
      items: [],
      loading: true,
      nextCursor: null,
    });
    const requestBinding = {
      projectId: activeProjectId,
      shotId: activeTarget.shotId,
      slot: activeTarget.slot,
      anchorRevisionId,
    };
    void listShotSlotAssociations(
      activeProjectId,
      activeTarget.shotId,
      activeTarget.slot,
      anchorRevisionId,
      undefined,
      ASSOCIATION_PAGE_SIZE,
      controller.signal,
    )
      .then((page) => {
        if (controller.signal.aborted || generation !== associationGenerationRef.current) return;
        const problem = associationPageProblem(page, requestBinding, new Set(), new Set());
        if (problem) {
          setAssociationState({
            bindingKey,
            error: problem,
            items: [],
            loading: false,
            nextCursor: null,
          });
          return;
        }
        associationEventKeysRef.current = new Set(page.items.map(eventKey));
        associationCursorsRef.current = new Set(page.next_cursor ? [page.next_cursor] : []);
        setAssociationState({
          bindingKey,
          error: null,
          items: page.items,
          loading: false,
          nextCursor: page.next_cursor ?? null,
        });
      })
      .catch((error: unknown) => {
        if (
          controller.signal.aborted ||
          generation !== associationGenerationRef.current ||
          (error instanceof DOMException && error.name === "AbortError")
        ) {
          return;
        }
        setAssociationState({
          bindingKey,
          error: "无法加载关联记录，请重试。",
          items: [],
          loading: false,
          nextCursor: null,
        });
      });
    return () => {
      controller.abort();
      if (generation === associationGenerationRef.current) {
        associationAbortRef.current?.abort();
        associationAbortRef.current = null;
        associationGenerationRef.current += 1;
      } else if (associationAbortRef.current === controller) {
        associationAbortRef.current = null;
      }
    };
  }, [activeProjectId, activeTarget, anchorRevisionId, associationRetry, bindingKey]);

  const selected = useMemo(() => {
    if (selectedKey?.startsWith("artifact:")) {
      const revisionId = selectedKey.slice("artifact:".length);
      return visibleArtifactState.items.find((item) => item.revision_id === revisionId) ?? null;
    }
    if (selectedKey?.startsWith("association:")) {
      const key = selectedKey.slice("association:".length);
      return (
        visibleAssociationState.items.find((item) => eventKey(item) === key)
          ?.artifact_revision ?? null
      );
    }
    return null;
  }, [selectedKey, visibleArtifactState.items, visibleAssociationState.items]);
  const selectedRevisionId = selected?.revision_id ?? activeTarget?.revisionId ?? null;
  const selectedPreview = useArtifactPreview(selectedRevisionId);
  const selectedAlreadyReferencesAnotherSlot = useCanvasStore((state) => {
    if (!activeTarget || activeTarget.slot !== "reference" || selected === null) return false;
    const shot = state.shots.find((candidate) => candidate.id === activeTarget.shotId);
    return (
      shot?.referenceRevisionIds.some(
        (revisionId) =>
          revisionId !== activeTarget.revisionId && revisionId === selected.revision_id,
      ) ?? false
    );
  });

  if (!activeTarget || !bindingKey || !activeProjectId || !anchorRevisionId) return null;

  const expectedKind = expectedMediaKind(activeTarget.slot);
  const canApply =
    !locked &&
    targetStillCurrent &&
    selected !== null &&
    selected.revision_id !== activeTarget.revisionId &&
    selected.media_kind === expectedKind &&
    !selectedAlreadyReferencesAnotherSlot &&
    selectedPreview.phase === "ready" &&
    selectedPreview.revision.revision_id === selected.revision_id &&
    selectedPreview.revision.media_kind === expectedKind;

  const closeAndRestoreFocus = () => {
    const focusTarget = activeTarget;
    onClose();
    queueMicrotask(() => {
      const triggers = document.querySelectorAll<HTMLElement>(
        '[data-shot-history-trigger="true"]',
      );
      for (let index = 0; index < triggers.length; index += 1) {
        const trigger = triggers.item(index);
        if (
          trigger.dataset.shotHistoryShotId === focusTarget.shotId &&
          trigger.dataset.shotHistorySlot === focusTarget.slot &&
          trigger.dataset.shotHistoryRevisionId === focusTarget.revisionId
        ) {
          trigger.focus({ preventScroll: true });
          return;
        }
      }
    });
  };

  const apply = () => {
    if (!canApply || !selected) return;
    const current = useCanvasStore.getState();
    if (
      current.status === "conflict" ||
      current.projectId !== activeProjectId ||
      (identity !== undefined &&
        (identity === null ||
          identity.projectId !== current.projectId ||
          identity.loadEpoch !== current.loadEpoch))
    ) {
      return;
    }
    const currentShot = current.shots.find((shot) => shot.id === activeTarget.shotId);
    if (!currentShot || selected.media_kind !== expectedKind) return;
    if (
      activeTarget.slot === "reference"
        ? !currentShot.referenceRevisionIds.includes(activeTarget.revisionId) ||
          currentShot.referenceRevisionIds.some(
            (revisionId) =>
              revisionId !== activeTarget.revisionId && revisionId === selected.revision_id,
          )
        : (activeTarget.slot === "image"
            ? currentShot.imageRevisionId
            : currentShot.videoRevisionId) !== activeTarget.revisionId
    ) {
      return;
    }
    const revision = { revisionId: selected.revision_id, mediaKind: selected.media_kind };
    const changed =
      activeTarget.slot === "reference"
        ? replaceShotReference(activeTarget.shotId, activeTarget.revisionId, revision)
        : setShotSlotIfCurrent(
            activeTarget.shotId,
            activeTarget.slot,
            activeTarget.revisionId,
            revision,
          );
    if (changed) closeAndRestoreFocus();
  };

  const loadMoreAssociations = () => {
    const cursor = visibleAssociationState.nextCursor;
    if (!cursor || visibleAssociationState.loading) return;
    const generation = associationGenerationRef.current;
    const controller = new AbortController();
    associationAbortRef.current?.abort();
    associationAbortRef.current = controller;
    setAssociationState((state) =>
      state.bindingKey !== bindingKey ? state : { ...state, error: null, loading: true },
    );
    const requestBinding = {
      projectId: activeProjectId,
      shotId: activeTarget.shotId,
      slot: activeTarget.slot,
      anchorRevisionId,
    };
    void listShotSlotAssociations(
      activeProjectId,
      activeTarget.shotId,
      activeTarget.slot,
      anchorRevisionId,
      cursor,
      ASSOCIATION_PAGE_SIZE,
      controller.signal,
    )
      .then((page) => {
        if (controller.signal.aborted || generation !== associationGenerationRef.current) return;
        const problem = associationPageProblem(
          page,
          requestBinding,
          associationEventKeysRef.current,
          associationCursorsRef.current,
        );
        if (problem) {
          setAssociationState((state) =>
            state.bindingKey !== bindingKey
              ? state
              : { ...state, error: problem, loading: false },
          );
          return;
        }
        for (const item of page.items) associationEventKeysRef.current.add(eventKey(item));
        if (page.next_cursor) associationCursorsRef.current.add(page.next_cursor);
        setAssociationState((state) =>
          state.bindingKey !== bindingKey
            ? state
            : {
                ...state,
                error: null,
                items: [...state.items, ...page.items],
                loading: false,
                nextCursor: page.next_cursor ?? null,
              },
        );
      })
      .catch((error: unknown) => {
        if (
          controller.signal.aborted ||
          generation !== associationGenerationRef.current ||
          (error instanceof DOMException && error.name === "AbortError")
        ) {
          return;
        }
        setAssociationState((state) =>
          state.bindingKey !== bindingKey
            ? state
            : {
                ...state,
                error: "加载更早关联记录失败，请重试。",
                loading: false,
              },
        );
      });
  };

  return (
    <aside
      aria-label="素材版本历史"
      className={`shot-history${floatingPanel.active ? " floating-panel--active" : ""}`}
      data-testid="artifact-history-drawer"
      ref={floatingPanel.panelRef}
      role="region"
      style={floatingPanel.style}
    >
      <header>
        <div>
          <p>素材与关联历史</p>
          <h2 ref={titleRef} tabIndex={-1}>选择历史素材</h2>
        </div>
        <FloatingPanelControls controller={floatingPanel} label="素材版本历史" />
        <button aria-label="关闭素材版本历史" onClick={closeAndRestoreFocus} type="button">关闭</button>
      </header>
      <RevisionPreview revisionId={selectedRevisionId} />
      <div className="shot-history__sources">
        <section aria-labelledby="artifact-history-heading" className="shot-history__source">
          <h3 id="artifact-history-heading">素材版本</h3>
          {visibleArtifactState.loading ? (
            <p aria-live="polite" role="status">正在加载素材版本…</p>
          ) : null}
          {visibleArtifactState.error ? (
            <div role="alert">
              <p>{visibleArtifactState.error}</p>
              <button onClick={() => setArtifactRetry((value) => value + 1)} type="button">
                重新加载素材版本
              </button>
            </div>
          ) : null}
          <ul aria-label="素材版本列表" className="shot-history__list">
            {visibleArtifactState.items.map((item) => {
              const key = artifactSelectionKey(item.revision_id);
              return (
                <li key={item.revision_id}>
                  <button
                    aria-label={item.display_filename}
                    aria-pressed={key === selectedKey}
                    className={key === selectedKey ? "is-selected" : ""}
                    onClick={() => setSelection({ bindingKey, key })}
                    type="button"
                  >
                    <bdi>{item.display_filename}</bdi>
                    <span>版本 {item.sequence}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
        <section aria-labelledby="association-history-heading" className="shot-history__source">
          <h3 id="association-history-heading">关联记录</h3>
          {visibleAssociationState.loading && visibleAssociationState.items.length === 0 ? (
            <p aria-live="polite" role="status">正在加载关联记录…</p>
          ) : null}
          {visibleAssociationState.error ? (
            <div role="alert">
              <p>{visibleAssociationState.error}</p>
              {visibleAssociationState.items.length === 0 ? (
                <button onClick={() => setAssociationRetry((value) => value + 1)} type="button">
                  重试关联记录
                </button>
              ) : null}
            </div>
          ) : null}
          <ul aria-label="关联记录列表" className="shot-history__list shot-history__association-list">
            {visibleAssociationState.items.map((item) => {
              const key = associationSelectionKey(item);
              const artifact = item.artifact_revision ?? null;
              return (
                <li data-event-key={eventKey(item)} key={eventKey(item)}>
                  {artifact ? (
                    <button
                      aria-label={artifact.display_filename}
                      aria-pressed={key === selectedKey}
                      className={key === selectedKey ? "is-selected" : ""}
                      onClick={() => setSelection({ bindingKey, key })}
                      type="button"
                    >
                      <bdi>{artifact.display_filename}</bdi>
                      <span>素材版本 {artifact.sequence}</span>
                    </button>
                  ) : null}
                  <p className="shot-history__event-meta">
                    <span>图序列 {item.graph_revision_sequence}</span>
                    <span>{actionLabel(item)}</span>
                    {item.reference_index !== null && item.reference_index !== undefined ? (
                      <span>参考位 {item.reference_index + 1}</span>
                    ) : null}
                    <time dateTime={item.occurred_at}>{formatOccurredAt(item.occurred_at)}</time>
                  </p>
                </li>
              );
            })}
          </ul>
          {visibleAssociationState.nextCursor ? (
            <button
              aria-busy={visibleAssociationState.loading}
              className="shot-history__load-more"
              disabled={visibleAssociationState.loading}
              onClick={loadMoreAssociations}
              type="button"
            >
              {visibleAssociationState.error ? "重试加载更早记录" : "加载更早记录"}
            </button>
          ) : null}
        </section>
      </div>
      <footer>
        <button disabled={!canApply} onClick={apply} type="button">应用所选素材</button>
      </footer>
    </aside>
  );
}
