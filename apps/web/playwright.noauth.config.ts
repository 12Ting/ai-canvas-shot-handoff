import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";
import { readNoAuthE2EConfig } from "./e2eRuntimeConfig";

const here = path.dirname(fileURLToPath(import.meta.url));
const runtime = readNoAuthE2EConfig();
const aiCanvasEnvironmentPrefix = "AI_CANVAS_";

function isAiCanvasEnvironmentName(name: string): boolean {
  return (
    name.slice(0, aiCanvasEnvironmentPrefix.length).toUpperCase() ===
    aiCanvasEnvironmentPrefix
  );
}

function environmentWithoutAiCanvas(): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const [name, value] of Object.entries(process.env)) {
    if (!isAiCanvasEnvironmentName(name) && value !== undefined) {
      environment[name] = value;
    }
  }
  return environment;
}

const apiEnvironment = environmentWithoutAiCanvas();
Object.assign(apiEnvironment, {
  PYTHONPATH: path.resolve(here, "../api/src"),
  AI_CANVAS_RUNTIME_PROFILE: "development",
  AI_CANVAS_AUTH_MODE: "development-auto-session",
  AI_CANVAS_PUBLIC_ORIGIN: runtime.webOrigin,
  AI_CANVAS_DEVELOPMENT_ORIGINS: JSON.stringify([runtime.webOrigin]),
  AI_CANVAS_ALLOWED_HOSTS: JSON.stringify(["127.0.0.1"]),
  AI_CANVAS_DATA_DIR: runtime.dataDirectory,
  AI_CANVAS_INSTANCE_ID: "noauth-e2e-child",
});

const viteEnvironment = environmentWithoutAiCanvas();
Object.assign(viteEnvironment, {
  AI_CANVAS_INSTANCE_ID: "noauth-e2e-child",
  AI_CANVAS_E2E_API_PORT: String(runtime.apiPort),
});

export default defineConfig({
  testDir: "./e2e",
  testMatch: [
    "local-dev-auto-session.spec.ts",
    "shot-interactions.spec.ts",
    "c1-prompt-resources.spec.ts",
  ],
  outputDir: path.join(runtime.dataDirectory, "playwright-results"),
  workers: 1,
  use: {
    baseURL: runtime.webOrigin,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command:
        `..\\api\\.venv\\Scripts\\python.exe -m uvicorn ai_canvas.main:app --app-dir ..\\api\\src --host 127.0.0.1 --port ${runtime.apiPort} --workers 1`,
      cwd: here,
      url: `${runtime.apiOrigin}/api/v1/health`,
      timeout: 300_000,
      reuseExistingServer: false,
      env: apiEnvironment,
    },
    {
      command: `npm.cmd run dev -- --host 127.0.0.1 --port ${runtime.webPort} --strictPort`,
      cwd: here,
      url: runtime.webOrigin,
      reuseExistingServer: false,
      env: viteEnvironment,
    },
  ],
});
