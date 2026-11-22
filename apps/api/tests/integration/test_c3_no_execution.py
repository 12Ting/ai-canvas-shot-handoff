import importlib.util
from pathlib import Path


_SOURCE = Path(__file__).parents[1] / "unit" / "test_agent_proposal_service.py"
_SPEC = importlib.util.spec_from_file_location("c3_agent_service_witness", _SOURCE)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_agent_flow = _MODULE.test_agent_graph_output_creates_pending_new_branch_changeset_once


def test_request_and_approval_are_zero_execution_and_execute_calls_once(
    tmp_path: Path,
) -> None:
    _agent_flow(tmp_path)
