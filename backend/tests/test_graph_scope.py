"""One/two hop views traverse both ways without reversing directed edges."""

from app.db import connect, dumps, new_id, now_iso
from tests.test_core import client  # noqa: F401


def make(client, title, course=None):
    course = course or client.get("/api/courses").json()[0]["id"]
    return client.post("/api/notes", json={"course_id": course, "title": title}).json()


def join(client, source, target):
    response = client.post(f"/api/notes/{source['id']}/links", json={"target_id": target["id"]})
    assert response.status_code == 200


def test_local_graph_depth_and_original_edge_direction(client):
    a, b, c, d = [make(client, f"Scope {letter}") for letter in "ABCD"]
    join(client, a, b)
    join(client, c, b)
    join(client, d, c)
    one = client.get("/api/graph", params={"center_id": b["id"], "depth": 1}).json()
    assert {node["id"] for node in one["nodes"]} == {a["id"], b["id"], c["id"]}
    assert {"source": a["id"], "target": b["id"], "kind": "related"} in one["edges"]
    assert {"source": c["id"], "target": b["id"], "kind": "related"} in one["edges"]
    assert not any(edge["source"] == b["id"] and edge["target"] == a["id"] for edge in one["edges"])
    two = client.get("/api/graph", params={"center_id": b["id"], "depth": 2}).json()
    assert {node["id"] for node in two["nodes"]} == {item["id"] for item in [a, b, c, d]}
    assert {"source": d["id"], "target": c["id"], "kind": "related"} in two["edges"]
    assert two["center_id"] == b["id"] and two["depth"] == 2
    assert one["total_nodes"] >= 4 and one["total_edges"] >= 3


def test_scope_excludes_deleted_nodes_and_cross_course_targets(client):
    courses = client.get("/api/courses").json()
    first, second = courses[0]["id"], courses[1]["id"]
    center, neighbor, cross = make(client, "Center", first), make(client, "Neighbor", first), make(client, "Cross", second)
    join(client, center, neighbor)
    join(client, center, cross)
    client.delete(f"/api/notes/{neighbor['id']}")
    scoped = client.get("/api/graph", params={"center_id": center["id"], "course_id": first}).json()
    assert [node["id"] for node in scoped["nodes"]] == [center["id"]]
    assert scoped["edges"] == []
    assert client.get("/api/graph", params={"center_id": neighbor["id"]}).status_code == 404
    assert client.get("/api/graph", params={"center_id": cross["id"], "course_id": first}).status_code == 404
    assert client.get("/api/graph", params={"center_id": center["id"], "depth": 3}).status_code == 422


def test_auto_scope_is_explicit_and_full_graph_remains_available(client):
    course = client.get("/api/courses").json()[0]["id"]
    conn = connect()
    try:
        with conn:
            conn.executemany("INSERT INTO notes(id,course_id,title,content,tags,version,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", [
                (new_id(), course, f"Auto {index}", "", dumps([]), now_iso(), now_iso()) for index in range(302)
            ])
    finally:
        conn.close()
    auto = client.get("/api/graph", params={"auto_local": "true"}).json()
    assert auto["center_id"] is not None and auto["depth"] == 1
    assert len(auto["nodes"]) < auto["total_nodes"]
    full = client.get("/api/graph").json()
    assert full["center_id"] is None and len(full["nodes"]) == full["total_nodes"]
