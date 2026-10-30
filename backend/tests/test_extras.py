"""番茄钟、周小结统计、备份与恢复。"""

import io
import json
import zipfile

from tests.test_core import client  # noqa: F401  显式加载测试示例数据


def test_focus(client):
    task = client.get("/api/tasks/today").json()["items"][0]
    r = client.post(f"/api/tasks/{task['id']}/focus", json={"minutes": 25, "started_at": "2026-10-17T10:00:00"}).json()
    assert r == {"actual_minutes": 25, "status": "in_progress"}
    r = client.post(f"/api/tasks/{task['id']}/focus", json={"minutes": 10, "started_at": "x", "done": True}).json()
    assert r == {"actual_minutes": 35, "status": "done"}
    assert client.get("/api/focus/today").json()["minutes"] == 35
    stats = client.get("/api/summaries/week-stats").json()
    assert stats["focus_minutes"] == 35 and len(stats["by_day"]) == 7


def test_backup_restore(client):
    cid = client.get("/api/courses").json()[0]["id"]
    client.post("/api/materials", data={"course_id": cid}, files=[("files", ("a.txt", b"hello\n" * 5, "text/plain"))])
    client.post("/api/ai/configs", json={"base_url": "https://api.example.com", "api_key": "sk-secret", "model": "m"})
    data = client.get("/api/backup").content
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert json.loads(zf.read("manifest.json"))["format"] == "knowflow-backup-1"
    assert b"sk-secret" not in zf.read("knowflow.db")
    assert any(n.startswith("materials/") for n in zf.namelist())

    before = len(client.get("/api/notes").json())
    client.post("/api/notes", json={"course_id": cid, "title": "备份之后新建的"})
    assert len(client.get("/api/notes").json()) == before + 1
    r = client.post("/api/restore", files={"file": ("b.zip", data, "application/zip")}).json()
    assert r["ok"] and r["materials"] >= 1
    assert len(client.get("/api/notes").json()) == before
    assert client.get("/api/ai/status").json()["configured"]  # 本机的模型配置保留
    bad = client.post("/api/restore", files={"file": ("x.zip", b"nope", "application/zip")})
    assert bad.status_code == 422


def test_weekly_overdue_excludes_tasks_due_today(client):
    from datetime import date, timedelta
    today = client.get("/api/tasks/today").json()["today"]
    plan = client.post("/api/plans", json={"goal": "检查周小结的日期边界"}).json()
    for title, offset in [("昨日未完成", -1), ("今日待完成", 0), ("明日安排", 1)]:
        response = client.post(f"/api/plans/{plan['id']}/tasks", json={
            "title": title, "kind": "read", "due_date": str(date.fromisoformat(today) + timedelta(days=offset)),
        })
        assert response.status_code == 201
    stats = client.get("/api/summaries/week-stats").json()
    overdue = {task["title"] for task in stats["tasks_overdue"]}
    assert "昨日未完成" in overdue
    assert "今日待完成" not in overdue and "明日安排" not in overdue
