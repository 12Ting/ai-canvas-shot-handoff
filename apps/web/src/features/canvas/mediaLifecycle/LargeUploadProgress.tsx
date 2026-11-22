export function LargeUploadProgress({
  fileName,
  receivedBytes,
  totalBytes,
  estimatedSecondsRemaining = null,
}: {
  fileName: string;
  receivedBytes: number;
  totalBytes: number;
  estimatedSecondsRemaining?: number | null;
}) {
  const received = Math.max(0, Math.min(receivedBytes, totalBytes));
  return (
    <p aria-live="polite" className="media-lifecycle__upload-progress" role="status">
      <bdi dir="auto">{fileName}</bdi>：已传 {received.toLocaleString("zh-CN")} /{" "}
      {totalBytes.toLocaleString("zh-CN")} 字节。{" "}
      {estimatedSecondsRemaining === null
        ? "剩余时间正在计算"
        : estimatedSecondsRemaining === 0
          ? "上传完成，正在校验"
          : `预计剩余 ${estimatedSecondsRemaining} 秒`}
      。仅本次页面会话内可恢复；刷新或关闭页面后请重新上传。
    </p>
  );
}
