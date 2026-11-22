import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

import {
  proposePromotion,
  proposePromotionUndo,
  type BranchWorkspaceResponse,
  type PromotionResponse,
} from "../../api/client";
import { PromotionReview } from "./PromotionReview";
import "./branching.css";

export function BranchPanel({
  projectId,
  viewedBranchId,
  workspace,
  onOpen,
  onSelectionChanged,
}: {
  projectId: string;
  viewedBranchId: string;
  workspace: BranchWorkspaceResponse;
  onOpen(branchId: string): Promise<void>;
  onSelectionChanged(): Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<PromotionResponse | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 64, right: 16 });
  const panelTriggerRef = useRef<HTMLButtonElement>(null);
  const reviewTriggerRef = useRef<HTMLElement | null>(null);
  const viewed = workspace.branches.find((branch) => branch.id === viewedBranchId);
  const selected = workspace.branches.find(
    (branch) => branch.id === workspace.selection.branch_id,
  );
  const triggerLabel =
    viewed && selected
      ? viewed.id === selected.id
        ? `查看/正式：${viewed.name}`
        : `查看：${viewed.name}｜正式：${selected.name}`
      : "分支状态不可用";
  const branchNames = new Map(
    workspace.branches.map((branch) => [branch.id, branch.name]),
  );

  useEffect(() => {
    if (!open) return;
    const updatePosition = () => {
      const trigger = panelTriggerRef.current;
      if (!trigger) return;
      const bounds = trigger.getBoundingClientRect();
      setPosition({
        top: Math.round(bounds.bottom + 10),
        right: Math.max(16, Math.round(window.innerWidth - bounds.right)),
      });
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open]);

  const closeAndRestoreFocus = () => {
    setOpen(false);
    queueMicrotask(() => panelTriggerRef.current?.focus({ preventScroll: true }));
  };

  const preparePromotion = async (trigger: HTMLElement) => {
    if (!viewed || viewed.is_selected || busy || pending) return;
    reviewTriggerRef.current = trigger;
    setBusy(true);
    setNotice("");
    try {
      const proposal = await proposePromotion(projectId, {
        client_mutation_id: crypto.randomUUID(),
        target_branch_id: viewed.id,
        expected_target_branch_head_revision_id: viewed.head.graph_revision_id,
        expected_target_branch_head_version: viewed.head.version,
        expected_selection_version: workspace.selection.version,
      });
      setPending(proposal);
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "无法准备提升审阅，请刷新后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  const prepareUndo = async (trigger: HTMLElement) => {
    const latest = workspace.latest_promotion;
    if (!latest || !workspace.can_undo_latest_promotion || busy || pending) return;
    reviewTriggerRef.current = trigger;
    setBusy(true);
    setNotice("");
    try {
      setPending(await proposePromotionUndo(projectId, latest.id));
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "无法准备撤销审阅，请刷新分支状态后重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  const finishReview = async (promotion: PromotionResponse) => {
    setPending(null);
    await onSelectionChanged();
    setNotice(
      promotion.kind === "UNDO_PROMOTION"
        ? "已撤销上次提升"
        : "已提升为正式选择",
    );
  };

  const surface = open ? createPortal(
      <section
        aria-label="图谱分支"
        className="branch-panel__surface"
        id="graph-branch-panel"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            closeAndRestoreFocus();
          }
        }}
        style={
          {
            "--branch-panel-top": `${position.top}px`,
            "--branch-panel-right": `${position.right}px`,
          } as CSSProperties
        }
      >
        <header>
          <div>
            <span>BRANCH WORKSPACE</span>
            <strong>{workspace.branches.length} 个分支</strong>
          </div>
          <div className="branch-panel__actions">
            {workspace.latest_promotion && workspace.can_undo_latest_promotion ? (
              <button
                disabled={busy || pending !== null}
                onClick={(event) => void prepareUndo(event.currentTarget)}
                type="button"
              >
                撤销上次提升
              </button>
            ) : null}
            {viewed && !viewed.is_selected ? (
              <button
                disabled={busy || pending !== null}
                onClick={(event) => void preparePromotion(event.currentTarget)}
                type="button"
              >
                {busy ? "正在准备审阅…" : "提升为当前选择"}
              </button>
            ) : null}
          </div>
        </header>
        {viewed && !viewed.is_selected ? (
          <p className="branch-panel__warning">正在编辑未提升分支；打开分支不会改变正式选择。</p>
        ) : null}
        <ul>
          {workspace.branches.map((branch) => (
            <li key={branch.id}>
              <button
                aria-label={`${branch.name} ${
                  branch.is_selected ? "正式选择" : "实验分支"
                }${branch.id === viewedBranchId ? " · 正在查看" : ""}`}
                aria-current={branch.id === viewedBranchId ? "page" : undefined}
                disabled={busy || pending !== null || branch.id === viewedBranchId}
                onClick={() => void onOpen(branch.id)}
                type="button"
              >
                <span>{branch.name}</span>
                <small>
                  {branch.is_selected ? "正式选择" : "实验分支"}
                  {branch.id === viewedBranchId ? " · 正在查看" : ""}
                </small>
                <small>
                  头 {branch.head.graph_revision_id.slice(0, 12)} · v{branch.head.version}
                </small>
                {branch.forked_from_branch_id ? (
                  <small>
                    派生自 {branchNames.get(branch.forked_from_branch_id) ?? "未知分支"} ·{" "}
                    {branch.forked_from_revision_id.slice(0, 12)}
                  </small>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
        {notice ? <p aria-live="polite">{notice}</p> : null}
      </section>,
      document.body,
    ) : null;

  return (
    <div className="branch-panel">
      <button
        aria-controls="graph-branch-panel"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        ref={panelTriggerRef}
        type="button"
      >
        {triggerLabel}
      </button>
      {surface}
      {pending ? (
        <PromotionReview
          onApplied={finishReview}
          onClose={() => {
            setPending(null);
            setNotice("已拒绝这次正式选择变更");
          }}
          projectId={projectId}
          proposal={pending}
          returnFocus={reviewTriggerRef.current}
          workspace={workspace}
        />
      ) : null}
    </div>
  );
}
