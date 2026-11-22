import {
  expect,
  test as base,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { createServer } from "node:http";

const SESSION_STORAGE_KEY = "ai-canvas-session";
const SESSION_TOKEN_ENV = "AI_CANVAS_E2E_SESSION_TOKEN_INTERNAL";

type E2EFixtures = {
  sessionToken: string;
  allowArtifactImportResultNetworkFailure: (url: string) => void;
  allowArtifactImportStatusNetworkFailure: (url: string) => void;
  allowGraphRevisionConflict: (page: Page, url: string) => void;
  allowConsumedRunAuthorization: (url: string) => void;
  allowConsumedAgentProposal: (url: string) => void;
  modelWitness: {
    calls: number;
    requests: unknown[];
    schemaCalls: Record<string, number>;
    shotCalls: Record<string, number>;
    failedShotId: string | null;
  };
};

type PageDiagnostics = {
  expectedClientErrorResponses: Map<number, number>;
  genericClientErrorConsoleErrors: Map<number, number>;
  expectedArtifactImportResultNetworkFailures: number;
  expectedArtifactImportStatusNetworkFailures: number;
  allowedArtifactImportResultNetworkFailurePaths: Set<string>;
  allowedArtifactImportStatusNetworkFailurePaths: Set<string>;
  allowedGraphRevisionConflictPaths: Set<string>;
  allowedConsumedRunAuthorizationPaths: Set<string>;
  allowedConsumedAgentProposalPaths: Set<string>;
  genericNetworkConsoleErrors: number;
};

type BrowserDiagnostics = {
  errors: string[];
  pages: PageDiagnostics[];
};

const chromiumClientErrorConsolePattern =
  /^Failed to load resource: the server responded with a status of ([45]\d{2})\b/;
const chromiumNetworkConsolePattern =
  /^Failed to load resource: net::ERR_(?:INTERNET_DISCONNECTED|FAILED)$/;
const graphChangeSetPathPattern =
  /^\/api\/v1\/projects\/[0-9a-f-]+\/graph-change-sets$/i;
const noEgressDryRunPathPattern =
  /^\/api\/v1\/projects\/[0-9a-f-]+\/run-requests\/[0-9a-f-]+\/dry-run$/i;
const agentExecutePathPattern =
  /^\/api\/v1\/projects\/[0-9a-f-]+\/agent-proposals\/[0-9a-f-]+\/execute$/i;
const discardResultPathPattern =
  /^\/api\/v1\/projects\/[0-9a-f-]+\/artifact-imports\/[0-9a-f-]+\/result$/i;
const artifactImportContentPathPattern =
  /^\/api\/v1\/projects\/[0-9a-f-]+\/artifact-imports\/[0-9a-f-]+\/content$/i;
const artifactImportStatusPathPattern =
  /^\/api\/v1\/projects\/[0-9a-f-]+\/artifact-imports\/[0-9a-f-]+$/i;
const expectedArtifactImportContentClientErrorHeader =
  "x-e2e-expected-artifact-import-content-client-error";
const pageDiagnosticsByPage = new WeakMap<Page, PageDiagnostics>();

function incrementStatusCount(counts: Map<number, number>, status: number): void {
  counts.set(status, (counts.get(status) ?? 0) + 1);
}

function sameStatusCounts(
  left: ReadonlyMap<number, number>,
  right: ReadonlyMap<number, number>,
): boolean {
  if (left.size !== right.size) return false;
  for (const [status, count] of left) {
    if (right.get(status) !== count) return false;
  }
  return true;
}

function registerPageDiagnostics(page: Page, diagnostics: BrowserDiagnostics): void {
  const { errors } = diagnostics;
  const pageDiagnostics = {
    expectedClientErrorResponses: new Map<number, number>(),
    genericClientErrorConsoleErrors: new Map<number, number>(),
    expectedArtifactImportResultNetworkFailures: 0,
    expectedArtifactImportStatusNetworkFailures: 0,
    allowedArtifactImportResultNetworkFailurePaths: new Set<string>(),
    allowedArtifactImportStatusNetworkFailurePaths: new Set<string>(),
    allowedGraphRevisionConflictPaths: new Set<string>(),
    allowedConsumedRunAuthorizationPaths: new Set<string>(),
    allowedConsumedAgentProposalPaths: new Set<string>(),
    genericNetworkConsoleErrors: 0,
  };
  diagnostics.pages.push(pageDiagnostics);
  pageDiagnosticsByPage.set(page, pageDiagnostics);
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const clientError = chromiumClientErrorConsolePattern.exec(message.text());
    if (clientError) {
      incrementStatusCount(
        pageDiagnostics.genericClientErrorConsoleErrors,
        Number(clientError[1]),
      );
      return;
    }
    if (chromiumNetworkConsolePattern.test(message.text())) {
      pageDiagnostics.genericNetworkConsoleErrors += 1;
      return;
    }
    errors.push(`console: ${message.text()}`);
  });
  page.on("requestfailed", (request) => {
    const url = new URL(request.url());
    const failure = request.failure()?.errorText ?? "";
    const isExpectedNetworkFailure = /net::ERR_(?:INTERNET_DISCONNECTED|FAILED)/.test(
      failure,
    );
    if (
      request.method() === "DELETE" &&
      discardResultPathPattern.test(url.pathname) &&
      isExpectedNetworkFailure
    ) {
      if (
        !pageDiagnostics.allowedArtifactImportResultNetworkFailurePaths.delete(
          url.pathname,
        )
      ) {
        errors.push(
          `request: unexpected artifact-import result network failure ${url.pathname}`,
        );
        return;
      }
      pageDiagnostics.expectedArtifactImportResultNetworkFailures += 1;
      return;
    }
    if (
      request.method() === "GET" &&
      artifactImportStatusPathPattern.test(url.pathname) &&
      isExpectedNetworkFailure
    ) {
      if (
        !pageDiagnostics.allowedArtifactImportStatusNetworkFailurePaths.delete(
          url.pathname,
        )
      ) {
        errors.push(
          `request: unexpected artifact-import status network failure ${url.pathname}`,
        );
        return;
      }
      pageDiagnostics.expectedArtifactImportStatusNetworkFailures += 1;
    }
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (response.status() >= 500) {
      errors.push(
        `response: unexpected server error ${response.status()} ` +
          `${response.request().method()} ${url.pathname}`,
      );
    }
    const isArtifactImportContent =
      response.request().method() === "PUT" &&
      artifactImportContentPathPattern.test(url.pathname);
    const expectsArtifactImportContentClientError =
      response.headers()[expectedArtifactImportContentClientErrorHeader] === "true";
    const isExpectedArtifactImportContentClientError =
      isArtifactImportContent &&
      response.status() >= 400 &&
      response.status() < 500 &&
      expectsArtifactImportContentClientError;
    const isGraphChangeSetConflict =
      response.status() === 409 &&
      response.request().method() === "POST" &&
      graphChangeSetPathPattern.test(url.pathname);
    const isExpectedGraphConflict =
      isGraphChangeSetConflict &&
      pageDiagnostics.allowedGraphRevisionConflictPaths.delete(url.pathname);
    const isConsumedRunAuthorization =
      response.status() === 409 &&
      response.request().method() === "POST" &&
      noEgressDryRunPathPattern.test(url.pathname);
    const isExpectedConsumedRunAuthorization =
      isConsumedRunAuthorization &&
      pageDiagnostics.allowedConsumedRunAuthorizationPaths.delete(url.pathname);
    const isConsumedAgentProposal =
      response.status() === 409 && response.request().method() === "POST" &&
      agentExecutePathPattern.test(url.pathname);
    const isExpectedConsumedAgentProposal = isConsumedAgentProposal &&
      pageDiagnostics.allowedConsumedAgentProposalPaths.delete(url.pathname);
    const isExpectedDiscardConflict =
      response.status() === 409 &&
      response.request().method() === "DELETE" &&
      discardResultPathPattern.test(url.pathname) &&
      response.headers()["x-e2e-expected-discard-conflict"] === "true";

    if (
      expectsArtifactImportContentClientError &&
      !isExpectedArtifactImportContentClientError
    ) {
      errors.push(
        "response: invalid expected artifact-import content client-error marker",
      );
    }
    if (response.status() >= 400 && response.status() < 500) {
      if (
        isExpectedArtifactImportContentClientError ||
        isExpectedGraphConflict ||
        isExpectedConsumedRunAuthorization ||
        isExpectedConsumedAgentProposal ||
        isExpectedDiscardConflict
      ) {
        incrementStatusCount(
          pageDiagnostics.expectedClientErrorResponses,
          response.status(),
        );
      } else {
        errors.push(
          `response: unexpected client error ${response.status()} ` +
            `${response.request().method()} ${url.pathname}`,
        );
      }
    }
  });
}

async function installContextGuards(
  context: BrowserContext,
  origin: string,
  token: string,
): Promise<BrowserDiagnostics> {
  const diagnostics: BrowserDiagnostics = {
    errors: [],
    pages: [],
  };
  await context.addInitScript(
    ({ allowedOrigin, sessionStorageKey, sessionToken }) => {
      if (location.origin === allowedOrigin) {
        sessionStorage.setItem(sessionStorageKey, sessionToken);
      }
      document.addEventListener("securitypolicyviolation", (event) => {
        console.error(
          `[securitypolicyviolation] ${event.effectiveDirective} blocked ${event.blockedURI}`,
        );
      });
    },
    {
      allowedOrigin: origin,
      sessionStorageKey: SESSION_STORAGE_KEY,
      sessionToken: token,
    },
  );
  context.on("page", (page) => registerPageDiagnostics(page, diagnostics));
  for (const page of context.pages()) registerPageDiagnostics(page, diagnostics);
  return diagnostics;
}

export const test = base.extend<E2EFixtures>({
  modelWitness: async ({ baseURL: _baseURL }, provide) => {
    const witness = {
      calls: 0,
      requests: [] as unknown[],
      schemaCalls: {} as Record<string, number>,
      shotCalls: {} as Record<string, number>,
      failedShotId: null as string | null,
    };
    const server = createServer((request, response) => {
      const chunks: Buffer[] = [];
      request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
      request.on("end", () => {
        witness.calls += 1;
        const payload = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
          messages: Array<{ role: string; content: unknown }>;
          response_format?: { json_schema?: { name?: string } };
        };
        witness.requests.push(payload);
        const schemaName = payload.response_format?.json_schema?.name ?? "unknown";
        witness.schemaCalls[schemaName] = (witness.schemaCalls[schemaName] ?? 0) + 1;
        const user = payload.messages.find((message) => message.role === "user")?.content as Record<string, unknown>;
        let output: Record<string, unknown>;
        if (schemaName === "prompt_profile_draft_v1") {
          output = {
            kind: "prompt_profile_draft",
            schema_version: 1,
            content_order: ["SUBJECT", "FREEFORM"],
            detail_density: "HIGH",
            format_rule_codes: ["SECTION_PER_BLOCK", "PRESERVE_LANGUAGE"],
            camera_rule_codes: ["EXPLICIT_SHOT_SIZE"],
            positive_rule_codes: ["INCLUDE_LIGHTING"],
            negative_rule_codes: ["NO_TEXT_WATERMARK"],
            applicability_codes: ["GENERAL_VISUAL_PROMPT"],
            summary: "Reusable structure only",
          };
        } else if (schemaName === "prompt_batch_item_draft_v1") {
          const shotId = String(user.shot_id);
          witness.shotCalls[shotId] = (witness.shotCalls[shotId] ?? 0) + 1;
          if (witness.failedShotId === null) {
            witness.failedShotId = shotId;
            output = { invalid: true };
          } else {
            const contexts = user.contexts as Array<{ category: string; content: unknown }>;
            const preserve = contexts.find((context) => context.category === "PRESERVE_CONSTRAINTS")
              ?.content as { editable_block_ids: string[] };
            output = {
              kind: "prompt_batch_item_draft",
              schema_version: 1,
              shot_id: shotId,
              prompt_node_id: String(user.prompt_node_id),
              base_document_sha256: String(user.base_document_sha256),
              replacements: preserve.editable_block_ids.map((blockId) => ({
                block_id: blockId,
                replacement_text: `C4 loopback improved ${shotId.slice(0, 8)}`,
              })),
              summary: "Improved editable blocks only",
            };
          }
        } else {
          output = {
            kind: "graph_change_set_draft",
            schema_version: 1,
            operation: {
              op: "replace_text",
              node_id: String(user.target_node_id),
              replacement_text: "由本地 C3 witness 改写🙂",
            },
            summary: "replace one selected node",
          };
        }
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ choices: [{ message: { content: JSON.stringify(output) } }], usage: { total_tokens: 1 } }));
      });
    });
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(8011, "127.0.0.1", resolve);
    });
    await provide(witness);
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  },
  sessionToken: async ({ baseURL }, provide) => {
    if (typeof baseURL !== "string") throw new Error("E2E baseURL is missing");
    const token = process.env[SESSION_TOKEN_ENV];
    if (!token) throw new Error("E2E session token was not initialized by global setup");
    await provide(token);
  },
  context: async ({ context, baseURL, sessionToken }, provide) => {
    if (typeof baseURL !== "string") throw new Error("E2E baseURL is missing");
    const origin = new URL(baseURL).origin;
    const diagnostics = await installContextGuards(context, origin, sessionToken);
    await provide(context);
    for (const pageDiagnostics of diagnostics.pages) {
      if (
        !sameStatusCounts(
          pageDiagnostics.genericClientErrorConsoleErrors,
          pageDiagnostics.expectedClientErrorResponses,
        )
      ) {
        diagnostics.errors.push(
          "console: unpaired Chromium expected client-error response " +
            `(console=${JSON.stringify([...pageDiagnostics.genericClientErrorConsoleErrors])}, ` +
            `response=${JSON.stringify([...pageDiagnostics.expectedClientErrorResponses])})`,
        );
      }
      if (pageDiagnostics.allowedArtifactImportStatusNetworkFailurePaths.size !== 0) {
        diagnostics.errors.push(
          "request: expected artifact-import status network failure was not observed " +
            `${JSON.stringify([...pageDiagnostics.allowedArtifactImportStatusNetworkFailurePaths])}`,
        );
      }
      if (pageDiagnostics.allowedArtifactImportResultNetworkFailurePaths.size !== 0) {
        diagnostics.errors.push(
          "request: expected artifact-import result network failure was not observed " +
            `${JSON.stringify([...pageDiagnostics.allowedArtifactImportResultNetworkFailurePaths])}`,
        );
      }
      if (pageDiagnostics.allowedGraphRevisionConflictPaths.size !== 0) {
        diagnostics.errors.push(
          "response: expected graph revision conflict was not observed " +
            `${JSON.stringify([...pageDiagnostics.allowedGraphRevisionConflictPaths])}`,
        );
      }
      if (pageDiagnostics.allowedConsumedRunAuthorizationPaths.size !== 0) {
        diagnostics.errors.push(
          "response: expected consumed run authorization was not observed " +
            `${JSON.stringify([...pageDiagnostics.allowedConsumedRunAuthorizationPaths])}`,
        );
      }
      if (pageDiagnostics.allowedConsumedAgentProposalPaths.size !== 0) {
        diagnostics.errors.push(
          "response: expected consumed Agent proposal was not observed " +
            `${JSON.stringify([...pageDiagnostics.allowedConsumedAgentProposalPaths])}`,
        );
      }
      const expectedNetworkFailures =
        pageDiagnostics.expectedArtifactImportResultNetworkFailures +
        pageDiagnostics.expectedArtifactImportStatusNetworkFailures;
      if (pageDiagnostics.genericNetworkConsoleErrors !== expectedNetworkFailures) {
        diagnostics.errors.push(
          "console: unpaired Chromium expected network failure",
        );
      }
    }
    expect(diagnostics.errors, "browser/page/CSP errors").toEqual([]);
  },
  allowArtifactImportStatusNetworkFailure: async ({ page }, provide) => {
    await provide((url) => {
      const diagnostics = pageDiagnosticsByPage.get(page);
      if (!diagnostics) {
        throw new Error("browser diagnostics are not registered for this page");
      }
      const pathname = new URL(url).pathname;
      if (!artifactImportStatusPathPattern.test(pathname)) {
        throw new Error(`expected artifact-import status URL is invalid: ${url}`);
      }
      if (diagnostics.allowedArtifactImportStatusNetworkFailurePaths.has(pathname)) {
        throw new Error(`artifact-import status failure is already allowed: ${pathname}`);
      }
      diagnostics.allowedArtifactImportStatusNetworkFailurePaths.add(pathname);
    });
  },
  allowArtifactImportResultNetworkFailure: async ({ page }, provide) => {
    await provide((url) => {
      const diagnostics = pageDiagnosticsByPage.get(page);
      if (!diagnostics) {
        throw new Error("browser diagnostics are not registered for this page");
      }
      const pathname = new URL(url).pathname;
      if (!discardResultPathPattern.test(pathname)) {
        throw new Error(`expected artifact-import result URL is invalid: ${url}`);
      }
      if (diagnostics.allowedArtifactImportResultNetworkFailurePaths.has(pathname)) {
        throw new Error(`artifact-import result failure is already allowed: ${pathname}`);
      }
      diagnostics.allowedArtifactImportResultNetworkFailurePaths.add(pathname);
    });
  },
  allowGraphRevisionConflict: async ({ browser: _browser }, provide) => {
    await provide((targetPage, url) => {
      const diagnostics = pageDiagnosticsByPage.get(targetPage);
      if (!diagnostics) {
        throw new Error("browser diagnostics are not registered for this page");
      }
      const pathname = new URL(url).pathname;
      if (!graphChangeSetPathPattern.test(pathname)) {
        throw new Error(`expected graph change-set conflict URL is invalid: ${url}`);
      }
      if (diagnostics.allowedGraphRevisionConflictPaths.has(pathname)) {
        throw new Error(`graph revision conflict is already allowed: ${pathname}`);
      }
      diagnostics.allowedGraphRevisionConflictPaths.add(pathname);
    });
  },
  allowConsumedRunAuthorization: async ({ page }, provide) => {
    await provide((url) => {
      const diagnostics = pageDiagnosticsByPage.get(page);
      if (!diagnostics) {
        throw new Error("browser diagnostics are not registered for this page");
      }
      const pathname = new URL(url).pathname;
      if (!noEgressDryRunPathPattern.test(pathname)) {
        throw new Error(`expected consumed run authorization URL is invalid: ${url}`);
      }
      if (diagnostics.allowedConsumedRunAuthorizationPaths.has(pathname)) {
        throw new Error(`consumed run authorization is already allowed: ${pathname}`);
      }
      diagnostics.allowedConsumedRunAuthorizationPaths.add(pathname);
    });
  },
  allowConsumedAgentProposal: async ({ page }, provide) => {
    await provide((url) => {
      const diagnostics = pageDiagnosticsByPage.get(page);
      if (!diagnostics) throw new Error("browser diagnostics are not registered for this page");
      const pathname = new URL(url).pathname;
      if (!agentExecutePathPattern.test(pathname)) throw new Error(`expected Agent execute URL is invalid: ${url}`);
      diagnostics.allowedConsumedAgentProposalPaths.add(pathname);
    });
  },
});

export { expect };
