from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from ai_canvas.contracts.graph import GraphSnapshotV3, PromptNodeV3, ShotNodeV3
from ai_canvas.domain.graph.node_registry import PROMPT_DOCUMENT_PORT


ShotPromptBlockerCode = Literal[
    "SHOT_NOT_FOUND",
    "SHOT_NODE_NOT_FOUND",
    "SHOT_PROMPT_NOT_LINKED",
    "SHOT_PROMPT_LINK_AMBIGUOUS",
    "PROMPT_NODE_NOT_FOUND",
    "PROMPT_NODE_SHARED",
]


@dataclass(frozen=True)
class ShotPromptTarget:
    shot_id: UUID
    shot_node_id: UUID
    prompt_node_id: UUID
    shot_order: int


@dataclass(frozen=True)
class ShotPromptBlocker:
    shot_id: UUID
    code: ShotPromptBlockerCode


@dataclass(frozen=True)
class ShotPromptResolution:
    targets: tuple[ShotPromptTarget, ...]
    blockers: tuple[ShotPromptBlocker, ...]


def resolve_shot_prompt_targets(
    graph: GraphSnapshotV3,
    *,
    selected_shot_ids: list[UUID] | None = None,
) -> ShotPromptResolution:
    shots_by_id = {shot.id: shot for shot in graph.shots}
    shot_nodes_by_shot_id: dict[UUID, list[ShotNodeV3]] = {}
    prompt_node_ids = {
        node.id for node in graph.nodes if isinstance(node, PromptNodeV3)
    }
    for node in graph.nodes:
        if isinstance(node, ShotNodeV3):
            shot_nodes_by_shot_id.setdefault(node.data.shot_id, []).append(node)

    blockers: list[ShotPromptBlocker] = []
    if selected_shot_ids is None:
        selected_shots = sorted(graph.shots, key=lambda shot: (shot.order, shot.id))
    else:
        selected_ids = set(selected_shot_ids)
        selected_shots = sorted(
            (shot for shot in graph.shots if shot.id in selected_ids),
            key=lambda shot: (shot.order, shot.id),
        )
        for shot_id in selected_shot_ids:
            if shot_id not in shots_by_id:
                blockers.append(
                    ShotPromptBlocker(shot_id=shot_id, code="SHOT_NOT_FOUND")
                )

    semantic_edges = [
        edge
        for edge in graph.edges
        if edge.source_port == PROMPT_DOCUMENT_PORT
        and edge.target_port == PROMPT_DOCUMENT_PORT
    ]
    outgoing_counts: dict[UUID, int] = {}
    incoming_by_node: dict[UUID, list[UUID]] = {}
    for edge in semantic_edges:
        outgoing_counts[edge.source] = outgoing_counts.get(edge.source, 0) + 1
        incoming_by_node.setdefault(edge.target, []).append(edge.source)

    targets: list[ShotPromptTarget] = []
    for shot in selected_shots:
        shot_nodes = shot_nodes_by_shot_id.get(shot.id, [])
        if len(shot_nodes) != 1:
            blockers.append(
                ShotPromptBlocker(shot_id=shot.id, code="SHOT_NODE_NOT_FOUND")
            )
            continue
        shot_node = shot_nodes[0]
        source_ids = incoming_by_node.get(shot_node.id, [])
        if not source_ids:
            blockers.append(
                ShotPromptBlocker(shot_id=shot.id, code="SHOT_PROMPT_NOT_LINKED")
            )
            continue
        if len(source_ids) != 1:
            blockers.append(
                ShotPromptBlocker(
                    shot_id=shot.id,
                    code="SHOT_PROMPT_LINK_AMBIGUOUS",
                )
            )
            continue
        prompt_node_id = source_ids[0]
        if prompt_node_id not in prompt_node_ids:
            blockers.append(
                ShotPromptBlocker(shot_id=shot.id, code="PROMPT_NODE_NOT_FOUND")
            )
            continue
        if outgoing_counts[prompt_node_id] != 1:
            blockers.append(
                ShotPromptBlocker(shot_id=shot.id, code="PROMPT_NODE_SHARED")
            )
            continue
        targets.append(
            ShotPromptTarget(
                shot_id=shot.id,
                shot_node_id=shot_node.id,
                prompt_node_id=prompt_node_id,
                shot_order=shot.order,
            )
        )

    return ShotPromptResolution(targets=tuple(targets), blockers=tuple(blockers))


__all__ = [
    "PROMPT_DOCUMENT_PORT",
    "ShotPromptBlocker",
    "ShotPromptResolution",
    "ShotPromptTarget",
    "resolve_shot_prompt_targets",
]
