import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useCanvasStore } from "../canvas/canvasStore";
import type { CanvasResourceBinding, ResourceScopeKind } from "../canvas/canvasTypes";
import type { ResourceDetailResponse } from "../../api/client";

export function ResourceBindingDialog({
  binding,
  onClose,
  resource,
  trigger,
}: {
  binding?: CanvasResourceBinding;
  onClose(): void;
  resource: ResourceDetailResponse;
  trigger: HTMLElement | null;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const nodes = useCanvasStore((state) => state.nodes);
  const shots = useCanvasStore((state) => state.shots);
  const status = useCanvasStore((state) => state.status);
  const [scopeKind, setScopeKind] = useState<ResourceScopeKind>(binding?.scopeKind ?? "PROJECT");
  const [scopeId, setScopeId] = useState(binding?.scopeId ?? "");
  const [bindingKey, setBindingKey] = useState(binding?.bindingKey ?? `${resource.kind.toLowerCase()}.primary`);
  const [revisionId, setRevisionId] = useState(
    binding?.resourceRevisionId ?? resource.latest_revision.id,
  );
  const [notice, setNotice] = useState("");

  useEffect(() => {
    dialogRef.current?.focus();
    return () => trigger?.focus();
  }, [trigger]);

  const targets = useMemo(() => {
    if (scopeKind === "SHOT") {
      return [...shots]
        .sort((left, right) => left.order - right.order)
        .map((shot) => ({ id: shot.id, label: `分镜 ${shot.order + 1}` }));
    }
    if (scopeKind === "NODE") {
      return nodes.map((node, index) => ({
        id: node.id,
        label: `节点 ${index + 1} · ${node.data.kind}`,
      }));
    }
    return [];
  }, [nodes, scopeKind, shots]);

  useEffect(() => {
    if (scopeKind === "PROJECT") setScopeId("");
    else if (!targets.some((target) => target.id === scopeId)) setScopeId(targets[0]?.id ?? "");
  }, [scopeId, scopeKind, targets]);

  const save = () => {
    const next: CanvasResourceBinding = {
      id: binding?.id ?? crypto.randomUUID(),
      resourceKind: resource.kind,
      bindingKey: bindingKey.trim(),
      scopeKind,
      scopeId: scopeKind === "PROJECT" ? null : scopeId,
      resourceRevisionId: revisionId,
    };
    if (!useCanvasStore.getState().setResourceBinding(next)) {
      setNotice(status === "conflict" ? "版本冲突期间资源绑定只读。" : "绑定无效或同一作用域已存在相同绑定键。");
      return;
    }
    onClose();
  };

  const dialog = (
    <div className="resource-modal" role="presentation">
      <section
        aria-labelledby={titleId}
        aria-modal="true"
        className="resource-dialog"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <p className="resource-dialog__eyebrow">EXACT REVISION PIN</p>
        <h2 id={titleId}>绑定资源 · {resource.name}</h2>
        <p>绑定只写入本地图谱草稿，必须经过图谱变更审阅后才会成为新版本。</p>
        <label>
          作用域
          <select
            aria-label="作用域"
            disabled={status === "conflict"}
            onChange={(event) => setScopeKind(event.target.value as ResourceScopeKind)}
            value={scopeKind}
          >
            <option value="PROJECT">项目</option>
            <option value="SHOT">分镜</option>
            <option value="NODE">节点</option>
          </select>
        </label>
        {scopeKind !== "PROJECT" ? (
          <label>
            作用目标
            <select
              aria-label="作用目标"
              disabled={status === "conflict"}
              onChange={(event) => setScopeId(event.target.value)}
              value={scopeId}
            >
              {targets.map((target) => (
                <option key={target.id} value={target.id}>{target.label}</option>
              ))}
            </select>
          </label>
        ) : null}
        <label>
          绑定键
          <input
            aria-label="绑定键"
            disabled={status === "conflict"}
            maxLength={64}
            onChange={(event) => setBindingKey(event.target.value)}
            pattern="[A-Za-z0-9_][A-Za-z0-9_.-]*"
            value={bindingKey}
          />
        </label>
        <label>
          固定版本
          <select
            aria-label="固定版本"
            disabled={status === "conflict"}
            onChange={(event) => setRevisionId(event.target.value)}
            value={revisionId}
          >
            {resource.revision_history.map((revision) => (
              <option key={revision.id} value={revision.id}>
                v{revision.sequence} · {revision.id.slice(0, 12)}
              </option>
            ))}
          </select>
        </label>
        {notice ? <p aria-live="polite" role="status">{notice}</p> : null}
        <div className="resource-dialog__actions">
          <button onClick={onClose} type="button">取消</button>
          <button disabled={status === "conflict"} onClick={save} type="button">保存绑定</button>
        </div>
      </section>
    </div>
  );
  return createPortal(dialog, document.body);
}
