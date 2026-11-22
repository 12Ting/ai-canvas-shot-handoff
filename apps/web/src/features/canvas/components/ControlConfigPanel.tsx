import { useEffect, useRef, useState } from "react";

import {
  applyConfigChangeSet,
  approveConfigChangeSet,
  getActiveControlConfig,
  proposeConfigChangeSet,
  type ConfigChangeSetResponse,
  type ControlConfigRevisionResponse,
} from "../../../api/client";

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "无法更新受信控制配置。";
}

export function ControlConfigPanel({ projectId }: { projectId: string }) {
  const [config, setConfig] = useState<ControlConfigRevisionResponse | null>(null);
  const [value, setValue] = useState("1");
  const [pending, setPending] = useState<ConfigChangeSetResponse | null>(null);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const restoreFocusRef = useRef(false);

  const load = async () => {
    try {
      const current = await getActiveControlConfig();
      setConfig(current);
      setValue(String(current.dry_run_max_tasks));
    } catch (error: unknown) {
      setMessage(messageFor(error));
    }
  };

  useEffect(() => {
    void load();
  }, []);

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
    const next = Number(value);
    if (!config || submitting || !Number.isInteger(next) || next < 1 || next > 100) {
      setMessage("最大任务数必须是 1 到 100 的整数。");
      return;
    }
    setSubmitting(true);
    setMessage("");
    try {
      setPending(
        await proposeConfigChangeSet(projectId, {
          baseConfigRevisionId: config.id,
          dryRunMaxTasks: next,
        }),
      );
    } catch (error: unknown) {
      setMessage(messageFor(error));
    } finally {
      setSubmitting(false);
    }
  };

  const apply = async () => {
    if (!pending || submitting) return;
    setSubmitting(true);
    setMessage("");
    try {
      const approved = await approveConfigChangeSet(pending.id, pending.changeset_sha256);
      await applyConfigChangeSet(approved.id, approved.changeset_sha256);
      setPending(null);
      await load();
      setMessage("控制配置已应用");
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
      await approveConfigChangeSet(
        pending.id,
        pending.changeset_sha256,
        "REJECTED",
      );
      restoreFocusRef.current = true;
      setPending(null);
      setMessage("已拒绝本次控制配置变更");
    } catch (error: unknown) {
      setMessage(messageFor(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section aria-label="受信控制配置" className="f1-control-panel">
      <p className="f1-control-panel__eyebrow">TRUSTED CONTROL</p>
      <p>dry-run 最大任务数：{config?.dry_run_max_tasks ?? "读取中"}</p>
      <label>
        <span>新的最大任务数</span>
        <input
          aria-label="dry-run 最大任务数"
          disabled={submitting || pending !== null}
          inputMode="numeric"
          max={100}
          min={1}
          onChange={(event) => setValue(event.target.value)}
          type="number"
          value={value}
        />
      </label>
      <button
        disabled={!config || submitting || pending !== null}
        onClick={() => void requestReview()}
        ref={triggerRef}
        type="button"
      >
        审阅控制配置
      </button>
      {message ? <span aria-live="polite" role="status">{message}</span> : null}
      {pending ? (
        <section
          aria-labelledby="control-config-review-title"
          aria-modal="true"
          className="f1-review-dialog"
          ref={dialogRef}
          role="dialog"
          tabIndex={-1}
        >
          <p className="f1-control-panel__eyebrow">CONFIG CHANGE SET</p>
          <h2 id="control-config-review-title">控制配置变更审阅</h2>
          <p>仅变更 dry-run 最大任务数；不会运行媒体、GPU、付费或外发操作。</p>
          <p>新值：{value}</p>
          <div className="f1-review-dialog__actions">
            <button disabled={submitting} onClick={() => void reject()} type="button">
              拒绝
            </button>
            <button disabled={submitting} onClick={() => void apply()} type="button">
              {submitting ? "正在应用…" : "确认应用控制配置"}
            </button>
          </div>
        </section>
      ) : null}
    </section>
  );
}
