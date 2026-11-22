import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
import { readDevelopmentE2EConfig } from "./e2eRuntimeConfig";

const here = path.dirname(fileURLToPath(import.meta.url));
const runtime = readDevelopmentE2EConfig();

export default defineConfig({
  globalSetup: "./e2e/global-setup.ts",
  metadata: { AI_CANVAS_E2E_BOOTSTRAP_TOKEN: "e2e-bootstrap" },
  testDir: "./e2e",
  outputDir: path.join(runtime.dataDirectory, "playwright-results"),
  testMatch: [
    "canvas-persistence.spec.ts",
    "c1-prompt-resources.spec.ts",
    "c2-branch-selection-promotion.spec.ts",
    "f1-control-contracts.spec.ts",
    "c3-agent-proposal.spec.ts",
    "c4-prompt-profile-batch.spec.ts",
    "media-import.spec.ts",
    "media-lifecycle.spec.ts",
    "media-ingress-discard.spec.ts",
    "media-ingress.spec.ts",
  ],
  workers: 1,
  use: {
    baseURL: runtime.webOrigin,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        `..\\api\\.venv\\Scripts\\python.exe -m uvicorn ai_canvas.main:app --app-dir ..\\api\\src --host 127.0.0.1 --port ${runtime.apiPort}`,
      cwd: here,
      url: `${runtime.apiOrigin}/api/v1/health`,
      timeout: 300_000,
      reuseExistingServer: false,
      env: {
        ...process.env,
        PYTHONPATH: path.resolve(here, "../api/src"),
        AI_CANVAS_BOOTSTRAP_TOKEN: "e2e-bootstrap",
        AI_CANVAS_PUBLIC_ORIGIN: runtime.webOrigin,
        AI_CANVAS_DATA_DIR: runtime.dataDirectory,
        AI_CANVAS_INSTANCE_ID: "development-e2e-child",
      },
    },
    {
      command: `npm.cmd run dev -- --host 127.0.0.1 --port ${runtime.webPort} --strictPort`,
      cwd: here,
      url: runtime.webOrigin,
      reuseExistingServer: false,
      env: {
        ...process.env,
        AI_CANVAS_INSTANCE_ID: "development-e2e-child",
      },
    },
  ],
});
