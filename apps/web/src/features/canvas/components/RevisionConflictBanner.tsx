import { useEffect, useRef, useState } from "react";

type ConflictAction = "reload" | "copy";

export type RevisionConflictBannerProps = {
  reloadServer(): Promise<void>;
  keepLocalCopy(): Promise<void>;
  disabledReason?: string | null;
};

export function RevisionConflictBanner({
  reloadServer,
  keepLocalCopy,
  disabledReason = null,
}: RevisionConflictBannerProps) {
  const [busy, setBusy] = useState<ConflictAction | null>(null);
  const busyRef = useRef<ConflictAction | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const choose = async (kind: ConflictAction, action: () => Promise<void>) => {
    if (busyRef.current !== null || disabledReason !== null) return;
    busyRef.current = kind;
    setBusy(kind);
    try {
      await action();
    } catch {
      // CanvasPage owns the visible error; the button boundary prevents unhandled rejections.
    } finally {
      busyRef.current = null;
      if (mountedRef.current) setBusy(null);
    }
  };

  return (
    <section className="conflict-banner" role="alert">
      <p>另一页面已保存了新版本。你的本地画布仍被保留，请明确选择处理方式。</p>
      <button
        aria-busy={busy === "reload"}
        disabled={busy !== null || disabledReason !== null}
        onClick={() => void choose("reload", reloadServer)}
        title={disabledReason ?? undefined}
        type="button"
      >
        重新加载服务端版本
      </button>
      <button
        aria-busy={busy === "copy"}
        disabled={busy !== null || disabledReason !== null}
        onClick={() => void choose("copy", keepLocalCopy)}
        title={disabledReason ?? undefined}
        type="button"
      >
        保留本地副本
      </button>
    </section>
  );
}
