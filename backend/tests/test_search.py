"""Search keeps existing ranking, filtering and highlights while bounding snippets."""

import random

import pytest

from app import config, db
from app.logic import search as module


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "search.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    db.init_db()
    conn = db.connect()
    with conn:
        conn.executemany("INSERT INTO courses(id, name, created_at, updated_at) VALUES (?, ?, 'now', 'now')",
                         [("a", "A"), ("b", "B")])
        randomizer = random.Random(42)
        for index in range(180):
            title = randomizer.choice(["Common concept", "Unrelated title", "COMMON Common", "共同概念"])
            body = randomizer.choice(["common answer", "COMMON " * 18, "unrelated", "共同概念 common", "common %_ literal"])
            course, updated = ("a" if index % 2 else "b"), f"2026-10-{1 + index % 20:02d}"
            if index % 3 == 0:
                conn.execute("INSERT INTO notes(id, course_id, title, content, created_at, updated_at) VALUES (?, ?, ?, ?, 'now', ?)",
                             (f"note-{index}", course, title, body, updated))
            elif index % 3 == 1:
                conn.execute("INSERT INTO cards(id, course_id, front, back, due, created_at, updated_at) VALUES (?, ?, ?, ?, 'now', 'now', ?)",
                             (f"card-{index}", course, title, body, updated))
            else:
                conn.execute("INSERT INTO materials(id, course_id, original_name, display_title, format, ext, size_bytes, sha256, status, uploaded_at, updated_at) "
                             "VALUES (?, ?, 'original.txt', ?, 'text', '.txt', 0, 'hash', 'ready', 'now', ?)",
                             (f"material-{index}", course, title, updated))
                for ordinal, text in enumerate([body, body + " common common"]):
                    conn.execute("INSERT INTO material_units(id, material_id, kind, ordinal, locator, label, text) VALUES (?, ?, 'paragraph', ?, 'p', 'P', ?)",
                                 (f"unit-{index}-{ordinal}", f"material-{index}", ordinal, text))
    try:
        yield conn
    finally:
        conn.close()


def reference(conn, query, *, course=None, types=None):
    """Small exhaustive oracle: construct all candidates before stable ranking."""
    terms = module._terms(query)
    wanted = {"note", "card", "material"} if types is None else types
    rows = []
    for kind, table, title, body in [("note", "notes", "title", "content"), ("card", "cards", "front", "back")]:
        if kind not in wanted:
            continue
        for row in conn.execute(f"SELECT * FROM {table} WHERE deleted_at IS NULL"):
            if (not course or row["course_id"] == course) and all(term.lower() in (row[title] + " " + row[body]).lower() for term in terms):
                rows.append({"type": kind, "id": row["id"], "score": module._score(row[title], row[body], terms), "updated": row["updated_at"]})
    if "material" in wanted:
        best = {}
        for row in conn.execute("SELECT m.id, m.course_id, m.display_title, m.original_name, m.updated_at, u.id AS uid, u.text "
                                "FROM material_units u JOIN materials m ON m.id = u.material_id"):
            if (not course or row["course_id"] == course) and all(term.lower() in (row["display_title"] + " " + row["original_name"] + " " + row["text"]).lower() for term in terms):
                score = module._score(row["display_title"], row["text"], terms)
                if row["id"] not in best or score > best[row["id"]]["score"]:
                    best[row["id"]] = {"type": "material", "id": row["id"], "score": score, "updated": row["updated_at"], "unit_id": row["uid"]}
        # Existing category order is notes, materials, cards.
        notes = [row for row in rows if row["type"] == "note"]
        cards = [row for row in rows if row["type"] == "card"]
        rows = notes + list(best.values()) + cards
    rows.sort(key=lambda row: row["updated"], reverse=True)
    rows.sort(key=lambda row: row["score"], reverse=True)
    return [(row["type"], row["id"], row.get("unit_id")) for row in rows]


@pytest.mark.parametrize("query,course,types", [
    ("common", None, None), ("COMMON", "a", None), ("common answer", None, None),
    ("共同", None, {"note", "material"}), ("%_", None, {"card"}),
])
def test_top_results_match_exhaustive_rank_and_material_best_unit(database, query, course, types):
    expected = reference(database, query, course=course, types=types)
    for limit in [1, 7, 30, 100]:
        result = module.search(database, query, course_id=course, types=types, limit=limit)
        assert [(row["type"], row["id"], row.get("unit_id")) for row in result] == expected[:limit]
        assert all("_body" not in row and "_score" not in row for row in result)
        for row in result:
            for start, end in row["snippet"]["highlights"]:
                assert row["snippet"]["text"][start:end].lower() in module._terms(query.lower())


def test_only_visible_results_build_snippets_and_materials_are_deduplicated(database, monkeypatch):
    calls = []
    original = module.snippet
    def count(text, terms):
        calls.append(text)
        return original(text, terms)
    monkeypatch.setattr(module, "snippet", count)
    result = module.search(database, "common", limit=12)
    assert len(result) == len(calls) == 12
    assert len({(row["type"], row["id"]) for row in result}) == 12
