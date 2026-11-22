import { describe, expect, it } from "vitest";
import {
  documentFromText,
  rebuildPromptDocument,
  validatePromptDocument,
} from "./promptDocument";

const FIRST_BLOCK_ID = "11111111-1111-4111-8111-111111111111";
const SECOND_BLOCK_ID = "22222222-2222-4222-8222-222222222222";

describe("prompt document", () => {
  it("uses browser UTF-16 offsets", () => {
    const document = documentFromText("人🙂物", FIRST_BLOCK_ID);

    expect(document.rendered_text).toBe("人🙂物");
    expect(document.spans).toEqual([
      { block_id: FIRST_BLOCK_ID, start_utf16: 0, end_utf16: 4 },
    ]);
  });

  it("renders ordered blocks with stable newline separators", () => {
    const document = rebuildPromptDocument([
      { id: FIRST_BLOCK_ID, kind: "SUBJECT", text: "主体🙂", locked: true },
      { id: SECOND_BLOCK_ID, kind: "MOTION", text: "向前", locked: false },
    ]);

    expect(document.rendered_text).toBe("主体🙂\n向前");
    expect(document.spans).toEqual([
      { block_id: FIRST_BLOCK_ID, start_utf16: 0, end_utf16: 4 },
      { block_id: SECOND_BLOCK_ID, start_utf16: 5, end_utf16: 7 },
    ]);
    expect(validatePromptDocument(document)).toEqual({ ok: true });
  });

  it("rejects duplicate block ids and rendered-text drift", () => {
    const duplicate = rebuildPromptDocument([
      { id: FIRST_BLOCK_ID, kind: "SUBJECT", text: "A", locked: false },
      { id: FIRST_BLOCK_ID, kind: "MOTION", text: "B", locked: false },
    ]);
    expect(validatePromptDocument(duplicate)).toMatchObject({ ok: false });

    const drifted = {
      ...documentFromText("原文", FIRST_BLOCK_ID),
      rendered_text: "被篡改",
    };
    expect(validatePromptDocument(drifted)).toMatchObject({ ok: false });
  });
});
