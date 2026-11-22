import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("index document metadata", () => {
  const html = readFileSync("index.html", "utf8");

  it("declares the Chinese locale and AI Canvas product title", () => {
    expect(html).toContain('<html lang="zh-CN">');
    expect(html).toContain("<title>AI Canvas 创作工作台</title>");
  });

  it("does not retain the Vite favicon", () => {
    expect(html).not.toContain("favicon.svg");
  });
});
