import {
  type ChangeEvent,
  useContext,
  useRef,
  useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import { MediaIngressContext } from "../mediaIngressContext";
import type {
  IngressBatchCounts,
  IngressSlotSnapshot,
  MediaIngressController,
} from "../mediaIngressTypes";
import { LargeUploadProgress } from "../../mediaLifecycle/LargeUploadProgress";

const sourceLabels = {
  drop: "拖放",
  paste: "粘贴",
  multi_picker: "多文件选择",
} as const;

const acceptedMediaTypes = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "video/mp4",
  "video/webm",
  "video/quicktime",
  "video/x-matroska",
].join(",");
const staleTargetWarning =
  "目标已变化，成功后需决定是否作为新节点放置";

function FailedSlotRow({
  batchId,
  counts,
  controller,
  slot,
}: {
  batchId: string;
  counts: IngressBatchCounts;
  controller: MediaIngressController;
  slot: IngressSlotSnapshot;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const displayName = slot.currentItem.displayName;
  const succeeded = counts.placed + counts.savedUnplaced;
  const continuing = counts.queued + counts.processing;

  const openPicker = () => {
    const input = inputRef.current;
    if (!input) return;
    input.value = "";
    input.click();
  };

  const handleSelection = (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const file = input.files?.[0] ?? null;
    input.value = "";
    if (file) controller.reselect(batchId, slot.slotId, file);
  };

  return (
    <li
      aria-label={`失败项 ${displayName}`}
      className="media-ingress-failed-item"
    >
      <div className="media-ingress-failed-item__heading">
        <span aria-hidden="true" className="media-ingress-state-badge">
          失败
        </span>
        <bdi dir="auto">{displayName}</bdi>
      </div>
      <p className="media-ingress-failed-item__error">
        {slot.currentItem.errorMessage ?? "导入失败，请重新选择文件或忽略此项。"}
      </p>
      <p className="media-ingress-failed-item__guidance">
        <span>此项导入失败。</span>
        <span>
          本批已成功 {succeeded} 项（已放置 {counts.placed} 项，待放置{" "}
          {counts.savedUnplaced} 项）；
          {continuing > 0
            ? `另有 ${continuing} 项继续处理`
            : "其他任务均已结束"}
          ；可重新选择文件，或删除本地素材并忽略此项。
        </span>
      </p>
      <div className="media-ingress-failed-item__actions">
        <input
          accept={acceptedMediaTypes}
          aria-label={`为 ${displayName} 重新选择文件`}
          className="media-ingress-reselect-input"
          onChange={handleSelection}
          ref={inputRef}
          tabIndex={-1}
          type="file"
        />
        <button
          disabled={!slot.canReselect}
          onClick={openPicker}
          type="button"
        >
          {`重新选择 ${displayName}`}
        </button>
        <button
          disabled={!slot.canIgnore}
          onClick={() => controller.ignoreSlot(batchId, slot.slotId)}
          type="button"
        >
          {`删除本地素材并忽略 ${displayName}`}
        </button>
      </div>
    </li>
  );
}

function ActionSlotRow({
  batchId,
  controller,
  slot,
}: {
  batchId: string;
  controller: MediaIngressController;
  slot: IngressSlotSnapshot;
}) {
  const item = slot.currentItem;
  if (item.state === "CANCELLING") {
    return (
      <li
        aria-label={`取消确认项 ${item.displayName}`}
        className="media-ingress-failed-item"
      >
        <div className="media-ingress-failed-item__heading">
          <span aria-hidden="true" className="media-ingress-state-badge">
            取消中
          </span>
          <bdi dir="auto">{item.displayName}</bdi>
        </div>
        <p>正在确认取消结果</p>
        {item.errorMessage ? (
          <p className="media-ingress-failed-item__error">
            {item.errorMessage}
          </p>
        ) : null}
        {item.canRetryCancellation || item.canForceRefresh ? (
          <div className="media-ingress-failed-item__actions">
            {item.canRetryCancellation ? (
              <button
                onClick={() =>
                  controller.retryCancellation(batchId, item.itemId)
                }
                type="button"
              >
                {`重试确认 ${item.displayName}`}
              </button>
            ) : null}
            {item.canForceRefresh ? (
              <button
                aria-label="强制刷新并放弃内存批次"
                onClick={() => controller.requestForceRefresh()}
                type="button"
              >
                强制刷新并放弃内存批次
              </button>
            ) : null}
          </div>
        ) : null}
      </li>
    );
  }
  if (item.state === "DISCARDING") {
    return (
      <li
        aria-label={`删除本地素材项 ${item.displayName}`}
        className="media-ingress-failed-item"
      >
        <div className="media-ingress-failed-item__heading">
          <span aria-hidden="true" className="media-ingress-state-badge">
            删除中
          </span>
          <bdi dir="auto">{item.displayName}</bdi>
        </div>
        {item.errorMessage ? (
          <p className="media-ingress-failed-item__error">
            {item.errorMessage}
          </p>
        ) : null}
        <p>
          {item.discardErrorMessage ??
            "正在删除本地素材，请勿关闭工作台"}
        </p>
        {item.canRetryDiscard ? (
          <div className="media-ingress-failed-item__actions">
            <button
              onClick={() =>
                controller.retryDiscard(batchId, slot.slotId)
              }
              type="button"
            >
              {`重试删除本地素材 ${item.displayName}`}
            </button>
          </div>
        ) : null}
      </li>
    );
  }
  if (item.state === "SAVED_UNPLACED") {
    const ignoreActionLabel = item.resumeScopeMessage
      ? `标记待清理并忽略 ${item.displayName}`
      : `删除本地素材并忽略 ${item.displayName}`;
    return (
      <li
        aria-label={`待放置项 ${item.displayName}`}
        className="media-ingress-failed-item"
      >
        <div className="media-ingress-failed-item__heading">
          <span aria-hidden="true" className="media-ingress-state-badge">
            待放置
          </span>
          <bdi dir="auto">{item.displayName}</bdi>
        </div>
        <p>素材已保存，但尚未放入画布</p>
        {item.errorMessage ? (
          <p className="media-ingress-failed-item__error">
            {item.errorMessage}
          </p>
        ) : null}
        <div className="media-ingress-failed-item__actions media-ingress-saved-unplaced-actions">
          <button
            aria-label={`放入画布 ${item.displayName}`}
            className="media-ingress-saved-unplaced-action"
            disabled={!slot.canPlaceSavedUnplaced}
            onClick={() =>
              controller.placeSavedUnplaced(batchId, slot.slotId)
            }
            title={`放入画布 ${item.displayName}`}
            type="button"
          >
            放入画布
          </button>
          <button
            aria-label={ignoreActionLabel}
            className="media-ingress-saved-unplaced-action"
            disabled={!slot.canIgnore}
            onClick={() => controller.ignoreSlot(batchId, slot.slotId)}
            title={ignoreActionLabel}
            type="button"
          >
            {item.resumeScopeMessage ? "标记待清理" : "删除本地素材"}
          </button>
        </div>
      </li>
    );
  }
  const queued = item.state === "QUEUED";
  return (
    <li
      aria-label={`${queued ? "排队项" : "处理中项"} ${item.displayName}`}
      className="media-ingress-failed-item"
    >
      <div className="media-ingress-failed-item__heading">
        <span aria-hidden="true" className="media-ingress-state-badge">
          {queued ? "排队中" : "处理中"}
        </span>
        <bdi dir="auto">{item.displayName}</bdi>
      </div>
      {item.resumeScopeMessage ? (
        <p className="media-ingress-failed-item__guidance">
          {item.resumeScopeMessage}
        </p>
      ) : null}
      {item.largeUploadProgress ? (
        <LargeUploadProgress
          estimatedSecondsRemaining={
            item.largeUploadProgress.estimatedSecondsRemaining
          }
          fileName={item.displayName}
          receivedBytes={item.largeUploadProgress.receivedByteSize}
          totalBytes={item.largeUploadProgress.totalByteSize}
        />
      ) : null}
      <button
        disabled={!item.canCancel}
        onClick={() => controller.cancelItem(batchId, item.itemId)}
        type="button"
      >
        {`取消 ${item.displayName}`}
      </button>
    </li>
  );
}

export function BatchResultCard() {
  const ingress = useContext(MediaIngressContext);
  const controller = ingress?.controller;
  const subscribe = (listener: () => void) =>
    controller?.subscribe(listener) ?? (() => undefined);
  const getSnapshot = () => controller?.getSnapshot() ?? null;
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  if (!snapshot || !controller || snapshot.batches.length === 0) return null;

  const batches = [...snapshot.batches].sort(
    (a, b) => b.createdOrder - a.createdOrder,
  );
  const aggregate = batches.reduce(
    (counts, batch) => ({
      terminal: counts.terminal + batch.counts.terminal,
      total: counts.total + batch.counts.total,
      failed: counts.failed + batch.counts.failed,
    }),
    { terminal: 0, total: 0, failed: 0 },
  );
  const discardAnnouncements = [
    ...new Set(
      batches.flatMap((batch) =>
        batch.slots
          .filter((slot) => slot.currentItem.state === "DISCARDING")
          .map(
            (slot) =>
              slot.currentItem.discardErrorMessage ??
              "正在删除本地素材，请勿关闭工作台",
          ),
      ),
    ),
  ];

  return createPortal(
    <section
      aria-label="媒体导入批次中心"
      className="media-ingress-batches"
    >
      <header className="media-ingress-batches__header">
        <strong>媒体导入</strong>
        <p aria-atomic="true" aria-live="polite" role="status">
          共 {batches.length} 个批次 · 完成 {aggregate.terminal} /{" "}
          {aggregate.total} · 失败 {aggregate.failed}
          {discardAnnouncements.length > 0
            ? ` · 删除状态：${discardAnnouncements.join("；")}`
            : null}
        </p>
      </header>
      {batches.map((batch) => {
        const failedSlots = batch.slots.filter(
          (slot) => slot.currentItem.state === "FAILED",
        );
        const noticeSlots = batch.slots.filter(
          (slot) =>
            slot.currentItem.state !== "FAILED" &&
            slot.currentItem.state !== "IGNORED" &&
            slot.currentItem.state !== "CANCELLED" &&
            slot.currentItem.state !== "PLACED" &&
            slot.currentItem.state !== "SAVED_UNPLACED" &&
            slot.currentItem.errorMessage?.includes(staleTargetWarning),
        );
        const actionSlots = batch.slots.filter(
          (slot) =>
            slot.currentItem.canCancel ||
            slot.currentItem.state === "CANCELLING" ||
            slot.currentItem.state === "DISCARDING" ||
            slot.currentItem.state === "SAVED_UNPLACED",
        );
        const destructiveClose = batch.counts.failed > 0;
        return (
          <article
            aria-label={`批次 ${batch.batchId}`}
            className="media-ingress-batch"
            key={batch.batchId}
            role="group"
          >
            <header>
              <span>
                {sourceLabels[batch.source]} · {batch.batchId}
              </span>
              <button
                aria-label={`${batch.collapsed ? "展开" : "折叠"}批次 ${batch.batchId}`}
                onClick={() =>
                  controller.toggleBatchCollapsed(batch.batchId)
                }
                type="button"
              >
                {batch.collapsed ? "▸" : "▾"}
              </button>
              {batch.canStop ? (
                <button
                  aria-label={`停止剩余任务 ${batch.batchId}`}
                  onClick={() => controller.stopBatch(batch.batchId)}
                  type="button"
                >
                  停止剩余任务
                </button>
              ) : null}
              {batch.canClose ? (
                <button
                  aria-label={
                    destructiveClose
                      ? `删除失败项本地素材并关闭批次 ${batch.batchId}`
                      : `关闭批次 ${batch.batchId}`
                  }
                  onClick={() => controller.closeBatch(batch.batchId)}
                  type="button"
                >
                  {destructiveClose ? "删除失败项本地素材并关闭批次" : "×"}
                </button>
              ) : null}
            </header>
            {!batch.collapsed ? (
              <div className="media-ingress-batch__details">
                <p className="media-ingress-batch__progress">
                  完成 {batch.counts.terminal} / {batch.counts.total}
                </p>
                <p className="media-ingress-batch__states">
                  ⏳ 排队 {batch.counts.queued} · ⚙️ 处理中{" "}
                  {batch.counts.processing} · ✅ 已放置 {batch.counts.placed} ·
                  ⚠️ 失败 {batch.counts.failed} · ⛔ 已取消{" "}
                  {batch.counts.cancelled} · 📌 待放置{" "}
                  {batch.counts.savedUnplaced} · ◌ 已忽略{" "}
                  {batch.counts.ignored}
                </p>
                {actionSlots.length > 0 ? (
                  <ul
                    aria-label={`批次 ${batch.batchId} 的可操作项`}
                    className="media-ingress-failed-list"
                  >
                    {actionSlots.map((slot) => (
                      <ActionSlotRow
                        batchId={batch.batchId}
                        controller={controller}
                        key={slot.slotId}
                        slot={slot}
                      />
                    ))}
                  </ul>
                ) : null}
                {failedSlots.length > 0 ? (
                  <ul
                    aria-label={`批次 ${batch.batchId} 的失败项`}
                    className="media-ingress-failed-list"
                  >
                    {failedSlots.map((slot) => (
                      <FailedSlotRow
                        batchId={batch.batchId}
                        counts={batch.counts}
                        controller={controller}
                        key={slot.slotId}
                        slot={slot}
                      />
                    ))}
                  </ul>
                ) : null}
                {noticeSlots.length > 0 ? (
                  <ul
                    aria-label={`批次 ${batch.batchId} 的放置提醒`}
                    className="media-ingress-notice-list"
                  >
                    {noticeSlots.map((slot) => (
                      <li key={slot.slotId}>
                        <bdi dir="auto">
                          {slot.currentItem.displayName}
                        </bdi>
                        <p>{slot.currentItem.errorMessage}</p>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </article>
        );
      })}
    </section>,
    document.body,
  );
}
