from collections.abc import Mapping
from types import MappingProxyType

from ai_canvas.contracts.prompt_profile_extractions import PromptProfileDraftV1
from ai_canvas.contracts.resources import PromptResourcePayload
from ai_canvas.domain.control.canonical import canonical_sha256


FORMAT_RULES: Mapping[str, str] = MappingProxyType(
    {
        "SECTION_PER_BLOCK": "每个结构块单独成段",
        "LABELLED_BLOCKS": "为每个结构块保留明确标签",
        "COMMA_DELIMITED": "同一结构块内使用逗号分隔要点",
        "SINGLE_PARAGRAPH": "将可编辑内容组织为单段",
        "PRESERVE_LANGUAGE": "保持目标提示词的原始语言",
        "CONCISE_SENTENCES": "使用简洁句式",
    }
)
CAMERA_RULES: Mapping[str, str] = MappingProxyType(
    {
        "EXPLICIT_SHOT_SIZE": "明确写出景别",
        "EXPLICIT_CAMERA_ANGLE": "明确写出机位角度",
        "EXPLICIT_LENS": "明确写出镜头或焦段语言",
        "EXPLICIT_CAMERA_MOVEMENT": "明确写出摄影机运动",
    }
)
POSITIVE_RULES: Mapping[str, str] = MappingProxyType(
    {
        "INCLUDE_LIGHTING": "包含光线描述",
        "INCLUDE_PALETTE": "包含色彩描述",
        "INCLUDE_MATERIAL": "包含材质描述",
        "INCLUDE_STYLE_TERMS": "包含视觉风格描述",
        "INCLUDE_SPATIAL_RELATION": "包含空间关系描述",
    }
)
NEGATIVE_RULES: Mapping[str, str] = MappingProxyType(
    {
        "EXPLICIT_NEGATIVE_BLOCK": "使用独立负向约束块",
        "NO_TEXT_WATERMARK": "排除文字和水印",
        "NO_IDENTITY_DRIFT": "排除角色身份漂移",
        "NO_ANATOMY_DEFECTS": "排除明显解剖结构缺陷",
    }
)
APPLICABILITY_RULES: Mapping[str, str] = MappingProxyType(
    {
        "IMAGE_PROMPT": "适用于图片提示词",
        "VIDEO_PROMPT": "适用于视频提示词",
        "GENERAL_VISUAL_PROMPT": "适用于通用视觉提示词",
    }
)


def _values(codes: list[str], rules: Mapping[str, str]) -> list[str]:
    return [rules[code] for code in codes]


def compile_profile_draft(
    draft: PromptProfileDraftV1,
    *,
    source_example_sha256: str,
) -> PromptResourcePayload:
    return PromptResourcePayload(
        content_order=list(draft.content_order),
        detail_density=draft.detail_density,
        format_rules=_values(draft.format_rule_codes, FORMAT_RULES),
        camera_rules=_values(draft.camera_rule_codes, CAMERA_RULES),
        positive_rules=_values(draft.positive_rule_codes, POSITIVE_RULES),
        negative_rules=_values(draft.negative_rule_codes, NEGATIVE_RULES),
        applicability=_values(draft.applicability_codes, APPLICABILITY_RULES),
        source_example_sha256=source_example_sha256,
    )

def rule_code_set_sha256() -> str:
    return canonical_sha256(
        {
            "schema_version": 1,
            "format": dict(FORMAT_RULES),
            "camera": dict(CAMERA_RULES),
            "positive": dict(POSITIVE_RULES),
            "negative": dict(NEGATIVE_RULES),
            "applicability": dict(APPLICABILITY_RULES),
        }
    )
