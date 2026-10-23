"""Reproducible API benchmark; all databases and material files are temporary.

Run from backend: .venv/Scripts/python.exe scripts/benchmark.py --output ../docs/performance-baseline.json
The first request is reported separately. OS page caches are not flushed.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import platform
import pstats
import io
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config, db


def _id(kind: str, index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"knowflow-benchmark/{kind}/{index}"))


def populate(notes: int, cards: int, materials: int, units: int) -> dict:
    db.init_db()
    paragraph = ("common benchmark learning retention prerequisite application. "
                 "通过例子理解知识，并通过复习检验掌握情况。资料中的学习步骤应保留来源，笔记之间通过稳定链接关联。\n") * 22
    conn = db.connect()
    try:
        with conn:
            conn.executemany("INSERT INTO courses(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                             [(f"course-{i}", f"Course {i}", "2026-10-01", "2026-10-01") for i in range(10)])
            conn.executemany(
                "INSERT INTO notes(id, course_id, title, content, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(_id("note", i), f"course-{i % 10}", f"Knowledge {i:05d}",
                  f"# Knowledge {i:05d}\nneedle{i % 97:04d}\n" + paragraph +
                  f"\n[[{'Knowledge ' + format((i + 1) % notes, '05d') if i % 4 == 0 else 'id:' + _id('note', (i + 1) % notes)}|Next]] " +
                  f"[[id:{_id('note', (i + 7) % notes)}|Application]]",
                  json.dumps([f"topic-{i % 8}"]), "2026-10-01", f"2026-10-{1 + i % 20:02d}") for i in range(notes)],
            )
            conn.executemany(
                "INSERT INTO cards(id, course_id, source_note_id, front, back, due, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(_id("card", i), f"course-{i % 10}", _id("note", i % notes),
                  f"common retention question {i:05d}", f"needle{i % 97:04d} common explanation " + paragraph[:500],
                  "2026-10-01", "2026-10-01", f"2026-10-{1 + i % 20:02d}") for i in range(cards)],
            )
            source = ("Synthetic material, never real study data. " + paragraph).encode("utf-8")
            conn.executemany(
                "INSERT INTO materials(id, course_id, original_name, display_title, format, ext, size_bytes, sha256, status, "
                "unit_count, char_count, uploaded_at, updated_at) VALUES (?, ?, ?, ?, 'text', '.txt', ?, ?, 'ready', ?, ?, ?, ?)",
                [(_id("material", i), f"course-{i % 10}", f"source-{i}.txt", f"common material {i:04d}", len(source),
                  hashlib.sha256(source).hexdigest(), units // materials + int(i < units % materials), len(paragraph),
                  "2026-10-01", f"2026-10-{1 + i % 20:02d}") for i in range(materials)],
            )
            conn.executemany(
                "INSERT INTO material_units(id, material_id, kind, ordinal, locator, label, text) VALUES (?, ?, 'paragraph', ?, ?, ?, ?)",
                [(_id("unit", i), _id("material", i % materials), i // materials,
                  f"paragraph:{i // materials}", f"Paragraph {i // materials}",
                  f"needle{i % 97:04d} common material source " + paragraph[:1100]) for i in range(units)],
            )
        for i in range(materials):
            (config.MATERIALS_DIR / (_id("material", i) + ".txt")).write_bytes(source)
        return {"notes": notes, "cards": cards, "materials": materials, "material_units": units,
                "links": 2 * notes, "note_content_characters": conn.execute("SELECT SUM(length(content)) FROM notes").fetchone()[0]}
    finally:
        conn.close()


def measure(client: TestClient, path: str, rounds: int) -> dict:
    elapsed: list[float] = []
    payloads: list[int] = []
    item_count = None
    for _ in range(rounds + 1):
        start = time.perf_counter()
        response = client.get(path)
        elapsed.append((time.perf_counter() - start) * 1000)
        response.raise_for_status()
        payloads.append(len(response.content))
        body = response.json()
        item_count = len(body) if isinstance(body, list) else len(body.get("items", body.get("nodes", [])))
    warm = sorted(elapsed[1:])
    return {"path": path, "first_ms": round(elapsed[0], 3), "warm_median_ms": round(statistics.median(warm), 3),
            "warm_p95_ms": round(warm[math.ceil(len(warm) * .95) - 1], 3),
            "payload_bytes": int(statistics.median(payloads)), "returned_items": item_count,
            "warm_samples_ms": [round(value, 3) for value in elapsed[1:]]}


def run(args) -> dict:
    saved = config.DATA_DIR, config.DB_PATH, config.MATERIALS_DIR
    try:
        with tempfile.TemporaryDirectory(prefix="knowflow-benchmark-") as temp:
            config.DATA_DIR = Path(temp)
            config.DB_PATH = Path(temp) / "benchmark.db"
            config.MATERIALS_DIR = Path(temp) / "materials"
            dataset = populate(args.notes, args.cards, args.materials, args.units)
            from app.main import app
            targets = [
                ("notes_all", "/api/notes"), ("notes_course", "/api/notes?course_id=course-1"),
                ("search_common", "/api/search?q=common&limit=30"),
                ("search_selective", "/api/search?q=needle0042&limit=30"),
                ("search_multiple", "/api/search?q=common%20retention&limit=30"),
                ("graph_all", "/api/graph"), ("graph_course", "/api/graph?course_id=course-1"),
            ]
            if args.extra_path:
                targets.extend((f"extra_{index}", path) for index, path in enumerate(args.extra_path))
            if args.only:
                wanted = set(args.only.split(","))
                targets = [(name, path) for name, path in targets if name in wanted]
            with TestClient(app) as client:
                measurements = {}
                for name, path in targets:
                    measurements[name] = measure(client, path, args.rounds)
                    print(name + ": " + json.dumps({k: v for k, v in measurements[name].items() if k != "warm_samples_ms"}), flush=True)
            profile_text = None
            if args.profile_search:
                from app.logic.search import search
                conn = db.connect()
                profile = cProfile.Profile()
                try:
                    profile.runcall(search, conn, "common", limit=30)
                    output = io.StringIO()
                    pstats.Stats(profile, stream=output).sort_stats("cumulative").print_stats(20)
                    profile_text = output.getvalue()
                    print(profile_text, flush=True)
                finally:
                    conn.close()
            return {"dataset": dataset, "environment": {"python": platform.python_version(), "platform": platform.platform(),
                    "sqlite": db.sqlite3.sqlite_version, "app_version": config.APP_VERSION}, "warm_rounds": args.rounds,
                    "method": "TestClient wall time includes routing, SQLite connection/query, response serialization and middleware. First request is not an OS-cold-cache measurement. OS caches are uncontrolled; all data and source files are synthetic and temporary.",
                    "measurements": measurements, "search_profile": profile_text}
    finally:
        config.DATA_DIR, config.DB_PATH, config.MATERIALS_DIR = saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=int, default=5000)
    parser.add_argument("--cards", type=int, default=10000)
    parser.add_argument("--materials", type=int, default=1000)
    parser.add_argument("--units", type=int, default=10000)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--extra-path", action="append", default=[])
    parser.add_argument("--only", help="comma-separated measurement names, e.g. search_common,search_selective")
    parser.add_argument("--profile-search", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.notes, args.cards, args.materials, args.units, args.rounds) < 1:
        parser.error("all dataset counts and rounds must be positive")
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
