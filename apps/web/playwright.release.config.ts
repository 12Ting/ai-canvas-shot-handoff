import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
import { readReleaseE2EConfig } from "./e2eRuntimeConfig";

const here = path.dirname(fileURLToPath(import.meta.url));
const runtime = readReleaseE2EConfig();

export default defineConfig({
  globalSetup: "./e2e/global-setup.ts",
  metadata: { AI_CANVAS_E2E_BOOTSTRAP_TOKEN: "release-e2e-bootstrap" },
  testDir: "./e2e",
  outputDir: path.join(runtime.dataDirectory, "playwright-results"),
  testMatch: [
    "release-smoke.spec.ts",
    "media-import.spec.ts",
    "media-ingress-discard.spec.ts",
    "media-ingress.spec.ts",
  ],
  workers: 1,
  use: {
    baseURL: runtime.origin,
    trace: "retain-on-failure",
  },
  webServer: {
    command: ".venv\\Scripts\\python.exe -m ai_canvas.launcher",
    // The first Windows launch applies every SQLite migration before serving health.
    timeout: 300_000,
    cwd: path.resolve(here, "../api"),
    url: `${runtime.origin}/api/v1/health`,
    reuseExistingServer: false,
    env: {
      ...process.env,
      PYTHONPATH: path.resolve(here, "../api/src"),
      AI_CANVAS_BOOTSTRAP_TOKEN: "release-e2e-bootstrap",
      AI_CANVAS_DATA_DIR: runtime.dataDirectory,
      AI_CANVAS_WEB_DIST: path.resolve(here, "dist"),
      AI_CANVAS_LISTEN_PORT: String(runtime.port),
      AI_CANVAS_OPEN_BROWSER: "false",
      AI_CANVAS_INSTANCE_ID: "release-e2e-child",
    },
  },
});
