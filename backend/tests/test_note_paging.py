"""Pagination filters before slicing and preserves the legacy list contract."""

from app.db import connect
from tests.test_core import client  # noqa: F401


def add(client, course, title, content="", tags=None):
    response = client.post("/api/notes", json={"course_id": course, "title": title, "content": content, "tags": tags or []})
    assert response.status_code == 201, response.text
    return response.json()


def test_pages_cover_all_matches_once_with_stable_tie_breaking_and_correct_totals(client):
    course = client.get("/api/courses").json()[0]["id"]
    created = [add(client, course, f"Paging {index:02}", "body", ["paging"]) for index in range(9)]
    conn = connect()
    try:
        with conn:
            conn.executemany("UPDATE notes SET updated_at = '2026-01-01T12:00:00' WHERE id = ?", [(note["id"],) for note in created])
    finally:
        conn.close()
    pages = [client.get("/api/notes/page", params={"tag": "paging", "offset": offset, "limit": 4}).json() for offset in [0, 4, 8]]
    ids = [note["id"] for page in pages for note in page["items"]]
    assert ids == sorted((note["id"] for note in created), reverse=True)
    assert [len(page["items"]) for page in pages] == [4, 4, 1]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert all(page["total"] == 9 for page in pages)
    assert len(set(ids)) == 9


def test_course_exact_tag_and_content_search_apply_before_pagination(client):
    courses = client.get("/api/courses").json()
    first, second = courses[0]["id"], courses[1]["id"]
    matches = [add(client, first, f"Selected {index}", "needle deep in " + "x" * 160 + " searchable body", ["exact"]) for index in range(5)]
    add(client, second, "Wrong course", "searchable body", ["exact"])
    add(client, first, "Wrong tag", "searchable body", ["exactly"])
    add(client, first, "Wrong text", "other content", ["exact"])
    response = client.get("/api/notes/page", params={"course_id": first, "tag": "exact", "q": "searchable body", "offset": 2, "limit": 2}).json()
    assert response["total"] == 5 and len(response["items"]) == 2
    assert {item["id"] for item in response["items"]} <= {item["id"] for item in matches}
    assert all(item["course_id"] == first and item["tags"] == ["exact"] for item in response["items"])


def test_search_escapes_sql_wildcards_and_page_rejects_invalid_bounds(client):
    course = client.get("/api/courses").json()[0]["id"]
    exact = add(client, course, "Literal 100%_done", "content")
    add(client, course, "Literal 100XXdone", "content")
    response = client.get("/api/notes/page", params={"q": "100%_done"}).json()
    assert [item["id"] for item in response["items"]] == [exact["id"]]
    for params in [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"q": "x" * 101}]:
        assert client.get("/api/notes/page", params=params).status_code == 422
    beyond = client.get("/api/notes/page", params={"q": "100%_done", "offset": 99}).json()
    assert beyond["total"] == 1 and beyond["items"] == [] and beyond["has_more"] is False


def test_lightweight_index_excludes_deleted_notes_and_updates_after_rename(client):
    course = client.get("/api/courses").json()[0]["id"]
    active = add(client, course, "Index before", "large body " * 100)
    deleted = add(client, course, "Index deleted")
    client.delete(f"/api/notes/{deleted['id']}")
    client.patch(f"/api/notes/{active['id']}", json={"base_version": 1, "title": "Index after"})
    index = client.get("/api/notes/index").json()
    item = next(item for item in index if item["id"] == active["id"])
    assert item == {"id": active["id"], "title": "Index after", "course_id": course}
    assert not any(item["id"] == deleted["id"] for item in index)
    legacy = client.get("/api/notes").json()
    assert isinstance(legacy, list) and "excerpt" in legacy[0] and "char_count" in legacy[0]
