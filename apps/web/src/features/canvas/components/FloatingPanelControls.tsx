import type { FloatingPanelController } from "./useFloatingPanel";

export function FloatingPanelResetButton({
  controller,
  label,
}: {
  controller: FloatingPanelController;
  label: string;
}) {
  return (
    <button
      aria-label={`重置${label}布局`}
      className="floating-panel__reset"
      onClick={controller.reset}
      type="button"
    >
      重置
    </button>
  );
}

export function FloatingPanelControls({
  controller,
  label,
}: {
  controller: FloatingPanelController;
  label: string;
}) {
  return (
    <>
      <div className="floating-panel__title-controls">
        <button
          {...controller.moveHandleProps}
          aria-label={`移动${label}`}
          className="floating-panel__move-handle"
          tabIndex={-1}
          title="拖动移动"
          type="button"
        >
          移动
        </button>
        <FloatingPanelResetButton controller={controller} label={label} />
      </div>
      <button
        {...controller.resizeHandleProps}
        aria-label={`调整${label}大小`}
        className="floating-panel__resize-handle"
        tabIndex={-1}
        title="拖动缩放"
        type="button"
      >
        <span aria-hidden="true">↘</span>
      </button>
    </>
  );
}
