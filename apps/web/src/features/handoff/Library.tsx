import { useState } from "react";
import {
  allCandidates,
  candidateLabel,
  kinds,
  roles,
  type Action,
  type AssetKind,
  type FileLocation,
  type Group,
  type Shot,
  type Sound,
  type Workspace,
} from "./client";
import { Media } from "./Media";

type Props = {
  state: Workspace;
  group?: Group;
  shot?: Shot;
  action: Action;
  open: (where: FileLocation) => void;
  copy: (where: FileLocation) => void;
  visit: (groupId: string, candidateId?: string) => void;
};
export function Library({
  state,
  group,
  shot,
  action,
  open,
  copy,
  visit,
}: Props) {
  const [kind, setKind] = useState<AssetKind>("character");
  const [name, setName] = useState("");
  const [selected, setSelected] = useState("");
  const [chosen, setChosen] = useState("");
  const [role, setRole] = useState<Sound["role"]>("temp");
  const assets = state.assets.filter((x) => x.kind === kind);
  const asset = assets.find((x) => x.id === selected) ?? assets[0];
  const file =
    asset?.versions.find((x) => x.id === chosen) ?? asset?.versions.at(-1);
  const usages = file
    ? state.groups.flatMap((g) => {
        const current = g.bindings.some((x) => x.version_id === file.id);
        const ids = g.conditions
          .filter((c) => c.bindings.some((b) => b.version_id === file.id))
          .map((c) => c.id);
        if (!ids.length && !current) return [];
        const linked = g.candidates.find(
          (c) => c.condition_id && ids.includes(c.condition_id),
        );
        return [{ g, linked, current }];
      })
    : [];
  return (
    <>
      <div className="hf-tabs">
        {(Object.entries(kinds) as [AssetKind, string][]).map(
          ([key, label]) => (
            <button
              type="button"
              aria-pressed={key === kind}
              key={key}
              onClick={() => {
                setKind(key);
                setSelected("");
                setChosen("");
              }}
            >
              {label}
            </button>
          ),
        )}
      </div>
      <form
        className="hf-inline"
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const updated = await action("add_asset", {
              kind,
              name: name.trim(),
            });
            setSelected(
              updated.assets.find(
                (x) => x.kind === kind && x.name === name.trim(),
              )?.id ?? "",
            );
            setName("");
          } catch {
            /* Shown by workspace. */
          }
        }}
      >
        <label>
          新{kinds[kind]}名称
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
          />
        </label>
        <button disabled={!name.trim()}>新建素材目录</button>
      </form>
      {asset ? (
        <>
          <div className="hf-inline">
            <label>
              素材
              <select
                value={asset.id}
                onChange={(e) => {
                  setSelected(e.target.value);
                  setChosen("");
                }}
              >
                {assets.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <button onClick={() => open({ asset_id: asset.id })}>
              打开素材目录
            </button>
          </div>
          {file ? (
            <>
              <label>
                版本
                <select
                  value={file.id}
                  onChange={(e) => setChosen(e.target.value)}
                >
                  {asset.versions.map((f, i) => (
                    <option key={f.id} value={f.id}>
                      {f.version_label ?? "V" + (f.version ?? i + 1)} · {f.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="hf-mini-preview">
                <Media project={state.project_id} file={file} key={file.id} />
              </div>
              <div className="hf-inline">
                <button onClick={() => copy({ file_id: file.id })}>
                  复制文件路径
                </button>
                {kind === "sound" ? (
                  <>
                    <select
                      aria-label="声音用途"
                      value={role}
                      onChange={(e) => setRole(e.target.value as Sound["role"])}
                    >
                      {Object.entries(roles).map(([v, t]) => (
                        <option value={v} key={v}>
                          {t}
                        </option>
                      ))}
                    </select>
                    <button
                      disabled={!shot}
                      onClick={() => {
                        void action("set_sound", {
                          shot_id: shot?.id,
                          asset_id: asset.id,
                          version_id: file.id,
                          role,
                        }).catch(() => {});
                      }}
                    >
                      关联当前镜头
                    </button>
                  </>
                ) : (
                  <button
                    disabled={!group}
                    onClick={() => {
                      const attached = group?.bindings.some(
                        (b) =>
                          b.asset_id === asset.id && b.version_id === file.id,
                      );
                      void action("bind_asset", {
                        group_id: group?.id,
                        asset_id: asset.id,
                        version_id: attached ? null : file.id,
                      }).catch(() => {});
                    }}
                  >
                    {group?.bindings.some(
                      (b) =>
                        b.asset_id === asset.id && b.version_id === file.id,
                    )
                      ? "解除当前组引用"
                      : "引用到当前组"}
                  </button>
                )}
              </div>
              <h3>此版本的引用</h3>
              {usages.map(({ g, current, linked }) => {
                const s = state.shots.find((x) => x.id === g.shot_id);
                return (
                  <button
                    key={g.id}
                    className="hf-result"
                    onClick={() => visit(g.id, linked?.id)}
                  >
                    {state.episodes.find((x) => x.id === s?.episode_id)?.title}{" "}
                    / {s?.title} / {g.title} ·{" "}
                    {current ? "当前条件" : "历史条件"}
                  </button>
                );
              })}
              {state.shots
                .filter((s) => s.sounds.some((x) => x.version_id === file.id))
                .map((s) => (
                  <p key={s.id}>
                    {state.episodes.find((e) => e.id === s.episode_id)?.title} /{" "}
                    {s.title} · 当前声音
                  </p>
                ))}
              {!usages.length &&
                !state.shots.some((s) =>
                  s.sounds.some((x) => x.version_id === file.id),
                ) && <p className="hf-hint">当前没有关联这版素材的镜头。</p>}
            </>
          ) : (
            <p className="hf-hint">打开素材目录，放入原文件后同步目录。</p>
          )}
        </>
      ) : (
        <p className="hf-hint">
          先为这项素材命名，再将原文件放入目录中的 V1 文件夹。
        </p>
      )}
    </>
  );
}

export function Dialogue({
  state,
  shot,
  action,
  showLibrary,
}: {
  state: Workspace;
  shot: Shot;
  action: Action;
  showLibrary: () => void;
}) {
  const [draft, setDraft] = useState(shot.dialogue);
  const [source, setSource] = useState("");
  const other = state.shots.filter((s) => s.id !== shot.id && s.dialogue);
  return (
    <>
      <label>
        本镜台词
        <textarea
          rows={4}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      </label>
      <button
        disabled={draft === shot.dialogue}
        onClick={() => {
          void action("set_dialogue", { shot_id: shot.id, text: draft }).catch(
            () => {},
          );
        }}
      >
        保存台词版本
      </button>
      {other.length > 0 && (
        <div className="hf-inline">
          <label>
            沿用另一镜台词
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">选择镜头</option>
              {other.map((s) => (
                <option key={s.id} value={s.id}>
                  {state.episodes.find((e) => e.id === s.episode_id)?.title} /{" "}
                  {s.title}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={!source}
            onClick={() => {
              void action("link_dialogue", {
                shot_id: shot.id,
                source_shot_id: source,
              })
                .then(() =>
                  setDraft(other.find((s) => s.id === source)!.dialogue),
                )
                .catch(() => {});
            }}
          >
            沿用此版台词
          </button>
        </div>
      )}
      <h3>镜头声音</h3>
      {shot.sounds.map((sound) => {
        const a = state.assets.find((x) => x.id === sound.asset_id);
        const f = a?.versions.find((v) => v.id === sound.version_id);
        return (
          <div className="hf-inline" key={sound.role + sound.asset_id}>
            <span>
              {roles[sound.role]} · {f?.name ?? "原文件未找到"}
            </span>
            {f && <Media project={state.project_id} file={f} key={f.id} />}
            <button
              onClick={() => {
                void action("set_sound", {
                  shot_id: shot.id,
                  asset_id: sound.asset_id,
                  version_id: null,
                  role: sound.role,
                }).catch(() => {});
              }}
            >
              解除关联
            </button>
          </div>
        );
      })}
      <button onClick={showLibrary}>选择声音素材</button>
      <details>
        <summary>台词与声音历史</summary>
        {(shot.dialogue_history ?? []).map((v, i) => (
          <p key={i}>
            {v.at} · {v.text || "空台词"}
          </p>
        ))}
        {(shot.sound_history ?? []).map((v, i) => (
          <p key={i}>
            {v.at} ·{" "}
            {v.sounds
              .map(
                (s) =>
                  state.assets
                    .find((a) => a.id === s.asset_id)
                    ?.versions.find((f) => f.id === s.version_id)?.name ??
                  "未找到",
              )
              .join("、") || "无声音"}
          </p>
        ))}
        {!shot.dialogue_history?.length && !shot.sound_history?.length && (
          <p>尚无历史版本。</p>
        )}
      </details>
    </>
  );
}

export function Issues({
  state,
  shot,
  candidateId: initialCandidateId,
  temporary: initialComparison,
  action,
  visit,
  exportIssue,
}: {
  state: Workspace;
  shot?: Shot;
  candidateId?: string;
  temporary: { previous_id?: string; next_id?: string };
  action: Action;
  visit: (g: string, c?: string) => void;
  exportIssue: (id: string) => void;
}) {
  // A scan can deliver another take while an editor is writing their note.
  // Both the source and the temporary comparisons belong to this open form.
  const [{ candidateId, temporary }] = useState(() => ({
    candidateId: initialCandidateId,
    temporary: initialComparison,
  }));
  const [note, setNote] = useState("");
  const [repairs, setRepairs] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<"open" | "resolved">("open");
  const issues = state.issues.filter((x) => x.status === filter);
  const candidates = allCandidates(state);
  const versionName = (id?: string | null) => {
    const entry = candidates.find((x) => x.candidate.id === id);
    return entry ? candidateLabel(state.groups, entry) : "待选";
  };
  return (
    <>
      {shot && candidateId && (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await action("open_issue", {
                candidate_id: candidateId,
                note: note.trim(),
                ...temporary,
              });
              setNote("");
              setFilter("open");
            } catch {
              /* Shown by workspace. */
            }
          }}
        >
          <label>
            给 {versionName(candidateId)} 留补镜意见
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              maxLength={4000}
            />
          </label>
          {(temporary.previous_id || temporary.next_id) && (
            <p className="hf-hint">
              当前临时对照会随这条意见保存，并在交接包中标记。
            </p>
          )}
          <button disabled={!note.trim()}>记录补镜</button>
        </form>
      )}
      <div className="hf-tabs">
        <button
          aria-pressed={filter === "open"}
          onClick={() => setFilter("open")}
        >
          待补镜
        </button>
        <button
          aria-pressed={filter === "resolved"}
          onClick={() => setFilter("resolved")}
        >
          已解决
        </button>
      </div>
      {!issues.length && <p className="hf-hint">此列表暂无补镜意见。</p>}
      {issues.map((issue) => {
        const s = state.shots.find((x) => x.id === issue.shot_id);
        const original = candidates.find(
          (x) => x.candidate.id === issue.candidate_id,
        );
        const context = issue.context;
        const frozen = original?.group.conditions.find(
          (c) => c.id === issue.condition_id,
        );
        return (
          <article className="hf-issue" key={issue.id}>
            <small>
              {state.episodes.find((x) => x.id === s?.episode_id)?.title} /{" "}
              {s?.title} / {versionName(issue.candidate_id)}
              {s?.selected_id === issue.candidate_id && " · 已采用"}
            </small>
            <p>{issue.note}</p>
            {context && (
              <details>
                <summary>提出意见时的镜头资料</summary>
                <p>
                  前镜{" "}
                  {context.previous_shot_id
                    ? versionName(context.previous_id)
                    : "本集无前镜"}
                  {context.previous_temporary ? "（临时对照）" : ""}　后镜{" "}
                  {context.next_shot_id
                    ? versionName(context.next_id)
                    : "本集无后镜"}
                  {context.next_temporary ? "（临时对照）" : ""}
                </p>
                <p>台词　{context.dialogue || "未记录"}</p>
                <p>
                  声音　
                  {(context.sounds ?? [])
                    .map(
                      (sound) =>
                        state.assets
                          .find((a) => a.id === sound.asset_id)
                          ?.versions.find((f) => f.id === sound.version_id)
                          ?.name ?? "缺少版本",
                    )
                    .join("、") || "未记录"}
                </p>
                <h3>原版登记条件</h3>
                <pre>{frozen?.prompt || "提示词未记录"}</pre>
                <pre>{frozen?.config || "生成配置未记录"}</pre>
                {!!frozen?.references.length && (
                  <p>
                    当时参考　{frozen.references.map((f) => f.name).join("、")}
                  </p>
                )}
                {!!frozen?.bindings.length && (
                  <p>
                    共享素材　
                    {frozen.bindings
                      .map((b) => {
                        const a = state.assets.find((v) => v.id === b.asset_id);
                        const f = a?.versions.find(
                          (v) => v.id === b.version_id,
                        );
                        return a && f ? a.name + " / " + f.name : "缺少版本";
                      })
                      .join("、")}
                  </p>
                )}
              </details>
            )}
            {issue.status === "resolved" && issue.repair_id && (
              <p>修复版　{versionName(issue.repair_id)}</p>
            )}
            <div className="hf-inline">
              {original && (
                <button
                  onClick={() => visit(original.group.id, issue.candidate_id)}
                >
                  查看原版
                </button>
              )}
              <button onClick={() => exportIssue(issue.id)}>导出此问题</button>
            </div>
            {issue.status === "open" && (
              <div className="hf-inline">
                <select
                  aria-label="修复候选"
                  value={repairs[issue.id] ?? ""}
                  onChange={(e) =>
                    setRepairs({ ...repairs, [issue.id]: e.target.value })
                  }
                >
                  <option value="">手动解决（不关联修复版）</option>
                  {candidates
                    .filter(
                      (x) =>
                        x.group.shot_id === issue.shot_id &&
                        x.candidate.id !== issue.candidate_id,
                    )
                    .map((x) => (
                      <option key={x.candidate.id} value={x.candidate.id}>
                        {candidateLabel(state.groups, x)}
                      </option>
                    ))}
                </select>
                <button
                  onClick={() => {
                    void action("resolve_issue", {
                      issue_id: issue.id,
                      candidate_id: repairs[issue.id] || null,
                    }).catch(() => {});
                  }}
                >
                  确认解决
                </button>
              </div>
            )}
          </article>
        );
      })}
    </>
  );
}
