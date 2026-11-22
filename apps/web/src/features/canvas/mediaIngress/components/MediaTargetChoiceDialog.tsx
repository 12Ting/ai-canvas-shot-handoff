import {
  useContext,
  useEffect,
  useRef,
  useSyncExternalStore,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { MediaIngressContext } from "../mediaIngressContext";

export type MediaTargetChoiceDialogProps = Readonly<{
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  getTriggerElement?(choiceId: string): HTMLElement | null;
  onChoiceRejected?(message: string): void;
  onChoiceSettled?(choiceId: string): void;
}>;

function focusElement(element: HTMLElement | null | undefined): boolean {
  if (!element?.isConnected) return false;
  element.focus({ preventScroll: true });
  return document.activeElement === element;
}

export function MediaTargetChoiceDialog({
  fallbackFocusRef,
  getTriggerElement,
  onChoiceRejected,
  onChoiceSettled,
}: MediaTargetChoiceDialogProps) {
  const ingress = useContext(MediaIngressContext);
  const controller = ingress?.controller;
  const snapshot = useSyncExternalStore(
    (listener) => controller?.subscribe(listener) ?? (() => undefined),
    () => controller?.getSnapshot() ?? null,
    () => controller?.getSnapshot() ?? null,
  );
  const activeChoice = snapshot?.pendingTargetChoices[0] ?? null;
  const dialogRef = useRef<HTMLElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  const replaceRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const focusedChoiceId = useRef<string | null>(null);

  useEffect(() => {
    if (!activeChoice || focusedChoiceId.current === activeChoice.choiceId) {
      return;
    }
    focusedChoiceId.current = activeChoice.choiceId;
    createRef.current?.focus({ preventScroll: true });
  }, [activeChoice]);

  if (!controller || !activeChoice) return null;

  const settle = (selected: "create" | "replace" | "cancel") => {
    const choiceId = activeChoice.choiceId;
    const trigger = getTriggerElement?.(choiceId) ?? null;
    const admission = controller.resolveTargetChoice(choiceId, selected);
    if (admission.kind === "rejected") {
      onChoiceRejected?.(admission.message);
    }
    onChoiceSettled?.(choiceId);
    if (controller.getSnapshot().pendingTargetChoices.length > 0) return;
    queueMicrotask(() => {
      if (focusElement(trigger)) return;
      focusElement(fallbackFocusRef?.current);
    });
  };

  const enabledButtons = () =>
    [createRef.current, replaceRef.current, cancelRef.current].filter(
      (button): button is HTMLButtonElement =>
        button !== null && !button.disabled,
    );

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      settle("cancel");
      return;
    }
    if (
      event.key === "Enter" &&
      (event.target === event.currentTarget ||
        event.target === createRef.current)
    ) {
      event.preventDefault();
      settle("create");
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

  return (
    <div className="media-target-choice-layer">
      <section
        aria-describedby="media-target-choice-description"
        aria-labelledby="media-target-choice-title"
        aria-modal="true"
        className="media-target-choice"
        onKeyDown={onKeyDown}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <p className="media-target-choice__eyebrow">目标媒体节点</p>
        <h2 id="media-target-choice-title">选择导入方式</h2>
        <p id="media-target-choice-description">
          “<bdi dir="auto">{activeChoice.displayName}</bdi>”要新建为独立节点，还是替换当前媒体？
        </p>
        {activeChoice.replacementBlockedReason ? (
          <p className="media-target-choice__reason">
            {activeChoice.replacementBlockedReason}
          </p>
        ) : null}
        <div className="media-target-choice__actions">
          <button
            className="media-target-choice__create"
            onClick={() => settle("create")}
            ref={createRef}
            type="button"
          >
            新建节点
          </button>
          <button
            className="media-target-choice__replace"
            disabled={!activeChoice.canReplace}
            onClick={() => settle("replace")}
            ref={replaceRef}
            type="button"
          >
            替换当前
          </button>
          <button
            className="media-target-choice__cancel"
            onClick={() => settle("cancel")}
            ref={cancelRef}
            type="button"
          >
            取消
          </button>
        </div>
      </section>
    </div>
  );
}
