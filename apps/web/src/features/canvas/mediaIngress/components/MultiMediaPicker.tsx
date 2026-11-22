import { useReactFlow } from "@xyflow/react";
import { useContext, useRef, useState, type ChangeEvent } from "react";
import { useCanvasStore } from "../../canvasStore";
import { MediaIngressContext } from "../mediaIngressContext";

const ACCEPTED_MEDIA = "image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime,video/x-matroska,.mov,.mkv";

function flowCenter(input: HTMLInputElement | null): { x: number; y: number } {
  const root = input?.closest<HTMLElement>(".react-flow");
  const bounds = root?.getBoundingClientRect();
  if (
    bounds &&
    Number.isFinite(bounds.left) &&
    Number.isFinite(bounds.top) &&
    Number.isFinite(bounds.width) &&
    Number.isFinite(bounds.height) &&
    bounds.width > 0 &&
    bounds.height > 0
  ) {
    return {
      x: bounds.left + bounds.width / 2,
      y: bounds.top + bounds.height / 2,
    };
  }
  return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
}

export function MultiMediaPicker() {
  const ingress = useContext(MediaIngressContext);
  const input = useRef<HTMLInputElement>(null);
  const { screenToFlowPosition } = useReactFlow();
  const conflict = useCanvasStore((state) => state.status === "conflict");
  const [message, setMessage] = useState("");

  if (!ingress?.controller) return null;
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    setMessage("");
    if (files.length === 0) return;
    if (conflict) {
      setMessage("请先处理画布版本冲突");
      return;
    }
    const anchor = screenToFlowPosition(flowCenter(input.current));
    const admission = ingress.controller.enqueue({
      projectId: ingress.projectId,
      loadEpoch: ingress.loadEpoch,
      source: "multi_picker",
      candidates: files.map((file) => ({
        kind: "file" as const,
        file,
        displayName: file.name,
      })),
      anchor,
      targetMode: "create",
      targetBinding: null,
    });
    if (admission.kind === "rejected") setMessage(admission.message);
  };

  return (
    <div className="media-ingress-picker">
      <button onClick={() => input.current?.click()} type="button">
        ＋ 导入多个媒体
      </button>
      <input
        accept={ACCEPTED_MEDIA}
        aria-label="选择多个本地媒体"
        className="media-ingress-picker__input"
        multiple
        onChange={onChange}
        ref={input}
        tabIndex={-1}
        type="file"
      />
      {message ? <p role="alert">{message}</p> : null}
    </div>
  );
}
