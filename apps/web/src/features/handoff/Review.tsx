import { useEffect, useRef, useState } from "react";
import {
  allCandidates,
  candidateLabel,
  errText,
  locate,
  type Action,
  type Candidate,
  type FileLocation,
  type Group,
  type Located,
  type MediaFile,
  type Shot,
  type Workspace,
} from "./client";
import { Media } from "./Media";

async function resultDigest(file: File, signal: AbortSignal): Promise<string> {
  if (file.size <= 16 * 1024 * 1024) {
    const digest = await crypto.subtle.digest(
      "SHA-256",
      await file.arrayBuffer(),
    );
    return Array.from(new Uint8Array(digest), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join("");
  }
  return new Promise((resolve, reject) => {
    const worker = new Worker(
      new URL("./fileDigest.worker.ts", import.meta.url),
      { type: "module" },
    );
    const cleanup = () => {
      worker.terminate();
      signal.removeEventListener("abort", cancel);
    };
    const cancel = () => {
      cleanup();
      reject(new Error("查找已取消"));
    };
    signal.addEventListener("abort", cancel, { once: true });
    worker.onerror = () => {
      cleanup();
      reject(new Error("未能读取文件，可粘贴源路径查找"));
    };
    worker.onmessage = (event) => {
      cleanup();
      if (event.data.error) reject(new Error(event.data.error));
      else resolve(event.data.sha256);
    };
    worker.postMessage(file);
  });
}

export function Locate({
  state,
  visit,
}: {
  state: Workspace;
  visit: (groupId: string, candidateId?: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Located[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const pending = useRef<AbortController>(undefined);
  useEffect(
    () => () => {
      ++generation.current;
      pending.current?.abort();
    },
    [],
  );
  async function search(input: string | File) {
    const turn = ++generation.current;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError("");
    setMatches(null);
    try {
      let value: string;
      if (typeof input === "string") value = input.trim().replace(/^"|"$/g, "");
      else {
        value = await resultDigest(input, controller.signal);
        if (turn === generation.current) setQuery(input.name);
      }
      if (controller.signal.aborted) return;
      const result = await locate(state.project_id, value);
      if (turn !== generation.current) return;
      const entries = allCandidates(state);
      const found = result.matches.flatMap((m) =>
        entries.filter(
          (x) => x.group.id === m.group_id && x.candidate.id === m.candidate_id,
        ),
      );
      setMatches(found);
      if (found.length === 1) visit(found[0].group.id, found[0].candidate.id);
    } catch (e) {
      if (turn === generation.current) setError(errText(e));
    } finally {
      if (turn === generation.current) setBusy(false);
    }
  }
  return (
    <>
      <form
        className="hf-inline"
        onSubmit={(e) => {
          e.preventDefault();
          void search(query);
        }}
      >
        <label>
          源文件路径或文件名
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="粘贴剪辑软件中显示的源文件路径"
          />
        </label>
        <button disabled={!query.trim() || busy}>查找</button>
      </form>
      <label
        className="hf-drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files[0];
          if (file) void search(file);
        }}
      >
        拖入源文件，或选择文件按内容匹配
        <input
          type="file"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void search(file);
          }}
        />
      </label>
      {busy && <p role="status">正在匹配本项目索引…</p>}
      {error && <p role="alert">{error}</p>}
      {matches && !matches.length && (
        <p>本项目没有匹配项。可先同步目录，或按集和镜头手动选择。</p>
      )}
      {matches && matches.length > 1 && (
        <>
          <p>找到多个归属，请选择镜头。</p>
          {matches.map(({ group, candidate }) => {
            const shot = state.shots.find((s) => s.id === group.shot_id);
            return (
              <button
                key={candidate.id}
                className="hf-result"
                onClick={() => visit(group.id, candidate.id)}
              >
                {state.episodes.find((e) => e.id === shot?.episode_id)?.title} /{" "}
                {shot?.title} / {candidateLabel(state.groups, { group, candidate })}
              </button>
            );
          })}
        </>
      )}
    </>
  );
}

export function Neighbour({
  state,
  shot,
  side,
  temp,
  setTemp,
}: {
  state: Workspace;
  shot?: Shot;
  side: "previous" | "next";
  temp: string;
  setTemp: (id: string) => void;
}) {
  const entries = shot ? allCandidates(state, shot.id) : [];
  const chosen = entries.find(
    (e) => e.candidate.id === (temp || shot?.selected_id),
  )?.candidate;
  const label = side === "previous" ? "前镜" : "后镜";
  const legacy =
    shot && !chosen && shot.legacy_video_revision_id
      ? shot.legacy_video_revision_id
      : undefined;
  const source: MediaFile | undefined =
    chosen ??
    (legacy
      ? {
          id: legacy,
          name: "已有绑定",
          kind: "video",
          path: "",
          sha256: "",
          mime: "video/mp4",
        }
      : undefined);
  return (
    <section className="hf-neighbor" aria-label={label} data-has-preview={source ? true : undefined}>
      <div className="hf-cell-label">
        <span>{label}</span>
        <small>{side === "previous" ? "源片末帧" : "源片首帧"}</small>
      </div>
      <div className="hf-screen">
        {source ? (
          <Media
            key={source.id}
            project={state.project_id}
            file={source}
            legacyPath={
              legacy
                ? "/api/v1/artifact-revisions/" +
                  encodeURIComponent(legacy) +
                  "/content"
                : undefined
            }
            edge={side === "previous" ? "last" : "first"}
            compact
          />
        ) : (
          <span>{shot ? "待选" : "本集无" + label}</span>
        )}
      </div>
      {shot && (
        <>
          <strong>{shot.title}</strong>
          {entries.length > 0 && (
            <select
              aria-label={label + "临时对照"}
              value={temp}
              onChange={(e) => setTemp(e.target.value)}
            >
              <option value="">
                {shot.selected_id
                  ? "采用版"
                  : legacy
                    ? "已有绑定"
                    : "选择临时对照"}
              </option>
              {entries.map((entry) => (
                <option key={entry.candidate.id} value={entry.candidate.id}>
                  {candidateLabel(state.groups, entry)}
                </option>
              ))}
            </select>
          )}
        </>
      )}
    </section>
  );
}

export function Conditions({
  state,
  group,
  candidate,
  action,
  copy,
  open,
}: {
  state: Workspace;
  group: Group;
  candidate?: Candidate;
  action: Action;
  open: (where: FileLocation) => void;
  copy: (where: FileLocation) => void;
}) {
  const [chosen, setChosen] = useState("");
  const [checked, setChecked] = useState<string[]>([]);
  const frozen = candidate
    ? group.conditions.find((x) => x.id === candidate.condition_id)
    : group.conditions.at(-1);
  const condition = group.conditions.find((c) => c.id === chosen) ?? frozen;
  const refs = condition?.references ?? [];
  const bindings = condition?.bindings ?? [];
  const add = (id: string, on: boolean) =>
    setChecked((list) => (on ? [...list, id] : list.filter((x) => x !== id)));
  return (
    <>
      <label>
        条件版本
        <select
          value={condition?.id ?? ""}
          onChange={(e) => setChosen(e.target.value)}
        >
          <option value="">
            {frozen ? "候选登记时的条件" : "未记录 / 待核对"}
          </option>
          {group.conditions.map((c, i) => (
            <option key={c.id} value={c.id}>
              V{i + 1}
              {c.id === candidate?.condition_id ? " · 此候选登记版" : ""}
            </option>
          ))}
        </select>
      </label>
      <h3>提示词</h3>
      <pre>{condition?.prompt || "未记录"}</pre>
      <h3>生成配置</h3>
      <pre>{condition?.config || "未记录"}</pre>
      <h3>参考素材</h3>
      {!refs.length && !bindings.length && <p className="hf-hint">未记录</p>}
      {refs.map((f) => (
        <div key={f.id} className="hf-fileline">
          <span>{f.name}</span>
          <button onClick={() => copy({ file_id: f.id })}>复制路径</button>
          <button onClick={() => open({ file_id: f.id })}>所在目录</button>
        </div>
      ))}
      {bindings.map((b) => {
        const asset = state.assets.find((a) => a.id === b.asset_id);
        const f = asset?.versions.find((v) => v.id === b.version_id);
        return (
          <div className="hf-fileline" key={b.asset_id}>
            <span>
              {asset?.name} / {f?.name ?? "缺少版本"}
            </span>
            {f && (
              <button onClick={() => copy({ file_id: f.id })}>复制路径</button>
            )}
          </div>
        );
      })}
      <details>
        <summary>编辑组资料与补记</summary>
        <p>
          在组目录编辑「提示词.txt」与「生成配置.txt」，将原文件放进「参考素材」，再同步目录。
        </p>
        <div className="hf-inline">
          <button
            onClick={() => open({ group_id: group.id, location: "group" })}
          >
            打开组目录
          </button>
          <button
            onClick={() => open({ group_id: group.id, location: "refs" })}
          >
            参考素材目录
          </button>
          <button
            onClick={() => copy({ group_id: group.id, location: "results" })}
          >
            复制结果目录路径
          </button>
        </div>
        {group.candidates.length > 0 && (
          <>
            <p>核对生成记录后，勾选使用上方条件版本的候选。</p>
            {group.candidates.map((c) => (
              <label className="hf-check" key={c.id}>
                <input
                  type="checkbox"
                  checked={checked.includes(c.id)}
                  onChange={(e) => add(c.id, e.target.checked)}
                />
                {c.name} · {c.condition_id ? "已有关联" : "待核对"}
              </label>
            ))}
            <button
              disabled={!checked.length || !condition}
              onClick={() => {
                void action("set_conditions", {
                  group_id: group.id,
                  condition_id: condition?.id,
                  candidate_ids: checked,
                })
                  .then(() => setChecked([]))
                  .catch(() => {});
              }}
            >
              给所选候选补记此版条件
            </button>
          </>
        )}
      </details>
    </>
  );
}
