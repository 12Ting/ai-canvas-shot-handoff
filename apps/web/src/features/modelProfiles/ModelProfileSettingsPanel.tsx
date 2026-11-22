import { useEffect, useRef, useState } from "react";

import {
  applyConfigChangeSet,
  approveConfigChangeSet,
  getActiveControlConfig,
  listModelProfileRevisions,
  listModelProfiles,
  proposeModelProfileConfigChangeSet,
  type ConfigChangeSetResponse,
  type ControlConfigRevisionResponse,
  type ModelProfileRevisionInput,
  type ModelProfileRevisionResponse,
} from "../../api/client";

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, stable(item)]));
  }
  return value;
}

async function sha256(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(stable(value)));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "模型配置操作失败。";
}

const defaultRevision: ModelProfileRevisionInput = {
  role: "CANVAS_AGENT",
  model_id: "loopback-canvas-agent",
  model_version: null,
  endpoint_url: "http://127.0.0.1:8011/v1/chat/completions",
  locality: "LOCAL_LOOPBACK",
  data_residency: "LOCAL_DEVICE",
  allowed_content_categories: ["USER_INSTRUCTION", "SELECTED_NODE_TEXT", "SELECTED_TEXT"],
  retention_policy: "NO_RETENTION_DECLARED",
  retention_note: "local loopback witness",
  tool_permissions: ["GRAPH_CHANGESET_DRAFT", "RUN_REQUEST_DRAFT"],
  cost_mode: "HARD",
  currency: "CNY",
  cost_bound_minor: 0,
  max_output_tokens: 2048,
  timeout_seconds: 15,
  credential_kind: "NONE",
  fallback_model_profile_revision_id: null,
};

export function ModelProfileSettingsPanel({ projectId }: { projectId: string }) {
  const [config, setConfig] = useState<ControlConfigRevisionResponse | null>(null);
  const [revision, setRevision] = useState<ModelProfileRevisionInput>(defaultRevision);
  const [active, setActive] = useState<ModelProfileRevisionResponse | null>(null);
  const [pending, setPending] = useState<ConfigChangeSetResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);

  const load = async () => {
    const [nextConfig, profiles] = await Promise.all([getActiveControlConfig(), listModelProfiles()]);
    setConfig(nextConfig);
    const owner = profiles.find((profile) => profile.active_revision_id === nextConfig.active_canvas_agent_profile_revision_id);
    if (owner) {
      const revisions = await listModelProfileRevisions(owner.id);
      setActive(revisions.find((item) => item.id === owner.active_revision_id) ?? null);
    } else setActive(null);
  };

  useEffect(() => { void load().catch((error) => setMessage(errorMessage(error))); }, []);

  const requestReview = async () => {
    if (!config || busy) return;
    setBusy(true); setMessage("");
    try {
      const operation = {
        provider_id: crypto.randomUUID(),
        provider: { provider_key: "loopback", display_name: "C3 Loopback", adapter_kind: "OPENAI_COMPATIBLE_CHAT_JSON_V1" as const },
        model_profile_id: crypto.randomUUID(),
        profile_key: "canvas-agent-default",
        model_profile_revision_id: crypto.randomUUID(),
        revision,
        declaration_sha256: await sha256(revision),
      };
      setPending(await proposeModelProfileConfigChangeSet(projectId, config.id, operation));
    } catch (error) { setMessage(errorMessage(error)); } finally { setBusy(false); }
  };

  const decide = async (decision: "APPROVED" | "REJECTED") => {
    if (!pending || busy) return;
    setBusy(true); setMessage("");
    try {
      const decided = await approveConfigChangeSet(pending.id, pending.changeset_sha256, decision);
      if (decision === "APPROVED") {
        await applyConfigChangeSet(decided.id, decided.changeset_sha256);
        await load();
        setMessage("模型配置版本已通过受信 ConfigChangeSet 激活。无密钥被保存或返回。");
      } else setMessage("已拒绝模型配置变更；活动版本未改变。");
      setPending(null);
      queueMicrotask(() => triggerRef.current?.focus());
    } catch (error) { setMessage(errorMessage(error)); } finally { setBusy(false); }
  };

  return (
    <section aria-label="模型配置" className="c3-profile-panel">
      <p className="f1-control-panel__eyebrow">C3 MODEL PROFILE</p>
      <p>活动版本：{config?.active_canvas_agent_profile_revision_id?.slice(0, 12) ?? "未配置"}</p>
      {active ? <p>凭据可用：{active.credential_available ? "是（仅运行时）" : "否"}</p> : null}
      <div className="c3-profile-panel__grid">
        <label>模型 ID<input value={revision.model_id} onChange={(event) => setRevision({ ...revision, model_id: event.target.value })} /></label>
        <label>端点<input value={revision.endpoint_url} onChange={(event) => setRevision({ ...revision, endpoint_url: event.target.value })} /></label>
        <label>位置<select value={revision.locality} onChange={(event) => setRevision({ ...revision, locality: event.target.value as ModelProfileRevisionInput["locality"] })}><option value="LOCAL_LOOPBACK">LOCAL_LOOPBACK</option><option value="REMOTE_HTTPS">REMOTE_HTTPS</option></select></label>
        <label>数据驻留<select value={revision.data_residency} onChange={(event) => setRevision({ ...revision, data_residency: event.target.value as ModelProfileRevisionInput["data_residency"] })}><option>LOCAL_DEVICE</option><option>CN</option><option>US</option><option>EU</option><option>UNKNOWN</option></select></label>
        <label>保留策略<select value={revision.retention_policy} onChange={(event) => setRevision({ ...revision, retention_policy: event.target.value as ModelProfileRevisionInput["retention_policy"] })}><option>NO_RETENTION_DECLARED</option><option>PROVIDER_DECLARED</option><option>UNKNOWN</option></select></label>
        <label>成本上限（分）<input min={0} type="number" value={revision.cost_bound_minor ?? 0} onChange={(event) => setRevision({ ...revision, cost_bound_minor: Number(event.target.value) })} /></label>
      </div>
      <p>上下文：{revision.allowed_content_categories.join(" · ")}</p>
      <p>工具：{revision.tool_permissions.join(" · ")}</p>
      <button disabled={!config || busy || pending !== null} onClick={() => void requestReview()} ref={triggerRef} type="button">审阅模型配置</button>
      {message ? <p aria-live="polite">{message}</p> : null}
      {pending ? <section aria-labelledby="c3-profile-review-title" aria-modal="true" className="f1-review-dialog" role="dialog" tabIndex={-1}>
        <h2 id="c3-profile-review-title">模型配置 ConfigChangeSet 审阅</h2>
        <p>端点：{revision.endpoint_url}</p><p>位置/驻留：{revision.locality} / {revision.data_residency}</p>
        <p>保留：{revision.retention_policy}；成本：{revision.cost_mode} {revision.currency} {revision.cost_bound_minor}</p>
        <p>凭据声明：{revision.credential_kind}。此界面没有密钥输入，密钥不会进入配置、事件或响应。</p>
        <div className="f1-review-dialog__actions"><button disabled={busy} onClick={() => void decide("REJECTED")}>拒绝</button><button disabled={busy} onClick={() => void decide("APPROVED")}>确认激活</button></div>
      </section> : null}
    </section>
  );
}
