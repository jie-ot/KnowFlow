# KnowFlow 后端

FastAPI + SQLite。启动：`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`，接口文档在 /docs。测试：`uv run pytest`。

回归测试使用临时数据库、临时资料文件与模拟模型传输，不读取个人数据或调用收费模型。可复现安装与测试：

```sh
uv sync --locked --group dev
uv run --locked pytest -q
```

测试默认隔离存储并拒绝未模拟的模型请求。前端构建完成后，可在此目录运行 `uv run --locked python scripts/smoke_static.py`，用临时数据检查页面、静态资源和API启动；该检查不启动真实浏览器或pywebview。

前端开发检查（Node.js 24，在 `frontend/` 执行）：`npm ci`、`npm test`、`npm run typecheck`、`npm run build`。构建会更新 `backend/app/web/`，网页和 pywebview 桌面使用相同静态文件。GitHub Actions 的 `checks.yml` 执行后端 Windows/Linux 测试与前端检查；不会发布应用。
