import { useEffect, useRef, useState } from "react";

import {
  approveAgentProposal,
  executeAgentProposal,
  getAgentProposal,
  requestAgentProposal,
  type AgentProposalResponse,
  type GraphChangeSetResponse,
} from "../../api/client";
import type { CanvasNode } from "../canvas/canvasTypes";
import "./agentProposal.css";

type BranchContext = Readonly<{
  branchId: string;
  branchHeadRevisionId: string;
  branchHeadVersion: number;
  selectionVersion: number;
}>;

function textNode(node: CanvasNode): boolean {
  return node.data.kind === "text_source" || node.data.kind === "note" || node.data.kind === "prompt";
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "Agent 提案操作失败。";
}

export function AgentProposalPanel({
  branchContext,
  nodes,
  onGraphChangeSet,
  projectId,
}: {
  branchContext: BranchContext | null;
  nodes: CanvasNode[];
  onGraphChangeSet(changeSet: GraphChangeSetResponse): void;
  projectId: string;
}) {
  const eligible = nodes.filter(textNode);
  const [nodeId, setNodeId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [selectionMode, setSelectionMode] = useState(false);
  const [startUtf16, setStartUtf16] = useState(0);
  const [endUtf16, setEndUtf16] = useState(0);
  const [adjacent, setAdjacent] = useState(false);
  const [resources, setResources] = useState(false);
  const [branchName, setBranchName] = useState("Agent proposal");
  const [proposal, setProposal] = useState<AgentProposalResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!nodeId && eligible[0]) setNodeId(eligible[0].id);
  }, [eligible, nodeId]);

  useEffect(() => {
    const stored = sessionStorage.getItem(`ai-canvas-agent-proposal:${projectId}`);
    if (!stored) return;
    void getAgentProposal(projectId, stored).then(setProposal).catch(() => sessionStorage.removeItem(`ai-canvas-agent-proposal:${projectId}`));
  }, [projectId]);

  const requestReview = async () => {
    if (!branchContext || !nodeId || !instruction.trim() || busy) return;
    setBusy(true); setMessage("");
    try {
      const contexts = ["USER_INSTRUCTION", selectionMode ? "SELECTED_TEXT" : "SELECTED_NODE_TEXT"] as const;
      const result = await requestAgentProposal(projectId, {
        base_graph_revision_id: branchContext.branchHeadRevisionId,
        branch_id: branchContext.branchId,
        expected_branch_head_revision_id: branchContext.branchHeadRevisionId,
        expected_branch_head_version: branchContext.branchHeadVersion,
        expected_selection_version: branchContext.selectionVersion,
        node_id: nodeId,
        scope: selectionMode ? { kind: "TEXT_SELECTION", start_utf16: startUtf16, end_utf16: endUtf16 } : { kind: "NODE" },
        instruction: instruction.trim(),
        context_categories: [...contexts, ...(adjacent ? ["ADJACENT_NODE_TEXT" as const] : []), ...(resources ? ["RESOURCE_TEXT_METADATA" as const] : [])],
        new_branch_name: branchName.trim(),
      });
      setProposal(result); sessionStorage.setItem(`ai-canvas-agent-proposal:${projectId}`, result.id);
    } catch (error) { setMessage(messageFor(error)); } finally { setBusy(false); }
  };

  const decide = async (decision: "APPROVED" | "REJECTED") => {
    if (!proposal || busy) return;
    setBusy(true); setMessage("");
    try {
      const result = await approveAgentProposal(projectId, proposal.id, proposal.run_request.request_sha256, decision);
      setProposal(result);
      setMessage(decision === "APPROVED" ? "已批准；尚未发送。请再次明确执行。" : "已拒绝；模型端点未收到请求。");
      if (decision === "REJECTED") queueMicrotask(() => triggerRef.current?.focus());
    } catch (error) { setMessage(messageFor(error)); } finally { setBusy(false); }
  };

  const execute = async () => {
    if (!proposal || proposal.state !== "PENDING_EXECUTION" || busy) return;
    setBusy(true); setMessage("");
    try {
      const result = await executeAgentProposal(projectId, proposal.id);
      setProposal(result);
      if (result.graph_change_set) {
        onGraphChangeSet(result.graph_change_set);
        setMessage("模型仅生成提案；NEW_BRANCH GraphChangeSet 正等待独立审阅。主分支未改变。");
      } else if (result.output?.kind === "run_request_draft") {
        setMessage("模型生成了不可执行的 RunRequest 草案；未创建执行任务。");
      } else setMessage(result.failure_code ? `提案失败：${result.failure_code}` : `提案状态：${result.state}`);
    } catch (error) { setMessage(messageFor(error)); } finally { setBusy(false); }
  };

  const plan = proposal?.run_request.egress_plan;
  return <section aria-label="Agent 提案" className="c3-agent-panel">
    <p className="f1-control-panel__eyebrow">C3 SINGLE-SCOPE AGENT</p>
    <label>目标文本节点<select value={nodeId} onChange={(event) => setNodeId(event.target.value)}>{eligible.map((node) => <option key={node.id} value={node.id}>{node.id.slice(0, 12)} · {node.data.kind}</option>)}</select></label>
    <label>指令<textarea maxLength={20000} value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label>
    <label><input checked={selectionMode} onChange={(event) => setSelectionMode(event.target.checked)} type="checkbox" />仅文本选择区</label>
    {selectionMode ? <div className="c3-agent-panel__offsets"><label>start UTF-16<input min={0} type="number" value={startUtf16} onChange={(event) => setStartUtf16(Number(event.target.value))} /></label><label>end UTF-16<input min={0} type="number" value={endUtf16} onChange={(event) => setEndUtf16(Number(event.target.value))} /></label></div> : null}
    <label><input checked={adjacent} onChange={(event) => setAdjacent(event.target.checked)} type="checkbox" />相邻节点文本</label>
    <label><input checked={resources} onChange={(event) => setResources(event.target.checked)} type="checkbox" />资源文本元数据</label>
    <label>新分支名<input maxLength={100} value={branchName} onChange={(event) => setBranchName(event.target.value)} /></label>
    <button disabled={!branchContext || !eligible.length || !instruction.trim() || !branchName.trim() || busy || Boolean(proposal)} onClick={() => void requestReview()} ref={triggerRef} type="button">生成外发计划（不发送）</button>
    {plan ? <section className="c3-agent-panel__plan"><h3>完整 EgressPlan</h3><dl><div><dt>端点</dt><dd>{plan.endpoint}</dd></div><div><dt>位置 / 驻留</dt><dd>{plan.locality} / {plan.data_residency}</dd></div><div><dt>外发类别</dt><dd>{plan.external_data_classes.join(" · ")}</dd></div><div><dt>保留策略</dt><dd>{plan.retention_policy}</dd></div><div><dt>成本</dt><dd>{plan.cost_mode} {plan.currency ?? "—"} {plan.cost_bound_minor ?? "—"}</dd></div><div><dt>重试 / 任务</dt><dd>{plan.max_retries} / {plan.task_count}</dd></div><div><dt>请求哈希</dt><dd>{plan.request_sha256.slice(0, 12)}</dd></div></dl>
      {proposal?.state === "PENDING_APPROVAL" ? <div><button disabled={busy} onClick={() => void decide("REJECTED")}>拒绝</button><button disabled={busy} onClick={() => void decide("APPROVED")}>批准但不发送</button></div> : null}
      {proposal?.state === "PENDING_EXECUTION" ? <button disabled={busy} onClick={() => void execute()}>明确执行一次模型调用</button> : null}
    </section> : null}
    {message ? <p aria-live="polite">{message}</p> : null}
    {proposal && ["REJECTED", "SUCCEEDED", "FAILED", "UNKNOWN_TERMINAL"].includes(proposal.state) ? <button onClick={() => { setProposal(null); sessionStorage.removeItem(`ai-canvas-agent-proposal:${projectId}`); }} type="button">新建提案</button> : null}
  </section>;
}
