import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  applyGraphChangeSet,
  approveGraphChangeSet,
  BranchHeadStaleError,
  GraphChangeSetConflictError,
  proposeGraphChangeSet,
  proposeGraphChangeSetUndo,
  SelectionPointerStaleError,
  type GraphChangeSetResponse,
} from "../../../api/client";
import { type SaveCapture, useCanvasStore } from "../canvasStore";

type PendingReview = Readonly<{
  capture: SaveCapture | null;
  kind: "APPLY" | "UNDO" | "AGENT";
  proposal: GraphChangeSetResponse;
}>;

function matchesCapture(capture: SaveCapture): boolean {
  const state = useCanvasStore.getState();
  return (
    state.loadEpoch === capture.loadEpoch &&
    state.projectId === capture.projectId &&
    state.viewedBranchId === capture.viewedBranchId &&
    state.viewedBranchHeadRevisionId === capture.viewedBranchHeadRevisionId &&
    state.viewedBranchHeadVersion === capture.viewedBranchHeadVersion &&
    state.selectionVersion === capture.selectionVersion
  );
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "无法创建图谱变更，请重试。";
}

function isGraphCasConflict(
  error: unknown,
): error is GraphChangeSetConflictError | BranchHeadStaleError | SelectionPointerStaleError {
  return (
    error instanceof GraphChangeSetConflictError ||
    error instanceof BranchHeadStaleError ||
    error instanceof SelectionPointerStaleError
  );
}

export function GraphChangeReview({
  branchContext,
  disabled,
  externalProposal,
  onExternalConsumed,
  onApplied,
  projectId,
}: {
  branchContext?: Readonly<{
    branchId: string;
    branchHeadRevisionId: string;
    branchHeadVersion: number;
    selectionVersion: number;
  }>;
  disabled?: boolean;
  externalProposal?: GraphChangeSetResponse | null;
  onExternalConsumed?(): void;
  onApplied?(changeSet: GraphChangeSetResponse): Promise<void> | void;
  projectId: string;
}) {
  const status = useCanvasStore((state) => state.status);
  const [pending, setPending] = useState<PendingReview | null>(null);
  const [lastDirectApply, setLastDirectApply] = useState<GraphChangeSetResponse | null>(null);
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [mode, setMode] = useState<"DIRECT" | "NEW_BRANCH">("DIRECT");
  const [branchName, setBranchName] = useState("");
  const [confirmedStale, setConfirmedStale] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const reviewTriggerRef = useRef<HTMLElement | null>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef(false);

  useEffect(() => {
    if (externalProposal && pending === null) {
      reviewTriggerRef.current = triggerRef.current;
      setPending({ capture: null, kind: "AGENT", proposal: externalProposal });
    }
  }, [externalProposal, pending]);

  useEffect(() => {
    if (pending) dialogRef.current?.focus();
  }, [pending]);

  useEffect(() => {
    if (!submitting && pending === null && restoreFocusRef.current) {
      restoreFocusRef.current = false;
      reviewTriggerRef.current?.focus();
    }
  }, [pending, submitting]);

  const propose = async () => {
    if (disabled || submitting || pending || status !== "dirty") return;
    let capture: SaveCapture;
    try {
      capture = useCanvasStore.getState().beginSave();
    } catch (error: unknown) {
      setNotice(messageFor(error));
      return;
    }
    setSubmitting(true);
    setNotice("");
    reviewTriggerRef.current = triggerRef.current;
    try {
      const capturedBranch =
        capture.viewedBranchId !== null &&
        capture.viewedBranchHeadRevisionId !== null &&
        capture.viewedBranchHeadVersion !== null &&
        capture.selectionVersion !== null
          ? {
              branchId: capture.viewedBranchId,
              branchHeadRevisionId: capture.viewedBranchHeadRevisionId,
              branchHeadVersion: capture.viewedBranchHeadVersion,
              selectionVersion: capture.selectionVersion,
            }
          : branchContext;
      const proposal = await proposeGraphChangeSet(projectId, {
        baseRevisionId: capture.baseRevisionId,
        clientMutationId: crypto.randomUUID(),
        snapshot: capture.snapshot,
        ...(capturedBranch
          ? {
              applicationTarget:
                mode === "DIRECT"
                  ? {
                      mode: "DIRECT" as const,
                      branch_id: capturedBranch.branchId,
                      expected_branch_head_revision_id:
                        capturedBranch.branchHeadRevisionId,
                      expected_branch_head_version: capturedBranch.branchHeadVersion,
                      expected_selection_version: capturedBranch.selectionVersion,
                    }
                  : {
                      mode: "NEW_BRANCH" as const,
                      base_branch_id: capturedBranch.branchId,
                      branch_name: branchName.trim(),
                      expected_branch_head_revision_id:
                        capturedBranch.branchHeadRevisionId,
                      expected_branch_head_version: capturedBranch.branchHeadVersion,
                      expected_selection_version: capturedBranch.selectionVersion,
                    },
            }
          : {}),
      });
      if (!matchesCapture(capture)) return;
      setPending({ capture, kind: "APPLY", proposal });
    } catch (error: unknown) {
      if (isGraphCasConflict(error)) {
        useCanvasStore
          .getState()
          .markConflict(
            capture.loadEpoch,
            capture.projectId,
            error.currentRevisionId,
            capture.viewedBranchId,
            capture.viewedBranchHeadVersion,
            capture.selectionVersion,
          );
      } else {
        useCanvasStore
          .getState()
          .failSave(
            capture.loadEpoch,
            capture.projectId,
            messageFor(error),
            capture.viewedBranchId,
            capture.viewedBranchHeadVersion,
            capture.selectionVersion,
          );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const proposeUndo = async (trigger: HTMLElement) => {
    if (!lastDirectApply || submitting || pending || status !== "clean") return;
    let capture: SaveCapture;
    try {
      capture = useCanvasStore.getState().beginSave();
    } catch (error: unknown) {
      setNotice(messageFor(error));
      return;
    }
    reviewTriggerRef.current = trigger;
    setSubmitting(true);
    setNotice("");
    try {
      const proposal = await proposeGraphChangeSetUndo(
        projectId,
        lastDirectApply.id,
      );
      if (!matchesCapture(capture)) return;
      setPending({ capture, kind: "UNDO", proposal });
    } catch (error: unknown) {
      if (matchesCapture(capture)) {
        const current = useCanvasStore.getState();
        useCanvasStore.setState({
          status:
            current.editSequence === current.savedSequence ? "clean" : "dirty",
          errorMessage: null,
        });
      }
      setNotice(messageFor(error));
      setLastDirectApply(null);
    } finally {
      setSubmitting(false);
    }
  };

  const closeReview = async () => {
    if (submitting || !pending) return;
    const capture = pending.capture;
    setSubmitting(true);
    setNotice("");
    try {
      await approveGraphChangeSet(
        projectId,
        pending.proposal.id,
        pending.proposal.changeset_sha256,
        "REJECTED",
      );
      if (capture && matchesCapture(capture)) {
        const state = useCanvasStore.getState();
        if (state.status === "saving") {
          useCanvasStore.setState({
            status: state.editSequence === state.savedSequence ? "clean" : "dirty",
            errorMessage: null,
          });
        }
      }
      restoreFocusRef.current = true;
      setPending(null);
      if (pending.kind === "AGENT") onExternalConsumed?.();
      setNotice(
        pending.kind === "UNDO"
          ? "已拒绝撤销，当前分支保持不变"
          : "已拒绝本次图谱变更，草稿仍保留",
      );
    } catch (error: unknown) {
      setNotice(messageFor(error));
    } finally {
      setSubmitting(false);
    }
  };

  const apply = async () => {
    if (!pending || submitting) return;
    setSubmitting(true);
    setNotice("");
    try {
      const impact = pending.proposal.impact;
      const approved = impact?.confirmation_required
        ? await approveGraphChangeSet(
            projectId,
            pending.proposal.id,
            pending.proposal.changeset_sha256,
            "APPROVED",
            confirmedStale ? impact.impact_sha256 : undefined,
          )
        : await approveGraphChangeSet(
            projectId,
            pending.proposal.id,
            pending.proposal.changeset_sha256,
          );
      const applied = await applyGraphChangeSet(
        projectId,
        approved.id,
        approved.changeset_sha256,
      );
      if (applied.state !== "APPLIED" || applied.applied_graph_revision_id === null) {
        throw new Error("图谱变更没有生成新的版本。");
      }
      if (pending.capture) {
        useCanvasStore.getState().completeSave(
          pending.capture.editSequence,
          pending.capture.loadEpoch,
          pending.capture.projectId,
          applied.applied_graph_revision_id,
          pending.capture.viewedBranchId,
          pending.capture.viewedBranchHeadVersion,
          pending.capture.selectionVersion,
        );
      }
      await onApplied?.(applied);
      setLastDirectApply(
        applied.created_branch_id === null && applied.applied_branch_id !== null
          ? applied
          : null,
      );
      setPending(null);
      if (pending.kind === "AGENT") onExternalConsumed?.();
      setNotice(
        pending.kind === "UNDO"
          ? "已创建撤销版本"
          : "已应用为新的图谱版本",
      );
      restoreFocusRef.current = true;
    } catch (error: unknown) {
      if (isGraphCasConflict(error) && pending.capture) {
        useCanvasStore
          .getState()
          .markConflict(
            pending.capture.loadEpoch,
            pending.capture.projectId,
            error.currentRevisionId,
            pending.capture.viewedBranchId,
            pending.capture.viewedBranchHeadVersion,
            pending.capture.selectionVersion,
          );
        setPending(null);
        restoreFocusRef.current = true;
      } else if (pending.capture) {
        useCanvasStore
          .getState()
          .failSave(
            pending.capture.loadEpoch,
            pending.capture.projectId,
            messageFor(error),
            pending.capture.viewedBranchId,
            pending.capture.viewedBranchHeadVersion,
            pending.capture.selectionVersion,
          );
        setNotice(messageFor(error));
      } else setNotice(messageFor(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="graph-change-review">
      <button
        disabled={
          disabled ||
          submitting ||
          pending !== null ||
          status !== "dirty" ||
          (mode === "NEW_BRANCH" && branchName.trim().length === 0)
        }
        onClick={() => void propose()}
        ref={triggerRef}
        type="button"
      >
        {submitting ? "正在准备审阅…" : "审阅并应用画布变更"}
      </button>
      {branchContext ? (
        <span className="graph-change-review__mode">
          <label>
            应用方式
            <select
              disabled={submitting || pending !== null}
              onChange={(event) => setMode(event.currentTarget.value as "DIRECT" | "NEW_BRANCH")}
              value={mode}
            >
              <option value="DIRECT">直接应用到当前查看分支</option>
              <option value="NEW_BRANCH">应用为新分支</option>
            </select>
          </label>
          {mode === "NEW_BRANCH" ? (
            <input
              aria-label="新分支名称"
              maxLength={100}
              onChange={(event) => setBranchName(event.currentTarget.value)}
              placeholder="新分支名称"
              value={branchName}
            />
          ) : null}
        </span>
      ) : null}
      {notice ? <span aria-live="polite" role="status">{notice}</span> : null}
      {lastDirectApply && status === "clean" && pending === null ? (
        <button
          disabled={submitting}
          onClick={(event) => void proposeUndo(event.currentTarget)}
          type="button"
        >
          撤销刚才的直接应用
        </button>
      ) : null}
      {pending ? createPortal(
        <section
          aria-labelledby="graph-change-review-title"
          aria-modal="true"
          className="graph-change-review__dialog"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              void closeReview();
            }
          }}
          ref={dialogRef}
          role="dialog"
          tabIndex={-1}
        >
          <p className="graph-change-review__eyebrow">GRAPH CHANGE SET</p>
          <h2 id="graph-change-review-title">
            {pending.kind === "UNDO" ? "撤销直接应用审阅" : pending.kind === "AGENT" ? "Agent 图谱提案审阅" : "图谱变更审阅"}
          </h2>
          <p>将创建一个新的图谱版本；不会运行媒体、GPU、付费或外发操作。</p>
          {pending.proposal.application_target.mode === "NEW_BRANCH" ? (
            <p>
              将创建实验分支“{pending.proposal.application_target.branch_name}”；正式选择保持不变，
              应用后自动打开该分支。
            </p>
          ) : (
            <p>
              将直接推进当前查看分支；只有它仍是正式选择时，正式选择才会同步到新版本。
            </p>
          )}
          {pending.proposal.impact ? (
            <p>
              影响 {pending.proposal.impact.items.length} 个节点 · 未物化 {pending.proposal.impact.summary.unmaterialized} ·
              当前 {pending.proposal.impact.summary.current} · 陈旧 {pending.proposal.impact.summary.stale}
            </p>
          ) : null}
          <dl>
            <div>
              <dt>变更</dt>
              <dd>{String(pending.proposal.diff.op ?? "replace_snapshot")}</dd>
            </div>
            <div>
              <dt>提示词文档</dt>
              <dd>{" "}{pending.proposal.diff.prompt_documents_changed?.length ?? 0}</dd>
            </div>
            <div>
              <dt>资源绑定</dt>
              <dd>
                {" "}+{pending.proposal.diff.resource_bindings_added?.length ?? 0} · -
                {pending.proposal.diff.resource_bindings_removed?.length ?? 0} · 更新 {pending.proposal.diff.resource_bindings_updated?.length ?? 0}
              </dd>
            </div>
            <div>
              <dt>风险</dt>
              <dd>
                {Array.isArray(pending.proposal.risk_summary.classes)
                  ? pending.proposal.risk_summary.classes.join(" · ")
                  : "GRAPH_WRITE"}
              </dd>
            </div>
            <div>
              <dt>基线</dt>
              <dd>{pending.proposal.base_graph_revision_id.slice(0, 12)}</dd>
            </div>
          </dl>
          {pending.proposal.diff.resource_bindings_updated?.length ? (
            <ul className="graph-change-review__resource-updates">
              {pending.proposal.diff.resource_bindings_updated.map((item) => (
                <li key={item.binding_id}>
                  <strong>{item.binding_key}</strong>: {item.old_resource_revision_id.slice(0, 13)} → {item.new_resource_revision_id.slice(0, 13)}
                </li>
              ))}
            </ul>
          ) : null}
          {pending.proposal.impact?.confirmation_required ? (
            <label>
              <input
                checked={confirmedStale}
                onChange={(event) => setConfirmedStale(event.currentTarget.checked)}
                type="checkbox"
              />
              我确认这些已物化节点需要重新运行
            </label>
          ) : null}
          <div className="graph-change-review__actions">
            <button disabled={submitting} onClick={() => void closeReview()} type="button">
              保留草稿
            </button>
            <button
              disabled={
                submitting ||
                Boolean(pending.proposal.impact?.confirmation_required && !confirmedStale)
              }
              onClick={() => void apply()}
              type="button"
            >
              {submitting
                ? "正在应用…"
                : pending.kind === "UNDO"
                  ? "确认创建撤销版本"
                  : "确认应用变更"}
            </button>
          </div>
        </section>,
        document.body,
      ) : null}
    </div>
  );
}
