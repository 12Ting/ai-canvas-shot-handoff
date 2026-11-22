/** Run after handoff_demo.py --out <fresh-directory> and a production web build.
 * node apps/web/scripts/verify-handoff-release.mjs <demo-output> [screenshot.png] [demonstration.mp4]
 * Uses the redistributable paper-boat project, a copy of its database, and the release launcher.
 */
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { chromium, expect } from "@playwright/test";

const root = fileURLToPath(new URL("../../..", import.meta.url));
const api = path.join(root, "apps", "api");
const python = path.join(api, ".venv", "Scripts", "python.exe");
const demo = path.resolve(process.argv[2] ?? "");
const verification = JSON.parse(
  await fs.readFile(path.join(demo, "verification.json"), "utf8"),
);
const seededIssue = JSON.parse(
  await fs.readFile(path.join(demo, "receiver", "shot-issue", "manifest.json"), "utf8"),
).issues[0];
const selectedManifest = JSON.parse(
  await fs.readFile(path.join(demo, "receiver", "selected-shots", "manifest.json"), "utf8"),
);
const selected = selectedManifest.episodes[0].shots[1].selected;
assert.ok(selected?.selection_reason, "请先创建新版纸船选片样例");
assert.equal(
  verification.mode,
  "可再分发的程序合成样例",
  "本验收仅使用纸船样例",
);
const scratch = path.join(root, ".local");
await fs.mkdir(scratch, { recursive: true });
const run = await fs.mkdtemp(path.join(scratch, "handoff-browser-"));
const database = path.join(run, "database");
await fs.cp(path.join(demo, "database"), database, { recursive: true });
const screenshot = path.resolve(
  process.argv[3] ?? path.join(run, "paper-boat-workbench.png"),
);
const movie = process.argv[4] && path.resolve(process.argv[4]);
const hold = (milliseconds) =>
  movie ? delay(milliseconds) : Promise.resolve();
const captions = [];
let recordingStart = 0;
const cue = (text) =>
  captions.push({ text, start: Date.now() - recordingStart });
function timecode(ms) {
  return new Date(ms).toISOString().slice(11, 23).replace(".", ",");
}

async function verifyHover(page, viewport) {
  const viewer = page.locator(".hf-viewer");
  const screens = viewer.locator(".hf-screen");
  const widths = () => screens.evaluateAll(nodes => nodes.map(el => el.getBoundingClientRect().width));
  const moveTo = async (index, fraction = 0.5) => {
    const box = await screens.nth(index).boundingBox();
    await page.mouse.move(box.x + box.width * fraction, box.y + box.height * fraction);
  };
  await viewer.scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await expect.poll(async () => {
    const [left, , right] = await widths();
    return Math.abs(left - right);
  }).toBeLessThan(0.5);
  const baseline = await widths();
  const large = baseline[1];
  assert.ok(large > baseline[0] * 1.5);
  const expectLarge = (index) => expect.poll(async () => Math.abs((await widths())[index] - large)).toBeLessThan(2);

  const player = await screens.first().locator("video").elementHandle();
  const initialFrames = await player.evaluate(async video => {
    video.currentTime = 0;
    video.loop = true;
    await video.play();
    return video.getVideoPlaybackQuality().totalVideoFrames;
  });
  const requests = [];
  const onRequest = r => { if (/\/(play|files|artifact-revisions)\//.test(r.url())) requests.push(r.url()); };
  page.on("request", onRequest);

  // Measure real pointer entry, including intermediate frames where auto-sized rows can jump.
  const probe = await screens.first().evaluateHandle((screen, target) => {
    const original = screen.getBoundingClientRect().width;
    const stage = screen.closest(".hf-viewer");
    const below = stage.nextElementSibling;
    const belowOffset = () => below.getBoundingClientRect().top - stage.getBoundingClientRect().top;
    const offset = belowOffset();
    const result = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("未检测到完整的悬浮放大")), 3000);
      screen.addEventListener("pointerenter", event => {
        const start = event.timeStamp;
        let firstChange;
        let maxShift = 0;
        function frame() {
          const width = screen.getBoundingClientRect().width;
          const elapsed = performance.now() - start;
          if (Math.abs(width - original) > 0.5) firstChange ??= elapsed;
          maxShift = Math.max(maxShift, Math.abs(belowOffset() - offset));
          if (Math.abs(width - target) < 0.5) {
            clearTimeout(timer);
            resolve({ first_change_ms: firstChange, reached_size_ms: elapsed, max_shift_px: maxShift });
          } else if (elapsed < 3000) {
            requestAnimationFrame(frame);
          }
        }
        requestAnimationFrame(frame);
      }, { once: true });
    });
    return { result };
  }, large);
  await moveTo(0, 0.02);
  const timing = await probe.evaluate(p => p.result);
  await probe.dispose();
  assert.ok(timing.max_shift_px < 2, "放大时下方内容发生位移");
  await moveTo(1);
  await expectLarge(1);
  await moveTo(2, 0.98);
  await expectLarge(2);
  for (const index of [0, 1, 2, 0]) await moveTo(index);
  await expectLarge(0);
  await page.mouse.move(0, 0);
  await expectLarge(1);
  assert.ok(await player.evaluate((video, frames) =>
    video.isConnected && !video.paused && video.getVideoPlaybackQuality().totalVideoFrames > frames, initialFrames));
  await player.evaluate(video => { video.pause(); video.loop = false; });
  await player.dispose();
  page.off("request", onRequest);
  assert.deepEqual(requests, [], "悬浮时额外请求了媒体");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await moveTo(2);
  assert.ok(Math.abs((await widths())[2] - large) < 2, "减弱动态效果时应直接放大");
  await page.mouse.move(0, 0);
  await page.setViewportSize({ width: 600, height: 850 });
  await viewer.scrollIntoViewIfNeeded();
  const narrow = await widths();
  await moveTo(0);
  assert.deepEqual(await widths(), narrow, "窄屏双行布局发生了重排");
  await page.mouse.move(0, 0);
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  return timing;
}
const listener = createServer();
await new Promise((resolve, reject) =>
  listener.on("error", reject).listen(0, "127.0.0.1", resolve),
);
const port = listener.address().port;
await new Promise((resolve) => listener.close(resolve));
const origin = "http://127.0.0.1:" + port;
const token = randomUUID();
const instance = randomUUID();
const child = spawn(python, ["-m", "ai_canvas.launcher"], {
  cwd: api,
  windowsHide: true,
  stdio: ["ignore", "pipe", "pipe"],
  env: {
    ...process.env,
    PYTHONPATH: path.join(api, "src"),
    AI_CANVAS_DATA_DIR: database,
    AI_CANVAS_WEB_DIST: path.join(root, "apps/web/dist"),
    AI_CANVAS_LISTEN_PORT: String(port),
    AI_CANVAS_BOOTSTRAP_TOKEN: token,
    AI_CANVAS_OPEN_BROWSER: "false",
    AI_CANVAS_INSTANCE_ID: instance,
    AI_CANVAS_AUTH_MODE: "session",
    AI_CANVAS_RUNTIME_PROFILE: "release",
  },
});
let tail = "";
child.stdout.on("data", (b) => (tail = (tail + String(b)).slice(-1900)));
child.stderr.on("data", (b) => (tail = (tail + String(b)).slice(-1900)));
let browser;
try {
  await expect
    .poll(
      async () => {
        if (child.exitCode !== null)
          throw new Error("发行服务启动失败：" + tail);
        try {
          const r = await fetch(origin + "/api/v1/health");
          return r.headers.get("x-ai-canvas-instance");
        } catch {
          return null;
        }
      },
      { timeout: 25_000 },
    )
    .toBe(instance);
  browser = await chromium.launch();
  const viewport = movie
    ? { width: 1600, height: 842 }
    : { width: 1440, height: 900 };
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 1,
    recordVideo: movie ? { dir: run, size: viewport } : undefined,
  });
  const captureStart = Date.now();
  const page = await context.newPage();
  const errors = [];
  const ranges = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (r) => {
    if (
      new URL(r.url()).pathname.includes("/play/") &&
      r.request().method() === "GET"
    )
      ranges.push(r.status());
  });
  const document = await page.goto(origin + "/#bootstrap=" + token);
  assert.match(
    document.headers()["content-security-policy"],
    /default-src 'self'/,
  );
  const button = (name) => page.getByRole("button", { name, exact: typeof name === "string" });
  const pickGroup = (direction) => page.locator(".hf-groups").getByRole("button", { name: new RegExp(direction) }).click();
  const current = page.locator(".hf-current");
  await button(selectedManifest.name).click();
  await expect(page.getByRole("heading", { name: selectedManifest.name })).toBeVisible();
  let hoverTiming;
  if (!movie) {
    const empty = page.locator(".hf-neighbor").first().locator(".hf-screen");
    await expect(empty).toHaveText("本集无前镜");
    await page.emulateMedia({ reducedMotion: "reduce" });
    const width = (await empty.boundingBox()).width;
    await empty.hover();
    assert.equal((await empty.boundingBox()).width, width, "没有素材的格子占用了大画幅");
    await page.emulateMedia({ reducedMotion: "no-preference" });
  }
  // Set up the recording in the isolated copy; the supplied project stays fully worked.
  await button(/经过灯下/).click();
  await button("编辑采用理由").click();
  await page.getByRole("textbox", { name: "采用理由", exact: true }).fill("");
  await button("保存理由").click();
  await expect(button("编辑采用理由")).toHaveCount(0);
  await button("取消采用").click();
  await expect(button("采用此版")).toBeVisible();
  await button(/补镜清单/).click();
  await button("确认解决").click();
  await expect(page.getByText(seededIssue.note, { exact: true })).toHaveCount(0);
  await button("关闭").click();
  await pickGroup("向左");

  async function play(cell) {
    await cell.getByRole("button", { name: /播放/ }).click();
    const clip = cell.locator("video");
    await expect.poll(() => clip.evaluate(el => el.videoWidth === 192 && el.currentTime > 0)).toBe(true);
    await hold(1100);
    await clip.evaluate(el => el.pause());
  }
  if (!movie) {
    const bytes = await fs.readFile(path.join(demo, "original-media", "SH03-向右.mp4"));
    await button("从结果找镜头").click();
    const transfer = await page.evaluateHandle((values) => {
      const data = new DataTransfer();
      data.items.add(new File([Uint8Array.from(values)], "已改名.mp4", { type: "video/mp4" }));
      return data;
    }, Array.from(bytes));
    await page.locator(".hf-drop").dispatchEvent("drop", { dataTransfer: transfer });
    await transfer.dispose();
    await expect(page.getByRole("heading", { name: "离开水面", exact: true })).toBeVisible();
    await button("从结果找镜头").click();
    await page.getByLabel("源文件路径或文件名").fill("SH02-向左.mp4");
    await button("查找").click();
    await expect(page.getByRole("heading", { name: "经过灯下", exact: true })).toBeVisible();
    await button("生成条件").click();
    await expect(page.getByText("固定机位，纸船向左划过。", { exact: true })).toBeVisible();
    await button("关闭").click();
    await button(/台词与声音/).click();
    const dialogue = page.getByRole("dialog", { name: "台词与声音" });
    await expect(dialogue.getByLabel("本镜台词")).toHaveValue("灯亮了。");
    await dialogue.getByRole("button", { name: /播放/ }).click();
    await expect.poll(() => dialogue.locator("audio").evaluate(el => el.currentTime)).toBeGreaterThan(0);
    await button("关闭").click();
  }
  const before = page.locator(".hf-neighbor:first-child");
  const after = page.locator(".hf-neighbor:last-child");
  for (const cell of [before, current, after])
    await expect.poll(() => cell.locator(".hf-screen img").evaluate(el => el.naturalWidth)).toBeGreaterThan(0);

  recordingStart = Date.now();
  cue("第 2 镜有两个拍法。先看看哪一个能接上前后镜。");
  await hold(1800);
  await play(before);
  if (!movie) hoverTiming = await verifyHover(page, viewport);
  await play(current);
  await pickGroup("向右");
  await expect(current).toContainText(selected.file.name);
  cue("换到向右版，纸船的运动方向连贯。");
  await play(current);
  await play(after);
  cue("点「采用此版」。想让剪辑知道为什么，可以顺手写一句。");
  await button("写采用理由").click();
  await page.getByRole("textbox", { name: "采用理由", exact: true }).fill(selected.selection_reason);
  await hold(1800);
  await button("采用此版").click();
  await expect(current).toContainText("采用版");
  await expect(page.getByText(selected.selection_reason, { exact: false }).first()).toBeVisible();
  await hold(1800);

  // Restore the stills in the workbench screenshot after comparing the moving clips.
  await button(/离开水面/).click();
  await button(/经过灯下/).click();
  for (const cell of [before, current, after])
    await expect.poll(() => cell.locator(".hf-screen img").evaluate(el => el.naturalWidth)).toBeGreaterThan(0);
  await page.screenshot({ path: screenshot });
  cue("向左版还想留作备选。只给这条原片写修改意见。");
  await pickGroup("向左");
  await button("记补镜意见").click();
  const issues = page.getByRole("dialog", { name: "补镜清单" });
  await issues.getByRole("textbox", { name: /SH02-向左.mp4/ }).fill(seededIssue.note);
  await hold(2000);
  await button("记录补镜").click();
  await expect(issues.locator("article").getByText(seededIssue.note, { exact: true })).toBeVisible();
  await hold(1200);
  if (!movie) {
    const download = page.waitForEvent("download");
    await button("导出此问题").click();
    const issueZip = await download;
    assert.equal(await issueZip.failure(), null);
    await issueZip.saveAs(path.join(run, "browser-issue.zip"));
  }
  await button("关闭").click();
  await expect(issues).toHaveCount(0);
  cue("整片交给剪辑，下载这一个选片包就够了。");
  await button("导出交接包").click();
  await hold(1400);
  const save = page.waitForEvent("download");
  await button("下载选片交接包").click();
  const downloaded = await save;
  assert.equal(await downloaded.failure(), null);
  const zip = path.join(run, "browser-selected.zip");
  await downloaded.saveAs(zip);
  execFileSync(python, ["-c", [
    "import sys,zipfile",
    "with zipfile.ZipFile(sys.argv[1]) as z:",
    "    assert z.testzip() is None",
    "    z.extractall(sys.argv[2])",
  ].join("\n"), zip, path.join(run, "receiver")]);
  const received = JSON.parse(await fs.readFile(path.join(run, "receiver", "manifest.json"), "utf8"));
  const second = received.episodes[0].shots[1];
  assert.deepEqual(received.missing, []);
  assert.equal(second.selected.id, selected.id);
  assert.equal(second.selected.selection_reason, selected.selection_reason);
  assert.equal(second.open_issues.length, 1);
  assert.equal(second.open_issues[0].candidate_id, seededIssue.original.id);
  assert.equal(second.open_issues[0].note, seededIssue.note);
  assert.notEqual(second.selected.id, second.open_issues[0].candidate_id);
  await context.setOffline(true);
  await page.goto(pathToFileURL(path.join(run, "receiver", "index.html")).href);
  const shot = page.locator("section").filter({ has: page.getByRole("heading", { name: /经过灯下/ }) });
  await expect(shot).toContainText(selected.file.name);
  await expect(shot).toContainText(selected.selection_reason);
  await expect(shot.locator("li")).toContainText(seededIssue.original.file.name);
  await expect(shot.locator("li")).toContainText(seededIssue.note);
  await shot.locator("h3").evaluate(el => el.scrollIntoView({ block: "start" }));
  cue("接手人离线打开清单：现在用向右版，待改意见指向向左版。");
  const deliveredClip = shot.locator("video").first();
  await deliveredClip.evaluate(el => el.play());
  await expect.poll(() => deliveredClip.evaluate(el => el.currentTime)).toBeGreaterThan(0);
  await hold(3800);
  const finished = Date.now() - recordingStart;
  const video = page.video();
  await context.close();
  if (movie) {
    const source = await video.path();
    const srt = captions
      .map(
        (line, index) =>
          index +
          1 +
          "\n" +
          timecode(line.start) +
          " --> " +
          timecode(captions[index + 1]?.start ?? finished) +
          "\n" +
          line.text +
          "\n",
      )
      .join("\n");
    await fs.writeFile(path.join(run, "captions.srt"), srt, "utf8");
    execFileSync(
      "ffmpeg",
      [
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        String((recordingStart - captureStart) / 1000),
        "-i",
        source,
        "-vf",
        "pad=iw:ih+58:0:0:color=0x252637,subtitles=captions.srt:force_style='FontName=Microsoft YaHei,FontSize=11,Alignment=2,MarginV=4,BorderStyle=1,Outline=0,Shadow=0'",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        movie,
      ],
      { cwd: run, windowsHide: true },
    );
  }
  assert.deepEqual(errors, []);
  assert.ok(ranges.includes(206), "浏览器未收到部分内容响应");
  console.log(
    JSON.stringify(
      {
        screenshot,
        movie,
        zip,
        neighbor_frames: 2,
        played_videos: 4,
        played_audio: movie ? 0 : 1,
        range_responses: ranges,
        download_bytes: (await fs.stat(zip)).size,
        page_errors: errors,
        hover: hoverTiming,
      },
      null,
      2,
    ),
  );
} finally {
  if (browser) await browser.close();
  if (child.exitCode === null) {
    if (process.platform === "win32")
      execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      });
    else child.kill("SIGTERM");
  }
}
