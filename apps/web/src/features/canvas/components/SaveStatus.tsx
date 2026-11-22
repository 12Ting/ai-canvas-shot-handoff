import { useCanvasStore } from "../canvasStore";

const labels = {
  clean: "已保存",
  dirty: "有未保存修改",
  saving: "保存中",
  conflict: "保存冲突",
  error: "保存失败",
} as const;

export function SaveStatus() {
  const status = useCanvasStore((state) => state.status);
  const errorMessage = useCanvasStore((state) => state.errorMessage);

  return (
    <div
      aria-live="polite"
      className={`save-status save-status--${status}`}
      role="status"
    >
      {labels[status]}
      {errorMessage ? `：${errorMessage}` : ""}
    </div>
  );
}
