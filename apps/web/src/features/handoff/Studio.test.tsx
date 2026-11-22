import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Studio } from "./Studio";
import { Issues } from "./Library";
import type { Candidate, Workspace } from "./client";
import { getWorkspace, post, locate } from "./client";

vi.mock("./client", async (importActual) => {
  const actual = await importActual<typeof import("./client")>();
  return {
    ...actual,
    getWorkspace: vi.fn(),
    post: vi.fn(),
    locate: vi.fn(),
    fetchBlob: vi.fn(() => new Promise(() => {})),
  };
});
const clip = (id: string): Candidate => ({
  id,
  name: id + ".mp4",
  sha256: id,
  path: id + ".mp4",
  mime: "video/mp4",
  kind: "video",
  condition_id: null,
});
function project(): Workspace {
  return {
    project_id: "film",
    name: "回声",
    version: 2,
    episodes: [{ id: "ep", title: "第一集" }],
    shots: ["敲门", "进屋"].map((title, i) => ({
      id: "s" + i,
      episode_id: "ep",
      title,
      order: i,
      selected_id: null,
      dialogue: "",
      sounds: [],
    })),
    groups: [
      {
        id: "g0",
        shot_id: "s0",
        title: "雨夜",
        path: "",
        conditions: [],
        references: [],
        bindings: [],
        candidates: [clip("A"), clip("B")],
      },
      {
        id: "g1",
        shot_id: "s1",
        title: "室内",
        path: "",
        conditions: [],
        references: [],
        bindings: [],
        candidates: [clip("C")],
      },
    ],
    assets: [],
    issues: [],
  };
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getWorkspace).mockResolvedValue(project());
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("镜头工作台", () => {
  it("adopts in one click without overwriting a reason or moving an older version's open issue", async () => {
    const state = project();
    const [first, second] = state.groups[0].candidates;
    Object.assign(first, { name: "敲门.mp4", version: 1 });
    Object.assign(second, { name: "敲门.mp4", version: 2, selection_reason: "转头接得上" });
    state.shots[0].selected_id = first.id;
    state.issues.push({
      id: "issue", shot_id: "s0", candidate_id: first.id, condition_id: null,
      status: "open", note: "杯子换手了",
    });
    vi.mocked(getWorkspace).mockResolvedValue(state);
    const next = structuredClone(state);
    next.shots[0].selected_id = second.id;
    vi.mocked(post).mockResolvedValue(next);
    render(<Studio projectId="film" />);
    await screen.findByRole("button", { name: /V1.*敲门.mp4.*已采用.*待补镜/ });
    fireEvent.click(screen.getByRole("button", { name: /V2.*敲门.mp4/ }));
    expect(screen.getByText(/采用理由.*转头接得上/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "采用此版" }));
    await screen.findByRole("button", { name: /V2.*敲门.mp4.*已采用/ });
    expect(post).toHaveBeenCalledWith(expect.any(String), {
      action: "select_candidate", data: { shot_id: "s0", candidate_id: second.id }, expected_version: 2,
    });
    expect(screen.getByRole("button", { name: /V1.*敲门.mp4.*待补镜/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "记补镜意见" }));
    const issues = screen.getByRole("dialog", { name: "补镜清单" });
    expect(within(issues).getByRole("textbox", { name: "给 雨夜 / V2 / 敲门.mp4 留补镜意见" })).toBeTruthy();
    expect(within(issues).getByText(/敲门 \/ 雨夜 \/ V1 \/ 敲门.mp4/)).toBeTruthy();
    expect(within(issues).getByRole("option", { name: "雨夜 / V2 / 敲门.mp4" })).toBeTruthy();
  });

  it("pins a reason to the previewed version through a scan and lets its author clear it later", async () => {
    const scanned = project();
    scanned.version = 3;
    scanned.groups[0].candidates.push(clip("new"));
    const adopted = structuredClone(scanned);
    adopted.version = 4;
    adopted.shots[0].selected_id = "B";
    adopted.groups[0].candidates[1].selection_reason = "视线接上后镜";
    const cleared = structuredClone(adopted);
    cleared.version = 5;
    cleared.groups[0].candidates[1].selection_reason = "";
    vi.mocked(post).mockResolvedValueOnce(scanned).mockResolvedValueOnce(adopted).mockResolvedValueOnce(cleared);
    render(<Studio projectId="film" />);
    fireEvent.click(await screen.findByRole("button", { name: "写采用理由" }));
    fireEvent.change(screen.getByRole("textbox", { name: "采用理由" }), { target: { value: "视线接上后镜" } });
    fireEvent.click(screen.getByRole("button", { name: /同步目录/ }));
    await screen.findByRole("button", { name: /new.mp4/ });
    expect(screen.getByRole("textbox", { name: "采用理由" })).toHaveProperty("value", "视线接上后镜");
    fireEvent.click(screen.getByRole("button", { name: "采用此版" }));
    fireEvent.click(await screen.findByRole("button", { name: "编辑采用理由" }));
    fireEvent.change(screen.getByRole("textbox", { name: "采用理由" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存理由" }));
    await screen.findByRole("button", { name: "写采用理由" });
    expect(post).toHaveBeenNthCalledWith(2, expect.any(String), {
      action: "select_candidate", data: { shot_id: "s0", candidate_id: "B", reason: "视线接上后镜" }, expected_version: 3,
    });
    expect(post).toHaveBeenNthCalledWith(3, expect.any(String), {
      action: "save_selection_reason", data: { shot_id: "s0", candidate_id: "B", reason: "" }, expected_version: 4,
    });
    expect(screen.getByRole("button", { name: /B.mp4.*已采用/ })).toBeTruthy();
  });

  it("drops an unsubmitted draft when the user switches candidates", async () => {
    render(<Studio projectId="film" />);
    fireEvent.click(await screen.findByRole("button", { name: "写采用理由" }));
    fireEvent.change(screen.getByRole("textbox", { name: "采用理由" }), { target: { value: "只适用于 B" } });
    fireEvent.click(screen.getByRole("button", { name: /A.mp4/ }));
    expect(screen.queryByRole("textbox", { name: "采用理由" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "写采用理由" }));
    expect(screen.getByRole("textbox", { name: "采用理由" })).toHaveProperty("value", "");
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps a partially written issue attached to the original candidate after a directory scan", async () => {
    const state = project();
    const action = vi.fn().mockResolvedValue(state);
    const props = {
      state,
      shot: state.shots[0],
      action,
      visit: vi.fn(),
      exportIssue: vi.fn(),
    };
    const form = render(
      <Issues {...props} candidateId="A" temporary={{ next_id: "C" }} />,
    );
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "手势不连贯" },
    });
    form.rerender(<Issues {...props} candidateId="B" temporary={{}} />);
    expect(screen.getByLabelText("给 雨夜 / V1 / A.mp4 留补镜意见")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "记录补镜" }));
    await waitFor(() =>
      expect(action).toHaveBeenCalledWith("open_issue", {
        candidate_id: "A",
        note: "手势不连贯",
        next_id: "C",
      }),
    );
  });

  it.each(["记补镜意见", "补镜清单"])("keeps the shot, group and version viewed from %s through a background refresh", async (entry) => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const before = project();
    const scanned = structuredClone(before);
    scanned.version = 3;
    scanned.shots[1].order = -1;
    scanned.groups[0].candidates.push(clip("new"));
    scanned.groups.unshift({ ...scanned.groups[0], id: "extra", title: "新组", candidates: [clip("D")] });
    vi.mocked(getWorkspace).mockResolvedValueOnce(before).mockResolvedValue(scanned);
    vi.mocked(post).mockResolvedValue(scanned);

    render(<Studio projectId="film" />);
    fireEvent.click(await screen.findByRole("button", { name: entry }));
    const dialog = within(screen.getByRole("dialog", { name: "补镜清单" }));
    const original = { name: "给 雨夜 / V2 / B.mp4 留补镜意见" };
    fireEvent.change(dialog.getByRole("textbox", original), { target: { value: "保留停顿，转身向右" } });
    await act(() => vi.advanceTimersByTimeAsync(4500));
    expect(getWorkspace).toHaveBeenCalledTimes(2);
    expect(dialog.getByRole("textbox", original)).toHaveProperty("value", "保留停顿，转身向右");

    fireEvent.click(dialog.getByRole("button", { name: "记录补镜" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(expect.any(String), {
      action: "open_issue", expected_version: 3,
      data: expect.objectContaining({ candidate_id: "B", note: "保留停顿，转身向右" }),
    }));
    fireEvent.click(dialog.getByRole("button", { name: "关闭" }));
    expect(screen.getByRole("heading", { name: "敲门" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /V2.*B.mp4/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("allows a temporary neighbor without adopting a candidate and preserves it with the issue", async () => {
    const state = project();
    vi.mocked(post).mockResolvedValue(state);
    render(<Studio projectId="film" />);
    await screen.findByRole("heading", { name: "敲门" });
    expect(screen.getByText("本集无前镜")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("后镜临时对照"), {
      target: { value: "C" },
    });
    fireEvent.click(screen.getByRole("button", { name: /A\.mp4.*待核对/ }));
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "记补镜意见" }));
    const dialog = await screen.findByRole("dialog", { name: "补镜清单" });
    fireEvent.change(within(dialog).getByRole("textbox"), {
      target: { value: "改为右手敲门" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "记录补镜" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/api/v1/projects/film/handoff/actions",
        {
          action: "open_issue",
          data: {
            candidate_id: "A",
            note: "改为右手敲门",
            next_id: "C",
            previous_id: undefined,
          },
          expected_version: 2,
        },
      ),
    );
  });

  it("opens an identified source result and keeps the adoption explicit", async () => {
    vi.mocked(locate).mockResolvedValue({
      matches: [{ group_id: "g1", candidate_id: "C" }],
    });
    render(<Studio projectId="film" />);
    await screen.findByRole("heading", { name: "敲门" });
    fireEvent.click(screen.getByRole("button", { name: "从结果找镜头" }));
    fireEvent.change(screen.getByLabelText("源文件路径或文件名"), {
      target: { value: "C.mp4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查找" }));
    await screen.findByRole("heading", { name: "进屋" });
    expect(locate).toHaveBeenCalledWith("film", "C.mp4");
    expect(screen.getByRole("button", { name: "采用此版" })).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("reports an unavailable project root while retaining saved shots", async () => {
    vi.mocked(getWorkspace).mockResolvedValue({
      ...project(),
      directory_error: "原目录已移动",
    });
    render(<Studio projectId="film" />);
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      expect.stringContaining("原目录已移动"),
    );
    expect(screen.getByRole("heading", { name: "敲门" })).toBeTruthy();
  });
});
