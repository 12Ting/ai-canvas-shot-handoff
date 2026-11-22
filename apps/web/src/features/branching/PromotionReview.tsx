import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  applyPromotion,
  decidePromotion,
  type BranchWorkspaceResponse,
  type PromotionResponse,
} from "../../api/client";

function shortHash(value: unknown): string {
  return typeof value === "string" && value.length > 0 ? value.slice(0, 12) : "—";
}

function materialization(
  proposal: PromotionResponse,
): { unmaterialized: number; current: number; stale: number } {
  const value = proposal.impact.materialization;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { unmaterialized: 0, current: 0, stale: 0 };
  }
  const record = value as Record<string, unknown>;
  const count = (key: string) =>
    Number.isInteger(record[key]) && Number(record[key]) >= 0 ? Number(record[key]) : 0;
  return {
    unmaterialized: count("unmaterialized"),
    current: count("current"),
    stale: count("stale"),
  };
}

export function PromotionReview({
  onApplied,
  onClose,
  projectId,
  proposal,
  returnFocus,
  workspace,
}: {
  onApplied(promotion: PromotionResponse): Promise<void> | void;
  onClose(): void;
  projectId: string;
  proposal: PromotionResponse;
  returnFocus?: HTMLElement | null;
  workspace: BranchWorkspaceResponse;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const dialogRef = useRef<HTMLElement>(null);
  const source = workspace.branches.find(
    (branch) => branch.id === proposal.source_branch_id,
  );
  const target = workspace.branches.find(
    (branch) => branch.id === proposal.target_branch_id,
  );
  const counts = materialization(proposal);
  const isUndo = proposal.kind === "UNDO_PROMOTION";

  useEffect(() => {
    dialogRef.current?.focus();
    return () => returnFocus?.focus({ preventScroll: true });
  }, [returnFocus]);

  const reject = async () => {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      await decidePromotion(
        projectId,
        proposal.id,
        proposal.promotion_sha256,
        "REJECTED",
      );
      onClose();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法拒绝这次选择变更。");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      const approved = await decidePromotion(
        projectId,
        proposal.id,
        proposal.promotion_sha256,
      );
      const applied = await applyPromotion(
        projectId,
        approved.id,
        approved.promotion_sha256,
      );
      await onApplied(applied);
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "选择已变化；保留当前审阅，请刷新分支状态后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <section
      aria-labelledby="promotion-review-title"
      aria-modal="true"
      className="promotion-review"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          void reject();
        }
      }}
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="promotion-review__card">
        <header>
          <p>SELECTION POINTER</p>
          <h2 id="promotion-review-title">
            {isUndo ? "撤销上次提升" : "提升分支为正式选择"}
          </h2>
          <span>{isUndo ? "恢复先前的正式选择，不删除任何分支。" : "只移动正式选择，不复制或重写图谱版本。"}</span>
        </header>

        <div aria-label="选择变化" className="promotion-review__route">
          <article>
            <span>当前正式选择</span>
            <strong>{source?.name ?? proposal.source_branch_id}</strong>
            <code>{proposal.source_graph_revision_id.slice(0, 12)}</code>
          </article>
          <span aria-hidden="true" className="promotion-review__arrow">→</span>
          <article>
            <span>{isUndo ? "恢复到" : "提升到"}</span>
            <strong>{target?.name ?? proposal.target_branch_id}</strong>
            <code>{proposal.target_graph_revision_id.slice(0, 12)}</code>
          </article>
        </div>

        <dl className="promotion-review__facts">
          <div>
            <dt>并发见证</dt>
            <dd>
              选择版本 {proposal.expected_selection_version} · 目标头版本{" "}
              {proposal.target_branch_head_version}
            </dd>
          </div>
          <div>
            <dt>图谱哈希</dt>
            <dd>
              {shortHash(proposal.impact.source_graph_revision_sha256)} →{" "}
              {shortHash(proposal.impact.target_graph_revision_sha256)}
            </dd>
          </div>
          <div>
            <dt>目标物化状态</dt>
            <dd>
              未物化 {counts.unmaterialized} · 当前 {counts.current} · 陈旧 {counts.stale}
            </dd>
          </div>
          <div>
            <dt>审批哈希</dt>
            <dd>{proposal.promotion_sha256.slice(0, 12)}</dd>
          </div>
        </dl>

        <p className="promotion-review__boundary">
          此操作不会运行节点、创建媒体、使用 GPU、付费或向外部服务发送数据。
        </p>
        {notice ? <p aria-live="assertive" role="alert">{notice}</p> : null}
        <footer>
          <button disabled={busy} onClick={() => void reject()} type="button">
            取消并拒绝
          </button>
          <button disabled={busy} onClick={() => void apply()} type="button">
            {busy
              ? "正在更新正式选择…"
              : isUndo
                ? "确认撤销提升"
                : "提升为正式选择"}
          </button>
        </footer>
      </div>
    </section>,
    document.body,
  );
}
