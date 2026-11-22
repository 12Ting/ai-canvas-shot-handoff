from uuid import uuid4

from fastapi.testclient import TestClient

from ai_canvas.config import Settings
from ai_canvas.main import create_app


def _authorized_headers(app, client: TestClient) -> dict[str, str]:
    exchanged = client.post(
        "/api/v1/session/exchange",
        headers={
            "X-Canvas-Bootstrap": app.state.session_manager.bootstrap_token,
            "Origin": "http://testserver",
        },
    )
    assert exchanged.status_code == 200
    return {
        "Authorization": f"Bearer {exchanged.json()['access_token']}",
        "Origin": "http://testserver",
    }


def test_event_routes_query_and_explicitly_ack_a_delivered_cursor(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Experience events route"},
            headers=headers,
        ).json()
        active = client.get(
            f"/api/v1/projects/{project['id']}/graph",
            headers=headers,
        ).json()
        proposal = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json={
                "kind": "graph_change_set",
                "schema_version": 1,
                "base_graph_revision_id": active["revision_id"],
                "client_mutation_id": str(uuid4()),
                "operations": [{"op": "replace_snapshot", "snapshot": active["snapshot"]}],
            },
            headers=headers,
        ).json()

        queried = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={"after": 0, "limit": 10, "correlation_id": proposal["correlation_id"]},
            headers=headers,
        )
        delivered = client.get(
            f"/api/v1/projects/{project['id']}/experience-event-cursors/timeline/events",
            params={"limit": 10},
            headers=headers,
        )
        acknowledged = client.put(
            f"/api/v1/projects/{project['id']}/experience-event-cursors/timeline",
            json={"ack_stream_position": delivered.json().get("next_after", 0)},
            headers=headers,
        )

    assert queried.status_code == 200, queried.text
    assert [event["event_type"] for event in queried.json()["events"]] == ["graph_change_set.requested"]
    assert delivered.status_code == 200, delivered.text
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["last_ack_stream_position"] == delivered.json()["next_after"]


def test_event_cursor_ack_rejects_an_undelivered_position_with_stable_code(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(
        app,
        base_url="http://testserver",
        raise_server_exceptions=False,
    ) as client:
        headers = _authorized_headers(app, client)
        project = client.post(
            "/api/v1/projects",
            json={"name": "Experience events cursor error"},
            headers=headers,
        ).json()
        response = client.put(
            f"/api/v1/projects/{project['id']}/experience-event-cursors/timeline",
            json={"ack_stream_position": 1},
            headers=headers,
        )

    assert response.status_code == 409, response.text
    assert response.json() == {"code": "event_cursor_ack_out_of_range"}


def test_event_query_filters_a_comma_separated_known_type_list(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver") as client:
        headers = _authorized_headers(app, client)
        project = client.post("/api/v1/projects", json={"name": "Event type filter"}, headers=headers).json()
        active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
        proposal = client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets",
            json={
                "kind": "graph_change_set",
                "schema_version": 1,
                "base_graph_revision_id": active["revision_id"],
                "client_mutation_id": str(uuid4()),
                "operations": [{"op": "replace_snapshot", "snapshot": active["snapshot"]}],
            },
            headers=headers,
        ).json()
        client.post(
            f"/api/v1/projects/{project['id']}/graph-change-sets/{proposal['id']}/approval-decisions",
            json={"expected_changeset_sha256": proposal["changeset_sha256"]},
            headers=headers,
        )

        filtered = client.get(
            f"/api/v1/projects/{project['id']}/experience-events",
            params={
                "correlation_id": proposal["correlation_id"],
                "types": "approval.recorded",
            },
            headers=headers,
        )

    assert filtered.status_code == 200, filtered.text
    assert [event["event_type"] for event in filtered.json()["events"]] == ["approval.recorded"]


def test_cursor_ack_rejects_a_global_position_owned_by_another_project(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, public_origin="http://testserver"))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as client:
        headers = _authorized_headers(app, client)
        first = client.post("/api/v1/projects", json={"name": "Cursor owner A"}, headers=headers).json()
        second = client.post("/api/v1/projects", json={"name": "Cursor owner B"}, headers=headers).json()

        def propose(project: dict) -> dict:
            active = client.get(f"/api/v1/projects/{project['id']}/graph", headers=headers).json()
            return client.post(
                f"/api/v1/projects/{project['id']}/graph-change-sets",
                json={
                    "kind": "graph_change_set",
                    "schema_version": 1,
                    "base_graph_revision_id": active["revision_id"],
                    "client_mutation_id": str(uuid4()),
                    "operations": [{"op": "replace_snapshot", "snapshot": active["snapshot"]}],
                },
                headers=headers,
            ).json()

        propose(first)
        other = propose(second)
        propose(first)
        delivered = client.get(
            f"/api/v1/projects/{first['id']}/experience-event-cursors/timeline/events",
            params={"limit": 10},
            headers=headers,
        ).json()
        other_position = client.get(
            f"/api/v1/projects/{second['id']}/experience-events",
            params={"correlation_id": other["correlation_id"]},
            headers=headers,
        ).json()["events"][0]["stream_position"]
        assert 0 < other_position < delivered["next_after"]

        rejected = client.put(
            f"/api/v1/projects/{first['id']}/experience-event-cursors/timeline",
            json={"ack_stream_position": other_position},
            headers=headers,
        )

    assert rejected.status_code == 409, rejected.text
    assert rejected.json() == {"code": "event_cursor_ack_out_of_range"}
