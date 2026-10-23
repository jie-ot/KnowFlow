"""Stable identities, legacy ambiguity, reversible deletion, export and concurrent graph writes."""

from concurrent.futures import ThreadPoolExecutor

from app.logic.links import extract_links, stable_target
from tests.test_core import client  # noqa: F401


def make_note(client, title, content=""):
    course = client.get("/api/courses").json()[0]["id"]
    result = client.post("/api/notes", json={"course_id": course, "title": title, "content": content})
    assert result.status_code == 201, result.text
    return result.json()


def links(client, note):
    response = client.get(f"/api/notes/{note['id']}/links")
    assert response.status_code == 200, response.text
    return response.json()


def edge(client, source, target):
    return any(item["source"] == source["id"] and item["target"] == target["id"] for item in client.get("/api/graph").json()["edges"])


def test_explicit_same_title_links_keep_selected_identity_and_unlink_only_it(client):
    first = make_note(client, "Identical")
    second = make_note(client, "Identical")
    source = make_note(client, "Source")
    path = f"/api/notes/{source['id']}/links"
    assert client.post(path, json={"target_id": first["id"]}).json()["changed"] is True
    assert edge(client, source, first)
    assert not edge(client, source, second)
    assert client.post(path, json={"target_id": first["id"]}).json()["changed"] is False
    assert client.post(path, json={"target_id": second["id"]}).json()["changed"] is True
    assert {item["note_id"] for item in links(client, source)["outgoing"]} == {first["id"], second["id"]}
    assert client.delete(f"{path}/{first['id']}").json()["removed"] == 1
    assert not edge(client, source, first)
    assert edge(client, source, second)


def test_ambiguous_legacy_link_requires_choice_even_after_one_candidate_is_deleted(client):
    first, second = make_note(client, "Duplicate"), make_note(client, "Duplicate")
    source = make_note(client, "Source", "See [[Duplicate|my alias]] and `[[Duplicate]]`.")
    outgoing = links(client, source)["outgoing"][0]
    assert outgoing["status"] == "ambiguous" and outgoing["note_id"] is None
    assert {item["id"] for item in outgoing["candidates"]} == {first["id"], second["id"]}
    assert not edge(client, source, first) and not edge(client, source, second)
    assert client.delete(f"/api/notes/{first['id']}").status_code == 200
    still_ambiguous = links(client, source)["outgoing"][0]
    assert still_ambiguous["status"] == "ambiguous"
    assert still_ambiguous["candidates"][0]["id"] == second["id"]
    current = client.get(f"/api/notes/{source['id']}").json()
    resolved = client.post(f"/api/notes/{source['id']}/links/resolve", json={
        "target": "Duplicate", "target_id": second["id"], "base_version": current["version"],
    })
    assert resolved.status_code == 200, resolved.text
    assert f"[[id:{second['id']}|my alias]]" in resolved.json()["content"]
    assert "`[[Duplicate]]`" in resolved.json()["content"]
    assert edge(client, source, second)


def test_unique_legacy_link_pins_before_duplicate_creation_without_prior_graph_read(client):
    original = make_note(client, "Old target")
    source = make_note(client, "Source", "[[Old target]]")
    duplicate = make_note(client, "Old target")
    assert links(client, source)["outgoing"][0]["note_id"] == original["id"]
    client.delete(f"/api/notes/{original['id']}")
    outgoing = links(client, source)["outgoing"][0]
    assert outgoing["status"] == "missing" and outgoing["bound_id"] == original["id"]
    assert not edge(client, source, duplicate)
    client.post(f"/api/notes/{original['id']}/restore")
    assert edge(client, source, original) and not edge(client, source, duplicate)


def test_unvisited_preexisting_legacy_links_are_pinned_before_duplicate_creation(client):
    from app.db import connect
    target = make_note(client, "Legacy target")
    source = make_note(client, "Source")
    # Simulate a pre-migration note whose body has never been read by the new resolver.
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE notes SET content = ? WHERE id = ?", ("[[Legacy target]]", source["id"]))
            conn.execute("DELETE FROM legacy_link_bindings WHERE source_note_id = ?", (source["id"],))
    finally:
        conn.close()
    other = make_note(client, "Legacy target")
    assert links(client, source)["outgoing"][0]["note_id"] == target["id"]
    assert not edge(client, source, other)


def test_missing_legacy_title_can_resolve_to_a_later_created_note(client):
    source = make_note(client, "Source", "[[Future note]]")
    assert links(client, source)["outgoing"][0]["status"] == "missing"
    future = make_note(client, "Future note")
    assert links(client, source)["outgoing"][0]["note_id"] == future["id"]


def test_rename_to_duplicate_title_preserves_id_alias_code_and_backlinks(client):
    target = make_note(client, "Before")
    other = make_note(client, "After")
    source = make_note(client, "Source", "[[Before]] [[Before|custom]] `[[Before]]`")
    response = client.patch(f"/api/notes/{target['id']}", json={"base_version": 1, "title": "After"})
    assert response.status_code == 200, response.text
    current = client.get(f"/api/notes/{source['id']}").json()["content"]
    assert f"[[id:{target['id']}|After]]" in current
    assert f"[[id:{target['id']}|custom]]" in current
    assert "`[[Before]]`" in current
    assert edge(client, source, target) and not edge(client, source, other)
    assert source["id"] in {item["id"] for item in links(client, target)["backlinks"]}
    assert source["id"] not in {item["id"] for item in links(client, other)["backlinks"]}


def test_purge_keeps_missing_identity_and_never_redirects_to_replacement(client):
    original = make_note(client, "Target")
    source = make_note(client, "Source", "[[Target]]")
    client.delete(f"/api/notes/{original['id']}")
    assert client.delete(f"/api/notes/{original['id']}/purge").status_code == 200
    replacement = make_note(client, "Target")
    outgoing = links(client, source)["outgoing"][0]
    assert outgoing["bound_id"] == original["id"] and outgoing["status"] == "missing"
    assert not edge(client, source, replacement)


def test_concurrent_graph_adds_do_not_lose_either_source_update(client):
    source = make_note(client, "Source")
    targets = [make_note(client, "A"), make_note(client, "B")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda target: client.post(f"/api/notes/{source['id']}/links", json={"target_id": target["id"]}), targets))
    assert [response.status_code for response in outcomes] == [200, 200]
    assert {item["note_id"] for item in links(client, source)["outgoing"]} == {item["id"] for item in targets}
    revisions = client.get(f"/api/notes/{source['id']}/revisions").json()
    assert len(revisions) == 2
    snapshots = [client.get(f"/api/notes/{source['id']}/revisions/{revision['id']}").json()["content"] for revision in revisions]
    assert sorted(len(extract_links(content)) for content in snapshots) == [0, 1]


def test_resolution_checks_source_version_without_discarding_concurrent_edits(client):
    first, second = make_note(client, "Twin"), make_note(client, "Twin")
    source = make_note(client, "Source", "[[Twin]]")
    changed = client.patch(f"/api/notes/{source['id']}", json={"base_version": 1, "content": "new text [[Twin]]"})
    assert changed.status_code == 200
    response = client.post(f"/api/notes/{source['id']}/links/resolve", json={"target": "Twin", "target_id": first["id"], "base_version": 1})
    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert client.get(f"/api/notes/{source['id']}").json()["content"] == "new text [[Twin]]"
    assert links(client, source)["outgoing"][0]["status"] == "ambiguous"


def test_export_uses_pinned_legacy_identity_and_explicit_id_with_duplicate_titles(client):
    target = make_note(client, "Same")
    source = make_note(client, "Source", "[[Same|legacy alias]]")
    other = make_note(client, "Same")
    client.post(f"/api/notes/{source['id']}/links", json={"target_id": other["id"]})
    html = client.post("/api/export/html", json={"note_ids": [source["id"], target["id"], other["id"]]}).text
    source_html = html.split('<article class="note" id="n1">')[1].split("</article>")[0]
    assert 'href="#n2">legacy alias</a>' in source_html
    assert 'href="#n3">Same</a>' in source_html


def test_exporting_only_one_ambiguous_candidate_does_not_make_the_link_unique(client):
    target = make_note(client, "Ambiguous export")
    make_note(client, "Ambiguous export")
    source = make_note(client, "Source", "[[Ambiguous export|ambiguous label]]")
    html = client.post("/api/export/html", json={"note_ids": [source["id"], target["id"]]}).text
    source_html = html.split('<article class="note" id="n1">')[1].split("</article>")[0]
    assert "ambiguous label" in source_html
    assert 'href="#n2"' not in source_html


def test_imported_duplicate_does_not_rebind_existing_legacy_links(client):
    target = make_note(client, "Imported duplicate")
    source = make_note(client, "Source", "[[Imported duplicate]]")
    result = client.post("/api/notes/import", data={"course_id": source["course_id"]}, files=[
        ("files", ("Imported duplicate.md", b"duplicate contents", "text/markdown")),
    ]).json()
    assert len(result["created"]) == 1
    assert links(client, source)["outgoing"][0]["note_id"] == target["id"]


def test_stable_target_extraction_excludes_inline_and_fenced_code():
    target = "b31d10a6-e792-453c-93d3-e15e21017785"
    token = stable_target(target)
    assert extract_links(f"[[{token}|label]] `[[{token}]]`\n```\n[[ignored]]\n```") == [token]


def test_unlink_does_not_rewrite_related_notes_examples_inside_code_blocks():
    from app.logic.links import remove_link
    token = "id:b31d10a6-e792-453c-93d3-e15e21017785"
    code = f"```\n相关笔记：[[{token}|code example]]\n```"
    text, removed = remove_link(f"{code}\n\n相关笔记：[[{token}|actual link]]", token)
    assert removed == 1 and code in text and "actual link" not in text


def test_graph_append_over_content_limit_fails_without_changing_note_or_history(client):
    source = make_note(client, "Full note", "x" * 200_000)
    target = make_note(client, "Target")
    response = client.post(f"/api/notes/{source['id']}/links", json={"target_id": target["id"]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "CONTENT_TOO_LARGE"
    unchanged = client.get(f"/api/notes/{source['id']}").json()
    assert unchanged["version"] == 1 and unchanged["content"] == source["content"]
    assert client.get(f"/api/notes/{source['id']}/revisions").json() == []


def test_resolving_legacy_link_over_content_limit_retains_original_draft(client):
    make_note(client, "Twin")
    target = make_note(client, "Twin")
    source = make_note(client, "Full note", "x" * (200_000 - len("[[Twin]]")) + "[[Twin]]")
    response = client.post(f"/api/notes/{source['id']}/links/resolve", json={"target": "Twin", "target_id": target["id"], "base_version": 1})
    assert response.status_code == 422 and response.json()["error"]["code"] == "CONTENT_TOO_LARGE"
    assert client.get(f"/api/notes/{source['id']}").json()["content"] == source["content"]
    assert links(client, source)["outgoing"][0]["status"] == "ambiguous"
