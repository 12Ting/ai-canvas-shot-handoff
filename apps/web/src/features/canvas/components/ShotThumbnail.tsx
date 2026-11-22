import { useArtifactPreview } from "../useArtifactPreview";
import { useNearViewport } from "./useNearViewport";

export function ShotThumbnail({
  compact,
  id,
  revisionId,
  shotLabel,
}: {
  compact: boolean;
  id?: string;
  revisionId: string | null;
  shotLabel: string;
}) {
  const { isNearViewport, ref } = useNearViewport<HTMLSpanElement>(
    revisionId !== null,
  );
  const preview = useArtifactPreview(isNearViewport ? revisionId : null);
  const className = `shot-thumbnail${compact ? " shot-thumbnail--compact" : ""}`;

  if (revisionId === null) {
    return (
      <span className={className} data-thumbnail-state="empty" id={id} ref={ref}>
        <span>暂无分镜图</span>
      </span>
    );
  }
  if (!isNearViewport) {
    return (
      <span
        className={className}
        data-thumbnail-state="deferred"
        id={id}
        ref={ref}
      >
        <span>分镜图将在进入可见区域时加载</span>
      </span>
    );
  }
  if (preview.phase === "idle" || preview.phase === "loading") {
    return (
      <span
        className={className}
        data-thumbnail-state="loading"
        id={id}
        ref={ref}
        role="status"
      >
        <span>正在加载分镜图…</span>
      </span>
    );
  }
  if (
    preview.phase === "error" ||
    preview.revision.media_kind !== "image"
  ) {
    return (
      <span
        className={className}
        data-thumbnail-state="error"
        id={id}
        ref={ref}
        role="alert"
      >
        <span>分镜图预览不可用</span>
      </span>
    );
  }
  return (
    <span
      className={className}
      data-thumbnail-state="ready"
      id={id}
      ref={ref}
    >
      <img
        alt={`${shotLabel} 缩略图：${preview.revision.display_filename}`}
        draggable={false}
        src={preview.objectUrl}
      />
    </span>
  );
}
