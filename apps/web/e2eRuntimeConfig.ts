import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const canvasRoot = path.resolve(webRoot, "../..");
const localDataRoot = path.join(canvasRoot, ".local");
const minimumUnprivilegedPort = 1_024;
const maximumPort = 65_535;

type Environment = Readonly<Record<string, string | undefined>>;

export type DevelopmentE2EConfig = {
  apiPort: number;
  webPort: number;
  apiOrigin: string;
  webOrigin: string;
  dataDirectory: string;
};

export type ReleaseE2EConfig = {
  port: number;
  origin: string;
  dataDirectory: string;
};

export type NoAuthE2EConfig = {
  apiPort: number;
  webPort: number;
  apiOrigin: string;
  webOrigin: string;
  dataDirectory: string;
};

function readPort(
  environment: Environment,
  name: string,
  fallback: number,
): number {
  const raw = environment[name];
  if (raw === undefined || raw === "") return fallback;
  if (!/^[1-9][0-9]{3,4}$/.test(raw)) {
    throw new Error(`${name} must be a decimal loopback port from 1024 to 65535`);
  }
  const port = Number(raw);
  if (port < minimumUnprivilegedPort || port > maximumPort) {
    throw new Error(`${name} must be a decimal loopback port from 1024 to 65535`);
  }
  return port;
}

function isStrictChild(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return (
    relative.length > 0 &&
    relative !== ".." &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}

function pathKey(value: string): string {
  const normalized = path.normalize(value);
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

function pathsMatch(first: string, second: string): boolean {
  return pathKey(path.resolve(first)) === pathKey(path.resolve(second));
}

function assertDistinctExplicitDataDirectories(environment: Environment): void {
  const entries = [
    ["AI_CANVAS_E2E_DATA_DIR", environment.AI_CANVAS_E2E_DATA_DIR],
    ["AI_CANVAS_RELEASE_E2E_DATA_DIR", environment.AI_CANVAS_RELEASE_E2E_DATA_DIR],
    ["AI_CANVAS_NOAUTH_E2E_DATA_DIR", environment.AI_CANVAS_NOAUTH_E2E_DATA_DIR],
  ] as const;
  for (let firstIndex = 0; firstIndex < entries.length; firstIndex += 1) {
    const [firstName, firstValue] = entries[firstIndex] ?? [];
    if (!firstName || !firstValue) continue;
    for (let secondIndex = firstIndex + 1; secondIndex < entries.length; secondIndex += 1) {
      const [secondName, secondValue] = entries[secondIndex] ?? [];
      if (secondName && secondValue && pathsMatch(firstValue, secondValue)) {
        throw new Error(`${firstName} and ${secondName} must differ`);
      }
    }
  }
}

function assertDistinctExplicitPorts(environment: Environment): void {
  const definitions = [
    ["AI_CANVAS_E2E_API_PORT", 18_765],
    ["AI_CANVAS_E2E_WEB_PORT", 15_173],
    ["AI_CANVAS_RELEASE_E2E_PORT", 18_876],
    ["AI_CANVAS_NOAUTH_E2E_API_PORT", 29_876],
    ["AI_CANVAS_NOAUTH_E2E_WEB_PORT", 25_174],
  ] as const;
  const explicit = definitions.flatMap(([name, fallback]) => {
    const raw = environment[name];
    return raw === undefined || raw === ""
      ? []
      : [{ name, port: readPort(environment, name, fallback) }];
  });
  for (let firstIndex = 0; firstIndex < explicit.length; firstIndex += 1) {
    const first = explicit[firstIndex];
    if (!first) continue;
    for (let secondIndex = firstIndex + 1; secondIndex < explicit.length; secondIndex += 1) {
      const second = explicit[secondIndex];
      if (second && first.port === second.port) {
        throw new Error(`${first.name} and ${second.name} ports must differ`);
      }
    }
  }
}

function assertPhysicalDataBoundary(name: string, candidate: string): void {
  const relative = path.relative(localDataRoot, candidate);
  const parts = relative.split(path.sep).filter(Boolean);
  if (parts.length === 0 || !/^e2e(?:-|$)/i.test(parts[0] ?? "")) {
    throw new Error(`${name} must use a test-specific e2e or e2e-* directory`);
  }

  const paths = [localDataRoot];
  let current = localDataRoot;
  for (const part of parts) {
    current = path.join(current, part);
    paths.push(current);
  }

  let physicalRoot: string | null = null;
  for (const existingPath of paths) {
    let metadata: fs.Stats;
    try {
      metadata = fs.lstatSync(existingPath);
    } catch (error: unknown) {
      if (
        typeof error === "object" &&
        error !== null &&
        "code" in error &&
        error.code === "ENOENT"
      ) {
        break;
      }
      throw error;
    }
    if (metadata.isSymbolicLink()) {
      throw new Error(`${name} cannot traverse a symlink, junction or reparse point`);
    }
    if (!metadata.isDirectory()) {
      throw new Error(`${name} cannot traverse a non-directory path`);
    }
    const physicalPath = fs.realpathSync.native(existingPath);
    if (physicalRoot === null) {
      physicalRoot = physicalPath;
    } else if (
      !pathsMatch(physicalRoot, physicalPath) &&
      !isStrictChild(physicalRoot, physicalPath)
    ) {
      throw new Error(`${name} physically escapes the intended .local test root`);
    }
  }
}

function readDataDirectory(
  environment: Environment,
  name: string,
  prefix: string,
): string {
  const configured = environment[name];
  const resolved = configured
    ? path.resolve(configured)
    : path.join(
        localDataRoot,
        `${prefix}-${process.pid}-${Date.now()}-${randomUUID()}`,
      );
  if (!isStrictChild(localDataRoot, resolved)) {
    throw new Error(`${name} must resolve to a child of ${localDataRoot}`);
  }
  assertPhysicalDataBoundary(name, resolved);
  return resolved;
}

export function readDevelopmentE2EConfig(
  environment: Environment = process.env,
): DevelopmentE2EConfig {
  assertDistinctExplicitDataDirectories(environment);
  assertDistinctExplicitPorts(environment);
  const apiPort = readPort(environment, "AI_CANVAS_E2E_API_PORT", 18_765);
  const webPort = readPort(environment, "AI_CANVAS_E2E_WEB_PORT", 15_173);
  if (apiPort === webPort) {
    throw new Error("AI_CANVAS_E2E_API_PORT and AI_CANVAS_E2E_WEB_PORT must differ");
  }
  return {
    apiPort,
    webPort,
    apiOrigin: `http://127.0.0.1:${apiPort}`,
    webOrigin: `http://127.0.0.1:${webPort}`,
    dataDirectory: readDataDirectory(
      environment,
      "AI_CANVAS_E2E_DATA_DIR",
      "e2e-development",
    ),
  };
}

export function readDevelopmentApiOrigin(
  environment: Environment = process.env,
): string {
  const apiPort = readPort(environment, "AI_CANVAS_E2E_API_PORT", 18_765);
  return `http://127.0.0.1:${apiPort}`;
}

export function readReleaseE2EConfig(
  environment: Environment = process.env,
): ReleaseE2EConfig {
  assertDistinctExplicitDataDirectories(environment);
  assertDistinctExplicitPorts(environment);
  const port = readPort(environment, "AI_CANVAS_RELEASE_E2E_PORT", 18_876);
  return {
    port,
    origin: `http://127.0.0.1:${port}`,
    dataDirectory: readDataDirectory(
      environment,
      "AI_CANVAS_RELEASE_E2E_DATA_DIR",
      "e2e-release",
    ),
  };
}

export function readNoAuthE2EConfig(
  environment: Environment = process.env,
): NoAuthE2EConfig {
  assertDistinctExplicitDataDirectories(environment);
  assertDistinctExplicitPorts(environment);
  const apiPort = readPort(
    environment,
    "AI_CANVAS_NOAUTH_E2E_API_PORT",
    29_876,
  );
  const webPort = readPort(
    environment,
    "AI_CANVAS_NOAUTH_E2E_WEB_PORT",
    25_174,
  );
  if (apiPort === webPort) {
    throw new Error(
      "AI_CANVAS_NOAUTH_E2E_API_PORT and AI_CANVAS_NOAUTH_E2E_WEB_PORT must differ",
    );
  }
  return {
    apiPort,
    webPort,
    apiOrigin: `http://127.0.0.1:${apiPort}`,
    webOrigin: `http://127.0.0.1:${webPort}`,
    dataDirectory: readDataDirectory(
      environment,
      "AI_CANVAS_NOAUTH_E2E_DATA_DIR",
      "e2e-noauth",
    ),
  };
}
