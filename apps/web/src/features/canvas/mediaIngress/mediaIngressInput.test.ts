import { describe, expect, it, vi } from "vitest";
import {
  MAX_INGRESS_BATCH_BYTES,
  MAX_INGRESS_ITEM_BYTES,
  MAX_INGRESS_ITEMS,
  candidatesFromClipboard,
  candidatesFromDrop,
  candidatesFromPicker,
  preflightBatch,
  validateCandidate,
} from "./mediaIngressInput";
import type { IngressCandidate } from "./mediaIngressTypes";

function fileStub(
  name: string,
  size = 4,
  type = "image/png",
): Pick<File, "name" | "size" | "type"> {
  return { name, size, type };
}

function fileCandidate(
  name: string,
  size = 4,
  type = "image/png",
): IngressCandidate {
  const file = fileStub(name, size, type) as File;
  return { kind: "file", file, displayName: name };
}

function item(
  kind: DataTransferItem["kind"],
  type: string,
  getAsFile: () => File | Blob | null,
  entry?: Readonly<{ isDirectory: boolean; name: string }>,
): DataTransferItem {
  return {
    kind,
    type,
    getAsFile,
    webkitGetAsEntry: entry === undefined ? undefined : () => entry,
  } as unknown as DataTransferItem;
}

function transfer(
  items: readonly DataTransferItem[],
  files: readonly File[],
): Pick<DataTransfer, "items" | "files"> {
  return {
    items: items as unknown as DataTransferItemList,
    files: files as unknown as FileList,
  };
}

describe("media ingress input adapters", () => {
  it("normalizes picker files in their original order", () => {
    const first = fileStub("first.png") as File;
    const second = fileStub("second.webm", 8, "video/webm") as File;

    expect(candidatesFromPicker([first, second])).toEqual([
      { kind: "file", file: first, displayName: "first.png" },
      { kind: "file", file: second, displayName: "second.webm" },
    ]);
  });

  it("treats file-kind transfer items as authoritative and preserves their order", () => {
    const calls: string[] = [];
    const first = fileStub("first.png") as File;
    const second = fileStub("second.png") as File;
    const filesOnly = fileStub("files-only.png") as File;
    const data = transfer(
      [
        item("string", "text/plain", () => null),
        item("file", "image/png", () => {
          calls.push("first");
          return first;
        }),
        item("file", "image/png", () => {
          calls.push("second");
          return second;
        }),
      ],
      [first, second, filesOnly],
    );

    expect(candidatesFromDrop(data)).toEqual([
      { kind: "file", file: first, displayName: "first.png" },
      { kind: "file", file: second, displayName: "second.png" },
    ]);
    expect(calls).toEqual(["first", "second"]);
  });

  it("falls back to FileList order only when there are no file-kind items", () => {
    const first = fileStub("first.png") as File;
    const second = fileStub("second.png") as File;
    const data = transfer([item("string", "text/plain", () => null)], [
      first,
      second,
    ]);

    expect(candidatesFromDrop(data)).toEqual([
      { kind: "file", file: first, displayName: "first.png" },
      { kind: "file", file: second, displayName: "second.png" },
    ]);
  });

  it("keeps top-level folders and unavailable files as ordered error slots", () => {
    const valid = fileStub("valid.png") as File;
    const data = transfer(
      [
        item("file", "", () => null, { isDirectory: true, name: "frames" }),
        item("file", "image/png", () => null),
        item("file", "image/png", () => valid),
      ],
      [],
    );

    const candidates = candidatesFromDrop(data);
    expect(candidates).toHaveLength(3);
    expect(candidates[0]).toMatchObject({
      kind: "entry_error",
      displayName: "frames",
      code: "folder_not_supported",
    });
    expect(candidates[1]).toMatchObject({
      kind: "entry_error",
      code: "file_unavailable",
    });
    expect(candidates[2]).toEqual({
      kind: "file",
      file: valid,
      displayName: "valid.png",
    });
  });

  it("accepts only genuine image blobs from clipboard items and never duplicates FileList", () => {
    const image = new File(["image"], "clipboard.png", { type: "image/png" });
    const nonImage = new File(["text"], "notes.txt", { type: "text/plain" });
    const fakeImage = fileStub("pretend.png") as File;
    const fallback = new File(["fallback"], "fallback.png", {
      type: "image/png",
    });
    const data = transfer(
      [
        item("file", "image/png", () => image),
        item("file", "image/png", () => fakeImage),
        item("file", "text/plain", () => nonImage),
      ],
      [image, fallback],
    );

    expect(candidatesFromClipboard(data)).toEqual([
      { kind: "file", file: image, displayName: "clipboard.png" },
    ]);
  });

  it("keeps file-kind clipboard items authoritative even when none is an admissible image", () => {
    const text = new File(["text"], "notes.txt", { type: "text/plain" });
    const fallback = new File(["fallback"], "fallback.png", {
      type: "image/png",
    });
    const data = transfer(
      [
        item("file", "text/plain", () => text),
        item("file", "image/png", () => null),
      ],
      [fallback],
    );

    expect(candidatesFromClipboard(data)).toEqual([]);
  });

  it("falls back to genuine image files in FileList order only with zero file-kind items", () => {
    const first = new File(["first"], "first.png", { type: "image/png" });
    const text = new File(["text"], "notes.txt", { type: "text/plain" });
    const second = new File(["second"], "second.webp", {
      type: "image/webp",
    });
    const data = transfer(
      [item("string", "text/plain", () => null)],
      [first, text, second],
    );

    expect(candidatesFromClipboard(data)).toEqual([
      { kind: "file", file: first, displayName: "first.png" },
      { kind: "file", file: second, displayName: "second.webp" },
    ]);
  });

  it("preserves an image item MIME hint when its genuine File has an empty type", async () => {
    const image = new File(["clipboard-bytes"], "clipboard-capture", {
      type: "",
    });
    const data = transfer([item("file", "image/png", () => image)], [image]);

    const candidates = candidatesFromClipboard(data);

    expect(candidates).toHaveLength(1);
    const [candidate] = candidates;
    expect(candidate.kind).toBe("file");
    if (candidate.kind !== "file") return;
    expect(candidate.displayName).toBe("clipboard-capture");
    expect(candidate.file.name).toBe("clipboard-capture");
    expect(candidate.file.type).toBe("image/png");
    expect(candidate.file.size).toBe(image.size);
    expect(await candidate.file.text()).toBe("clipboard-bytes");
    expect(validateCandidate(candidate)).toMatchObject({
      displayName: "clipboard-capture",
      mediaKind: "image",
      errorMessage: null,
    });
  });

  it("ignores an unavailable clipboard image item instead of creating a non-image slot", () => {
    const data = transfer([item("file", "image/png", () => null)], []);

    expect(candidatesFromClipboard(data)).toEqual([]);
  });

  it("wraps an unnamed image Blob with a clock-based safe display filename", async () => {
    const blob = new Blob(["clipboard-bytes"], { type: "image/png" });
    const data = transfer([item("file", "image/png", () => blob)], []);

    const candidates = candidatesFromClipboard(
      data,
      () => new Date(2026, 6, 22, 14, 30, 15),
    );

    expect(candidates).toHaveLength(1);
    const [candidate] = candidates;
    expect(candidate.kind).toBe("file");
    if (candidate.kind !== "file") return;
    expect(candidate.displayName).toBe("clipboard-20260722-143015.png");
    expect(candidate.file).toBeInstanceOf(File);
    expect(candidate.file.name).toBe("clipboard-20260722-143015.png");
    expect(candidate.file.type).toBe("image/png");
    expect(await candidate.file.text()).toBe("clipboard-bytes");
  });

  it("replaces an empty File name with the same clock-based image filename", () => {
    const unnamed = new File(["clipboard-bytes"], "", { type: "image/webp" });
    const data = transfer([item("file", "image/webp", () => unnamed)], []);

    const [candidate] = candidatesFromClipboard(
      data,
      () => new Date(2026, 6, 22, 14, 30, 15),
    );

    expect(candidate).toMatchObject({
      kind: "file",
      displayName: "clipboard-20260722-143015.webp",
    });
  });

  it("samples the injected clock once for multiple unnamed images and uses safe MIME extensions", () => {
    const data = transfer(
      [
        item(
          "file",
          "image/png",
          () => new Blob(["first"], { type: "image/png" }),
        ),
        item(
          "file",
          "image/jpeg",
          () => new Blob(["second"], { type: "image/jpeg" }),
        ),
      ],
      [],
    );
    const now = vi
      .fn<() => Date>()
      .mockReturnValueOnce(new Date(2026, 6, 22, 14, 30, 15))
      .mockReturnValueOnce(new Date(2030, 0, 1, 0, 0, 0));

    const candidates = candidatesFromClipboard(data, now);

    expect(now).toHaveBeenCalledOnce();
    expect(candidates.map((candidate) => candidate.displayName)).toEqual([
      "clipboard-20260722-143015.png",
      "clipboard-20260722-143015.jpg",
    ]);
  });
});

describe("preflightBatch", () => {
  it("accepts 20 slots and rejects 21 error slots with actual and limit values", () => {
    const errorCandidate: IngressCandidate = {
      kind: "entry_error",
      displayName: "folder",
      code: "folder_not_supported",
      message: "folder",
    };

    expect(
      preflightBatch(Array.from({ length: MAX_INGRESS_ITEMS }, () => errorCandidate)),
    ).toEqual({ kind: "accepted" });
    const rejected = preflightBatch(
      Array.from({ length: MAX_INGRESS_ITEMS + 1 }, () => errorCandidate),
    );
    expect(rejected.kind).toBe("rejected");
    if (rejected.kind === "rejected") {
      expect(rejected.message).toContain(String(MAX_INGRESS_ITEMS + 1));
      expect(rejected.message).toContain(String(MAX_INGRESS_ITEMS));
      expect(rejected.message).toContain("拆分导入");
    }
  });

  it("accepts exactly 536870912 bytes and rejects one byte more without allocating blobs", () => {
    expect(
      preflightBatch([fileCandidate("boundary.png", MAX_INGRESS_BATCH_BYTES)]),
    ).toEqual({ kind: "accepted" });

    const rejected = preflightBatch([
      fileCandidate("over.png", MAX_INGRESS_BATCH_BYTES + 1),
    ]);
    expect(rejected.kind).toBe("rejected");
    if (rejected.kind === "rejected") {
      expect(rejected.message).toContain(String(MAX_INGRESS_BATCH_BYTES + 1));
      expect(rejected.message).toContain(String(MAX_INGRESS_BATCH_BYTES));
      expect(rejected.message).toContain("拆分导入");
    }
  });
});

describe("validateCandidate", () => {
  it("admits a B4 large upload and rejects only the item above 10 GiB", () => {
    expect(
      validateCandidate(fileCandidate("boundary.png", MAX_INGRESS_ITEM_BYTES))
        .errorMessage,
    ).toBeNull();
    expect(
      validateCandidate(fileCandidate("large.mov", 65 * 1024 * 1024, "video/quicktime"))
        .errorMessage,
    ).toBeNull();
    expect(
      validateCandidate(fileCandidate("over.png", MAX_INGRESS_ITEM_BYTES + 1))
        .errorMessage,
    ).toContain("10 GiB");
  });

  it("uses MIME and extension only as media-kind hints, including GIF", () => {
    expect(validateCandidate(fileCandidate("still.gif", 4, "image/gif"))).toMatchObject({
      mediaKind: "image",
      errorMessage: null,
    });
    expect(
      validateCandidate(fileCandidate("animated.png", 4, "image/png")),
    ).toMatchObject({ mediaKind: "image", errorMessage: null });
    expect(
      validateCandidate(fileCandidate("animated.webp", 4, "image/webp")),
    ).toMatchObject({ mediaKind: "image", errorMessage: null });
  });

  it("reduces an untrusted local path to an inert declaration basename", () => {
    expect(
      validateCandidate(
        fileCandidate(
          `..\\private\\<img src=x onerror=alert("clipboard")>.png`,
        ),
      ),
    ).toMatchObject({
      displayName: `<img src=x onerror=alert("clipboard")>.png`,
      mediaKind: "image",
      errorMessage: null,
    });
  });

  it("preserves a local entry error as a failed-slot message", () => {
    expect(
      validateCandidate({
        kind: "entry_error",
        displayName: "folder",
        code: "folder_not_supported",
        message: "不支持导入文件夹",
      }),
    ).toEqual({
      displayName: "folder",
      mediaKind: null,
      errorMessage: "不支持导入文件夹",
    });
  });
});
