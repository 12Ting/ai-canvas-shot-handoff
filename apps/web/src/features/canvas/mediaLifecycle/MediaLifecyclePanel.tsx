import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  createExternalReference,
  createGcPlan,
  grantExternalRoot,
  importExternalCopy,
  reconfirmExternalReference,
  requestRendition,
  storageSummary,
  TranscodeConfirmationRequiredError,
  verifyExternalReference,
  type ExternalReference,
  type ExternalRootGrant,
  type GcPlan,
  type StorageSummary,
} from "../../../api/client";
import { GcPlanDialog } from "./GcPlanDialog";
import { StoragePanel } from "./StoragePanel";
import "./mediaLifecycle.css";

function formatBytes(value: number): string {
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value / 1024 / 1024)} MiB`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function MediaLifecyclePanel({ projectId }: { projectId: string | null }) {
  const [isOpen, setIsOpen] = useState(false);
  const [summary, setSummary] = useState<StorageSummary | null>(null);
  const [grant, setGrant] = useState<ExternalRootGrant | null>(null);
  const [reference, setReference] = useState<ExternalReference | null>(null);
  const [gcPlan, setGcPlan] = useState<GcPlan | null>(null);
  const [pendingTranscode, setPendingTranscode] = useState<TranscodeConfirmationRequiredError | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const transcodeTrigger = useRef<HTMLButtonElement>(null);

  const refreshSummary = useCallback(() => {
    void storageSummary().then(setSummary).catch((error: unknown) => {
      setMessage(errorMessage(error, "无法读取存储用量。"));
    });
  }, []);
  useEffect(() => {
    if (isOpen) refreshSummary();
  }, [isOpen, refreshSummary]);

  const selectExternal = async () => {
    if (!grant || !projectId) return;
    try {
      setReference(await createExternalReference(grant.grant_id, { projectId, requestedMediaKind: "video" }));
    } catch (error) {
      setMessage(errorMessage(error, "外部媒体导入失败。"));
    }
  };
  const startTranscode = async () => {
    if (!reference) return;
    try {
      await requestRendition(reference.revision_id, "preview_720p");
      setMessage("转码作业已进入队列。");
    } catch (error) {
      if (error instanceof TranscodeConfirmationRequiredError) {
        setPendingTranscode(error);
        return;
      }
      setMessage(errorMessage(error, "无法创建转码作业。"));
    }
  };
  const closeTranscode = () => {
    setPendingTranscode(null);
    queueMicrotask(() => transcodeTrigger.current?.focus());
  };

  if (!isOpen) {
    return (
      <button
        aria-controls="media-lifecycle-panel"
        aria-expanded="false"
        className="media-lifecycle__launcher"
        onClick={() => setIsOpen(true)}
        type="button"
      >
        打开媒体生命周期
      </button>
    );
  }

  return createPortal(
    <aside aria-label="媒体生命周期" className="media-lifecycle" id="media-lifecycle-panel">
      <header className="media-lifecycle__header">
        <h2>媒体生命周期</h2>
        <button aria-label="关闭媒体生命周期" onClick={() => setIsOpen(false)} type="button">关闭</button>
      </header>
      <p>大文件仅在当前页面会话内可续传；外部文件永不静默替换原版本。</p>
      {message ? <p aria-live="polite" role="status">{message}</p> : null}
      <section>
        <h3>受控外部媒体</h3>
        <button onClick={() => void grantExternalRoot().then(setGrant).catch((error: unknown) => setMessage(errorMessage(error, "目录授权失败。")))} type="button">授权外部目录</button>
        {grant ? <p>已授权：{grant.display_name}</p> : null}
        <button disabled={!grant || !projectId} onClick={() => void selectExternal()} type="button">从已授权目录添加视频</button>
        {reference ? (
          <div className="media-lifecycle__reference">
            <p>来源：外部目录 · 状态：{reference.health} · revision：{reference.revision_id}</p>
            <button onClick={() => void verifyExternalReference(reference.revision_id).then(setReference).catch((error: unknown) => setMessage(errorMessage(error, "外部状态检查失败。")))} type="button">检查外部状态</button>
            {reference.health === "UNAVAILABLE" || reference.health === "DRIFTED" ? (
              <>
                <button onClick={() => void reconfirmExternalReference(reference.revision_id, grant!.grant_id).then(setReference).catch((error: unknown) => setMessage(errorMessage(error, "重新确认失败。")))} type="button">重新确认新版本</button>
                <button onClick={() => void importExternalCopy(reference.revision_id).then((copy) => setMessage(`已导入工作区副本：${copy.revision_id}`)).catch((error: unknown) => setMessage(errorMessage(error, "导入副本失败。")))} type="button">导入工作区副本</button>
              </>
            ) : null}
            <button onClick={() => void startTranscode()} ref={transcodeTrigger} type="button">生成 720p 代理</button>
          </div>
        ) : null}
      </section>
      <StoragePanel onCreateGcPlan={() => void createGcPlan().then(setGcPlan).catch((error: unknown) => setMessage(errorMessage(error, "无法创建清理计划。")))} onRefresh={refreshSummary} summary={summary} />
      {pendingTranscode ? (
        <div aria-labelledby="transcode-title" aria-modal="true" className="media-lifecycle__dialog" role="dialog">
          <div className="media-lifecycle__dialog-card">
            <h3 id="transcode-title">确认生成代理</h3>
            <p>源素材不会改变。将生成 {pendingTranscode.profile}（{pendingTranscode.width} × {pendingTranscode.height}），预计新增 {formatBytes(pendingTranscode.estimatedOutputBytes)}，并占用本机 CPU 与受保护磁盘空间。</p>
            <button onClick={() => void requestRendition(reference!.revision_id, pendingTranscode.profile, pendingTranscode.confirmationToken).then(() => { setMessage("转码作业已进入队列。"); closeTranscode(); }).catch((error: unknown) => setMessage(errorMessage(error, "无法确认转码。")))} type="button">确认开始转码</button>
            <button onClick={closeTranscode} type="button">取消</button>
          </div>
        </div>
      ) : null}
      {gcPlan ? <GcPlanDialog onClose={() => setGcPlan(null)} onCompleted={refreshSummary} plan={gcPlan} /> : null}
    </aside>,
    document.body,
  );
}
