import base64
import hashlib
import io
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.contracts.graph import GraphSnapshotV3
from ai_canvas.application.handoff_graph import reconcile_graph
from ai_canvas.main import create_app
from ai_canvas.infrastructure.media.native_picker import DirectorySelectionCancelled


class DirectoryChoice:
    def __init__(self, parent):
        self.parent = parent

    def choose_directory(self):
        if self.parent is None:
            raise DirectorySelectionCancelled("已取消")
        return self.parent


def test_switching_legacy_revisions_keeps_shot_identity_and_candidate_ownership():
    first, second, take = (str(uuid4()) for _ in range(3))
    state = {
        "episodes": [{"id": "ep"}],
        "shots": [],
        "groups": [{"shot_id": first, "candidates": [{"id": take}]}],
    }

    def revision(ids):
        return GraphSnapshotV3.model_validate(
            {
                "shots": [
                    {"id": key, "order": order, "video_revision_id": take} for order, key in enumerate(ids)
                ]
            }
        )

    reconcile_graph(state, "both", revision([first, second]))
    assert state["shots"][0]["selected_id"] == take
    assert state["shots"][1]["selected_id"] is None
    assert state["shots"][1]["legacy_video_revision_id"] == take
    state["shots"][0]["dialogue"] = "别回头。"
    reconcile_graph(state, "earlier", revision([second]))
    assert [s["id"] for s in state["shots"]] == [second]
    reconcile_graph(state, "both", revision([first, second]))
    assert state["shots"][0]["dialogue"] == "别回头。"


@contextmanager
def local_client(data_dir, picker):
    app = create_app(Settings(data_dir=data_dir, public_origin="http://testserver", open_browser=False))
    app.state.native_media_picker = picker
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        token = client.post(
            "/api/v1/session/exchange",
            headers={
                "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            },
        ).json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        app.state.handoff_service.files.settle_seconds = 0
        yield client


def test_handoff_reopens_on_changed_disk_and_serves_fixed_media(tmp_path):
    workspace = tmp_path / "项目"
    workspace.mkdir()
    picker = DirectoryChoice(None)
    with local_client(tmp_path / "data", picker) as client:
        create = "/api/v1/handoff/projects"
        cancelled = client.post(create, json={"name": "试片"})
        assert cancelled.status_code == 422
        assert cancelled.json()["detail"]["code"] == "directory_selection_cancelled"
        assert client.get("/api/v1/projects").json() == []
        picker.parent = workspace
        state = client.post(create, json={"name": "试片"}).json()
        assert client.post(create, json={"name": "试片"}).json()["project_id"] == state["project_id"]
        project = "/api/v1/projects/" + state["project_id"]
        handoff = project + "/handoff"

        def act(action, **data):
            response = client.post(handoff + "/actions", json={"action": action, "data": data})
            assert response.is_success, response.text
            return response.json()

        shot = act("add_shot", episode_id=state["episodes"][0]["id"])["shots"][0]
        group = act("add_group", shot_id=shot["id"])["groups"][0]
        base = workspace / "试片" / group["path"]
        original = base64.b64decode((Path(__file__).parents[1] / "fixtures/tiny_mp4.b64").read_bytes())
        clip = base / "生成结果/候选.mp4"
        clip.write_bytes(original)
        (base / "提示词.txt").write_text("回头", encoding="utf-8")
        act("scan")
        state = act("scan")
        take = state["groups"][0]["candidates"][0]
        selected = act("select_candidate", shot_id=shot["id"], candidate_id=take["id"], reason="停顿自然")
        assert selected["groups"][0]["candidates"][0]["selection_reason"] == "停顿自然"
        graph = client.get(project + "/graph").json()
        assert graph["revision_id"] == selected["graph_revision"]
        assert graph["snapshot"]["shots"][0]["video_revision_id"] == take["id"]
        revised = act("save_selection_reason", shot_id=shot["id"], candidate_id=take["id"], reason="能接前镜")
        assert revised["graph_revision"] == selected["graph_revision"]
        act("open_issue", candidate_id=take["id"], note="保留停顿，改转头方向")
        stale = client.post(handoff + "/actions", json={"action": "add_episode", "expected_version": 0})
        assert stale.status_code == 409
        assert client.get(handoff + "/locate", params={"query": str(clip)}).json()["matches"] == [
            {"candidate_id": take["id"], "group_id": group["id"]},
        ]
        play = handoff + "/play/" + take["sha256"]
        auth = client.headers.pop("Authorization")
        assert client.get(play).status_code == 401
        client.headers["Authorization"] = auth
        assert client.post(play, json={}).json()["url"] == play
        del client.headers["Authorization"]
        part = client.get(play, headers={"Range": "bytes=0-15"})
        assert part.status_code == 206 and part.content == original[:16]
        assert client.get(handoff + "/files/" + take["sha256"]).status_code == 401
        client.headers["Authorization"] = auth
        export_url = client.post(handoff + "/export", json={}).json()["url"]
        del client.headers["Authorization"]
        assert client.get(export_url).content.startswith(b"PK")
        assert client.get(export_url, params={"issue_id": shot["id"]}).status_code == 401
        client.headers["Authorization"] = auth
        for edge in ("first", "last"):
            response = client.get(handoff + "/files/" + take["sha256"] + "/frame", params={"edge": edge})
            assert response.status_code == 200 and response.content.startswith(b"\xff\xd8")

    clip.write_bytes(b"incomplete")
    (base / "提示词.txt").write_text("向左回头", encoding="utf-8")
    with local_client(tmp_path / "data", picker) as client:
        state = act("scan")
        assert state["groups"][0]["conditions"][-1]["prompt"] == "向左回头"
        assert state["shots"][0]["selected_id"] == take["id"]
        assert state["groups"][0]["candidates"][0]["selection_reason"] == "能接前镜"
        path = Path(client.get(handoff + "/directory", params={"file_id": take["id"]}).json()["path"])
        assert path != clip and path.name == clip.name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == take["sha256"]
        assert client.get(handoff + "/files/" + take["sha256"]).content == original
        other_clip = clip.with_name("改方向.webm")
        fixture = Path(__file__).parents[1] / "fixtures/tiny_webm.b64"
        other_clip.write_bytes(base64.b64decode(fixture.read_bytes()))
        act("scan")
        candidates = act("scan")["groups"][0]["candidates"]
        replacement = next(c for c in candidates if c["name"] == other_clip.name)
        act("select_candidate", shot_id=shot["id"], candidate_id=replacement["id"])
        with ZipFile(io.BytesIO(client.get(handoff + "/export").content)) as package:
            row = json.loads(package.read("manifest.json"))["episodes"][0]["shots"][0]
            assert row["selected"]["id"] == replacement["id"]
            assert row["selected"]["selection_reason"] == ""
            assert [(i["candidate_id"], i["note"]) for i in row["open_issues"]] == [
                (take["id"], "保留停顿，改转头方向"),
            ]
        new_root = workspace / "搬家"
        (workspace / "试片").rename(new_root)
        assert act("scan").get("directory_error")
        picker.parent = new_root
        assert client.post(handoff + "/rebind", json={}).is_success
        assert not act("scan").get("directory_error")
