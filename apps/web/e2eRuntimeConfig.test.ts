import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readDevelopmentE2EConfig,
  readNoAuthE2EConfig,
  readReleaseE2EConfig,
} from "./e2eRuntimeConfig";

function isAiCanvasEnvironmentName(name: string): boolean {
  return name.slice(0, "AI_CANVAS_".length).toUpperCase() === "AI_CANVAS_";
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("E2E runtime configuration", () => {
  it("keeps every Task 7 TypeScript source in a dedicated build gate", () => {
    const tsconfigPath = path.resolve("tsconfig.e2e.json");
    expect(fs.existsSync(tsconfigPath)).toBe(true);
    if (!fs.existsSync(tsconfigPath)) return;

    const tsconfig = JSON.parse(fs.readFileSync(tsconfigPath, "utf8")) as {
      include?: string[];
    };
    expect(tsconfig.include).toEqual([
      "playwright.config.ts",
      "playwright.release.config.ts",
      "playwright.noauth.config.ts",
      "e2e/**/*.ts",
      "e2eRuntimeConfig.ts",
      "e2eRuntimeConfig.test.ts",
    ]);
    const packageJson = JSON.parse(
      fs.readFileSync(path.resolve("package.json"), "utf8"),
    ) as { scripts?: { build?: string } };
    expect(packageJson.scripts?.build).toContain(
      "tsc -p tsconfig.e2e.json --noEmit",
    );
    expect(packageJson.scripts).toMatchObject({
      "e2e:noauth": "playwright test --config playwright.noauth.config.ts",
    });
    expect(fs.existsSync(path.resolve("playwright.noauth.config.ts"))).toBe(true);
  });

  it("propagates automatic-session ports and data without a bootstrap setup", async () => {
    const dataDirectory = path.resolve("../../.local/e2e-noauth-config-test");
    vi.stubEnv("AI_CANVAS_NOAUTH_E2E_API_PORT", "29876");
    vi.stubEnv("AI_CANVAS_NOAUTH_E2E_WEB_PORT", "25174");
    vi.stubEnv("AI_CANVAS_NOAUTH_E2E_DATA_DIR", dataDirectory);
    expect(readNoAuthE2EConfig()).toEqual({
      apiPort: 29876,
      webPort: 25174,
      apiOrigin: "http://127.0.0.1:29876",
      webOrigin: "http://127.0.0.1:25174",
      dataDirectory,
    });
  });

  it("keeps automatic-session API authority out of the Vite child", async () => {
    const dataDirectory = path.resolve("../../.local/e2e-noauth-config-test");
    vi.stubEnv("AI_CANVAS_NOAUTH_E2E_API_PORT", "29876");
    vi.stubEnv("AI_CANVAS_NOAUTH_E2E_WEB_PORT", "25174");
    vi.stubEnv("AI_CANVAS_NOAUTH_E2E_DATA_DIR", dataDirectory);
    vi.stubEnv("AI_CANVAS_BOOTSTRAP_TOKEN", "inherited-must-be-scrubbed");
    vi.stubEnv("AI_CANVAS_RUNTIME_PROFILE", "release");
    vi.stubEnv("AI_CANVAS_AUTH_MODE", "session");
    vi.stubEnv("aI_cAnVaS_mIxEd_SeNtInEl", "must-be-scrubbed");

    const noAuthConfig = (await import("./playwright.noauth.config")).default;
    const webServers = Array.isArray(noAuthConfig.webServer)
      ? noAuthConfig.webServer
      : [];
    expect(noAuthConfig.globalSetup).toBeUndefined();
    expect(noAuthConfig.metadata?.AI_CANVAS_E2E_BOOTSTRAP_TOKEN).toBeUndefined();
    expect(noAuthConfig.testMatch).toEqual([
      "local-dev-auto-session.spec.ts",
      "shot-interactions.spec.ts",
      "c1-prompt-resources.spec.ts",
    ]);
    expect(noAuthConfig.workers).toBe(1);
    expect(noAuthConfig.use?.baseURL).toBe("http://127.0.0.1:25174");
    expect(webServers).toHaveLength(2);

    const apiServer = webServers[0];
    const viteServer = webServers[1];
    expect(apiServer?.command).toContain("--host 127.0.0.1");
    expect(apiServer?.command).toContain("--port 29876");
    expect(apiServer?.command).toContain("--workers 1");
    expect(apiServer?.reuseExistingServer).toBe(false);
    expect(apiServer?.env).toMatchObject({
      AI_CANVAS_RUNTIME_PROFILE: "development",
      AI_CANVAS_AUTH_MODE: "development-auto-session",
      AI_CANVAS_PUBLIC_ORIGIN: "http://127.0.0.1:25174",
      AI_CANVAS_DEVELOPMENT_ORIGINS: '["http://127.0.0.1:25174"]',
      AI_CANVAS_ALLOWED_HOSTS: '["127.0.0.1"]',
      AI_CANVAS_DATA_DIR: dataDirectory,
      AI_CANVAS_INSTANCE_ID: "noauth-e2e-child",
    });
    expect(apiServer?.env?.AI_CANVAS_BOOTSTRAP_TOKEN).toBeUndefined();
    expect(apiServer?.env?.AI_CANVAS_E2E_API_PORT).toBeUndefined();
    expect(apiServer?.env?.aI_cAnVaS_mIxEd_SeNtInEl).toBeUndefined();
    expect(
      Object.keys(apiServer?.env ?? {})
        .filter(isAiCanvasEnvironmentName)
        .sort(),
    ).toEqual([
      "AI_CANVAS_ALLOWED_HOSTS",
      "AI_CANVAS_AUTH_MODE",
      "AI_CANVAS_DATA_DIR",
      "AI_CANVAS_DEVELOPMENT_ORIGINS",
      "AI_CANVAS_INSTANCE_ID",
      "AI_CANVAS_PUBLIC_ORIGIN",
      "AI_CANVAS_RUNTIME_PROFILE",
    ]);

    expect(viteServer?.command).toContain("--host 127.0.0.1");
    expect(viteServer?.command).toContain("--port 25174");
    expect(viteServer?.command).toContain("--strictPort");
    expect(viteServer?.reuseExistingServer).toBe(false);
    expect(viteServer?.env?.AI_CANVAS_INSTANCE_ID).toBe("noauth-e2e-child");
    expect(viteServer?.env?.AI_CANVAS_E2E_API_PORT).toBe("29876");
    expect(viteServer?.env?.aI_cAnVaS_mIxEd_SeNtInEl).toBeUndefined();
    expect(
      Object.keys(viteServer?.env ?? {})
        .filter(isAiCanvasEnvironmentName)
        .sort(),
    ).toEqual(["AI_CANVAS_E2E_API_PORT", "AI_CANVAS_INSTANCE_ID"]);
    for (const forbidden of [
      "AI_CANVAS_BOOTSTRAP_TOKEN",
      "AI_CANVAS_RUNTIME_PROFILE",
      "AI_CANVAS_AUTH_MODE",
      "AI_CANVAS_PUBLIC_ORIGIN",
      "AI_CANVAS_DEVELOPMENT_ORIGINS",
      "AI_CANVAS_ALLOWED_HOSTS",
      "AI_CANVAS_DATA_DIR",
    ]) {
      expect(viteServer?.env?.[forbidden], `${forbidden} in Vite env`).toBeUndefined();
    }
  });

  it("uses distinct high-port and e2e-noauth defaults", async () => {
    const runtime = readNoAuthE2EConfig({});
    expect(runtime.apiPort).not.toBe(runtime.webPort);
    expect([5_173, 5_174, 8_765, 8_766, 8_876]).not.toContain(runtime.apiPort);
    expect([5_173, 5_174, 8_765, 8_766, 8_876]).not.toContain(runtime.webPort);
    expect(runtime.apiPort).toBeGreaterThan(10_000);
    expect(runtime.webPort).toBeGreaterThan(10_000);
    expect(path.basename(runtime.dataDirectory)).toMatch(/^e2e-noauth-/);
  });

  it("uses dedicated high-port defaults for authenticated development and release E2E", () => {
    const development = readDevelopmentE2EConfig({});
    const release = readReleaseE2EConfig({});

    expect(development.apiPort).toBe(18_765);
    expect(development.webPort).toBe(15_173);
    expect(release.port).toBe(18_876);
    expect(new Set([
      development.apiPort,
      development.webPort,
      release.port,
    ]).size).toBe(3);
  });

  it("rejects automatic-session services configured on the same port", async () => {
    expect(() =>
      readNoAuthE2EConfig({
        AI_CANVAS_NOAUTH_E2E_API_PORT: "29876",
        AI_CANVAS_NOAUTH_E2E_WEB_PORT: "29876",
        AI_CANVAS_NOAUTH_E2E_DATA_DIR: path.resolve(
          "../../.local/e2e-noauth-shared-port",
        ),
      }),
    ).toThrow(/must differ/);
  });

  it("rejects a noauth port that collides with another explicit E2E service", async () => {
    expect(() =>
      readNoAuthE2EConfig({
        AI_CANVAS_E2E_API_PORT: "29876",
        AI_CANVAS_NOAUTH_E2E_API_PORT: "29876",
        AI_CANVAS_NOAUTH_E2E_WEB_PORT: "25174",
        AI_CANVAS_NOAUTH_E2E_DATA_DIR: path.resolve(
          "../../.local/e2e-noauth-port-collision",
        ),
      }),
    ).toThrow(/port.*differ|differ.*port/i);
  });

  it("rejects noauth data outside the e2e boundary and normal dev-noauth data", async () => {
    expect(() =>
      readNoAuthE2EConfig({
        AI_CANVAS_NOAUTH_E2E_DATA_DIR: path.resolve("..", "outside-noauth"),
      }),
    ).toThrow(/\.local/);
    expect(() =>
      readNoAuthE2EConfig({
        AI_CANVAS_NOAUTH_E2E_DATA_DIR: path.resolve("../../.local/dev-noauth"),
      }),
    ).toThrow(/test-specific/);
  });

  it("rejects every pairwise explicit E2E data-directory collision", async () => {
    const shared = path.resolve("../../.local/e2e-pairwise-shared");
    expect(() =>
      readNoAuthE2EConfig({
        AI_CANVAS_E2E_DATA_DIR: shared,
        AI_CANVAS_NOAUTH_E2E_DATA_DIR: shared,
      }),
    ).toThrow(/must differ/);
    expect(() =>
      readNoAuthE2EConfig({
        AI_CANVAS_RELEASE_E2E_DATA_DIR: shared,
        AI_CANVAS_NOAUTH_E2E_DATA_DIR: shared,
      }),
    ).toThrow(/must differ/);
  });

  it("propagates non-default development ports and data directory everywhere", async () => {
    const dataDirectory = path.resolve("../../.local/e2e-config-test");
    vi.stubEnv("AI_CANVAS_E2E_API_PORT", "18765");
    vi.stubEnv("AI_CANVAS_E2E_WEB_PORT", "15173");
    vi.stubEnv("AI_CANVAS_E2E_DATA_DIR", dataDirectory);

    const playwrightConfig = (await import("./playwright.config")).default;
    const viteConfig = (await import("./vite.config")).default;
    const webServers = Array.isArray(playwrightConfig.webServer)
      ? playwrightConfig.webServer
      : [];

    expect(playwrightConfig.use?.baseURL).toBe("http://127.0.0.1:15173");
    expect(webServers).toHaveLength(2);
    expect(webServers[0]?.command).toContain("--port 18765");
    expect(webServers[0]?.url).toBe("http://127.0.0.1:18765/api/v1/health");
    expect(webServers[0]?.env?.AI_CANVAS_PUBLIC_ORIGIN).toBe(
      "http://127.0.0.1:15173",
    );
    expect(webServers[0]?.env?.AI_CANVAS_DATA_DIR).toBe(dataDirectory);
    expect(webServers[1]?.command).toContain("--port 15173");
    expect(webServers[1]?.url).toBe("http://127.0.0.1:15173");
    expect(playwrightConfig.globalSetup).toMatch(/global-setup/);
    expect(playwrightConfig.metadata?.AI_CANVAS_E2E_BOOTSTRAP_TOKEN).toBe(
      "e2e-bootstrap",
    );

    expect(typeof viteConfig).toBe("object");
    if (typeof viteConfig !== "object" || viteConfig === null) return;
    const proxy = viteConfig.server?.proxy;
    expect(proxy && typeof proxy === "object" ? proxy["/api"] : null).toMatchObject({
      target: "http://127.0.0.1:18765",
    });
  });

  it("propagates the release port and isolated data directory", async () => {
    const dataDirectory = path.resolve("../../.local/e2e-release-config-test");
    vi.stubEnv("AI_CANVAS_RELEASE_E2E_PORT", "18876");
    vi.stubEnv("AI_CANVAS_RELEASE_E2E_DATA_DIR", dataDirectory);

    const config = (await import("./playwright.release.config")).default;
    const webServer = Array.isArray(config.webServer)
      ? config.webServer[0]
      : config.webServer;

    expect(config.use?.baseURL).toBe("http://127.0.0.1:18876");
    expect(webServer?.url).toBe("http://127.0.0.1:18876/api/v1/health");
    expect(webServer?.env?.AI_CANVAS_LISTEN_PORT).toBe("18876");
    expect(webServer?.env?.AI_CANVAS_DATA_DIR).toBe(dataDirectory);
    expect(config.globalSetup).toMatch(/global-setup/);
    expect(config.metadata?.AI_CANVAS_E2E_BOOTSTRAP_TOKEN).toBe(
      "release-e2e-bootstrap",
    );
    expect(config.testMatch).toContain("media-import.spec.ts");
  });

  it("writes each runtime profile's Playwright artifacts beneath its own data directory", async () => {
    const noAuthDataDirectory = path.resolve("../../.local/e2e-noauth-output-test");
    const developmentDataDirectory = path.resolve("../../.local/e2e-development-output-test");
    const releaseDataDirectory = path.resolve("../../.local/e2e-release-output-test");
    vi.stubEnv("AI_CANVAS_NOAUTH_E2E_DATA_DIR", noAuthDataDirectory);
    vi.stubEnv("AI_CANVAS_E2E_DATA_DIR", developmentDataDirectory);
    vi.stubEnv("AI_CANVAS_RELEASE_E2E_DATA_DIR", releaseDataDirectory);

    const [noAuth, development, release] = await Promise.all([
      import("./playwright.noauth.config"),
      import("./playwright.config"),
      import("./playwright.release.config"),
    ]);
    const outputDirectories = [
      noAuth.default.outputDir,
      development.default.outputDir,
      release.default.outputDir,
    ];

    expect(outputDirectories).toEqual([
      path.join(noAuthDataDirectory, "playwright-results"),
      path.join(developmentDataDirectory, "playwright-results"),
      path.join(releaseDataDirectory, "playwright-results"),
    ]);
    expect(new Set(outputDirectories).size).toBe(3);
  });

  it("runs the flexible media ingress browser contract in both runtime profiles", async () => {
    const development = (await import("./playwright.config")).default;
    const release = (await import("./playwright.release.config")).default;

    for (const config of [development, release]) {
      expect(config.testMatch).toEqual(
        expect.arrayContaining([
          "media-import.spec.ts",
          "media-ingress-discard.spec.ts",
          "media-ingress.spec.ts",
        ]),
      );
    }
  });

  it.each([
    "0",
    "1023",
    "65536",
    "18765.5",
    " 18765",
    "18765 ",
    "1e4",
    "0x4000",
    "0.0.0.0:18765",
    "http://127.0.0.1:18765",
  ])(
    "rejects an invalid or non-loopback development port value %s",
    (port) => {
      expect(() =>
        readDevelopmentE2EConfig({
          AI_CANVAS_E2E_API_PORT: port,
          AI_CANVAS_E2E_DATA_DIR: path.resolve("../../.local/e2e-invalid-port"),
        }),
      ).toThrow(/AI_CANVAS_E2E_API_PORT/);
    },
  );

  it.each([
    "0",
    "1023",
    "65536",
    "29876.5",
    " 29876",
    "29876 ",
    "http://127.0.0.1:29876",
  ])("rejects an invalid automatic-session API port value %s", (port) => {
    expect(() =>
      readNoAuthE2EConfig({
        AI_CANVAS_NOAUTH_E2E_API_PORT: port,
        AI_CANVAS_NOAUTH_E2E_DATA_DIR: path.resolve(
          "../../.local/e2e-noauth-invalid-port",
        ),
      }),
    ).toThrow(/AI_CANVAS_NOAUTH_E2E_API_PORT/);
  });

  it("rejects development services configured on the same port", () => {
    expect(() =>
      readDevelopmentE2EConfig({
        AI_CANVAS_E2E_API_PORT: "18765",
        AI_CANVAS_E2E_WEB_PORT: "18765",
        AI_CANVAS_E2E_DATA_DIR: path.resolve("../../.local/e2e-shared-port"),
      }),
    ).toThrow(/must differ/);
  });

  it.each([
    ["AI_CANVAS_E2E_DATA_DIR", path.resolve("..", "outside-development")],
    ["AI_CANVAS_RELEASE_E2E_DATA_DIR", path.resolve("..", "outside-release")],
  ])("rejects %s outside AI_Canvas/.local", (name, dataDirectory) => {
    const environment = { [name]: dataDirectory };
    const read = name === "AI_CANVAS_E2E_DATA_DIR"
      ? readDevelopmentE2EConfig
      : readReleaseE2EConfig;
    expect(() => read(environment)).toThrow(/\.local/);
  });

  it("rejects the normal development data directory", () => {
    expect(() =>
      readDevelopmentE2EConfig({
        AI_CANVAS_E2E_DATA_DIR: path.resolve("../../.local/dev"),
      }),
    ).toThrow(/test-specific/);
  });

  it("rejects identical explicit development and release data directories", () => {
    const shared = path.resolve("../../.local/e2e-shared-root");
    expect(() =>
      readDevelopmentE2EConfig({
        AI_CANVAS_E2E_DATA_DIR: shared,
        AI_CANVAS_RELEASE_E2E_DATA_DIR: shared,
      }),
    ).toThrow(/must differ/);
  });

  it("rejects a junction or symlink that physically escapes .local", () => {
    const localRoot = path.resolve("../../.local");
    fs.mkdirSync(localRoot, { recursive: true });
    const container = fs.mkdtempSync(path.join(localRoot, "e2e-junction-"));
    const link = path.join(container, "escape");
    fs.symlinkSync(
      path.resolve("."),
      link,
      process.platform === "win32" ? "junction" : "dir",
    );
    try {
      expect(() =>
        readDevelopmentE2EConfig({
          AI_CANVAS_E2E_DATA_DIR: path.join(link, "run"),
        }),
      ).toThrow(/link|junction|reparse|physical/i);
      expect(() =>
        readNoAuthE2EConfig({
          AI_CANVAS_NOAUTH_E2E_DATA_DIR: path.join(link, "noauth-run"),
        }),
      ).toThrow(/link|junction|reparse|physical/i);
    } finally {
      fs.unlinkSync(link);
      fs.rmdirSync(container);
    }
  });

  it("creates separate task-specific children when data directories are omitted", () => {
    const firstDevelopment = readDevelopmentE2EConfig({});
    const secondDevelopment = readDevelopmentE2EConfig({});
    const release = readReleaseE2EConfig({});
    const noAuth = readNoAuthE2EConfig({});
    const expectedRoot = path.resolve("../../.local");

    for (const dataDirectory of [
      firstDevelopment.dataDirectory,
      secondDevelopment.dataDirectory,
      release.dataDirectory,
      noAuth.dataDirectory,
    ]) {
      expect(path.relative(expectedRoot, dataDirectory)).not.toMatch(/^\.\./);
      expect(dataDirectory).not.toBe(expectedRoot);
    }
    expect(firstDevelopment.dataDirectory).not.toBe(secondDevelopment.dataDirectory);
    expect(firstDevelopment.dataDirectory).not.toBe(release.dataDirectory);
    expect(firstDevelopment.dataDirectory).not.toBe(noAuth.dataDirectory);
    expect(release.dataDirectory).not.toBe(noAuth.dataDirectory);
  });
});
