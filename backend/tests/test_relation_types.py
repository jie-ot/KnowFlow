"""Relation types are directed metadata with reversible body edits and identity stability."""

import pytest

from tests.test_core import client  # noqa: F401


def make(client, title, content=""):
    course = client.get("/api/courses").json()[0]["id"]
    response = client.post("/api/notes", json={"course_id": course, "title": title, "content": content})
    assert response.status_code == 201
    return response.json()


def relation(client, source, target):
    return next((edge for edge in client.get("/api/graph").json()["edges"] if edge["source"] == source["id"] and edge["target"] == target["id"]), None)


def connect(client, source, target, kind=None):
    return client.post(f"/api/notes/{source['id']}/links", json={"target_id": target["id"], **({"kind": kind} if kind else {})})


@pytest.mark.parametrize("kind", ["related", "prerequisite", "contains", "applies"])
def test_create_and_edit_each_directed_relation_kind(client, kind):
    a, b = make(client, "A"), make(client, "B")
    assert connect(client, a, b, kind).status_code == 200
    assert relation(client, a, b)["kind"] == kind
    assert relation(client, b, a) is None
    assert connect(client, b, a, "contains").status_code == 200
    assert relation(client, b, a)["kind"] == "contains"
    response = client.patch(f"/api/notes/{a['id']}/links/{b['id']}", json={"kind": "applies"})
    assert response.status_code == 200
    assert relation(client, a, b)["kind"] == "applies"
    assert relation(client, b, a)["kind"] == "contains"


def test_legacy_links_default_to_related_and_repeated_add_preserves_explicit_type(client):
    target = make(client, "Legacy target")
    source = make(client, "Source", "[[Legacy target]]")
    assert relation(client, source, target)["kind"] == "related"
    assert client.patch(f"/api/notes/{source['id']}/links/{target['id']}", json={"kind": "prerequisite"}).status_code == 200
    assert connect(client, source, target).json()["changed"] is False
    assert relation(client, source, target)["kind"] == "prerequisite"
    assert connect(client, source, target, "contains").json()["changed"] is True
    assert relation(client, source, target)["kind"] == "contains"


def test_typed_relation_survives_rename_delete_restore_and_local_view(client):
    source, target = make(client, "Source"), make(client, "Target")
    connect(client, source, target, "prerequisite")
    assert client.patch(f"/api/notes/{target['id']}", json={"base_version": 1, "title": "Renamed target"}).status_code == 200
    assert relation(client, source, target)["kind"] == "prerequisite"
    client.delete(f"/api/notes/{target['id']}")
    assert relation(client, source, target) is None
    client.post(f"/api/notes/{target['id']}/restore")
    assert relation(client, source, target)["kind"] == "prerequisite"
    local = client.get("/api/graph", params={"center_id": target["id"], "depth": 1}).json()
    assert {"source": source["id"], "target": target["id"], "kind": "prerequisite"} in local["edges"]
    client.delete(f"/api/notes/{source['id']}")
    client.post(f"/api/notes/{source['id']}/restore")
    assert relation(client, source, target)["kind"] == "prerequisite"


def test_unlink_cleans_metadata_and_revision_restore_undoes_both_body_and_type(client):
    source, target = make(client, "Source"), make(client, "Target")
    connect(client, source, target, "contains")
    assert client.delete(f"/api/notes/{source['id']}/links/{target['id']}").json()["removed"] == 1
    assert relation(client, source, target) is None
    from app.db import connect as db_connect
    conn = db_connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM note_relation_types WHERE source_note_id = ?", (source["id"],)).fetchone()[0] == 0
    finally:
        conn.close()
    revisions = client.get(f"/api/notes/{source['id']}/revisions").json()
    revision = next(item for item in revisions if client.get(f"/api/notes/{source['id']}/revisions/{item['id']}").json()["content"])
    restored = client.post(f"/api/notes/{source['id']}/revisions/{revision['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert relation(client, source, target)["kind"] == "contains"


def test_textual_body_removal_cleans_type_and_readding_starts_related(client):
    source, target = make(client, "Source"), make(client, "Target")
    connect(client, source, target, "applies")
    latest = client.get(f"/api/notes/{source['id']}").json()
    assert client.patch(f"/api/notes/{source['id']}", json={"base_version": latest["version"], "content": "still other prose"}).status_code == 200
    connect(client, source, target)
    assert relation(client, source, target)["kind"] == "related"


def test_restoring_typed_revision_while_target_is_in_trash_preserves_its_future_type(client):
    source, target = make(client, "Source"), make(client, "Target")
    connect(client, source, target, "applies")
    client.delete(f"/api/notes/{source['id']}/links/{target['id']}")
    revisions = client.get(f"/api/notes/{source['id']}/revisions").json()
    typed_revision = next(item for item in revisions if client.get(f"/api/notes/{source['id']}/revisions/{item['id']}").json()["content"])
    client.delete(f"/api/notes/{target['id']}")
    assert client.post(f"/api/notes/{source['id']}/revisions/{typed_revision['id']}/restore").status_code == 200
    assert relation(client, source, target) is None
    client.post(f"/api/notes/{target['id']}/restore")
    assert relation(client, source, target)["kind"] == "applies"


def test_invalid_kind_and_nonexistent_direction_are_rejected_without_creating_data(client):
    source, target = make(client, "Source"), make(client, "Target")
    assert connect(client, source, target, "invalid").status_code == 422
    assert client.patch(f"/api/notes/{source['id']}/links/{target['id']}", json={"kind": "contains"}).status_code == 404
    assert relation(client, source, target) is None


def test_purging_a_target_cascades_relation_metadata(client):
    source, target = make(client, "Source"), make(client, "Target")
    connect(client, source, target, "contains")
    client.delete(f"/api/notes/{target['id']}")
    assert client.delete(f"/api/notes/{target['id']}/purge").status_code == 200
    from app.db import connect as db_connect
    conn = db_connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM note_relation_types WHERE target_note_id = ?", (target["id"],)).fetchone()[0] == 0
    finally:
        conn.close()
