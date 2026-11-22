import { useCallback, useEffect, useRef, useState } from "react";
import {
  base,
  downloadPackage,
  DirectorySelectionCancelled,
  errText,
  filePath,
  getWorkspace,
  groupLabel,
  post,
  rebindDirectory,
  studioUrl,
  type Action,
  type Candidate,
  type FileLocation,
  type Workspace,
} from "./client";
import { Dialog } from "./Dialog";
import { Dialogue, Issues, Library } from "./Library";
import { Media } from "./Media";
import { Conditions, Locate, Neighbour } from "./Review";
import "./studio.css";

type Sheet =
  | "episode"
  | "shot"
  | "group"
  | "copy"
  | "locate"
  | "conditions"
  | "library"
  | "sounds"
  | "issues"
  | "export"
  | "directory"
  | "order"
  | null;

// These commands append a new item under a stable parent, or only read the
// directory. A background media scan does not invalidate their target.
const independentActions = new Set(["add_episode","add_shot","add_group","add_asset","open_directory","scan"]);
const titles: Record<NonNullable<Sheet>, string> = {
  episode: "新建集",
  shot: "新建镜头",
  group: "新建生成组",
  copy: "复制生成组",
  locate: "从结果找镜头",
  conditions: "生成条件",
  library: "共享素材",
  sounds: "台词与声音",
  issues: "补镜清单",
  export: "导出项目选片",
  directory: "项目目录",
  order: "调整镜头顺序",
};
export function Studio({ projectId }: { projectId: string }) {
  const [state, setState] = useState<Workspace>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const locked = useRef(false);
  const [episodeId, setEpisode] = useState("");
  const [shotId, setShot] = useState("");
  const [groupId, setGroup] = useState("");
  const [candidateId, setCandidate] = useState("");
  const [comparisons, setComparisons] = useState<Record<string, string>>({});
  const [sheet, setSheet] = useState<Sheet>(null);
  const [title, setTitle] = useState("");
  const loadEpoch = useRef(0);
  const update = useCallback(
    (next: Workspace) =>
      setState((old) =>
        old && (next.version ?? 0) < (old.version ?? 0) ? old : next,
      ),
    [],
  );
  const refresh = useCallback(async () => {
    const epoch = ++loadEpoch.current;
    try {
      const next = await getWorkspace(projectId);
      if (epoch === loadEpoch.current) {
        update(next);
        setReadError("");
      }
    } catch (e) {
      if (epoch === loadEpoch.current) setReadError(errText(e));
    }
  }, [projectId, update]);
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (!document.hidden && !locked.current) void refresh();
    }, 4500);
    return () => {
      clearInterval(timer);
    };
  }, [refresh]);

  const run = async <T,>(
    operation: () => Promise<T>,
    success?: string,
  ): Promise<T> => {
    if (locked.current) throw new Error("有操作正在进行");
    locked.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    ++loadEpoch.current;
    try {
      const result = await operation();
      if (success) setNotice(success);
      return result;
    } catch (e) {
      if (!(e instanceof DirectorySelectionCancelled)) setError(errText(e));
      throw e;
    } finally {
      setBusy(false);
      locked.current = false;
    }
  };
  const action: Action = (action, data = {}) =>
    run(
      async () => {
        const next = await post<Workspace>(base(projectId) + "/actions", {
          action,
          data,
          ...(independentActions.has(action)
            ? {}
            : { expected_version: state?.version }),
        });
        update(next);
        return next;
      },
      action === "scan"
        ? "目录已同步"
        : action === "open_directory"
          ? undefined
          : "已保存",
    );
  const copy = (where: FileLocation) => {
    void run(async () => {
      await navigator.clipboard.writeText(await filePath(projectId, where));
    }, "文件路径已复制").catch(() => {});
  };
  const open = (where: FileLocation) => {
    void action("open_directory", where).catch(() => {});
  };
  const show = (s: Sheet) => {
    setTitle("");
    setError("");
    setNotice("");
    setSheet(s);
  };
  if (!state)
    return (
      <main className="hf-loading">
        <h1>镜头工作台</h1>
        {readError ? (
          <>
            <p role="alert">{readError}</p>
            <button onClick={() => void refresh()}>重新加载</button>
            <a href="?">返回项目列表</a>
          </>
        ) : (
          <p role="status">正在读取项目…</p>
        )}
      </main>
    );
  const episode =
    state.episodes.find((e) => e.id === episodeId) ?? state.episodes[0];
  const shots = state.shots
    .filter((s) => s.episode_id === episode?.id)
    .sort((a, b) => a.order - b.order);
  const shot = shots.find((x) => x.id === shotId) ?? shots[0];
  const index = shot ? shots.indexOf(shot) : -1;
  const groups = state.groups.filter((x) => x.shot_id === shot?.id);
  const group =
    groups.find((g) => g.id === groupId) ??
    groups.find((g) => g.candidates.some((c) => c.id === shot?.selected_id)) ??
    groups[0];
  const candidate =
    group?.candidates.find((c) => c.id === candidateId) ??
    group?.candidates.find((c) => c.id === shot?.selected_id) ??
    group?.candidates.at(-1);
  const legacy = shot && !candidate && shot.legacy_video_revision_id;
  const selectedCount = state.shots.filter((s) => s.selected_id).length;
  const openIssues = state.issues.filter((i) => i.status === "open");
  const needsRetake = new Set(openIssues.map((issue) => issue.candidate_id));
  const warnings = Object.entries(state.scan_warnings ?? {});
  const groupNav = (id: string) => {
    setGroup(id);
    setCandidate("");
  };
  const shotNav = (id: string) => {
    setShot(id);
    groupNav("");
  };
  const pinCurrent = () => {
    setEpisode(episode?.id ?? "");
    setShot(shot?.id ?? "");
    setGroup(group?.id ?? "");
    setCandidate(candidate?.id ?? "");
  };
  const showIssues = () => {
    pinCurrent();
    show("issues");
  };
  const visit = (gId: string, cId?: string) => {
    const g = state.groups.find((x) => x.id === gId);
    const s = state.shots.find((x) => x.id === g?.shot_id);
    if (!g || !s) return;
    setEpisode(s.episode_id);
    setShot(s.id);
    setGroup(g.id);
    setCandidate(cId ?? "");
    show(null);
  };
  const create = async () => {
    if (!title.trim()) return;
    const data: Record<string, unknown> = { title: title.trim() };
    if (sheet === "shot") data.episode_id = episode?.id;
    if (sheet === "group") data.shot_id = shot?.id;
    if (sheet === "copy") data.group_id = group?.id;
    const command =
      sheet === "episode"
        ? "add_episode"
        : sheet === "shot"
          ? "add_shot"
          : sheet === "copy"
            ? "copy_group"
            : "add_group";
    const next = await action(command, data);
    if (sheet === "episode")
      setEpisode(
        next.episodes.find((x) => !state.episodes.some((o) => o.id === x.id))
          ?.id ?? "",
      );
    if (sheet === "shot")
      shotNav(
        next.shots.find((x) => !state.shots.some((o) => o.id === x.id))?.id ??
          "",
      );
    if (sheet === "group" || sheet === "copy")
      groupNav(
        next.groups.find((x) => !state.groups.some((o) => o.id === x.id))?.id ??
          "",
      );
    show(null);
  };
  const adjacentKey = (neighborId?: string) =>
    shot?.id + "/" + (neighborId ?? "");
  const comparison = (neighborId?: string) =>
    comparisons[adjacentKey(neighborId)] ?? "";
  const changeComparison = (neighborId: string | undefined, id: string) =>
    setComparisons((values) => ({ ...values, [adjacentKey(neighborId)]: id }));
  const temporary = {
    previous_id: comparison(shots[index - 1]?.id) || undefined,
    next_id: comparison(shots[index + 1]?.id) || undefined,
  };
  const feedback = (
    <>
      {state.directory_error && (
        <p className="hf-error" role="alert">
          {state.directory_error} · 可在项目目录中重新定位
        </p>
      )}
      {readError && (
        <p className="hf-error" role="alert">
          读取目录状态失败 · {readError}
        </p>
      )}
      {error && (
        <p className="hf-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="hf-feedback" role="status">
          {notice}
        </p>
      )}
    </>
  );
  const exportZip = (issue?: string) => {
    void run(
      () => downloadPackage(projectId, state.name, issue),
      "交接包已准备，可在浏览器下载中查看",
    ).catch(() => {});
  };
  const modal = sheet && (
    <Dialog
      title={titles[sheet]}
      onClose={() => {
        if (!locked.current) show(null);
      }}
    >
      <fieldset disabled={busy} className="hf-controls">
        {["episode", "shot", "group", "copy"].includes(sheet) && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void create().catch(() => {});
            }}
          >
            <label>
              {sheet === "episode"
                ? "集名"
                : sheet === "shot"
                  ? "镜头名称"
                  : "生成组名称"}
              <input
                autoFocus
                required
                value={title}
                maxLength={100}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            {sheet === "copy" && (
              <p>沿用原组的文字和参考素材，结果目录从空开始。</p>
            )}
            <button className="hf-primary" disabled={!title.trim()}>
              {sheet === "copy" ? "复制生成组" : "创建"}
            </button>
          </form>
        )}
        {sheet === "locate" && <Locate state={state} visit={visit} />}
        {sheet === "conditions" && group && (
          <Conditions
            state={state}
            group={group}
            candidate={candidate}
            action={action}
            open={open}
            copy={copy}
          />
        )}
        {sheet === "library" && (
          <Library
            state={state}
            group={group}
            shot={shot}
            action={action}
            open={open}
            copy={copy}
            visit={visit}
          />
        )}
        {sheet === "sounds" && shot && (
          <Dialogue
            key={shot.id}
            state={state}
            shot={shot}
            action={action}
            showLibrary={() => show("library")}
          />
        )}
        {sheet === "issues" && (
          <Issues
            state={state}
            shot={shot}
            candidateId={candidate?.id}
            temporary={temporary}
            action={action}
            visit={visit}
            exportIssue={exportZip}
          />
        )}
        {sheet === "order" && (
          <ol className="hf-order">
            {shots.map((s, i) => (
              <li key={s.id}>
                <span>{s.title}</span>
                {[-1, 1].map((step) => (
                  <button
                    key={step}
                    disabled={!shots[i + step]}
                    aria-label={s.title + (step < 0 ? "上移" : "下移")}
                    onClick={() => {
                      const ids = shots.map((x) => x.id);
                      [ids[i], ids[i + step]] = [ids[i + step], ids[i]];
                      void action("reorder_shots", {
                        episode_id: episode.id,
                        shot_ids: ids,
                      }).catch(() => {});
                    }}
                  >
                    {step < 0 ? "↑" : "↓"}
                  </button>
                ))}
              </li>
            ))}
          </ol>
        )}
        {sheet === "directory" && (
          <>
            <p>
              将生成结果保存到对应组的「生成结果」目录。工作台会在后台登记新文件。
            </p>
            <div className="hf-inline">
              <button onClick={() => open({})}>打开项目目录</button>
              <button onClick={() => copy({})}>复制目录路径</button>
              <button
                onClick={() => {
                  void run(
                    async () => update(await rebindDirectory(projectId)),
                    "项目目录已重新定位",
                  ).catch(() => {});
                }}
              >
                重新定位项目
              </button>
            </div>
            {warnings.length > 0 && (
              <>
                <h3>待核对文件</h3>
                {warnings.map(([p, r]) => (
                  <p key={p}>
                    {p}
                    <br />
                    {String(r)}
                  </p>
                ))}
              </>
            )}
          </>
        )}
        {sheet === "export" && (
          <>
            <p>按镜序交付采用版和选片理由，清单标出待选镜头及具体版本的待补意见。</p>
            <p>
              {selectedCount} 镜已采用　{state.shots.length - selectedCount}{" "}
              镜待选　{openIssues.length} 条待补镜
            </p>
            <button className="hf-primary" onClick={() => exportZip()}>
              下载选片交接包
            </button>
            {openIssues.length > 0 && (
              <button onClick={showIssues}>
                选择补镜问题单独导出
              </button>
            )}
          </>
        )}
      </fieldset>
      {feedback}
    </Dialog>
  );

  return (
    <main className="hf-studio">
      <header className="hf-bar">
        <div className="hf-brand">
          <span className="hf-mark" aria-hidden="true" />
          <strong>镜头工作台</strong>
          <a href="?" title="返回项目列表">
            项目列表
          </a>
        </div>
        <nav aria-label="项目工具">
          <button onClick={() => show("locate")}>从结果找镜头</button>
          <button onClick={() => show("library")}>共享素材</button>
          <button onClick={showIssues}>
            补镜清单{openIssues.length ? " · " + openIssues.length : ""}
          </button>
          <a href={studioUrl(projectId, "canvas")}>画布 ↗</a>
          <button className="hf-primary" onClick={() => show("export")}>
            导出交接包
          </button>
        </nav>
      </header>
      <div className="hf-layout">
        <aside className="hf-rail" aria-label="集与镜头">
          <h1>{state.name}</h1>
          <div className="hf-collection">
            <select
              aria-label="选择集"
              value={episode?.id ?? ""}
              disabled={!episode}
              onChange={(e) => {
                setEpisode(e.target.value);
                shotNav("");
              }}
            >
              {!episode && <option value="">尚无集</option>}
              {state.episodes.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.title}
                </option>
              ))}
            </select>
            <button
              aria-label="新建集"
              title="新建集"
              onClick={() => show("episode")}
            >
              ＋
            </button>
          </div>
          <div className="hf-section-label">
            <h2>
              镜头 <small>{shots.length}</small>
            </h2>
            {shots.length > 1 && (
              <button onClick={() => show("order")}>排序</button>
            )}
          </div>
          <ol className="hf-shots">
            {shots.map((s, i) => (
              <li key={s.id}>
                <button
                  aria-current={s.id === shot?.id ? "true" : undefined}
                  onClick={() => shotNav(s.id)}
                >
                  <span className="hf-num">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span>{s.title}</span>
                  <span
                    className={"hf-dot" + (s.selected_id ? " chosen" : "")}
                    title={s.selected_id ? "已采用" : "待选"}
                    aria-label={s.selected_id ? "已采用" : "待选"}
                  />
                </button>
              </li>
            ))}
          </ol>
          {episode && (
            <button className="hf-add" onClick={() => show("shot")}>
              ＋ 新建镜头
            </button>
          )}
          <footer>
            <button
              disabled={busy}
              onClick={() => {
                void action("scan").catch(() => {});
              }}
            >
              ↻ 同步目录
            </button>
            <button onClick={() => show("directory")}>
              项目目录{warnings.length ? " · " + warnings.length : ""}
            </button>
          </footer>
        </aside>
        <div className="hf-work">
          <div className="hf-status" aria-live="polite">
            {!sheet && feedback}
            {busy && <span>正在处理…</span>}
          </div>
          {!shot ? (
            <section className="hf-start">
              <h2>{episode ? "为这一集添加镜头" : "从第一集开始"}</h2>
              <p>每个镜头可以放多组生成结果，保留当时的参考和提示词。</p>
              <button
                className="hf-primary"
                onClick={() => show(episode ? "shot" : "episode")}
              >
                {episode ? "新建第一个镜头" : "新建第一集"}
              </button>
            </section>
          ) : (
            <>
              <header className="hf-shothead">
                <div>
                  <small>
                    {episode.title}　/　镜头{" "}
                    {String(index + 1).padStart(2, "0")}
                  </small>
                  <h2>{shot.title}</h2>
                </div>
                <button onClick={() => show("sounds")}>
                  台词与声音
                  {shot.sounds.length ? " · " + shot.sounds.length : ""}
                </button>
              </header>
              <div className="hf-viewer">
                <Neighbour
                  key={"prev" + (shots[index - 1]?.id ?? "")}
                  state={state}
                  shot={shots[index - 1]}
                  side="previous"
                  temp={comparison(shots[index - 1]?.id)}
                  setTemp={(id) => changeComparison(shots[index - 1]?.id, id)}
                />
                <section className="hf-current">
                  <div className="hf-cell-label">
                    <span>本镜候选</span>
                    <small>
                      {candidate?.id === shot.selected_id && candidate
                        ? "采用版"
                        : legacy
                          ? "已有绑定"
                          : shot.selected_id
                            ? "查看备选"
                            : "尚未采用"}
                    </small>
                  </div>
                  <div className="hf-screen">
                    {candidate ? (
                      <Media
                        key={candidate.id}
                        project={projectId}
                        file={candidate}
                      />
                    ) : legacy ? (
                      <Media
                        key={legacy}
                        project={projectId}
                        legacyPath={
                          "/api/v1/artifact-revisions/" +
                          encodeURIComponent(legacy) +
                          "/content"
                        }
                        file={{
                          id: legacy,
                          sha256: "",
                          name: "已有绑定",
                          path: "",
                          mime: "video/mp4",
                          kind: "video",
                        }}
                      />
                    ) : (
                      <span>{group ? "等待生成结果" : "尚无生成组"}</span>
                    )}
                  </div>
                  <strong>
                    {candidate?.name ?? (legacy ? "已有绑定" : "本镜尚无候选")}
                  </strong>
                </section>
                <Neighbour
                  key={"next" + (shots[index + 1]?.id ?? "")}
                  state={state}
                  shot={shots[index + 1]}
                  side="next"
                  temp={comparison(shots[index + 1]?.id)}
                  setTemp={(id) => changeComparison(shots[index + 1]?.id, id)}
                />
              </div>
              <section className="hf-grouparea">
                <div className="hf-section-label">
                  <h3>生成组</h3>
                  <div>
                    <button onClick={() => show("group")}>＋ 新建组</button>
                    {group && (
                      <button onClick={() => show("copy")}>复制组</button>
                    )}
                  </div>
                </div>
                {groups.length > 0 ? (
                  <div className="hf-tabs hf-groups">
                    {groups.map((g) => (
                      <button
                        aria-pressed={group?.id === g.id}
                        key={g.id}
                        onClick={() => groupNav(g.id)}
                      >
                        {groupLabel(groups, g)}
                        <small>{g.candidates.length}</small>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p>新建生成组，把使用相同参考和提示词的结果放在一起。</p>
                )}
                {group && (
                  <>
                    <div className="hf-section-label">
                      <h3>
                        候选 <small>{group.candidates.length}</small>
                      </h3>
                      <div>
                        <button onClick={() => show("conditions")}>
                          生成条件
                        </button>
                        <button
                          disabled={busy}
                          onClick={() =>
                            open({ group_id: group.id, location: "results" })
                          }
                        >
                          打开结果目录 ↗
                        </button>
                      </div>
                    </div>
                    {!group.candidates.length && (
                      <p className="hf-hint">
                        将生成的视频或图片下载到结果目录，工作台会自动收录。
                      </p>
                    )}
                    <ul className="hf-candidates">
                      {group.candidates.map((c, i) => (
                        <li key={c.id}>
                          <button
                            aria-pressed={c.id === candidate?.id}
                            onClick={() => setCandidate(c.id)}
                          >
                            <small>V{c.version ?? i + 1}</small>
                            <span>{c.name}</span>
                            <small>
                              {c.id === shot.selected_id
                                ? "已采用"
                                : c.available === false
                                  ? "已保留历史版本"
                                  : c.condition_id
                                    ? "已记条件"
                                    : "条件待核对"}
                              {needsRetake.has(c.id) && " · 待补镜"}
                            </small>
                          </button>
                        </li>
                      ))}
                    </ul>
                    {candidate && (
                      <CandidateActions
                        key={candidate.id}
                        shotId={shot.id}
                        candidate={candidate}
                        adopted={shot.selected_id === candidate.id}
                        busy={busy}
                        action={action}
                        pin={pinCurrent}
                        onIssue={showIssues}
                        onCopy={() => copy({ file_id: candidate.id })}
                      />
                    )}
                  </>
                )}
              </section>
            </>
          )}
        </div>
      </div>
      {modal}
    </main>
  );
}

function CandidateActions({
  shotId, candidate, adopted, busy, action, pin, onIssue, onCopy,
}: {
  shotId: string;
  candidate: Candidate;
  adopted: boolean;
  busy: boolean;
  action: Action;
  pin: () => void;
  onIssue: () => void;
  onCopy: () => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const reason = candidate.selection_reason ?? "";
  const target = { shot_id: shotId, candidate_id: candidate.id };
  const save = (command: string, data: Record<string, unknown>) => {
    void action(command, data)
      .then(() => setDraft(null))
      .catch(() => {});
  };
  const toggleAdoption = () => {
    pin();
    if (adopted) {
      save("select_candidate", { shot_id: shotId, candidate_id: null });
    } else {
      save("select_candidate", draft === null ? target : { ...target, reason: draft.trim() });
    }
  };

  return (
    <>
      <div className="hf-actions">
        <button
          className={adopted ? undefined : "hf-primary"}
          disabled={busy}
          onClick={toggleAdoption}
        >
          {adopted ? "取消采用" : "采用此版"}
        </button>
        <button
          aria-expanded={draft !== null}
          disabled={busy}
          onClick={() => {
            pin();
            setDraft(draft === null ? reason : null);
          }}
        >
          {draft !== null ? "取消编辑" : reason ? "编辑采用理由" : "写采用理由"}
        </button>
        <button onClick={onIssue}>记补镜意见</button>
        <button disabled={busy} onClick={onCopy}>复制结果路径</button>
      </div>
      {draft !== null ? (
        <form
          className="hf-reason"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy) return;
            if (adopted) save("save_selection_reason", { ...target, reason: draft.trim() });
            else toggleAdoption();
          }}
        >
          <label>
            采用理由
            <input
              autoFocus
              maxLength={500}
              value={draft}
              disabled={busy}
              placeholder="可选，例如：转身的停顿刚好接上后镜。"
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
          {adopted && (
            <button disabled={busy || draft.trim() === reason}>保存理由</button>
          )}
        </form>
      ) : reason && <p className="hf-hint hf-saved-reason">采用理由　{reason}</p>}
    </>
  );
}
