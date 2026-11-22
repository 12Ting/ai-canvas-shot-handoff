import secrets
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from uuid import UUID

from fastapi import HTTPException, Request, status


class F1Capability(str, Enum):
    GRAPH_PROPOSE = "graph.propose"
    GRAPH_APPROVE = "graph.approve"
    GRAPH_APPLY = "graph.apply"
    CONFIG_PROPOSE = "control_config.propose"
    CONFIG_APPROVE = "control_config.approve"
    CONFIG_APPLY = "control_config.apply"
    RUN_REQUEST = "run.request"
    RUN_APPROVE = "run.approve"
    RUN_DRY_RUN = "run.dry_run"
    EVENTS_READ = "events.read"
    EVENTS_CURSOR_ACK = "events.cursor_ack"
    RESOURCE_WRITE = "resource.write"
    MODEL_PROFILE_READ = "model_profile.read"
    AGENT_REQUEST = "agent.request"
    AGENT_APPROVE = "agent.approve"
    AGENT_EXECUTE = "agent.execute"


@dataclass(frozen=True)
class RequestActor:
    actor_id: str
    actor_kind: str
    capabilities: frozenset[F1Capability]

    @classmethod
    def local_human(cls, actor_id: str) -> "RequestActor":
        return cls(
            actor_id=actor_id,
            actor_kind="HUMAN",
            capabilities=frozenset(F1Capability),
        )

    @classmethod
    def ai_canvas(cls, actor_id: str) -> "RequestActor":
        return cls(
            actor_id=actor_id,
            actor_kind="AI_CANVAS",
            capabilities=frozenset({F1Capability.GRAPH_PROPOSE, F1Capability.RUN_REQUEST}),
        )

    def permits(self, capability: F1Capability) -> bool:
        return capability in self.capabilities


class F1CapabilityForbidden(PermissionError):
    def __init__(self, capability: F1Capability) -> None:
        self.capability = capability
        super().__init__(f"F1 capability is required: {capability.value}")


def f1_request_actor(request: Request) -> RequestActor:
    resolver = getattr(request.app.state, "f1_actor_resolver", None)
    if resolver is None:
        return RequestActor.local_human("local-session")
    actor = resolver(request)
    if not isinstance(actor, RequestActor):
        raise RuntimeError("F1 actor resolver returned an invalid actor")
    return actor


def require_f1_capability(request: Request, capability: F1Capability) -> RequestActor:
    actor = f1_request_actor(request)
    if not actor.permits(capability):
        raise F1CapabilityForbidden(capability)
    return actor


class RequestBodyTooLarge(ValueError):
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"request body exceeds {max_bytes} bytes")


def is_graph_changeset_body_limited(method: str, path: str) -> bool:
    if method.upper() != "POST":
        return False
    parts = path.split("/")
    if len(parts) != 6 or parts[1:4] != ["api", "v1", "projects"]:
        return False
    if parts[5] != "graph-change-sets":
        return False
    try:
        UUID(parts[4])
    except ValueError:
        return False
    return True


def is_f1_control_body_limited(method: str, path: str) -> bool:
    if method.upper() != "POST":
        return False
    parts = path.split("/")
    if len(parts) >= 6 and parts[1:4] == ["api", "v1", "projects"]:
        try:
            UUID(parts[4])
        except ValueError:
            return False
        if parts[5] == "prompt-profile-extractions":
            if len(parts) == 6:
                return True
            if len(parts) == 8 and parts[7] in {
                "approval-decisions",
                "execute",
                "materialize",
            }:
                try:
                    UUID(parts[6])
                except ValueError:
                    return False
                return True
        if parts[5] == "prompt-batches":
            if len(parts) == 6 or (len(parts) == 7 and parts[6] == "preflight"):
                return True
            if len(parts) >= 8:
                try:
                    UUID(parts[6])
                except ValueError:
                    return False
            if len(parts) == 8 and parts[7] in {
                "approval",
                "decisions:accept-all-succeeded",
                "retries",
                "retry-approval",
                "finalize",
            }:
                return True
            if len(parts) == 10 and parts[7] == "items" and parts[9] == "decisions":
                try:
                    UUID(parts[8])
                except ValueError:
                    return False
                return True
            if (
                len(parts) == 12
                and parts[7] == "items"
                and parts[9] == "attempts"
                and parts[11] == "execute"
            ):
                try:
                    UUID(parts[8])
                    UUID(parts[10])
                except ValueError:
                    return False
                return True
    if (
        len(parts) in {6, 8}
        and parts[1:4] == ["api", "v1", "projects"]
        and parts[5] == "promotions"
    ):
        try:
            UUID(parts[4])
            if len(parts) == 8:
                UUID(parts[6])
        except ValueError:
            return False
        return len(parts) == 6 or parts[7] in {
            "approval-decisions",
            "apply",
            "undo-proposals",
        }
    if parts == ["", "api", "v1", "control", "config-change-sets"]:
        return True
    if (
        len(parts) == 7
        and parts[1:5] == ["api", "v1", "control", "config-change-sets"]
        and parts[6] in {"approval-decisions", "apply"}
    ):
        try:
            UUID(parts[5])
        except ValueError:
            return False
        return True
    if len(parts) == 6 and parts[1:4] == ["api", "v1", "projects"] and parts[5] == "run-requests":
        try:
            UUID(parts[4])
        except ValueError:
            return False
        return True
    if (
        len(parts) == 8
        and parts[1:4] == ["api", "v1", "projects"]
        and parts[5] == "run-requests"
        and parts[7] in {"approval-decisions", "dry-run"}
    ):
        try:
            UUID(parts[4])
            UUID(parts[6])
        except ValueError:
            return False
        return True
    if (
        len(parts) == 6
        and parts[1:4] == ["api", "v1", "projects"]
        and parts[5] == "agent-proposals"
    ):
        try:
            UUID(parts[4])
        except ValueError:
            return False
        return True
    if (
        len(parts) == 8
        and parts[1:4] == ["api", "v1", "projects"]
        and parts[5] == "agent-proposals"
        and parts[7] in {"approval-decisions", "execute"}
    ):
        try:
            UUID(parts[4])
            UUID(parts[6])
        except ValueError:
            return False
        return True
    return False


def is_resource_metadata_body_limited(method: str, path: str) -> bool:
    if method.upper() != "POST":
        return False
    parts = path.split("/")
    if len(parts) not in {6, 8} or parts[1:4] != ["api", "v1", "projects"]:
        return False
    try:
        project_id = UUID(parts[4])
    except ValueError:
        return False
    if str(project_id) != parts[4].lower() or parts[5] != "resources":
        return False
    if len(parts) == 6:
        return True
    try:
        resource_id = UUID(parts[6])
    except ValueError:
        return False
    return (
        str(resource_id) == parts[6].lower()
        and parts[7] in {"revisions", "archive"}
    )


def is_artifact_content_upload(method: str, path: str) -> bool:
    if method.upper() != "PUT":
        return False
    parts = path.split("/")
    if len(parts) != 8 or parts[1:4] != ["api", "v1", "projects"]:
        return False
    if parts[5] != "artifact-imports" or parts[7] != "content":
        return False
    try:
        project_id = UUID(parts[4])
        import_id = UUID(parts[6])
    except ValueError:
        return False
    return str(project_id) == parts[4].lower() and str(import_id) == parts[6].lower()


def is_large_upload_part_upload(method: str, path: str) -> bool:
    if method.upper() != "PUT":
        return False
    parts = path.split("/")
    if len(parts) != 7 or parts[1:4] != ["api", "v1", "large-uploads"] or parts[5] != "parts":
        return False
    try:
        upload_id = UUID(parts[4])
        part_number = int(parts[6])
    except ValueError:
        return False
    return str(upload_id) == parts[4].lower() and part_number >= 0


def is_media_stream_request(method: str, path: str) -> bool:
    if method.upper() != "GET":
        return False
    parts = path.split("/")
    if len(parts) != 6 or parts[1:4] != ["api", "v1", "media"] or parts[4] != "revisions":
        return False
    try:
        revision_id = UUID(parts[5])
    except ValueError:
        return False
    return str(revision_id) == parts[5].lower()


async def cache_bounded_request_body(request: Request, max_bytes: int) -> None:
    declared_value = request.headers.get("content-length")
    if declared_value is not None:
        try:
            declared_length = int(declared_value)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise RequestBodyTooLarge(max_bytes)

    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > max_bytes - len(body):
            raise RequestBodyTooLarge(max_bytes)
        body.extend(chunk)
    request._body = bytes(body)


def _tokens_equal(candidate: str, expected: str) -> bool:
    try:
        candidate_bytes = candidate.encode("utf-8")
        expected_bytes = expected.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(candidate_bytes, expected_bytes)


class SessionManager:
    def __init__(self, bootstrap_token: str | None = None) -> None:
        self.bootstrap_token = bootstrap_token or secrets.token_urlsafe(32)
        self._access_token: str | None = None
        self._bootstrap_used = False
        self._lock = Lock()

    def exchange(self, candidate: str) -> str:
        with self._lock:
            if self._bootstrap_used or not _tokens_equal(candidate, self.bootstrap_token):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid bootstrap token",
                )
            self._bootstrap_used = True
            if self._access_token is None:
                self._access_token = secrets.token_urlsafe(32)
            return self._access_token

    def issue_development_session(self) -> str:
        with self._lock:
            if self._access_token is None:
                self._access_token = secrets.token_urlsafe(32)
            return self._access_token

    def authenticate(self, candidate: str) -> None:
        with self._lock:
            if self._access_token is None or not _tokens_equal(candidate, self._access_token):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid session token",
                )


def bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token
