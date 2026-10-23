"""Check the built static deployment over HTTP, using an isolated data directory.

Run from backend/: .venv/Scripts/python.exe scripts/smoke_static.py
This does not launch a browser, pywebview, or a model request.
"""

from __future__ import annotations

import re
import sys
import tempfile
from contextlib import ExitStack
from html import unescape
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config


def main() -> None:
    pages = ("/", "/notes/", "/graph/", "/review/", "/materials/", "/materials/view/",
             "/plans/", "/settings/", "/guide/")
    assets: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="knowflow-static-") as temp, ExitStack() as stack:
        root = Path(temp)
        stack.enter_context(patch.object(config, "DATA_DIR", root))
        stack.enter_context(patch.object(config, "DB_PATH", root / "smoke.db"))
        stack.enter_context(patch.object(config, "MATERIALS_DIR", root / "materials"))
        from app.main import app

        with TestClient(app) as client:
            for page in pages:
                response = client.get(page)
                assert response.status_code == 200, (page, response.status_code)
                assert "text/html" in response.headers.get("content-type", ""), page
                assert "任务与失败记录" not in response.text, page
                assert 'href="/jobs"' not in response.text, page
                references = re.findall(r'(?:src|href)="(/_next/static/[^"<>]+)"', response.text)
                assert references, f"No built JavaScript/CSS in {page}"
                assets.update(unescape(reference) for reference in references)
            for asset in sorted(assets):
                response = client.get(asset)
                assert response.status_code == 200, (asset, response.status_code)
                assert response.content, asset
                assert "text/html" not in response.headers.get("content-type", ""), asset
            notes = client.get("/api/notes/page?limit=5")
            assert notes.status_code == 200 and isinstance(notes.json()["items"], list)
            assert client.get("/jobs/").status_code == 404
            assert client.get("/api/jobs?limit=5").status_code == 404
            assert client.get("/api/jobs/ai/removed/retry-input").status_code == 404
            assert client.post("/api/jobs/ai/removed/cancel").status_code == 404
            assert client.get("/api/does-not-exist").status_code == 404
    print(f"Static HTTP smoke passed: {len(pages)} pages, {len(assets)} assets, isolated API startup.")


if __name__ == "__main__":
    main()
