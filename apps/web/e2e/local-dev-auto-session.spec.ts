import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { expect, test, type Page } from "@playwright/test";

const execFileAsync = promisify(execFile);

type ObservedRequest = {
  method: string;
  path: string;
  bearerFingerprint: string | null;
};

function bearerFingerprint(value: string | undefined): string | null {
  if (!value?.startsWith("Bearer ") || value.length <= "Bearer ".length) {
    return null;
  }
  return createHash("sha256").update(value).digest("hex");
}

function observeApiRequests(page: Page): ObservedRequest[] {
  const observed: ObservedRequest[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/v1/")) return;
    observed.push({
      method: request.method(),
      path: url.pathname,
      bearerFingerprint: bearerFingerprint(
        request.headers()["authorization"],
      ),
    });
  });
  return observed;
}

function assertUrlContainsNoCredential(page: Page): void {
  const url = new URL(page.url());
  expect(url.hash).toBe("");
  for (const key of url.searchParams.keys()) {
    expect(key).not.toMatch(/bootstrap|access[_-]?token|token/i);
  }
}

function firstFingerprint(
  observed: readonly ObservedRequest[],
  path: string,
): string | null {
  return observed.find((request) => request.path === path)?.bearerFingerprint ?? null;
}

test("a naked fresh browser receives a protected development session and restores work", async ({
  browser,
  page,
  request,
  baseURL,
}) => {
  if (!baseURL) throw new Error("No-auth E2E baseURL is missing");

  const documentResponse = await request.get("/");
  expect(documentResponse.headers()["x-frame-options"]).toBe("DENY");
  expect(documentResponse.headers()["content-security-policy"]).toContain(
    "frame-ancestors 'none'",
  );

  const proxiedHealth = await request.get("/api/v1/health");
  expect(proxiedHealth.headers()["x-frame-options"]).toBe("DENY");
  expect(proxiedHealth.headers()["content-security-policy"]).toContain(
    "default-src 'self'",
  );
  expect(proxiedHealth.headers()["content-security-policy"]).toContain(
    "frame-ancestors 'none'",
  );

  const apiPort = process.env.AI_CANVAS_NOAUTH_E2E_API_PORT ?? "29876";
  const unauthenticated = await request.get(
    `http://127.0.0.1:${apiPort}/api/v1/projects`,
  );
  expect(unauthenticated.status()).toBe(401);

  const observed = observeApiRequests(page);
  await page.goto("/");
  await expect(
    page.getByRole("note", { name: "本地开发模式" }),
  ).toContainText("本地开发自动会话已启用");
  assertUrlContainsNoCredential(page);

  await expect
    .poll(() => observed.findIndex((item) => item.path === "/api/v1/session/development"))
    .toBeGreaterThanOrEqual(0);
  await expect
    .poll(() => observed.findIndex((item) => item.path === "/api/v1/session/status"))
    .toBeGreaterThanOrEqual(0);
  await expect
    .poll(() => observed.findIndex((item) => item.path === "/api/v1/projects"))
    .toBeGreaterThanOrEqual(0);

  const developmentIndex = observed.findIndex(
    (item) => item.path === "/api/v1/session/development",
  );
  const statusIndex = observed.findIndex(
    (item) => item.path === "/api/v1/session/status",
  );
  const projectsIndex = observed.findIndex(
    (item) => item.path === "/api/v1/projects",
  );
  expect(observed[developmentIndex]?.method).toBe("POST");
  expect(observed[developmentIndex]?.bearerFingerprint).toBeNull();
  expect(observed[statusIndex]?.method).toBe("GET");
  expect(observed[statusIndex]?.bearerFingerprint).not.toBeNull();
  expect(developmentIndex).toBeLessThan(statusIndex);
  expect(statusIndex).toBeLessThan(projectsIndex);

  const projectName = `noauth-${Date.now()}`;
  const noteText = "自动会话创建并保存的稳定便签";
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByRole("button", { name: "创建空白项目" }).click();
  await page.getByRole("button", { name: "添加便签" }).click();
  await page.getByLabel("便签内容").fill(noteText);
  await expect(page.locator(".save-status")).toHaveText("有未保存修改", {
    timeout: 5_000,
  });
  await page.getByRole("button", { name: "审阅并应用画布变更" }).click();
  await expect(page.getByRole("dialog", { name: "图谱变更审阅" })).toBeVisible();
  await page.getByRole("button", { name: "确认应用变更" }).click();
  await expect(page.locator(".save-status")).toHaveText("已保存", {
    timeout: 5_000,
  });
  assertUrlContainsNoCredential(page);

  const dataRequests = observed.filter(
    (item) =>
      item.path === "/api/v1/projects" ||
      /^\/api\/v1\/projects\/[^/]+(?:\/graph|\/graph-change-sets)?$/.test(
        item.path,
      ),
  );
  expect(dataRequests.length).toBeGreaterThan(2);
  expect(dataRequests.every((item) => item.bearerFingerprint !== null)).toBe(true);

  await page.reload();
  await expect(page.getByLabel("便签内容")).toHaveValue(noteText);
  assertUrlContainsNoCredential(page);

  const firstStatusFingerprint = firstFingerprint(
    observed,
    "/api/v1/session/status",
  );
  expect(firstStatusFingerprint).not.toBeNull();

  const secondContext = await browser.newContext({ baseURL });
  try {
    expect((await secondContext.storageState()).origins).toEqual([]);
    const secondPage = await secondContext.newPage();
    const secondObserved = observeApiRequests(secondPage);
    await secondPage.goto("/");
    await expect(
      secondPage.getByRole("note", { name: "本地开发模式" }),
    ).toBeVisible();
    await expect(secondPage.getByRole("button", { name: projectName })).toBeVisible();
    assertUrlContainsNoCredential(secondPage);

    await expect
      .poll(() =>
        firstFingerprint(secondObserved, "/api/v1/session/status"),
      )
      .not.toBeNull();
    expect(
      firstFingerprint(secondObserved, "/api/v1/session/status"),
    ).toBe(firstStatusFingerprint);

    await secondPage.getByRole("button", { name: projectName }).click();
    await expect(secondPage.getByLabel("便签内容")).toHaveValue(noteText);
    assertUrlContainsNoCredential(secondPage);
    const secondDataRequests = secondObserved.filter((item) =>
      item.path.startsWith("/api/v1/projects"),
    );
    expect(secondDataRequests.length).toBeGreaterThan(1);
    expect(
      secondDataRequests.every((item) => item.bearerFingerprint !== null),
    ).toBe(true);
  } finally {
    await secondContext.close();
  }
});

test("both real no-auth child listeners bind only literal IPv4 loopback", async () => {
  test.skip(
    process.platform !== "win32",
    "This assertion uses the Windows TCP listener inventory available in the target runtime.",
  );
  const ports = [
    Number(process.env.AI_CANVAS_NOAUTH_E2E_API_PORT ?? "29876"),
    Number(process.env.AI_CANVAS_NOAUTH_E2E_WEB_PORT ?? "25174"),
  ];

  for (const port of ports) {
    const script = [
      `$listeners = @(Get-NetTCPConnection -State Listen -LocalPort ${port} -ErrorAction Stop)`,
      "if ($listeners.Count -eq 0) { throw 'listener missing' }",
      "$listeners | Select-Object -ExpandProperty LocalAddress -Unique",
    ].join("; ");
    const { stdout } = await execFileAsync("powershell.exe", [
      "-NoProfile",
      "-Command",
      script,
    ]);
    const addresses = stdout
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean);
    expect(addresses, `listener addresses for ${port}`).toEqual(["127.0.0.1"]);
  }
});
