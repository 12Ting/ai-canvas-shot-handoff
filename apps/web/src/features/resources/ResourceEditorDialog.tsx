import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  createResource,
  createResourceRevision,
  getResource,
  ResourceRevisionConflictError,
  type ResourceDetailResponse,
} from "../../api/client";
import type { ResourceKind, ResourcePayload } from "./resourceTypes";
import type { ResourceArtifactReference } from "./resourceTypes";
import { RESOURCE_KIND_LABEL, RESOURCE_KINDS } from "./resourceTypes";

const promptKinds = [
  "SUBJECT",
  "SCENE_ACTION",
  "COMPOSITION_CAMERA",
  "LIGHT_COLOR_MATERIAL_STYLE",
  "NEGATIVE_CONSTRAINTS",
  "MOTION",
  "PROVIDER_EXTENSION",
  "FREEFORM",
] as const;

function list(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function initialArtifacts(resource?: ResourceDetailResponse): string {
  return (
    resource?.latest_revision.artifacts
      .map(
        (artifact) =>
          `${artifact.slot}, ${artifact.position}, ${artifact.artifact_revision_id}`,
      )
      .join("\n") ?? ""
  );
}

function parseArtifacts(value: string): ResourceArtifactReference[] {
  const rows = value.split(/\r?\n/).map((row) => row.trim()).filter(Boolean);
  if (rows.length > 64) throw new Error("ArtifactRevision 固定最多 64 项。");
  return rows.map((row, index) => {
    const [slot, rawPosition, revisionId, ...extra] = row
      .split(",")
      .map((part) => part.trim());
    const position = Number(rawPosition);
    if (
      !slot ||
      extra.length > 0 ||
      !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/.test(slot) ||
      !Number.isSafeInteger(position) ||
      position < 0 ||
      !revisionId ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        revisionId,
      )
    ) {
      throw new Error(`ArtifactRevision 固定第 ${index + 1} 行格式无效。`);
    }
    return { slot, position, artifact_revision_id: revisionId };
  });
}

function initialPrimary(resource?: ResourceDetailResponse): string {
  const payload = resource?.latest_revision.payload;
  if (!payload) return "";
  switch (payload.kind) {
    case "STYLE": return payload.summary;
    case "CHARACTER": return payload.canonical_name;
    case "PROMPT": return payload.format_rules.join("\n");
    case "MEDIA": return payload.usage_notes;
  }
}

function initialSecondary(resource?: ResourceDetailResponse): string {
  const payload = resource?.latest_revision.payload;
  if (!payload) return "";
  switch (payload.kind) {
    case "STYLE": return payload.negative_constraints?.join("\n") ?? "";
    case "CHARACTER": return payload.identity_anchor;
    case "PROMPT": return payload.content_order.join(", ");
    case "MEDIA": return payload.expected_media_kind ?? "";
  }
}

type AdvancedFields = {
  palette: string;
  lighting: string;
  materials: string;
  cameraLanguage: string;
  lockedTraits: string;
  wardrobeNotes: string;
  detailDensity: "LOW" | "MEDIUM" | "HIGH";
  cameraRules: string;
  positiveRules: string;
  negativeRules: string;
  applicability: string;
  sourceExampleSha256: string;
};

function initialAdvanced(resource?: ResourceDetailResponse): AdvancedFields {
  const initial: AdvancedFields = {
    palette: "",
    lighting: "",
    materials: "",
    cameraLanguage: "",
    lockedTraits: "",
    wardrobeNotes: "",
    detailDensity: "MEDIUM",
    cameraRules: "",
    positiveRules: "",
    negativeRules: "",
    applicability: "",
    sourceExampleSha256: "",
  };
  const payload = resource?.latest_revision.payload;
  if (!payload) return initial;
  if (payload.kind === "STYLE") {
    return {
      ...initial,
      palette: payload.palette?.join("\n") ?? "",
      lighting: payload.lighting?.join("\n") ?? "",
      materials: payload.materials?.join("\n") ?? "",
      cameraLanguage: payload.camera_language?.join("\n") ?? "",
    };
  }
  if (payload.kind === "CHARACTER") {
    return {
      ...initial,
      lockedTraits: payload.locked_traits?.join("\n") ?? "",
      wardrobeNotes: payload.wardrobe_notes?.join("\n") ?? "",
    };
  }
  if (payload.kind === "PROMPT") {
    return {
      ...initial,
      detailDensity: payload.detail_density,
      cameraRules: payload.camera_rules?.join("\n") ?? "",
      positiveRules: payload.positive_rules?.join("\n") ?? "",
      negativeRules: payload.negative_rules?.join("\n") ?? "",
      applicability: payload.applicability?.join("\n") ?? "",
      sourceExampleSha256: payload.source_example_sha256 ?? "",
    };
  }
  return initial;
}

function makePayload(
  kind: ResourceKind,
  primary: string,
  secondary: string,
  advanced: AdvancedFields,
): ResourcePayload {
  switch (kind) {
    case "STYLE":
      return {
        kind,
        summary: primary.trim(),
        palette: list(advanced.palette),
        lighting: list(advanced.lighting),
        materials: list(advanced.materials),
        camera_language: list(advanced.cameraLanguage),
        negative_constraints: list(secondary),
      };
    case "CHARACTER":
      return {
        kind,
        canonical_name: primary.trim(),
        identity_anchor: secondary.trim(),
        locked_traits: list(advanced.lockedTraits),
        wardrobe_notes: list(advanced.wardrobeNotes),
      };
    case "PROMPT": {
      const contentOrder = list(secondary).filter((item): item is (typeof promptKinds)[number] =>
        promptKinds.includes(item as (typeof promptKinds)[number]),
      );
      return {
        kind,
        content_order: contentOrder,
        detail_density: advanced.detailDensity,
        format_rules: list(primary),
        camera_rules: list(advanced.cameraRules),
        positive_rules: list(advanced.positiveRules),
        negative_rules: list(advanced.negativeRules),
        applicability: list(advanced.applicability),
        source_example_sha256: advanced.sourceExampleSha256.trim() || null,
      };
    }
    case "MEDIA":
      return {
        kind,
        usage_notes: primary.trim(),
        expected_media_kind:
          secondary === "image" || secondary === "video" ? secondary : null,
      };
  }
}

export function ResourceEditorDialog({
  initialKind,
  onClose,
  onSaved,
  projectId,
  resource,
  trigger,
}: {
  initialKind?: ResourceKind;
  onClose(): void;
  onSaved(resourceId: string): void;
  projectId: string;
  resource?: ResourceDetailResponse;
  trigger: HTMLElement | null;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const [kind, setKind] = useState<ResourceKind>(resource?.kind ?? initialKind ?? "STYLE");
  const [name, setName] = useState(resource?.name ?? "");
  const [primary, setPrimary] = useState(initialPrimary(resource));
  const [secondary, setSecondary] = useState(initialSecondary(resource));
  const initialAdvancedFields = initialAdvanced(resource);
  const [palette, setPalette] = useState(initialAdvancedFields.palette);
  const [lighting, setLighting] = useState(initialAdvancedFields.lighting);
  const [materials, setMaterials] = useState(initialAdvancedFields.materials);
  const [cameraLanguage, setCameraLanguage] = useState(initialAdvancedFields.cameraLanguage);
  const [lockedTraits, setLockedTraits] = useState(initialAdvancedFields.lockedTraits);
  const [wardrobeNotes, setWardrobeNotes] = useState(initialAdvancedFields.wardrobeNotes);
  const [detailDensity, setDetailDensity] = useState(initialAdvancedFields.detailDensity);
  const [cameraRules, setCameraRules] = useState(initialAdvancedFields.cameraRules);
  const [positiveRules, setPositiveRules] = useState(initialAdvancedFields.positiveRules);
  const [negativeRules, setNegativeRules] = useState(initialAdvancedFields.negativeRules);
  const [applicability, setApplicability] = useState(initialAdvancedFields.applicability);
  const [sourceExampleSha256, setSourceExampleSha256] = useState(initialAdvancedFields.sourceExampleSha256);
  const [tags, setTags] = useState(
    resource?.latest_revision.tags.map((tag) => tag.display_tag).join(", ") ?? "",
  );
  const [artifactsText, setArtifactsText] = useState(initialArtifacts(resource));
  const [baseRevisionId, setBaseRevisionId] = useState(resource?.latest_revision.id ?? null);
  const [currentRevisionId, setCurrentRevisionId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    dialogRef.current?.focus();
    return () => trigger?.focus();
  }, [trigger]);

  const save = async () => {
    let artifacts: ResourceArtifactReference[];
    try {
      artifacts = parseArtifacts(artifactsText);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "ArtifactRevision 固定格式无效。");
      return;
    }
    const payload = makePayload(kind, primary, secondary, {
      palette,
      lighting,
      materials,
      cameraLanguage,
      lockedTraits,
      wardrobeNotes,
      detailDensity,
      cameraRules,
      positiveRules,
      negativeRules,
      applicability,
      sourceExampleSha256,
    });
    if (!name.trim() || !primary.trim() || (kind === "CHARACTER" && !secondary.trim())) {
      setNotice("请填写名称和当前类型的必填内容。");
      return;
    }
    if (kind === "PROMPT" && (payload.kind !== "PROMPT" || payload.content_order.length === 0)) {
      setNotice("提示词资源至少需要一个有效的内容顺序类型。");
      return;
    }
    if (
      kind === "PROMPT" &&
      sourceExampleSha256.trim() &&
      !/^[0-9a-f]{64}$/.test(sourceExampleSha256.trim())
    ) {
      setNotice("来源示例 SHA-256 必须是 64 位小写十六进制字符。");
      return;
    }
    setSubmitting(true);
    setNotice("");
    try {
      if (resource && baseRevisionId) {
        const revision = await createResourceRevision(projectId, resource.id, {
          base_revision_id: baseRevisionId,
          payload,
          tags: list(tags),
          artifacts,
        });
        onSaved(revision.resource_id);
      } else {
        const created = await createResource(projectId, {
          name: name.trim(),
          payload,
          tags: list(tags),
          artifacts,
        });
        onSaved(created.id);
      }
    } catch (error: unknown) {
      if (error instanceof ResourceRevisionConflictError) {
        setCurrentRevisionId(error.currentRevisionId);
        setNotice("资源已产生新版本。你的输入已保留；请重新载入基线后手动再次保存。");
      } else {
        setNotice(error instanceof Error ? error.message : "资源保存失败，请重试。");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const reloadBase = async () => {
    if (!resource) return;
    setSubmitting(true);
    try {
      const latest = await getResource(resource.id);
      setBaseRevisionId(latest.latest_revision.id);
      setCurrentRevisionId(null);
      setNotice("已重新载入最新基线；编辑内容仍保留，请检查后手动保存。");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "重新载入失败。");
    } finally {
      setSubmitting(false);
    }
  };

  const secondaryLabel =
    kind === "STYLE"
      ? "负向约束（每行一项）"
      : kind === "CHARACTER"
        ? "身份锚点"
        : kind === "PROMPT"
          ? "内容顺序（类型名，逗号分隔）"
          : "预期媒体类型（image / video，可空）";
  const primaryLabel =
    kind === "STYLE"
      ? "风格摘要"
      : kind === "CHARACTER"
        ? "角色规范名"
        : kind === "PROMPT"
          ? "格式规则（每行一项）"
          : "使用说明";

  return createPortal(
    <div className="resource-modal" role="presentation">
      <section
        aria-labelledby={titleId}
        aria-modal="true"
        className="resource-dialog resource-editor"
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <p className="resource-dialog__eyebrow">VERSIONED RESOURCE</p>
        <h2 id={titleId}>{resource ? `创建新版本 · ${resource.name}` : "新建资源"}</h2>
        <label>
          类型
          <select disabled={Boolean(resource)} onChange={(event) => setKind(event.target.value as ResourceKind)} value={kind}>
            {RESOURCE_KINDS.map((value) => (
              <option key={value} value={value}>{RESOURCE_KIND_LABEL[value]}</option>
            ))}
          </select>
        </label>
        <label>
          名称
          <input disabled={Boolean(resource)} maxLength={200} onChange={(event) => setName(event.target.value)} value={name} />
        </label>
        <label>
          {primaryLabel}
          <textarea maxLength={10_000} onChange={(event) => setPrimary(event.target.value)} rows={5} value={primary} />
        </label>
        <label>
          {secondaryLabel}
          {kind === "MEDIA" ? (
            <select onChange={(event) => setSecondary(event.target.value)} value={secondary}>
              <option value="">不限定</option>
              <option value="image">image</option>
              <option value="video">video</option>
            </select>
          ) : (
            <textarea maxLength={10_000} onChange={(event) => setSecondary(event.target.value)} rows={3} value={secondary} />
          )}
        </label>
        {kind === "STYLE" ? (
          <div className="resource-editor__field-grid">
            <label>色板（每行一项）<textarea onChange={(event) => setPalette(event.target.value)} rows={2} value={palette} /></label>
            <label>光线（每行一项）<textarea onChange={(event) => setLighting(event.target.value)} rows={2} value={lighting} /></label>
            <label>材质（每行一项）<textarea onChange={(event) => setMaterials(event.target.value)} rows={2} value={materials} /></label>
            <label>镜头语言（每行一项）<textarea onChange={(event) => setCameraLanguage(event.target.value)} rows={2} value={cameraLanguage} /></label>
          </div>
        ) : null}
        {kind === "CHARACTER" ? (
          <div className="resource-editor__field-grid">
            <label>锁定特征（每行一项）<textarea onChange={(event) => setLockedTraits(event.target.value)} rows={3} value={lockedTraits} /></label>
            <label>服装备注（每行一项）<textarea onChange={(event) => setWardrobeNotes(event.target.value)} rows={3} value={wardrobeNotes} /></label>
          </div>
        ) : null}
        {kind === "PROMPT" ? (
          <div className="resource-editor__field-grid">
            <label>细节密度<select onChange={(event) => setDetailDensity(event.target.value as AdvancedFields["detailDensity"])} value={detailDensity}><option value="LOW">LOW</option><option value="MEDIUM">MEDIUM</option><option value="HIGH">HIGH</option></select></label>
            <label>镜头规则（每行一项）<textarea onChange={(event) => setCameraRules(event.target.value)} rows={2} value={cameraRules} /></label>
            <label>正向规则（每行一项）<textarea onChange={(event) => setPositiveRules(event.target.value)} rows={2} value={positiveRules} /></label>
            <label>负向规则（每行一项）<textarea onChange={(event) => setNegativeRules(event.target.value)} rows={2} value={negativeRules} /></label>
            <label>适用范围（每行一项）<textarea onChange={(event) => setApplicability(event.target.value)} rows={2} value={applicability} /></label>
            <label>来源示例 SHA-256（可空）<input maxLength={64} onChange={(event) => setSourceExampleSha256(event.target.value)} pattern="[0-9a-f]{64}" value={sourceExampleSha256} /></label>
          </div>
        ) : null}
        <label>
          标签（逗号或换行分隔）
          <textarea onChange={(event) => setTags(event.target.value)} rows={2} value={tags} />
        </label>
        <label>
          ArtifactRevision 固定（可选；每行 slot, position, UUID）
          <textarea
            aria-label="ArtifactRevision 固定"
            onChange={(event) => setArtifactsText(event.target.value)}
            placeholder="reference, 0, 00000000-0000-4000-8000-000000000000"
            rows={3}
            value={artifactsText}
          />
        </label>
        {currentRevisionId ? (
          <button disabled={submitting} onClick={() => void reloadBase()} type="button">
            重新载入最新基线 {currentRevisionId.slice(0, 12)}
          </button>
        ) : null}
        {notice ? <p aria-live="polite" role="status">{notice}</p> : null}
        <div className="resource-dialog__actions">
          <button disabled={submitting} onClick={onClose} type="button">取消</button>
          <button disabled={submitting} onClick={() => void save()} type="button">
            {submitting ? "正在保存…" : resource ? "创建新版本" : "创建资源"}
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}
