import { apiFetch } from "../../api/client";

export type MediaFile = {
  id: string;
  name: string;
  path: string;
  sha256: string;
  kind: "image" | "video" | "audio";
  mime: string;
  available?: boolean;
  version?: number;
  version_label?: string;
};
export type Candidate = MediaFile & {
  condition_id: string | null;
  selection_reason?: string;
};
export type Binding = { asset_id: string; version_id: string };
export type Condition = {
  id: string;
  prompt: string;
  config: string;
  references: MediaFile[];
  bindings: Binding[];
};
export type Group = {
  id: string;
  title: string;
  path: string;
  shot_id: string;
  conditions: Condition[];
  references: MediaFile[];
  candidates: Candidate[];
  bindings: Binding[];
};
export type AssetKind = "character" | "scene" | "prop" | "sound";
export type Asset = {
  id: string;
  kind: AssetKind;
  name: string;
  path: string;
  versions: MediaFile[];
};
export type Sound = Binding & { role: "temp" | "final" | "reference" };
export type Shot = {
  id: string;
  title: string;
  order: number;
  episode_id: string;
  selected_id: string | null;
  dialogue: string;
  sounds: Sound[];
  legacy_video_revision_id?: string;
  dialogue_history?: { text: string; at: string }[];
  sound_history?: { sounds: Sound[]; at: string }[];
};
export type Issue = {
  id: string;
  shot_id: string;
  candidate_id: string;
  condition_id: string | null;
  note: string;
  status: "open" | "resolved";
  repair_id?: string | null;
  context?: {
    previous_id?: string | null;
    next_id?: string | null;
    previous_temporary?: boolean;
    next_temporary?: boolean;
    previous_shot_id?: string | null;
    next_shot_id?: string | null;
    dialogue?: string;
    sounds?: Sound[];
  };
};
export type Workspace = {
  project_id: string;
  version?: number;
  name: string;
  episodes: { id: string; title: string }[];
  shots: Shot[];
  groups: Group[];
  assets: Asset[];
  issues: Issue[];
  scan_warnings?: Record<string, string>;
  directory_error?: string;
};
export type Action = (
  action: string,
  data?: Record<string, unknown>,
) => Promise<Workspace>;
export type FileLocation = {
  group_id?: string;
  asset_id?: string;
  file_id?: string;
  location?: string;
};
export const kinds: Record<AssetKind, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
  sound: "声音",
};
export const roles: Record<Sound["role"], string> = {
  temp: "临时配音",
  final: "正式配音",
  reference: "声音参考",
};
export const base = (id: string) =>
  "/api/v1/projects/" + encodeURIComponent(id) + "/handoff";

export class DirectorySelectionCancelled extends Error {}

async function checked(path: string, init?: RequestInit) {
  const response = await apiFetch(path, init);
  if (response.ok) return response;
  const body = await response.json().catch(() => null);
  const message = body?.detail ?? body?.message ?? body?.code;
  if (
    response.status === 422 &&
    body?.detail?.code === "directory_selection_cancelled"
  ) {
    throw new DirectorySelectionCancelled();
  }
  throw new Error(
    typeof message === "string"
      ? message
      : "操作失败（HTTP " + response.status + "）",
  );
}
export const getWorkspace = async (id: string): Promise<Workspace> =>
  (await checked(base(id))).json();
export async function post<T>(url: string, body: object): Promise<T> {
  return (
    await checked(url, { method: "POST", body: JSON.stringify(body) })
  ).json();
}
export const createDirectoryProject = (name: string) =>
  post<Workspace>("/api/v1/handoff/projects", { name });
export const bindDirectory = (id: string) =>
  post<Workspace>(base(id) + "/bind", {});
export const rebindDirectory = (id: string) =>
  post<Workspace>(base(id) + "/rebind", {});
export async function filePath(id: string, where: FileLocation) {
  const params = new URLSearchParams();
  Object.entries(where).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return (await (await checked(base(id) + "/directory?" + params)).json())
    .path as string;
}
export async function locate(
  id: string,
  query: string,
): Promise<{ matches: { group_id: string; candidate_id: string }[] }> {
  return (
    await checked(base(id) + "/locate?" + new URLSearchParams({ query }))
  ).json();
}
export async function fetchBlob(path: string, signal?: AbortSignal) {
  return (await checked(path, { signal })).blob();
}
export async function downloadPackage(
  id: string,
  name: string,
  issue?: string,
) {
  const { url } = await post<{ url: string }>(
    base(id) +
      "/export" +
      (issue ? "?" + new URLSearchParams({ issue_id: issue }) : ""),
    {},
  );
  const target = new URL(url, location.href);
  if (target.origin !== location.origin)
    throw new Error("下载地址超出本机项目");
  const a = document.createElement("a");
  a.href = target.href;
  a.download =
    name.replace(/[\\/:*?"<>|]/g, "_") + (issue ? "-补镜" : "-选片") + ".zip";
  document.body.append(a);
  a.click();
  a.remove();
}
export function studioUrl(id: string, view: "shots" | "canvas") {
  return "?" + new URLSearchParams({ project: id, view });
}
export type Located = { group: Group; candidate: Candidate };
export function groupLabel(groups: Group[], group: Group): string {
  const repeated = groups.some(
    (other) => other.id !== group.id && other.shot_id === group.shot_id && other.title === group.title,
  );
  const directory = group.path.split("/").at(-1);
  return repeated && directory ? group.title + "（" + directory + "）" : group.title;
}
export function candidateLabel(groups: Group[], { group, candidate }: Located): string {
  const version = candidate.version ?? group.candidates.indexOf(candidate) + 1;
  return [groupLabel(groups, group), "V" + version, candidate.name].join(" / ");
}
export function allCandidates(state: Workspace, shotId?: string): Located[] {
  return state.groups
    .filter((g) => !shotId || shotId === g.shot_id)
    .flatMap((group) =>
      group.candidates.map((candidate) => ({ group, candidate })),
    );
}
export const errText = (e: unknown) =>
  e instanceof Error ? e.message : String(e);
