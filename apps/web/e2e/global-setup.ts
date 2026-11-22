import { request, type FullConfig } from "@playwright/test";

const SESSION_TOKEN_ENV = "AI_CANVAS_E2E_SESSION_TOKEN_INTERNAL";

export default async function globalSetup(config: FullConfig): Promise<void> {
  const origin = config.projects[0]?.use.baseURL;
  const bootstrapToken = config.metadata.AI_CANVAS_E2E_BOOTSTRAP_TOKEN;
  if (typeof origin !== "string" || !origin.startsWith("http://127.0.0.1:")) {
    throw new Error("Playwright baseURL must be an explicit 127.0.0.1 origin");
  }
  if (typeof bootstrapToken !== "string" || bootstrapToken.length === 0) {
    throw new Error("Playwright bootstrap metadata is missing");
  }

  const api = await request.newContext({
    baseURL: origin,
    extraHTTPHeaders: { Origin: origin },
  });
  try {
    const response = await api.post("/api/v1/session/exchange", {
      headers: { "X-Canvas-Bootstrap": bootstrapToken },
    });
    if (!response.ok()) {
      throw new Error(`E2E session exchange failed (${response.status()})`);
    }
    const body: unknown = await response.json();
    if (
      typeof body !== "object" ||
      body === null ||
      !("access_token" in body) ||
      typeof body.access_token !== "string" ||
      body.access_token.length === 0
    ) {
      throw new Error("E2E session exchange returned an invalid response");
    }
    process.env[SESSION_TOKEN_ENV] = body.access_token;
  } finally {
    await api.dispose();
  }
}
