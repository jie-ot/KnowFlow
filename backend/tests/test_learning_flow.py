"""资料摘录、出处回溯和笔记制卡的持久化边界。"""

from tests.test_core import client  # noqa: F401


def upload_text(client, course_id, name, text):
    result = client.post("/api/materials", data={"course_id": course_id}, files=[("files", (name, text.encode(), "text/plain"))]).json()["results"][0]
    assert result["ok"]
    return client.get(f"/api/materials/{result['material_id']}").json()


def test_excerpt_source_and_card_roundtrip(client):
    course = client.get("/api/courses").json()[0]
    original = "\n".join(f"第 {i} 行：公式 x² + y² = 1，数值 3.14 不应改变。" for i in range(1, 46))
    material = upload_text(client, course["id"], "课程.txt", original)
    units = material["units"]
    assert len(units) >= 2
    response = client.post(f"/api/materials/{material['id']}/excerpt", json={"title": "圆的方程", "unit_ids": [u["id"] for u in reversed(units)] + [units[0]["id"]]})
    assert response.status_code == 201
    note = response.json()
    assert [s["unit_id"] for s in note["sources"]] == [u["id"] for u in units]
    assert all(s["available"] and s["material_id"] == material["id"] for s in note["sources"])
    for unit in units:
        assert f"&unit={unit['id']}" in note["content"]
        assert unit["text"].splitlines()[0] in note["content"]
    card = client.post("/api/cards", json={"course_id": course["id"], "source_note_id": note["id"], "front": "圆的方程是什么？", "back": "x² + y² = 1"}).json()
    queued = next(c for c in client.get("/api/review/queue").json()["items"] if c["id"] == card["id"])
    assert queued["note_title"] == "圆的方程" and queued["source_note_id"] == note["id"]
    assert client.get(f"/api/notes/{note['id']}").json()["card_count"] == 1
    client.delete(f"/api/materials/{material['id']}")
    retained = client.get(f"/api/notes/{note['id']}").json()
    assert retained["content"] == note["content"]
    assert all(not source["available"] for source in retained["sources"])


def test_invalid_excerpt_does_not_create_note(client):
    course = client.get("/api/courses").json()[0]
    first = upload_text(client, course["id"], "甲.txt", "第一份资料")
    second = upload_text(client, course["id"], "乙.txt", "第二份资料")
    before = len(client.get("/api/notes").json())
    response = client.post(f"/api/materials/{first['id']}/excerpt", json={"title": "摘录", "unit_ids": [first["units"][0]["id"], second["units"][0]["id"]]})
    assert response.status_code == 422
    assert len(client.get("/api/notes").json()) == before
    assert client.post("/api/cards", json={"course_id": course["id"], "front": "  ", "back": "答案"}).status_code == 422
    note = client.get("/api/notes").json()[0]
    other_course = next(c for c in client.get("/api/courses").json() if c["id"] != note["course_id"])
    assert client.post("/api/cards", json={"course_id": other_course["id"], "source_note_id": note["id"], "front": "问题", "back": "答案"}).status_code == 422
