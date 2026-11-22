import { useEffect, useState } from "react";
import { base, errText, fetchBlob, post, type MediaFile } from "./client";

function useObjectUrl(path?: string) {
  const [result, setResult] = useState<{
    path: string;
    url?: string;
    error?: string;
  }>();
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let url: string | undefined;
    void fetchBlob(path, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setResult({ path, url });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setResult({ path, error: errText(e) });
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [path]);
  return result?.path === path ? result : undefined;
}

type Props = {
  project: string;
  file: MediaFile;
  edge?: "first" | "last";
  legacyPath?: string;
  compact?: boolean;
};
export function Media({
  project,
  file,
  edge = "first",
  legacyPath,
  compact = false,
}: Props) {
  const source = legacyPath ?? base(project) + "/files/" + file.sha256;
  const [streamUrl, setStreamUrl] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [playError, setPlayError] = useState("");
  const [legacyPlaying, setLegacyPlaying] = useState(false);
  const previewPath =
    file.kind === "image"
      ? source
      : file.kind === "video" && !legacyPath
        ? source + "/frame?edge=" + edge
        : undefined;
  const preview = useObjectUrl(previewPath);
  const legacy = useObjectUrl(legacyPlaying ? legacyPath : undefined);
  const play = async () => {
    setPlayError("");
    if (legacyPath) {
      setLegacyPlaying(true);
      return;
    }
    setRequesting(true);
    try {
      const { url } = await post<{ url: string }>(
        base(project) + "/play/" + file.sha256,
        {},
      );
      const local = new URL(url, location.href);
      if (local.origin !== location.origin)
        throw new Error("播放地址超出本机项目");
      setStreamUrl(local.href);
    } catch (e) {
      setPlayError(errText(e));
    } finally {
      setRequesting(false);
    }
  };
  const stream = legacy?.url || streamUrl;
  const failed = () => {
    setStreamUrl("");
    setLegacyPlaying(false);
    setPlayError("未能播放。点击重试或在文件所在目录打开。");
  };
  if (file.kind === "image")
    return (
      <div className="hf-media">
        {preview?.url ? (
          <img src={preview.url} alt={file.name} />
        ) : (
          <span>{preview?.error ? "图片预览失败" : "正在读取图片…"}</span>
        )}
      </div>
    );
  if (stream)
    return file.kind === "audio" ? (
      <audio
        aria-label={file.name}
        controls
        autoPlay
        src={stream}
        onError={failed}
      />
    ) : (
      <video
        aria-label={file.name}
        controls
        autoPlay
        playsInline
        src={stream}
        onError={failed}
      />
    );
  return (
    <button
      type="button"
      className={"hf-play" + (compact ? " is-compact" : "")}
      aria-label={"播放 " + file.name}
      onClick={() => void play()}
      disabled={requesting || (legacyPlaying && !legacy?.error)}
    >
      {preview?.url && <img src={preview.url} alt="" />}
      <span>
        {playError ||
          legacy?.error ||
          (requesting || legacyPlaying
            ? "正在读取…"
            : file.kind === "audio"
              ? "▷ 试听"
              : "▷ 播放")}
      </span>
      {preview?.error && <small>帧预览不可用</small>}
    </button>
  );
}
