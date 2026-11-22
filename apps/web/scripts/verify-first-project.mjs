/** Exercise Windows folder-drop startup, with a new database and real files.
 * Run from any directory: node apps/web/scripts/verify-first-project.mjs
 */
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import { chromium, expect } from "@playwright/test";
const root = fileURLToPath(new URL("../../..", import.meta.url));
const scratch = path.join(root, ".local");
await fs.mkdir(scratch, { recursive: true });
const run = await fs.mkdtemp(path.join(scratch, "first-project-"));
const parent = path.join(run, "中文 父目录");
await fs.mkdir(parent);
const listener = createServer();
await new Promise((resolve, reject) =>
  listener.on("error", reject).listen(0, "127.0.0.1", resolve),
);
const port = listener.address().port;
await new Promise((resolve) => listener.close(resolve));
const origin = "http://127.0.0.1:" + port,
  instance = randomUUID(),
  token = randomUUID();
const proc = spawn(process.env.ComSpec, ["/d", "/c", "start.cmd", parent], {
  cwd: root,
  windowsHide: true,
  stdio: ["ignore", "pipe", "pipe"],
  env: {
    ...process.env,
    AI_CANVAS_DATA_DIR: path.join(run, "database"),
    AI_CANVAS_BOOTSTRAP_TOKEN: token,
    AI_CANVAS_INSTANCE_ID: instance,
    AI_CANVAS_OPEN_BROWSER: "false",
    AI_CANVAS_LISTEN_PORT: String(port),
  },
});
let tail = "";
const capture = (b) => (tail = (tail + String(b)).slice(-1500));
proc.stdout.on("data", capture);
proc.stderr.on("data", capture);
let browser;
try {
  await expect
    .poll(
      async () => {
        if (proc.exitCode !== null) throw Error("启动器失败：" + tail);
        try {
          return (await fetch(origin + "/api/v1/health")).headers.get(
            "x-ai-canvas-instance",
          );
        } catch {
          return null;
        }
      },
      { timeout: 210000 },
    )
    .toBe(instance);
  browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(origin + "/#bootstrap=" + token);
  await page.getByLabel("项目名称").fill("第一部 短片");
  const receipt = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/handoff/projects") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建镜头项目", exact: true }).click();
  const created = await receipt;
  assert.equal(created.status(), 201);
  await page
    .getByRole("heading", { name: "第一部 短片", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "新建第一个镜头" }).click();
  const shot = page.getByRole("dialog", { name: "新建镜头" });
  await shot.getByRole("textbox").fill("第一镜");
  await shot.getByRole("button", { name: "创建", exact: true }).click();
  await page.getByRole("heading", { name: "第一镜", exact: true }).waitFor();
  await page.getByRole("button", { name: /新建组/ }).click();
  const group = page.getByRole("dialog", { name: "新建生成组" });
  await group.getByRole("textbox").fill("第一组");
  await group.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("button", { name: /第一组/ })).toBeVisible();
  const groupDirectory = path.join(
    parent,
    "第一部 短片",
    "EP01",
    "SH01",
    "G01",
  );
  const files = (await fs.readdir(groupDirectory)).sort();
  assert.deepEqual(
    files,
    ["参考素材", "生成结果", "提示词.txt", "生成配置.txt"].sort(),
  );
  for (const f of ["提示词.txt", "生成配置.txt"])
    assert.equal(await fs.readFile(path.join(groupDirectory, f), "utf8"), "");
  for (const folder of ["参考素材", "生成结果"])
    assert.ok((await fs.stat(path.join(groupDirectory, folder))).isDirectory());
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "第一镜", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /第一组/ })).toBeVisible();
  assert.deepEqual(errors, []);
  const summary = {
    entry: "start.cmd <existing-parent>",
    status: created.status(),
    parent,
    groupDirectory,
    files,
    reopened: true,
    pageErrors: errors,
  };
  await fs.writeFile(
    path.join(run, "verification.json"),
    JSON.stringify(summary, null, 2),
  );
  console.log(JSON.stringify(summary, null, 2));
} finally {
  if (browser) await browser.close();
  if (proc.exitCode === null)
    execFileSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
}
