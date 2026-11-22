import { useEffect, useRef, useState } from "react";

import {
  ApiRequestError,
  approveNoEgressDryRun,
  executeNoEgressDryRun,
  requestNoEgressDryRun,
  type RunRequestResponse,
} from "../../../api/client";

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "无法创建无外发 dry-run。";
}

export function NoEgressDryRunPanel({ projectId }: { projectId: string }) {
  const [taskCount, setTaskCount] = useState("1");
  const [pending, setPending] = useState<RunRequestResponse | null>(null);
  const [completedRequestId, setCompletedRequestId] = useState<string | null>(null);
  const [consumptionVerified, setConsumptionVerified] = useState(false);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef(false);

  useEffect(() => {
    if (pending) dialogRef.current?.focus();
  }, [pending]);

  useEffect(() => {
    if (!submitting && pending === null && restoreFocusRef.current) {
      restoreFocusRef.current = false;
      triggerRef.current?.focus();
    }
  }, [pending, submitting]);

  const requestReview = async () => {
    const next = Number(taskCount);
    if (submitting || !Number.isInteger(next) || next < 1 || next > 100) {
      setMessage("任务数必须是 1 到 100 的整数。");
      return;
    }
    setSubmitting(true);
    setMessage("");
    setCompletedRequestId(null);
    setConsumptionVerified(false);
    try {
      setPending(await requestNoEgressDryRun(projectId, next));
    } catch (error: unknown) {
      setMessage(messageFor(error));
    } finally {
      setSubmitting(false);
    }
  };

  const execute = async () => {
    if (!pending || submitting) return;
    setSubmitting(true);
    setMessage("");
    try {
      await approveNoEgressDryRun(projectId, pending.id, pending.request_sha256);
      const receipt = await executeNoEgressDryRun(projectId, pending.id);
      setPending(null);
      setCompletedRequestId(pending.id);
      setMessage(
        receipt.terminal_state === "DRY_RUN_SUCCEEDED"
          ? "Dry-run 已完成"
          : "Dry-run 未完成",
      );
      restoreFocusRef.current = true;
    } catch (error: unknown) {
      setMessage(messageFor(error));
    } finally {
      setSubmitting(false);
    }
  };

  const reject = async () => {
    if (!pending || submitting) return;
    setSubmitting(true);
    setMessage("");
    try {
      await approveNoEgressDryRun(
        projectId,
        pending.id,
        pending.request_sha256,
        "REJECTED",
      );
      restoreFocusRef.current = true;
      setPending(null);
      setMessage("已拒绝本次 dry-run 请求");
    } catch (error: unknown) {
      setMessage(messageFor(error));
    } finally {
      setSubmitting(false);
    }
  };

  const verifyConsumedAuthorization = async () => {
    if (!completedRequestId || submitting || consumptionVerified) return;
    setSubmitting(true);
    setMessage("");
    try {
      await executeNoEgressDryRun(projectId, completedRequestId);
      setMessage("同一授权意外再次执行，请停止验收。");
    } catch (error: unknown) {
      if (
        error instanceof ApiRequestError &&
        error.status === 409 &&
        error.message === "run_authorization_consumed"
      ) {
        setConsumptionVerified(true);
        setMessage("已验证：同一授权再次执行被拒绝。");
      } else {
        setMessage(messageFor(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section aria-label="无外发 dry-run" className="f1-control-panel">
      <p className="f1-control-panel__eyebrow">SAFE RUN</p>
      <label>
        <span>dry-run 任务数</span>
        <input
          aria-label="dry-run 任务数"
          disabled={submitting || pending !== null}
          inputMode="numeric"
          max={100}
          min={1}
          onChange={(event) => setTaskCount(event.target.value)}
          type="number"
          value={taskCount}
        />
      </label>
      <button
        disabled={submitting || pending !== null}
        onClick={() => void requestReview()}
        ref={triggerRef}
        type="button"
      >
        审阅无外发 dry-run
      </button>
      {message ? <span aria-live="polite" role="status">{message}</span> : null}
      {completedRequestId ? (
        <button
          disabled={submitting || consumptionVerified}
          onClick={() => void verifyConsumedAuthorization()}
          type="button"
        >
          {consumptionVerified ? "同一授权已确认不可重复执行" : "验证同一授权不可重复执行"}
        </button>
      ) : null}
      {pending ? (
        <section
          aria-labelledby="no-egress-dry-run-review-title"
          aria-modal="true"
          className="f1-review-dialog"
          ref={dialogRef}
          role="dialog"
          tabIndex={-1}
        >
          <p className="f1-control-panel__eyebrow">EGRESS PLAN</p>
          <h2 id="no-egress-dry-run-review-title">无外发 dry-run 审阅</h2>
          <p>
            {pending.egress_plan.mode === "NONE" &&
            pending.egress_plan.endpoint === null &&
            pending.egress_plan.external_data_classes.length === 0 &&
            pending.egress_plan.cost_bound === 0 &&
            pending.egress_plan.gpu_count === 0
              ? "无外发、无费用、无 GPU、不产生媒体。"
              : "服务端计划不符合 F1 无外发约束，请拒绝。"}
          </p>
          <p>任务数：{pending.egress_plan.task_count}</p>
          <p>计划哈希：{pending.egress_plan.plan_sha256.slice(0, 12)}</p>
          <p>请求哈希：{pending.egress_plan.request_sha256.slice(0, 12)}</p>
          <div className="f1-review-dialog__actions">
            <button disabled={submitting} onClick={() => void reject()} type="button">
              拒绝
            </button>
            <button disabled={submitting} onClick={() => void execute()} type="button">
              {submitting ? "正在执行…" : "确认并执行 dry-run"}
            </button>
          </div>
        </section>
      ) : null}
    </section>
  );
}
