import { useCallback, useEffect, useState } from "react";

import {
  acknowledgeExperienceEventCursor,
  readExperienceEventCursor,
  type ExperienceEventPage,
} from "../../../api/client";

const CONSUMER_KEY = "f1-web";

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : "无法读取操作与审批记录。";
}

export function OperationTracePanel({ projectId }: { projectId: string }) {
  const [page, setPage] = useState<ExperienceEventPage | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setMessage("");
    try {
      setPage(await readExperienceEventCursor(projectId, CONSUMER_KEY));
    } catch (error: unknown) {
      setMessage(messageFor(error));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const acknowledge = async () => {
    if (!page || loading) return;
    setLoading(true);
    setMessage("");
    try {
      await acknowledgeExperienceEventCursor(projectId, CONSUMER_KEY, page.next_after);
      setPage(await readExperienceEventCursor(projectId, CONSUMER_KEY));
      setMessage("已确认读取到当前位置");
    } catch (error: unknown) {
      setMessage(messageFor(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section aria-label="操作与审批记录" className="f1-control-panel f1-control-panel--trace">
      <p className="f1-control-panel__eyebrow">EVENT SPINE</p>
      <div className="f1-control-panel__heading">
        <span>操作与审批记录</span>
        <button disabled={loading} onClick={() => void refresh()} type="button">
          刷新
        </button>
      </div>
      {page?.events.length ? (
        <ol>
          {page.events.map((event) => (
            <li key={event.event_id}>
              <strong>{event.event_type}</strong>
              <span>#{event.stream_position} · {event.correlation_id.slice(0, 8)}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p>暂未记录 F1 操作。</p>
      )}
      <button
        disabled={loading || !page || page.next_after === 0}
        onClick={() => void acknowledge()}
        type="button"
      >
        确认已读
      </button>
      {message ? <span aria-live="polite" role="status">{message}</span> : null}
    </section>
  );
}
