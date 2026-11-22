from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


PROMPT_DOCUMENT_PORT: Final = "prompt_document"
PROMPT_DOCUMENT_MEDIA_TYPE: Final = (
    "application/vnd.ai-canvas.prompt-document+json"
)


@dataclass(frozen=True)
class NodeDefinition:
    inputs: Mapping[str, str]
    outputs: Mapping[str, str]


NODE_DEFINITIONS: Final[Mapping[str, NodeDefinition]] = MappingProxyType(
    {
        "text_source": NodeDefinition(
            inputs=MappingProxyType({}),
            outputs=MappingProxyType({"text": "text/plain"}),
        ),
        "prompt": NodeDefinition(
            inputs=MappingProxyType({"text": "text/plain"}),
            outputs=MappingProxyType(
                {PROMPT_DOCUMENT_PORT: PROMPT_DOCUMENT_MEDIA_TYPE}
            ),
        ),
        "note": NodeDefinition(
            inputs=MappingProxyType({}),
            outputs=MappingProxyType({}),
        ),
        "media_input": NodeDefinition(
            inputs=MappingProxyType({}),
            outputs=MappingProxyType({}),
        ),
        "shot": NodeDefinition(
            inputs=MappingProxyType(
                {PROMPT_DOCUMENT_PORT: PROMPT_DOCUMENT_MEDIA_TYPE}
            ),
            outputs=MappingProxyType({}),
        ),
    }
)
