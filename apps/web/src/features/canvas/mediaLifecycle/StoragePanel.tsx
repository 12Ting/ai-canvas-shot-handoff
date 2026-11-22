import type { StorageSummary } from "../../../api/client";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let result = value / 1024;
  let index = 0;
  while (result >= 1024 && index < units.length - 1) {
    result /= 1024;
    index += 1;
  }
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(result)} ${units[index]}`;
}

export function StoragePanel({
  summary,
  onCreateGcPlan,
  onRefresh,
}: {
  summary: StorageSummary | null;
  onCreateGcPlan(): void;
  onRefresh(): void;
}) {
  return (
    <section aria-label="工作区存储" className="media-lifecycle__storage">
      <div className="media-lifecycle__section-heading">
        <h3>工作区存储</h3>
        <button onClick={onRefresh} type="button">刷新用量</button>
      </div>
      {summary ? (
        <dl className="media-lifecycle__metrics">
          <div><dt>配额</dt><dd>{formatBytes(summary.workspace_quota_bytes)}</dd></div>
          <div><dt>受管素材</dt><dd>{formatBytes(summary.managed_blob_bytes)}</dd></div>
          <div><dt>暂存与转码</dt><dd>{formatBytes(summary.staging_bytes)}</dd></div>
          <div><dt>隔离区</dt><dd>{formatBytes(summary.quarantine_bytes)}</dd></div>
          <div><dt>活动预留</dt><dd>{formatBytes(summary.active_reservation_bytes)}</dd></div>
          <div><dt>磁盘可用</dt><dd>{formatBytes(summary.physical_free_bytes)}</dd></div>
        </dl>
      ) : <p>正在读取本地存储用量…</p>}
      <button onClick={onCreateGcPlan} type="button">审阅可清理内容</button>
    </section>
  );
}
