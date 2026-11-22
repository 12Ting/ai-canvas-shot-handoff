import { ViewportPortal } from "@xyflow/react";
import { useContext, useSyncExternalStore } from "react";
import { MediaIngressContext } from "../mediaIngressContext";
import type { IngressItemState } from "../mediaIngressTypes";

const labels: Partial<Record<IngressItemState, string>> = {
  QUEUED: "排队中",
  INITIALIZING: "正在处理",
  RECEIVING: "正在处理",
  VALIDATING: "正在处理",
  CANCELLING: "正在确认取消结果",
  PLACING: "正在放入画布",
};

export function IngressOverlayLayer() {
  const ingress = useContext(MediaIngressContext);
  const controller = ingress?.controller;
  const subscribe = (listener: () => void) => controller?.subscribe(listener) ?? (() => undefined);
  const getSnapshot = () => controller?.getSnapshot() ?? null;
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  if (!snapshot) return null;

  const slots = snapshot.batches.flatMap((batch) =>
    batch.slots.filter(
      (slot) =>
        slot.targetNodeId === null && labels[slot.currentItem.state],
    ),
  );
  return (
    <ViewportPortal>
      {slots.map((slot) => (
        <article
          aria-label={`媒体导入：${slot.currentItem.displayName}`}
          className="media-ingress-reservation"
          key={slot.slotId}
          style={{
            left: slot.reservation.position.x,
            top: slot.reservation.position.y,
            width: slot.reservation.width,
            height: slot.reservation.height,
          }}
        >
          <span aria-hidden="true">{slot.currentItem.mediaKind === "video" ? "🎬" : "🖼️"}</span>
          <strong>{slot.currentItem.displayName}</strong>
          <span>⏳ {labels[slot.currentItem.state]}</span>
        </article>
      ))}
    </ViewportPortal>
  );
}
