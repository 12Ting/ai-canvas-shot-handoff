import { inferRequestedMediaKind } from "../../../api/client";
import type { IngressCandidate } from "./mediaIngressTypes";

export const MAX_INGRESS_ITEMS = 20;
export const MAX_INGRESS_BATCH_BYTES = 100 * 1024 * 1024 * 1024;
export const MAX_INGRESS_ITEM_BYTES = 10 * 1024 * 1024 * 1024;

export type BatchPreflight =
  | Readonly<{ kind: "accepted" }>
  | Readonly<{ kind: "empty" }>
  | Readonly<{ kind: "rejected"; message: string }>;

type TransferInput = Pick<DataTransfer, "items" | "files">;
type TransferEntry = Readonly<{
  isDirectory: boolean;
  name?: string;
}>;
type EntryAwareTransferItem = DataTransferItem & {
  getAsEntry?: () => TransferEntry | null;
  webkitGetAsEntry?: () => TransferEntry | null;
};

function asFileCandidate(file: File): IngressCandidate {
  return { kind: "file", file, displayName: file.name };
}

function entryError(
  displayName: string,
  code: "folder_not_supported" | "file_unavailable",
  message: string,
): IngressCandidate {
  return { kind: "entry_error", displayName, code, message };
}

function transferEntry(item: DataTransferItem): TransferEntry | null {
  const entryAware = item as EntryAwareTransferItem;
  const getEntry =
    typeof entryAware.getAsEntry === "function"
      ? entryAware.getAsEntry
      : entryAware.webkitGetAsEntry;
  if (typeof getEntry !== "function") return null;
  try {
    return getEntry.call(item);
  } catch {
    return null;
  }
}

function transferredFile(item: DataTransferItem): File | null {
  try {
    return item.getAsFile();
  } catch {
    return null;
  }
}

function isGenuineBlob(value: unknown): value is Blob {
  if (
    typeof Blob === "undefined" ||
    value === null ||
    (typeof value !== "object" && typeof value !== "function")
  ) {
    return false;
  }
  try {
    Blob.prototype.slice.call(value, 0, 0);
    return true;
  } catch {
    return false;
  }
}

function normalizedMediaType(value: string): string {
  return value.trim().toLowerCase();
}

const CLIPBOARD_IMAGE_EXTENSIONS: Readonly<Record<string, string>> =
  Object.freeze({
    "image/avif": "avif",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/svg+xml": "svg",
    "image/tiff": "tif",
    "image/vnd.microsoft.icon": "ico",
    "image/webp": "webp",
    "image/x-icon": "ico",
  });

function fileMetadata(blob: Blob): Readonly<{
  name: string;
  lastModified: number;
}> | null {
  if (typeof File === "undefined") return null;
  const nameGetter = Object.getOwnPropertyDescriptor(
    File.prototype,
    "name",
  )?.get;
  const lastModifiedGetter = Object.getOwnPropertyDescriptor(
    File.prototype,
    "lastModified",
  )?.get;
  if (!nameGetter || !lastModifiedGetter) return null;
  try {
    return {
      name: String(nameGetter.call(blob)),
      lastModified: Number(lastModifiedGetter.call(blob)),
    };
  } catch {
    return null;
  }
}

function clipboardTimestamp(value: Date): string {
  const pad = (part: number, width = 2) =>
    String(part).padStart(width, "0");
  return [
    pad(value.getFullYear(), 4),
    pad(value.getMonth() + 1),
    pad(value.getDate()),
    "-",
    pad(value.getHours()),
    pad(value.getMinutes()),
    pad(value.getSeconds()),
  ].join("");
}

function clipboardFilename(
  mediaType: string,
  timestamp: () => string,
): string {
  const extension = CLIPBOARD_IMAGE_EXTENSIONS[mediaType] ?? "img";
  return `clipboard-${timestamp()}.${extension}`;
}

function clipboardFile(
  blob: Blob,
  mediaType: string,
  timestamp: () => string,
): File {
  const metadata = fileMetadata(blob);
  const hasSafeName = metadata !== null && metadata.name.trim() !== "";
  if (
    metadata !== null &&
    hasSafeName &&
    normalizedMediaType(blob.type) === mediaType
  ) {
    return blob as File;
  }
  return new File(
    [blob],
    hasSafeName
      ? metadata.name
      : clipboardFilename(mediaType, timestamp),
    {
      type: mediaType,
      lastModified:
        metadata && Number.isFinite(metadata.lastModified)
          ? metadata.lastModified
          : undefined,
    },
  );
}

function clipboardBlob(
  value: unknown,
  mediaTypeHint: string,
  timestamp: () => string,
): File | null {
  if (!isGenuineBlob(value)) return null;
  const blobType = normalizedMediaType(value.type);
  const mediaType = blobType || normalizedMediaType(mediaTypeHint);
  if (!mediaType.startsWith("image/")) return null;
  return clipboardFile(value, mediaType, timestamp);
}

function fileKindItems(items: DataTransferItemList): DataTransferItem[] {
  return Array.from(items).filter((item) => item.kind === "file");
}

export function candidatesFromPicker(
  files: ArrayLike<File> | readonly File[],
): IngressCandidate[] {
  return Array.from(files, asFileCandidate);
}

function candidatesFromTransfer(input: TransferInput): IngressCandidate[] {
  const items = fileKindItems(input.items);
  if (items.length === 0) {
    return Array.from(input.files).map(asFileCandidate);
  }

  const candidates: IngressCandidate[] = [];
  items.forEach((item) => {
    const entry = transferEntry(item);
    if (entry?.isDirectory) {
      const displayName = entry.name?.trim() || "未命名文件夹";
      candidates.push(
        entryError(
          displayName,
          "folder_not_supported",
          `不支持导入文件夹“${displayName}”`,
        ),
      );
      return;
    }

    const file = transferredFile(item);
    if (file === null) {
      candidates.push(
        entryError(
          "无法取得的文件",
          "file_unavailable",
          "无法从此项目取得文件",
        ),
      );
      return;
    }
    candidates.push(asFileCandidate(file));
  });
  return candidates;
}

export function candidatesFromDrop(input: TransferInput): IngressCandidate[] {
  return candidatesFromTransfer(input);
}

export function candidatesFromClipboard(
  input: TransferInput,
  now: () => Date = () => new Date(),
): IngressCandidate[] {
  let sampledTimestamp: string | null = null;
  const timestamp = () => {
    sampledTimestamp ??= clipboardTimestamp(now());
    return sampledTimestamp;
  };
  const items = fileKindItems(input.items);
  if (items.length > 0) {
    const candidates: IngressCandidate[] = [];
    for (const item of items) {
      let value: unknown = null;
      try {
        value = item.getAsFile();
      } catch {
        value = null;
      }
      const file = clipboardBlob(value, item.type, timestamp);
      if (file) candidates.push(asFileCandidate(file));
    }
    return candidates;
  }

  const candidates: IngressCandidate[] = [];
  for (const value of Array.from(input.files)) {
    const file = clipboardBlob(value, "", timestamp);
    if (file) candidates.push(asFileCandidate(file));
  }
  return candidates;
}

export function preflightBatch(candidates: readonly IngressCandidate[]): BatchPreflight {
  if (candidates.length === 0) return { kind: "empty" };
  if (candidates.length > MAX_INGRESS_ITEMS) {
    return {
      kind: "rejected",
      message: `本次共 ${candidates.length} 项，上限为 ${MAX_INGRESS_ITEMS} 项，请拆分导入`,
    };
  }
  const bytes = candidates.reduce(
    (total, candidate) => total + (candidate.kind === "file" ? candidate.file.size : 0),
    0,
  );
  if (!Number.isSafeInteger(bytes) || bytes > MAX_INGRESS_BATCH_BYTES) {
    return {
      kind: "rejected",
      message: `本次总大小为 ${bytes} 字节，上限为 ${MAX_INGRESS_BATCH_BYTES} 字节，请拆分导入`,
    };
  }
  return { kind: "accepted" };
}

export function safeDisplayName(value: string): string {
  const normalized = value.replaceAll("\\", "/");
  return normalized.split("/").at(-1)?.trim() || "未命名文件";
}

export function validateCandidate(candidate: IngressCandidate): Readonly<{
  displayName: string;
  mediaKind: "image" | "video" | null;
  errorMessage: string | null;
}> {
  const displayName = safeDisplayName(candidate.displayName);
  if (candidate.kind === "entry_error") {
    return { displayName, mediaKind: null, errorMessage: candidate.message };
  }
  if (candidate.file.size > MAX_INGRESS_ITEM_BYTES) {
    return { displayName, mediaKind: null, errorMessage: "单个文件不能超过 10 GiB" };
  }
  const declaredType = candidate.file.type.trim().toLowerCase();
  const mediaKind =
    inferRequestedMediaKind(candidate.file) ??
    (declaredType.startsWith("image/") || /\.gif$/i.test(candidate.file.name)
      ? "image"
      : declaredType.startsWith("video/")
        ? "video"
        : null);
  return {
    displayName,
    mediaKind,
    errorMessage: mediaKind === null ? "不支持此文件类型" : null,
  };
}
