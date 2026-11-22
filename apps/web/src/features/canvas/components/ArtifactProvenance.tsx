import type { ArtifactRevision } from "../../../api/client";

function formatCreatedAt(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "未知";
  return new Date(timestamp)
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z")
    .replace("T", " ")
    .replace(/Z$/, " UTC");
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

export function ArtifactProvenance({ revision }: { revision: ArtifactRevision }) {
  const videoMetadata = revision.metadata.kind === "video" ? revision.metadata : null;
  const createdAt = formatCreatedAt(revision.created_at);
  return (
    <section aria-label="素材来源信息" className="artifact-provenance">
      <dl>
        <div><dt>来源</dt><dd>{revision.source_kind}</dd></div>
        <div><dt>来源项目</dt><dd><bdi>{revision.source_project_id}</bdi></dd></div>
        <div><dt>原始项目</dt><dd><bdi>{revision.origin_project_id}</bdi></dd></div>
        <div><dt>创建时间</dt><dd><time dateTime={revision.created_at}>{createdAt}</time></dd></div>
        <div><dt>画面</dt><dd>{revision.metadata.width} × {revision.metadata.height}</dd></div>
        {videoMetadata ? (
          <>
            <div><dt>时长</dt><dd>时长 {formatNumber(videoMetadata.duration_seconds)} 秒</dd></div>
            <div><dt>帧率</dt><dd>帧率 {formatNumber(videoMetadata.frames_per_second)} fps</dd></div>
          </>
        ) : null}
      </dl>
    </section>
  );
}
