import { useEffect, useState } from "react";
import { bootstrapSession, type SessionMode } from "../api/client";
import { CanvasPage } from "../features/canvas/CanvasPage";
import { Studio } from "../features/handoff/Studio";

type BootState =
  | { status: "loading" }
  | { status: "ready"; mode: SessionMode }
  | { status: "error"; message: string };

export default function App() {
  const [bootState, setBootState] = useState<BootState>({ status: "loading" });

  useEffect(() => {
    let active = true;

    bootstrapSession()
      .then((mode) => {
        if (active) setBootState({ status: "ready", mode });
      })
      .catch((error: unknown) => {
        if (active) {
          setBootState({
            status: "error",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      });

    return () => {
      active = false;
    };
  }, []);

  if (bootState.status === "loading") {
    return (
      <main className="boot-state">
        <div role="status" aria-live="polite">
          正在启动 AI Canvas…
        </div>
      </main>
    );
  }

  if (bootState.status === "error") {
    return (
      <main className="boot-state boot-state--error">
        <div role="alert">
          <h1>无法建立本地会话</h1>
          <p>{bootState.message}</p>
          <p>请关闭此页面并从 AI Canvas 启动器重新打开。</p>
        </div>
      </main>
    );
  }

  const developmentAutoSession = bootState.mode === "development-auto-session";
  const params = new URLSearchParams(location.search);
  const studioProject = params.get("view") === "shots" ? params.get("project") : null;
  return (
    <div
      className={
        developmentAutoSession
          ? "app-session-shell"
          : "app-session-shell app-session-shell--regular"
      }
    >
      {developmentAutoSession ? (
        <aside
          aria-label="本地开发模式"
          className="local-development-note"
          role="note"
        >
          <strong>本地开发自动会话已启用</strong>
          <span>仅用于 127.0.0.1 调试；数据 API 仍受临时会话保护。</span>
        </aside>
      ) : null}
      {studioProject ? <Studio projectId={studioProject} /> : <CanvasPage />}
    </div>
  );
}
