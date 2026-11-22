import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import App from "./App";

const bootstrapSessionMock = vi.hoisted(() => vi.fn());

vi.mock("../api/client", () => ({ bootstrapSession: bootstrapSessionMock }));
vi.mock("../features/canvas/CanvasPage", () => ({
  CanvasPage: () => <main>语义画布已就绪</main>,
}));

describe("App bootstrap states", () => {
  beforeEach(() => {
    bootstrapSessionMock.mockReset();
    sessionStorage.clear();
    history.replaceState({}, "", "/");
  });

  afterEach(() => {
    cleanup();
  });

  it("announces the loading state politely", () => {
    bootstrapSessionMock.mockReturnValue(new Promise<"session">(() => undefined));

    render(<App />);

    const main = screen.getByRole("main");
    const status = screen.getByRole("status");
    expect(status).not.toBe(main);
    expect(main.contains(status)).toBe(true);
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toBe("正在启动 AI Canvas…");
  });

  it("announces bootstrap failures as alerts", async () => {
    bootstrapSessionMock.mockRejectedValue(new Error("exchange failed"));

    render(<App />);

    const alert = await screen.findByRole("alert");
    const main = screen.getByRole("main");
    expect(alert).not.toBe(main);
    expect(main.contains(alert)).toBe(true);
    expect(alert.textContent).toContain("无法建立本地会话");
    expect(alert.textContent).toContain("exchange failed");
    expect(alert.textContent).toContain("请关闭此页面并从 AI Canvas 启动器重新打开。");
  });

  it("opens the semantic canvas without a development note for a regular session", async () => {
    bootstrapSessionMock.mockResolvedValue("session");

    render(<App />);

    expect((await screen.findByText("语义画布已就绪")).textContent).toBe("语义画布已就绪");
    expect(screen.queryByRole("note", { name: "本地开发模式" })).toBeNull();
  });

  it("renders a persistent accessible warning for a server-attested auto session", async () => {
    bootstrapSessionMock.mockResolvedValue("development-auto-session");

    render(<App />);

    await screen.findByText("语义画布已就绪");
    const note = screen.getByRole("note", { name: "本地开发模式" });
    expect(note.textContent).toContain("本地开发自动会话已启用");
    expect(note.textContent).toContain("仅用于 127.0.0.1 调试");
    expect(note.textContent).toContain("数据 API 仍受临时会话保护");
  });

  it("does not trust forged URL or storage values when status attests a regular session", async () => {
    history.replaceState({}, "", "/#mode=development-auto-session");
    sessionStorage.setItem("ai-canvas-mode", "development-auto-session");
    bootstrapSessionMock.mockResolvedValue("session");

    render(<App />);

    await screen.findByText("语义画布已就绪");
    expect(screen.queryByRole("note", { name: "本地开发模式" })).toBeNull();
  });
});
