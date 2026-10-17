"""双链、知识图谱、月历接口。"""

from app.logic.links import extract_links, rename_links
from tests.test_core import client  # noqa: F401  复用临时数据库夹具


def test_extract_and_rename():
    text = "见 [[进程]] 和 [[进程|这里]]，`[[代码里不算]]`\n```\n[[也不算]]\n```\n[[线程]]"
    assert extract_links(text) == ["进程", "线程"]
    new, n = rename_links(text, "进程", "进程概念")
    assert n == 2 and "[[进程概念|这里]]" in new and "`[[代码里不算]]`" in new


def test_backlinks_rename_graph_calendar(client):  # noqa: F811
    cid = client.get("/api/courses").json()[0]["id"]
    a = client.post("/api/notes", json={"course_id": cid, "title": "甲", "content": "链接到 [[乙]] 和 [[不存在]]"}).json()
    b = client.post("/api/notes", json={"course_id": cid, "title": "乙", "content": "内容"}).json()
    links = client.get(f"/api/notes/{b['id']}/links").json()
    assert [x["title"] for x in links["backlinks"]] == ["甲"]
    out = client.get(f"/api/notes/{a['id']}/links").json()["outgoing"]
    assert out[0]["note_id"] == b["id"] and out[1]["note_id"] is None

    renamed = client.patch(f"/api/notes/{b['id']}", json={"base_version": b["version"], "title": "乙2"}).json()
    assert renamed["renamed_links"] == 1
    assert "[[乙2]]" in client.get(f"/api/notes/{a['id']}").json()["content"]

    graph = client.get("/api/graph").json()
    assert {"source": a["id"], "target": b["id"]} in graph["edges"]
    assert len(graph["edges"]) >= 3  # 示例笔记之间也有双链

    cal = client.get("/api/calendar", params={"start": "2000-01-01", "end": "2100-01-01"}).json()
    assert len(cal["tasks"]) == 3 and sum(cal["reviews"].values()) == 4


def test_export_wikilinks():
    from app.logic.export import render_markdown
    html = render_markdown("见 [[进程与线程]] 和 [[不存在|别名]]，`[[代码]]`", "n1", {"进程与线程": "n2"})
    assert 'href="#n2"' in html and "别名" in html and "[[不存在" not in html and "[[代码]]" in html


def test_add_remove_link():
    from app.logic.links import add_link, remove_link
    text, changed = add_link("正文", "A")
    assert changed and text.endswith("相关笔记：[[A]]\n")
    text, _ = add_link(text, "B")
    assert "相关笔记：[[A]]、[[B]]" in text
    assert add_link(text, "a")[1] is False
    text2, n = remove_link(text + "\n见 [[A|这里]]", "A")
    assert n == 2 and "相关笔记：[[B]]" in text2 and "见 这里" in text2
    text3, _ = remove_link(text2, "B")
    assert "相关笔记" not in text3


def test_link_endpoints(client):
    notes = {n["title"]: n["id"] for n in client.get("/api/notes").json()}
    a, b = notes["泰勒公式"], notes["进程调度算法"]
    assert client.post(f"/api/notes/{a}/links", json={"target_id": b}).json()["changed"]
    edges = client.get("/api/graph").json()["edges"]
    assert {"source": a, "target": b} in edges
    assert client.delete(f"/api/notes/{a}/links/{b}").json()["removed"] == 1
    assert {"source": a, "target": b} not in client.get("/api/graph").json()["edges"]
