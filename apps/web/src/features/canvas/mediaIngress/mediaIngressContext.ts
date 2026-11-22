import { createContext } from "react";
import type { MediaIngressController } from "./mediaIngressTypes";
import type { MediaTargetLocks } from "./mediaTargetLocks";

export type NodeMediaImportPhase =
  | "idle"
  | "queued"
  | "initializing"
  | "receiving"
  | "validating"
  | "completed"
  | "retryable_error"
  | "final_error";
export type NodeMediaImportRecoveryKind =
  | "retry"
  | "reselect"
  | "reload_project"
  | null;
export type NodeMediaImportSnapshot = Readonly<{
  phase: NodeMediaImportPhase;
  active: boolean;
  errorMessage: string | null;
  recoveryKind: NodeMediaImportRecoveryKind;
}>;
export interface NodeMediaImportRuntimePort {
  readonly projectId: string;
  getSnapshot(nodeId: string): NodeMediaImportSnapshot;
  subscribe(nodeId: string, listener: () => void): () => void;
  selectFile(nodeId: string, file: File): void;
  retry(nodeId: string): void;
  requestReload(): void;
}
export interface MediaIngressRuntimePort {
  readonly projectId: string;
  readonly loadEpoch: number;
  readonly nodeImport: NodeMediaImportRuntimePort;
  readonly controller: MediaIngressController | null;
  readonly targetLocks: MediaTargetLocks;
}

export const MediaIngressContext = createContext<MediaIngressRuntimePort | null>(null);
