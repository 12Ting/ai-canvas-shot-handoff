from ai_canvas.api.security import F1Capability, RequestActor


def test_ai_canvas_actor_has_only_graph_and_run_proposal_capabilities() -> None:
    actor = RequestActor.ai_canvas("assistant-1")

    assert actor.permits(F1Capability.GRAPH_PROPOSE)
    assert actor.permits(F1Capability.RUN_REQUEST)
    assert not actor.permits(F1Capability.GRAPH_APPROVE)
    assert not actor.permits(F1Capability.GRAPH_APPLY)
    assert not actor.permits(F1Capability.CONFIG_PROPOSE)
    assert not actor.permits(F1Capability.CONFIG_APPROVE)
    assert not actor.permits(F1Capability.CONFIG_APPLY)
    assert not actor.permits(F1Capability.RUN_APPROVE)
    assert not actor.permits(F1Capability.RUN_DRY_RUN)


def test_local_human_actor_has_all_f1_control_capabilities() -> None:
    actor = RequestActor.local_human("local-session")

    assert all(actor.permits(capability) for capability in F1Capability)
