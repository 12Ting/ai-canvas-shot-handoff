import { useState } from "react";
import {
  confirmGcPlan,
  restoreQuarantine,
  type GcPlan,
  type QuarantineEntry,
} from "../../../api/client";

function formatBytes(value: number): string {
  return `${new Intl.NumberFormat("zh-CN").format(value)} 字节`;
}

export function GcPlanDialog({
  onClose,
  onCompleted,
  plan,
}: {
  onClose(): void;
  onCompleted(): void;
  plan: GcPlan;
}) {
  const [quarantined, setQuarantined] = useState<readonly QuarantineEntry[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const entries = await confirmGcPlan(plan.plan_id, plan.summary_sha256);
      setQuarantined(entries);
      setMessage("已移入隔离区；7 天内可以恢复，之后才会物理删除。");
      onCompleted();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "清理确认失败，请重试。");
    } finally {
      setBusy(false);
    }
  };
  const restore = async (entry: QuarantineEntry) => {
    setBusy(true);
    try {
      const restored = await restoreQuarantine(entry.quarantine_id);
      setMessage(restored ? "素材已从隔离区恢复。" : "该素材当前无法恢复。");
      onCompleted();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "恢复失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div aria-labelledby="gc-plan-title" aria-modal="true" className="media-lifecycle__dialog" role="dialog">
      <div className="media-lifecycle__dialog-card">
        <h3 id="gc-plan-title">审阅清理计划</h3>
        <p>确认前不会移动任何正式素材。确认后先隔离 7 天，期间可以恢复。</p>
        <p><code>{plan.summary_sha256}</code></p>
        {plan.entries.length === 0 ? <p>当前没有可清理的正式内容。</p> : (
          <ul>{plan.entries.map((entry) => <li key={entry.entry_id}>{entry.reason} · 逻辑 {formatBytes(entry.logical_reclaim_bytes)} · 物理 {formatBytes(entry.physical_reclaim_bytes)}</li>)}</ul>
        )}
        {message ? <p aria-live="polite" role="status">{message}</p> : null}
        {quarantined?.map((entry) => <button disabled={busy} key={entry.quarantine_id} onClick={() => void restore(entry)} type="button">恢复隔离素材</button>)}
        <div className="media-lifecycle__dialog-actions">
          <button disabled={busy || quarantined !== null || plan.entries.length === 0} onClick={() => void confirm()} type="button">确认移入隔离区</button>
          <button onClick={onClose} type="button">关闭</button>
        </div>
      </div>
    </div>
  );
}
