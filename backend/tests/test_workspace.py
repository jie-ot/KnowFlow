"""首次启动保持空库，重启后读取同一份 SQLite 数据。"""

from fastapi.testclient import TestClient


def test_empty_workspace_and_restart(tmp_path, monkeypatch):
    from app import config
    from app.main import app

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "knowflow.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    with TestClient(app) as client:
        for endpoint in ("courses", "notes", "materials", "cards", "plans"):
            assert client.get(f"/api/{endpoint}").json() == []
        course = client.post("/api/courses", json={"name": "软件工程", "color": "green"}).json()
        note = client.post("/api/notes", json={"course_id": course["id"], "title": "需求分析", "content": "内容保存在本机数据库"}).json()

    assert config.DB_PATH.is_file()
    with TestClient(app) as client:
        assert len(client.get("/api/courses").json()) == 1
        assert client.get(f"/api/notes/{note['id']}").json()["content"] == "内容保存在本机数据库"
