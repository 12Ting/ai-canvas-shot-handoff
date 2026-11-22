import { useEffect, useState } from "react";
import {
  approvePromptBatch, decidePromptBatchItem, executePromptBatchItem,
  finalizePromptBatch, getPromptBatch, preflightPromptBatch, requestPromptBatch,
  approvePromptProfileExtraction, executePromptProfileExtraction,
  materializePromptProfileExtraction, requestPromptProfileExtraction,
  acceptAllSucceededPromptBatch, approvePromptBatchRetry, retryFailedPromptBatch,
  type CreatePromptBatchRequest, type GraphChangeSetResponse,
  type PromptBatchPreflightResponse, type PromptBatchResponse,
  type PromptProfileExtractionResponse,
} from "../../api/client";
import type { CanvasNode, CanvasShot } from "../canvas/canvasTypes";

type BranchContext = Readonly<{ branchId: string; branchHeadRevisionId: string;
  branchHeadVersion: number; selectionVersion: number }>;

type AgentRunRequest = PromptProfileExtractionResponse["run_request"];

function prettyValue(value: unknown): string {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value) : JSON.stringify(value, null, 2);
}

function hasApprovalEvidence(runRequest: AgentRunRequest): boolean {
  const preview = runRequest.egress_plan.preview;
  const contexts = Array.isArray(preview.contexts) ? preview.contexts : [];
  return Boolean(
    runRequest.request_sha256
      && runRequest.egress_plan_sha256
      && runRequest.model_profile_revision_id
      && runRequest.egress_plan.endpoint
      && typeof preview.model_id === "string"
      && preview.model_id
      && contexts.length > 0
      && contexts.every((value) => value && typeof value === "object"
        && typeof (value as Record<string, unknown>).category === "string"
        && "content" in (value as Record<string, unknown>)),
  );
}

function EgressApprovalEvidence({ runRequest }: { runRequest: AgentRunRequest }) {
  const plan = runRequest.egress_plan;
  const preview = plan.preview;
  const contexts = Array.isArray(preview.contexts) ? preview.contexts : [];
  const modelId = typeof preview.model_id === "string" ? preview.model_id : "未声明";
  return <section className="c4-egress-evidence" aria-label="外发审批证据">
    <h4>外发审批证据</h4>
    <dl>
      <dt>操作 / 模型</dt><dd>{runRequest.operation_kind} · {modelId}</dd>
      <dt>端点</dt><dd>{plan.endpoint}</dd>
      <dt>位置 / 驻留 / 留存</dt><dd>{plan.locality} · {plan.data_residency} · {plan.retention_policy}</dd>
      <dt>成本上限</dt><dd>{plan.cost_mode} · {plan.currency ?? "无"} · {plan.cost_bound_minor ?? "无"}</dd>
      <dt>资源边界</dt><dd>{plan.task_count} task · GPU {"gpu_count" in plan ? String(plan.gpu_count) : "0"} ·
        {"media_execution" in plan && plan.media_execution ? "允许媒体执行" : "禁止媒体执行"} · {plan.max_retries} retry</dd>
      <dt>模型配置 revision</dt><dd><code>{runRequest.model_profile_revision_id}</code></dd>
      <dt>请求哈希</dt><dd><code>{runRequest.request_sha256}</code></dd>
      <dt>外发计划哈希</dt><dd><code>{runRequest.egress_plan_sha256}</code></dd>
    </dl>
    <h5>将发送的完整内容（{contexts.length} 类）</h5>
    {contexts.map((context, index) => {
      const fields = context && typeof context === "object" ? context as Record<string, unknown> : {};
      return <details key={`${String(fields.category)}-${index}`} open>
        <summary>{String(fields.category ?? "UNKNOWN")} · {String(fields.characters ?? "未计数")} 字符</summary>
        <pre>{prettyValue(fields.content)}</pre>
      </details>;
    })}
    {(["protected_manifest", "editable_manifest"] as const).map((key) => key in preview ? <details key={key}>
      <summary>{key}</summary><pre>{prettyValue(preview[key])}</pre>
    </details> : null)}
  </section>;
}

export function PromptBatchPanel({ branchContext, nodes, onGraphChangeSet, projectId, shots }: {
  branchContext: BranchContext | null; onGraphChangeSet(value: GraphChangeSetResponse): void;
  projectId: string; shots: CanvasShot[]; nodes: CanvasNode[];
}) {
  const [bindingKey, setBindingKey] = useState("visual-profile");
  const [branchName, setBranchName] = useState("C4 提示词候选");
  const [instruction, setInstruction] = useState("按已认证 PromptProfile 优化可编辑块");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [all, setAll] = useState(true);
  const [batch, setBatch] = useState<PromptBatchResponse | null>(null);
  const [preflight, setPreflight] = useState<PromptBatchPreflightResponse | null>(null);
  const [resumeId, setResumeId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const promptNodes = nodes.filter((node) => node.data.kind === "prompt");
  const [sourceNodeId, setSourceNodeId] = useState("");
  const [extraction, setExtraction] = useState<PromptProfileExtractionResponse | null>(null);
  const [profileName, setProfileName] = useState("C4 已认证 PromptProfile");

  useEffect(() => {
    setExtraction(null); setBatch(null); setPreflight(null); setResumeId("");
    setError(""); setSelected(new Set()); setSourceNodeId("");
  }, [projectId, branchContext?.branchId, branchContext?.branchHeadRevisionId,
    branchContext?.branchHeadVersion, branchContext?.selectionVersion]);

  function input(): CreatePromptBatchRequest {
    if (!branchContext) throw new Error("没有可用分支上下文");
    return { base_graph_revision_id: branchContext.branchHeadRevisionId,
      selection: all ? { mode: "ALL" } : { mode: "SELECTED", shot_ids: [...selected] },
      prompt_profile_binding_key: bindingKey,
      application_target: { mode: "NEW_BRANCH", base_branch_id: branchContext.branchId,
        branch_name: branchName, expected_branch_head_revision_id: branchContext.branchHeadRevisionId,
        expected_branch_head_version: branchContext.branchHeadVersion,
        expected_selection_version: branchContext.selectionVersion },
      instruction, client_mutation_id: crypto.randomUUID() };
  }

  async function run(action: () => Promise<void>): Promise<void> {
    setBusy(true); setError("");
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
    finally { setBusy(false); }
  }

  async function executePending(): Promise<void> {
    if (!batch) return;
    const pending = batch.items.filter((item) => item.current_attempt?.state === "PENDING_EXECUTION");
    let cursor = 0;
    let latest = batch;
    await Promise.all(Array.from({ length: Math.min(4, pending.length) }, async () => {
      while (cursor < pending.length) {
        const item = pending[cursor++];
        const attempt = item.current_attempt;
        if (attempt) latest = await executePromptBatchItem(projectId, batch.id, item.id,
          attempt.id, attempt.run_request.request_sha256);
      }
    }));
    setBatch(await getPromptBatch(projectId, latest.id));
  }

  async function decide(item: PromptBatchResponse["items"][number], decision: "ACCEPTED" | "REJECTED") {
    const attempt = item.current_attempt;
    if (!batch || !attempt?.output_sha256) return;
    setBatch(await decidePromptBatchItem(projectId, batch.id, item.id, { decision,
      attempt_id: attempt.id, expected_output_sha256: attempt.output_sha256,
      expected_decision_version: item.decision_version, client_mutation_id: crypto.randomUUID() }));
  }

  return <section className="f1-control-panel prompt-batch-panel" aria-label="C4 Prompt 批次中心">
    <h3>Prompt 批次中心</h3>
    <p>只生成可审提案；finalize 后仍需单独审批 GraphChangeSet。</p>
    <fieldset><legend>从示例提炼 PromptProfile</legend>
      <label>示例 PromptNode<select value={sourceNodeId} onChange={(event) => setSourceNodeId(event.target.value)}>
        <option value="">请选择</option>{promptNodes.map((node) => <option key={node.id} value={node.id}>{node.id.slice(0, 8)}</option>)}
      </select></label>
      <button disabled={busy || !branchContext || !sourceNodeId} onClick={() => void run(async () => {
        if (!branchContext) return;
        setExtraction(await requestPromptProfileExtraction(projectId, {
          base_graph_revision_id: branchContext.branchHeadRevisionId,
          branch_id: branchContext.branchId,
          expected_branch_head_revision_id: branchContext.branchHeadRevisionId,
          expected_branch_head_version: branchContext.branchHeadVersion,
          expected_selection_version: branchContext.selectionVersion,
          source_node_id: sourceNodeId,
          instruction: "只提炼组织、密度、镜头语言与格式规则，不复制内容事实",
        }));
      })} type="button">请求提炼预览</button>
      {extraction ? <div><p>提炼状态 {extraction.state} · 示例哈希 {extraction.source_document_sha256.slice(0, 12)}</p>
        <EgressApprovalEvidence runRequest={extraction.run_request} />
        {extraction.state === "PENDING_APPROVAL" ? <div>
          <button disabled={busy || !hasApprovalEvidence(extraction.run_request)} onClick={() => void run(async () => setExtraction(await approvePromptProfileExtraction(
            projectId, extraction.id, extraction.run_request.request_sha256)))} type="button">批准准确提炼请求</button>
          <button disabled={busy} onClick={() => void run(async () => setExtraction(await approvePromptProfileExtraction(
            projectId, extraction.id, extraction.run_request.request_sha256, "REJECTED")))} type="button">拒绝（零发送）</button></div> : null}
        {extraction.state === "PENDING_EXECUTION" ? <button disabled={busy} onClick={() => void run(async () =>
          setExtraction(await executePromptProfileExtraction(projectId, extraction.id)))} type="button">执行一次提炼</button> : null}
        {extraction.output ? <pre>{JSON.stringify(extraction.output, null, 2)}</pre> : null}
        {extraction.state === "SUCCEEDED" && extraction.output_sha256 ? <div>
          <label>资源名称<input value={profileName} onChange={(event) => setProfileName(event.target.value)} /></label>
          <button disabled={busy} onClick={() => void run(async () => setExtraction(await materializePromptProfileExtraction(
            projectId, extraction.id, { name: profileName, tags: ["c4-certified"],
            expected_output_sha256: extraction.output_sha256! })))} type="button">确认并保存不可变资源 revision</button>
        </div> : null}
      </div> : null}
    </fieldset>
    <label>绑定键<input value={bindingKey} onChange={(event) => setBindingKey(event.target.value)} /></label>
    <label>候选分支<input value={branchName} onChange={(event) => setBranchName(event.target.value)} /></label>
    <label>指令<textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label>
    <label><input type="checkbox" checked={all} onChange={(event) => setAll(event.target.checked)} />全部分镜（任一阻断则失败）</label>
    {!all ? <div>{[...shots].sort((a, b) => a.order - b.order).map((shot) => <label key={shot.id}>
      <input type="checkbox" checked={selected.has(shot.id)} onChange={(event) => {
        const next = new Set(selected);
        if (event.target.checked) next.add(shot.id);
        else next.delete(shot.id);
        setSelected(next);
      }} />分镜 {shot.order + 1} · {shot.id.slice(0, 8)}</label>)}</div> : null}
    <div><button disabled={busy || !branchContext} onClick={() => void run(async () =>
      setPreflight(await preflightPromptBatch(projectId, input())))} type="button">预检</button>
      <button disabled={busy || !branchContext} onClick={() => void run(async () =>
        setBatch(await requestPromptBatch(projectId, input())))} type="button">创建批次</button></div>
    {preflight ? <p role="status">预检：{preflight.eligible ? `${preflight.items.length} 项可执行` :
      preflight.blockers.map((item) => `${item.shot_id.slice(0, 8)} ${item.code}`).join("；")}</p> : null}
    <label>恢复批次 ID<input value={resumeId} onChange={(event) => setResumeId(event.target.value)} /></label>
    <button disabled={busy || !resumeId} onClick={() => void run(async () =>
      setBatch(await getPromptBatch(projectId, resumeId)))} type="button">恢复</button>
    {batch ? <div><p>状态 {batch.phase} · {batch.items.length} 项 · v{batch.version}</p>
      <code>{batch.request_set_sha256}</code>
      {batch.phase === "PENDING_APPROVAL" ? <div>
        <button disabled={busy || batch.items.some((item) => !item.current_attempt
          || !hasApprovalEvidence(item.current_attempt.run_request))} onClick={() => void run(async () => setBatch(await approvePromptBatch(
          projectId, batch.id, batch.request_set_sha256)))} type="button">批准准确批次</button>
        <button disabled={busy} onClick={() => void run(async () => setBatch(await approvePromptBatch(
          projectId, batch.id, batch.request_set_sha256, "REJECTED")))} type="button">拒绝（零发送）</button></div> : null}
      {batch.phase === "PENDING_EXECUTION" || batch.phase === "IN_PROGRESS" ? <button disabled={busy}
        onClick={() => void run(executePending)} type="button">执行未发送项（并发 4）</button> : null}
      <ol>{batch.items.map((item) => <li key={item.id}><p>分镜 {item.shot_order + 1} ·
        {item.current_attempt?.state ?? "无 attempt"} · Profile {item.prompt_profile_scope_kind}</p>
        {item.current_attempt ? <EgressApprovalEvidence runRequest={item.current_attempt.run_request} /> : null}
        {"base_document" in item ? <details><summary>基线 Prompt 文档</summary>
          <pre>{prettyValue(item.base_document)}</pre></details> : null}
        {item.current_attempt && "compiled_document" in item.current_attempt && item.current_attempt.compiled_document
          ? <details open><summary>候选 Prompt 文档（与基线对照）</summary>
            <pre>{prettyValue(item.current_attempt.compiled_document)}</pre></details> : null}
        {"attempts" in item && Array.isArray(item.attempts) ? <details><summary>attempt 历史（{item.attempts.length}）</summary>
          <pre>{prettyValue(item.attempts)}</pre></details> : null}
        {"decisions" in item && Array.isArray(item.decisions) ? <details><summary>决策历史（{item.decisions.length}）</summary>
          <pre>{prettyValue(item.decisions)}</pre></details> : null}
        {item.current_attempt?.failure_code ? <p role="status">失败代码：{item.current_attempt.failure_code}</p> : null}
        {item.current_attempt?.state === "SUCCEEDED" ? <span>
          <button disabled={busy || !["IN_PROGRESS", "REVIEW_PENDING"].includes(batch.phase)}
            onClick={() => void run(async () => decide(item, "ACCEPTED"))} type="button">接受</button>
          <button disabled={busy || !["IN_PROGRESS", "REVIEW_PENDING"].includes(batch.phase)}
            onClick={() => void run(async () => decide(item, "REJECTED"))} type="button">拒绝</button>
        </span> : null}</li>)}</ol>
      {batch.phase === "REVIEW_PENDING" ? <div>
        <button disabled={busy} onClick={() => void run(async () => setBatch(await acceptAllSucceededPromptBatch(
          projectId, batch.id, batch.version)))} type="button">接受全部成功项</button>
        {batch.items.some((item) => item.current_attempt?.state === "FAILED") ? <button disabled={busy}
          onClick={() => void run(async () => setBatch(await retryFailedPromptBatch(projectId, batch.id, {
            failed_item_ids: batch.items.filter((item) => item.current_attempt?.state === "FAILED").map((item) => item.id),
            expected_batch_version: batch.version, client_mutation_id: crypto.randomUUID(),
          })))} type="button">只重试失败项</button> : null}
      </div> : null}
      {batch.phase === "RETRY_APPROVAL_REQUIRED" && batch.retry_request_set_sha256 ? <button disabled={busy}
        onClick={() => void run(async () => setBatch(await approvePromptBatchRetry(projectId, batch.id,
          batch.retry_request_set_sha256!)))} type="button">批准失败子集重试</button> : null}
      {batch.phase === "REVIEW_PENDING" ? <button disabled={busy} onClick={() => void run(async () => {
        const result = await finalizePromptBatch(projectId, batch.id, batch.version, crypto.randomUUID());
        setBatch(result); if (result.graph_change_set) onGraphChangeSet(result.graph_change_set);
      })} type="button">汇总已接受项并进入 GraphChangeSet 复核</button> : null}
    </div> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
