import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  archiveResource,
  getResource,
  listProjectResourceUpdates,
  listResources,
  type ResourceDetailResponse,
  type ResourceResponse,
  type ResourceUpdateItem,
} from "../../api/client";
import { useCanvasStore } from "../canvas/canvasStore";
import type { ResourceKind } from "./resourceTypes";
import { RESOURCE_KIND_LABEL, RESOURCE_KINDS, resourceSummary } from "./resourceTypes";
import { ResourceBindingDialog } from "./ResourceBindingDialog";
import { ResourceEditorDialog } from "./ResourceEditorDialog";

type DialogState =
  | { kind: "create"; resourceKind: ResourceKind; trigger: HTMLElement | null }
  | { kind: "edit" | "bind" | "history"; resource: ResourceDetailResponse; trigger: HTMLElement | null }
  | null;

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "资源目录读取失败。";
}

export function ResourceCatalogPanel({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ResourceKind>("STYLE");
  const [query, setQuery] = useState("");
  const [tags, setTags] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [resources, setResources] = useState<ResourceResponse[]>([]);
  const [updates, setUpdates] = useState<ResourceUpdateItem[]>([]);
  const [pinnedResourceIds, setPinnedResourceIds] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [pendingArchive, setPendingArchive] = useState<ResourceResponse | null>(null);
  const requestEpoch = useRef(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const resourceBindings = useCanvasStore((state) => state.resourceBindings);
  const canvasStatus = useCanvasStore((state) => state.status);
  const pinnedRevisionIds = useMemo(
    () => new Set(resourceBindings.map((binding) => binding.resourceRevisionId)),
    [resourceBindings],
  );

  useEffect(() => {
    setOpen(false);
    setDialog(null);
    setPendingArchive(null);
  }, [projectId]);

  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const epoch = ++requestEpoch.current;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setNotice("");
      try {
        const [listed, availableUpdates] = await Promise.all([
          listResources({
            kind,
            query,
            tags: tags.split(/[，,]/).map((tag) => tag.trim()).filter(Boolean),
            includeArchived: true,
            signal: controller.signal,
          }),
          listProjectResourceUpdates(projectId, controller.signal),
        ]);
        const nextPinnedResourceIds = new Set(
          availableUpdates.map((update) => update.resource_id),
        );
        for (const resource of listed) {
          if (pinnedRevisionIds.has(resource.latest_revision.id)) {
            nextPinnedResourceIds.add(resource.id);
          }
        }
        if (epoch !== requestEpoch.current || controller.signal.aborted) return;
        const localBindingsById = new Map(
          resourceBindings.map((binding) => [binding.id, binding]),
        );
        setResources(
          listed.filter(
            (resource) =>
              resource.archived_at === null ||
              showArchived ||
              nextPinnedResourceIds.has(resource.id),
          ),
        );
        setPinnedResourceIds(nextPinnedResourceIds);
        setUpdates(
          availableUpdates.filter(
            (update) =>
              localBindingsById.get(update.binding_id)?.resourceRevisionId !==
              update.latest_revision_id,
          ),
        );
      } catch (error: unknown) {
        if (controller.signal.aborted || epoch !== requestEpoch.current) return;
        setNotice(messageFor(error));
      } finally {
        if (epoch === requestEpoch.current && !controller.signal.aborted) setLoading(false);
      }
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [kind, open, pinnedRevisionIds, projectId, query, refreshEpoch, resourceBindings, showArchived, tags]);

  const closePanel = () => {
    setDialog(null);
    setPendingArchive(null);
    setOpen(false);
    queueMicrotask(() => triggerRef.current?.focus());
  };

  const loadDetail = async (
    resource: ResourceResponse,
    nextKind: "edit" | "bind" | "history",
    trigger: HTMLElement,
  ) => {
    setNotice("");
    try {
      const detail = await getResource(resource.id);
      setDialog({ kind: nextKind, resource: detail, trigger });
    } catch (error: unknown) {
      setNotice(messageFor(error));
    }
  };

  const upgradeBinding = (update: ResourceUpdateItem) => {
    const binding = useCanvasStore.getState().resourceBindings.find(
      (candidate) => candidate.id === update.binding_id,
    );
    if (!binding || canvasStatus === "conflict") {
      setNotice(canvasStatus === "conflict" ? "版本冲突期间资源绑定只读。" : "本地草稿中找不到该绑定。");
      return;
    }
    if (
      useCanvasStore.getState().setResourceBinding({
        ...binding,
        resourceRevisionId: update.latest_revision_id,
      })
    ) {
      setUpdates((current) => current.filter((candidate) => candidate.binding_id !== update.binding_id));
      setNotice(`已把 ${update.binding_key} 升级到 v${update.latest_sequence}，等待图谱变更审阅。`);
    }
  };

  const confirmArchive = async () => {
    if (!pendingArchive) return;
    try {
      await archiveResource(projectId, pendingArchive.id);
      setPendingArchive(null);
      setRefreshEpoch((value) => value + 1);
      setNotice("资源已归档；现有固定版本绑定仍可读取。");
    } catch (error: unknown) {
      setNotice(messageFor(error));
    }
  };

  return (
    <>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        className="resource-catalog-trigger"
        onClick={() => setOpen((value) => !value)}
        ref={triggerRef}
        type="button"
      >
        打开资源目录
        {updates.length > 0 ? <span aria-label={`${updates.length} 个资源更新`}>{updates.length}</span> : null}
      </button>
      {open ? createPortal(
        <aside
          aria-label="资源目录"
          className="resource-catalog"
          ref={panelRef}
          role="dialog"
          tabIndex={-1}
        >
          <header className="resource-catalog__header">
            <div>
              <p>RESOURCE CATALOG</p>
              <h2>版本化资源</h2>
            </div>
            <button aria-label="关闭资源目录" onClick={closePanel} type="button">×</button>
          </header>
          <div aria-label="资源类型" className="resource-catalog__tabs" role="tablist">
            {RESOURCE_KINDS.map((value) => (
              <button
                aria-selected={kind === value}
                key={value}
                onClick={() => setKind(value)}
                role="tab"
                type="button"
              >
                {RESOURCE_KIND_LABEL[value]}
              </button>
            ))}
          </div>
          <div className="resource-catalog__filters">
            <label>
              <span>搜索</span>
              <input aria-label="搜索资源" onChange={(event) => setQuery(event.target.value)} type="search" value={query} />
            </label>
            <label>
              <span>标签</span>
              <input aria-label="按标签筛选" onChange={(event) => setTags(event.target.value)} value={tags} />
            </label>
            <label className="resource-catalog__check">
              <input checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} type="checkbox" />
              显示已归档
            </label>
          </div>
          <button
            className="resource-catalog__create"
            onClick={(event) => setDialog({ kind: "create", resourceKind: kind, trigger: event.currentTarget })}
            type="button"
          >
            新建{RESOURCE_KIND_LABEL[kind]}资源
          </button>
          {updates.length > 0 ? (
            <section aria-label="可用资源更新" className="resource-updates">
              <h3>可用更新</h3>
              {updates.map((update) => (
                <div key={update.binding_id}>
                  <span>{update.binding_key} · v{update.pinned_sequence} → v{update.latest_sequence}</span>
                  <button disabled={canvasStatus === "conflict"} onClick={() => upgradeBinding(update)} type="button">升级此绑定</button>
                </div>
              ))}
            </section>
          ) : null}
          {notice ? <p aria-live="polite" className="resource-catalog__notice" role="status">{notice}</p> : null}
          <div aria-busy={loading} className="resource-catalog__list">
            {loading ? <p>正在刷新资源目录…</p> : null}
            {!loading && resources.length === 0 ? <p>当前筛选条件下没有资源。</p> : null}
            {resources.map((resource) => (
              <article className="resource-card" key={resource.id}>
                <div className="resource-card__heading">
                  <h3>{resource.name}</h3>
                  <span>v{resource.latest_revision.sequence}</span>
                </div>
                <p>{resourceSummary(resource)}</p>
                <div className="resource-card__meta">
                  {resource.latest_revision.tags.map((tag) => <span key={tag.normalized_tag}>#{tag.display_tag}</span>)}
                  {resource.archived_at ? <strong>已归档</strong> : null}
                  {pinnedResourceIds.has(resource.id) ? <strong>已固定</strong> : null}
                </div>
                <div className="resource-card__actions">
                  <button onClick={(event) => void loadDetail(resource, "history", event.currentTarget)} type="button">历史</button>
                  <button disabled={Boolean(resource.archived_at)} onClick={(event) => void loadDetail(resource, "edit", event.currentTarget)} type="button">新版本</button>
                  <button onClick={(event) => void loadDetail(resource, "bind", event.currentTarget)} type="button">绑定</button>
                  {!resource.archived_at ? <button onClick={() => setPendingArchive(resource)} type="button">归档</button> : null}
                </div>
              </article>
            ))}
          </div>
          {pendingArchive ? (
            <section aria-label="确认归档资源" className="resource-catalog__archive">
              <p>归档“{pendingArchive.name}”？现有固定版本绑定不会被删除。</p>
              <button onClick={() => setPendingArchive(null)} type="button">取消</button>
              <button onClick={() => void confirmArchive()} type="button">确认归档</button>
            </section>
          ) : null}
          {dialog?.kind === "history" ? (
            <section aria-label={`${dialog.resource.name} 版本历史`} className="resource-history">
              <header><h3>版本历史</h3><button onClick={() => setDialog(null)} type="button">关闭</button></header>
              <ol>
                {dialog.resource.revision_history.map((revision) => (
                  <li key={revision.id}>v{revision.sequence} · {revision.id.slice(0, 12)} · {new Date(revision.created_at).toLocaleString()}</li>
                ))}
              </ol>
            </section>
          ) : null}
        </aside>,
        document.body,
      ) : null}
      {dialog?.kind === "create" ? (
        <ResourceEditorDialog
          initialKind={dialog.resourceKind}
          onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); setRefreshEpoch((value) => value + 1); }}
          projectId={projectId}
          trigger={dialog.trigger}
        />
      ) : null}
      {dialog?.kind === "edit" ? (
        <ResourceEditorDialog
          onClose={() => setDialog(null)}
          onSaved={() => { setDialog(null); setRefreshEpoch((value) => value + 1); }}
          projectId={projectId}
          resource={dialog.resource}
          trigger={dialog.trigger}
        />
      ) : null}
      {dialog?.kind === "bind" ? (
        <ResourceBindingDialog
          onClose={() => setDialog(null)}
          resource={dialog.resource}
          trigger={dialog.trigger}
        />
      ) : null}
    </>
  );
}
