from ai_canvas.contracts.graph import GraphSnapshotV3, ResourceBinding


def resolve_effective_prompt_profile_binding(
    graph: GraphSnapshotV3,
    *,
    binding_key: str,
    shot_id,
    prompt_node_id,
) -> ResourceBinding | None:
    candidates = [
        ("NODE", prompt_node_id),
        ("SHOT", shot_id),
        ("PROJECT", None),
    ]
    for scope_kind, scope_id in candidates:
        binding = next(
            (
                item
                for item in graph.resource_bindings
                if item.resource_kind == "PROMPT"
                and item.binding_key == binding_key
                and item.scope_kind == scope_kind
                and item.scope_id == scope_id
            ),
            None,
        )
        if binding is not None:
            return binding
    return None
