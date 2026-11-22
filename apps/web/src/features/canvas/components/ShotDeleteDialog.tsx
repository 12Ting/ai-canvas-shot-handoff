import {
  useEffect,
  useRef,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { useCanvasStore } from "../canvasStore";
import type { CanvasRuntimeIdentity } from "../canvasTypes";

function focusElement(element: HTMLElement | null | undefined): boolean {
  if (!element?.isConnected) return false;
  element.focus({ preventScroll: true });
  return document.activeElement === element;
}

export function ShotDeleteDialog({
  fallbackFocusRef,
  getTriggerElement,
  identity,
  onClose,
  shotId,
}: {
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  getTriggerElement?(): HTMLElement | null;
  identity?: CanvasRuntimeIdentity | null;
  onClose(): void;
  shotId: string | null;
}) {
  const currentProjectId = useCanvasStore((state) => state.projectId);
  const currentLoadEpoch = useCanvasStore((state) => state.loadEpoch);
  const targetIdentityIsCurrent =
    identity === undefined ||
    (identity !== null &&
      identity.projectId === currentProjectId &&
      identity.loadEpoch === currentLoadEpoch);
  const shot = useCanvasStore((state) =>
    shotId === null || !targetIdentityIsCurrent
      ? null
      : state.shots.find((candidate) => candidate.id === shotId) ?? null,
  );
  const locked = useCanvasStore((state) => state.status === "conflict");
  const dialogRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const focusedShotId = useRef<string | null>(null);

  useEffect(() => {
    if (!shot) {
      focusedShotId.current = null;
      return;
    }
    if (focusedShotId.current === shot.id) return;
    focusedShotId.current = shot.id;
    cancelRef.current?.focus({ preventScroll: true });
  }, [shot]);

  if (!shot) return null;

  const settle = () => {
    const trigger = getTriggerElement?.() ?? null;
    onClose();
    queueMicrotask(() => {
      if (focusElement(trigger)) return;
      focusElement(fallbackFocusRef?.current);
    });
  };

  const enabledButtons = () =>
    [cancelRef.current, confirmRef.current].filter(
      (button): button is HTMLButtonElement => button !== null && !button.disabled,
    );

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      settle();
      return;
    }
    if (event.key !== "Tab") return;
    const buttons = enabledButtons();
    if (buttons.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = buttons[0];
    const last = buttons.at(-1) ?? first;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const confirm = () => {
    const current = useCanvasStore.getState();
    if (
      locked ||
      (identity !== undefined &&
        (identity === null ||
          identity.projectId !== current.projectId ||
          identity.loadEpoch !== current.loadEpoch)) ||
      !current.shots.some((candidate) => candidate.id === shot.id)
    ) {
      return;
    }
    current.deleteShot(shot.id);
    if (useCanvasStore.getState().shots.some((candidate) => candidate.id === shot.id)) {
      return;
    }
    settle();
  };

  return (
    <div className="shot-delete-layer">
      <section
        aria-label="删除分镜"
        aria-modal="true"
        className="shot-delete-dialog"
        data-testid="shot-delete-dialog"
        onKeyDown={onKeyDown}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <h2>删除分镜 {shot.order + 1}？</h2>
        <p>这会删除分镜节点及其画布连接。媒体节点和本地素材会保留。</p>
        <div>
          <button onClick={settle} ref={cancelRef} type="button">取消</button>
          <button disabled={locked} onClick={confirm} ref={confirmRef} type="button">确认删除分镜</button>
        </div>
      </section>
    </div>
  );
}
